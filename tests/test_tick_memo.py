"""The tick prepares its history once per input state and writes player pages only when they change;
evidence replays persist across processes, keyed on what they read and on the code."""
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from nba_forecast import operations, player_sync, replay_cache, tick_memo
from nba_forecast.operations import _merge_finals, run_tick
from nba_forecast.player_sync import sync_player_data
from nba_forecast.schedule import normalize_season
from tests.test_model import row
from tests.test_schedule import payload, game

BOX = 'https://cdn.nba.com/static/json/liveData/boxscore/boxscore_00226000{:02d}.json'


def _history():
    return [row(i, pid=pid, team=team, season='2024-25') for i in range(6) for pid, team in (('1', 'A'), ('2', 'A'), ('3', 'B'))]


def _final(folder, i, pid=None, **changes):
    rows = [dict(r, **changes) if pid in (None, r['player_id']) else r for r in _history() if r['game_id'] == f'g{i}']
    (folder/'finals').mkdir(exist_ok=True)
    (folder/'finals'/f'g{i}.json').write_text(json.dumps(rows))
    return rows


def _provenance(folder, name, url):
    (folder/'raw').mkdir(exist_ok=True)
    (folder/'raw'/(name+'.provenance.json')).write_text(json.dumps(dict(source_url=url, sha256=name, retrieved_at='2025-03-02T00:00:00+00:00', bytes=2)))
    (folder/'raw'/(name+'.json')).write_text('{}')


def _naive_merge(history, finals):
    """``_merge_finals`` as it was before players were indexed by game, without error handling."""
    combined = {(r['game_id'], r['player_id']): r for r in history}
    for gid, rows in finals:
        players = {r['player_id'] for r in rows}
        for key in [k for k in combined if k[0] == gid and k[1] not in players]:
            del combined[key]
        for r in rows:
            key = (r['game_id'], r['player_id'])
            old = combined.get(key)
            if old is None or old['known_at'] <= r['known_at']:
                combined[key] = r
    return list(combined.values())


def _shifted_final(folder, i):
    """A captured final whose tipoff disagrees with the input history's (a game-identity mismatch)."""
    return _final(folder, i, game_time=f'2025-03-{i % 28 + 1:02d}T19:00:00+00:00')


class Stopped(BaseException):
    """The process is killed here (not an error any handler catches)."""


def _tick_patches(stack, schedule):
    for target, value in (('nba_forecast.operations._now', dict(return_value='2026-10-21T23:00:00Z')),
                          ('nba_forecast.operations.fetch_schedule', dict(return_value=schedule)),
                          ('nba_forecast.operations.fetch_roster', {})):
        stack.enter_context(patch(target, **value))


def _tick_folder(d):
    folder = Path(d)
    _final(folder, 2, minutes=31)
    (folder/'roster.json').write_text(json.dumps(dict(observed_at='2026-10-21T00:00:00Z', players=[
        dict(player_id='1', name='1', team_id='A', status='unknown')])))
    schedule = normalize_season(payload([game(start='2026-10-22T22:00:00Z')]), '2026-10-21T16:00:00Z')
    schedule['version'] = 'v1'
    return folder, schedule


class HistoryMemoTests(unittest.TestCase):
    def setUp(self):
        replay_cache.clear()

    def test_indexed_merge_keeps_the_rows_and_their_order(self):
        rng = random.Random(7)
        for trial in range(25):
            history = [row(i, pid=str(p), team='A', season='2024-25') for i in range(8) for p in range(rng.randint(1, 4))]
            rng.shuffle(history)
            with tempfile.TemporaryDirectory() as d:
                finals = []
                for i in sorted(rng.sample(range(10), 5)):
                    rows = [row(i, pid=str(p), team='A', season='2024-25', known_at='2025-04-01T00:00:00+00:00', minutes=20+p)
                            for p in rng.sample(range(5), rng.randint(1, 4))]
                    (Path(d)/f'g{i}.json').write_text(json.dumps(rows))
                    finals.append((f'g{i}', rows))
                merged = _merge_finals(history, Path(d))
            self.assertEqual(merged, _naive_merge(history, sorted(finals)), trial)

    def test_unchanged_inputs_skip_enrichment_merge_and_validation_in_a_later_tick(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            _final(folder, 2, minutes=31)
            first = tick_memo.HistoryMemo(folder, _history(), 'k')
            first.validate_input()
            merged, skipped, errors, key, failure = first.merged()
            self.assertIsNone(failure)
            with patch('nba_forecast.player_sync.enrich_final_cache', side_effect=AssertionError('enriched again')), \
                 patch('nba_forecast.operations._merge_finals', side_effect=AssertionError('merged again')), \
                 patch('nba_forecast.validation.validate_history', side_effect=AssertionError('validated again')):
                later = tick_memo.HistoryMemo(folder, _history(), 'k')
                later.validate_input()
                again = later.merged()
            self.assertEqual(again[0], merged)
            self.assertEqual(again[0], _merge_finals(_history(), folder/'finals'))
            self.assertEqual(again[1:4], (skipped, errors, key))

    def test_a_changed_final_or_input_history_merges_again(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            _final(folder, 2, minutes=31)
            merged = tick_memo.HistoryMemo(folder, _history(), 'k').merged()[0]
            _final(folder, 3, minutes=32)
            changed = tick_memo.HistoryMemo(folder, _history(), 'k').merged()[0]
            self.assertNotEqual(changed, merged)
            self.assertEqual(changed, _merge_finals(_history(), folder/'finals'))
            other = _history()[:9]
            self.assertEqual(tick_memo.HistoryMemo(folder, other, 'k2').merged()[0], _merge_finals(other, folder/'finals'))

    def test_a_new_box_score_archive_enriches_again_and_other_archives_do_not(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            _final(folder, 2)
            calls = []
            real = player_sync.enrich_final_cache
            with patch('nba_forecast.player_sync.enrich_final_cache', side_effect=lambda *a, **k: calls.append(1) or real(*a, **k)):
                tick_memo.HistoryMemo(folder, _history(), 'k').merged()
                _provenance(folder, 'a'*64, 'https://ak-static.cms.nba.com/referee/injury/report.pdf')
                tick_memo.HistoryMemo(folder, _history(), 'k').merged()
                self.assertEqual(len(calls), 1)
                _provenance(folder, 'b'*64, BOX.format(2))
                tick_memo.HistoryMemo(folder, _history(), 'k').merged()
                self.assertEqual(len(calls), 2)
                tick_memo.HistoryMemo(folder, _history(), 'k').merged()
                self.assertEqual(len(calls), 2)

    def test_enrichment_warnings_are_reported_again_when_it_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            _final(folder, 2)
            _provenance(folder, 'c'*64, BOX.format(2))
            first = tick_memo.HistoryMemo(folder, _history(), 'k').merged()[1]
            self.assertTrue(first)
            self.assertEqual(tick_memo.HistoryMemo(folder, _history(), 'k').merged()[1], first)

    def test_an_enrichment_failure_is_returned_and_not_kept(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            _final(folder, 2)
            with patch('nba_forecast.player_sync.enrich_final_cache', side_effect=RuntimeError('boom')):
                merged, skipped, errors, key, failure = tick_memo.HistoryMemo(folder, _history(), 'k').merged()
            self.assertIsInstance(failure, RuntimeError)
            self.assertEqual(merged, _merge_finals(_history(), folder/'finals'))
            calls = []
            with patch('nba_forecast.player_sync.enrich_final_cache', side_effect=lambda *a, **k: calls.append(1) or []):
                self.assertIsNone(tick_memo.HistoryMemo(folder, _history(), 'k').merged()[4])
            self.assertEqual(calls, [1])

    def test_an_unreadable_stored_history_is_merged_again(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            _final(folder, 2, minutes=31)
            merged = tick_memo.HistoryMemo(folder, _history(), 'k').merged()[0]
            for path in (folder/'cache'/'history').glob('*.pickle'):
                path.write_bytes(b'not a pickle')
            self.assertEqual(tick_memo.HistoryMemo(folder, _history(), 'k').merged()[0], merged)

    def test_history_key_follows_the_files(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'history.json'
            path.write_text('[]')
            before = tick_memo.history_key([path])
            self.assertEqual(before, tick_memo.history_key([path]))
            path.write_text('[ ]')
            self.assertNotEqual(before, tick_memo.history_key([path]))
            self.assertIsNone(tick_memo.history_key([Path(d)/'missing.json']))

    def test_the_tick_enriches_and_merges_once_and_a_quiet_tick_not_at_all(self):
        schedule = normalize_season(payload([game(start='2026-10-22T22:00:00Z')]), '2026-10-21T16:00:00Z')
        schedule['version'] = 'v1'
        enrich, merge = [], []
        real_enrich, real_merge = player_sync.enrich_final_cache, operations._merge_finals
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            _final(folder, 2, minutes=31)
            (folder/'roster.json').write_text(json.dumps(dict(observed_at='2026-10-21T00:00:00Z', players=[
                dict(player_id='1', name='1', team_id='A', status='unknown')])))
            with patch('nba_forecast.operations._now', return_value='2026-10-21T23:00:00Z'), \
                 patch('nba_forecast.operations.fetch_schedule', return_value=schedule), \
                 patch('nba_forecast.operations.fetch_roster'), \
                 patch('nba_forecast.player_sync.enrich_final_cache', side_effect=lambda *a, **k: enrich.append(1) or real_enrich(*a, **k)), \
                 patch('nba_forecast.operations._merge_finals', side_effect=lambda *a, **k: merge.append(1) or real_merge(*a, **k)):
                first = run_tick(d, _history(), history_key='k')
                self.assertEqual((len(enrich), len(merge)), (1, 1))
                second = run_tick(d, _history(), history_key='k')
                self.assertEqual((len(enrich), len(merge)), (1, 1))
        self.assertEqual(first['status'], 'complete', first['errors'])
        self.assertEqual(second['status'], 'complete')
        self.assertEqual(first['player_sync']['history_rows'], second['player_sync']['history_rows'])


    def test_a_stored_history_whose_bytes_changed_is_merged_again(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            _final(folder, 2, minutes=31)
            merged = tick_memo.HistoryMemo(folder, _history(), 'k').merged()[0]
            (stored,) = (folder/'cache'/'history').glob('*.pickle')
            raw = stored.read_bytes()
            self.assertIn(b'example.test', raw)
            # Still a valid pickle of a list of rows, with different content.
            stored.write_bytes(raw.replace(b'example.test', b'exbmple.test'))
            memo = tick_memo.HistoryMemo(folder, _history(), 'k')
            self.assertEqual(memo.merged()[0], merged)
            self.assertEqual(memo.warnings()[0]['task'], 'history_memo')
            self.assertEqual(tick_memo.HistoryMemo(folder, _history(), 'k').merged()[0], merged)

    def test_an_unwritable_cache_does_not_stop_the_tick(self):
        with tempfile.TemporaryDirectory() as d, ExitStack() as stack:
            folder, schedule = _tick_folder(d)
            (folder/'cache').write_text('not a folder')  # every write under cache/ fails
            _tick_patches(stack, schedule)
            first = run_tick(d, _history(), history_key='k')
            second = run_tick(d, _history(), history_key='k')
        for report in (first, second):
            self.assertEqual(report['status'], 'complete', report['errors'])
            self.assertTrue(any(w.get('task') == 'history_memo' for w in report['warnings']))
            self.assertEqual(report['player_sync']['history_rows'], len(_merge_finals(_history(), folder/'finals')))

    def test_a_malformed_record_is_no_record(self):
        with tempfile.TemporaryDirectory() as d, ExitStack() as stack:
            folder, schedule = _tick_folder(d)
            (folder/'cache').mkdir()
            stamp = dict(code=replay_cache.code_digest())
            (folder/tick_memo.HISTORY_RECORD).write_text(json.dumps(dict(stamp, version=tick_memo.VERSION, enrich='bad', merge=[])))
            (folder/player_sync.PAGE_RECORD).write_text(json.dumps(dict(stamp, version=player_sync.PAGE_VERSION, pages=[])))
            _tick_patches(stack, schedule)
            report = run_tick(d, _history(), history_key='k')
            self.assertEqual(report['status'], 'complete', report['errors'])
            self.assertTrue(any(w.get('task') == 'history_memo' for w in report['warnings']))
            self.assertEqual(tick_memo.HistoryMemo(folder, _history(), 'k').merged()[0], _merge_finals(_history(), folder/'finals'))
            self.assertTrue((folder/'players'/'1.json').exists())

    def test_stale_temporary_files_and_old_stores_are_removed(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            store = folder/'cache'/'history'
            store.mkdir(parents=True)
            old, young = store/'.a.pickle.1.1.tmp', store/'.b.pickle.2.2.tmp'
            for path in (old, young):
                path.write_bytes(b'partial')
            os.utime(old, (0, 0))
            tick_memo.HistoryMemo(folder, _history(), 'k').merged()
            self.assertFalse(old.exists())
            self.assertTrue(young.exists())
            # An old replay store whose removal fails is removed by a later save of the same process.
            replay_cache.clear()
            other = folder/'cache'/'replay'/('0'*16)
            (other/'stuck').mkdir(parents=True)
            replay_cache.replay(('k', 1), lambda: 1, d)
            self.assertTrue(other.exists())
            (other/'stuck').rmdir()
            (other/'entry.pickle').write_bytes(b'x')
            replay_cache.replay(('k', 2), lambda: 2, d)
            self.assertFalse(other.exists())
            replay_cache.clear()


class PlayerPageTests(unittest.TestCase):
    def setUp(self):
        replay_cache.clear()

    def _sync(self, folder, when, memo=True, history=None):
        roster = dict(observed_at='2025-03-01T00:00:00Z', players=[dict(player_id=p, name=p, team_id=t, status='unknown')
                                                                    for p, t in (('1', 'A'), ('2', 'A'), ('3', 'B'))])
        history = history or _history()
        schedule = dict(season='2024-25', games=[])
        memo = tick_memo.HistoryMemo(folder, history, 'k') if memo else None
        with patch('nba_forecast.player_sync.datetime') as clock:
            clock.now.return_value = when
            return sync_player_data(folder, history, roster=roster, schedule=schedule, max_shot_games=0, memo=memo)

    def test_the_sync_uses_the_ticks_merge(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            memo = tick_memo.HistoryMemo(folder, _history(), 'k')
            merged = memo.merged()[0]
            with patch('nba_forecast.player_sync.enrich_final_cache', side_effect=AssertionError('enriched twice')), \
                 patch('nba_forecast.operations._merge_finals', side_effect=AssertionError('merged twice')):
                summary = sync_player_data(folder, merged, roster=dict(observed_at='2025-03-01T00:00:00Z', players=[]),
                                           schedule=dict(season='2024-25', games=[]), max_shot_games=0, memo=memo)
            self.assertEqual(summary['history_rows'], len(merged))

    def test_pages_are_written_only_when_their_content_changes(self):
        t1, t2, t3 = (datetime(2025, 4, day, tzinfo=timezone.utc) for day in (1, 2, 3))
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            first = self._sync(folder, t1)
            stamps = {p.name: p.stat().st_mtime_ns for p in (folder/'players').glob('*.json')}
            with patch('nba_forecast.player_sync.build_player_pages', side_effect=AssertionError('rebuilt unchanged pages')):
                second = self._sync(folder, t2)
            self.assertEqual(second, first)
            self.assertEqual({p.name: p.stat().st_mtime_ns for p in (folder/'players').glob('*.json')}, stamps)
            page = json.loads((folder/'players'/'1.json').read_text())
            self.assertEqual(page['as_of'], t1.isoformat())
            # A corrected final changes player 1's page only; its as_of is when it changed.
            _final(folder, 2, pid='1', minutes=33)
            third = self._sync(folder, t3)
            pages = {p.stem: json.loads(p.read_text()) for p in (folder/'players').glob('*.json')}
            self.assertEqual(pages['1']['as_of'], t3.isoformat())
            self.assertEqual(pages['3']['as_of'], t1.isoformat())
            self.assertEqual(third['as_of'], t3.isoformat())
            self.assertEqual(third['player_summaries']['3']['as_of'], t1.isoformat())

    def test_pages_match_a_full_rebuild_apart_from_as_of(self):
        t1, t2 = (datetime(2025, 4, day, tzinfo=timezone.utc) for day in (1, 2))
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self._sync(Path(a), t1)
            _final(Path(a), 2, minutes=33)
            self._sync(Path(a), t2)
            _final(Path(b), 2, minutes=33)
            self._sync(Path(b), t2, memo=False)
            strip = lambda folder: {p.name: {k: v for k, v in json.loads(p.read_text()).items() if k != 'as_of'}
                                    for p in (Path(folder)/'players').glob('*.json') if p.name != 'index.json'}
            self.assertEqual(strip(a), strip(b))

    def test_a_deleted_page_is_written_again(self):
        t1, t2 = (datetime(2025, 4, day, tzinfo=timezone.utc) for day in (1, 2))
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            self._sync(folder, t1)
            (folder/'players'/'2.json').unlink()
            self._sync(folder, t2)
            self.assertEqual(json.loads((folder/'players'/'2.json').read_text())['as_of'], t2.isoformat())


    def test_a_sync_stopped_before_the_index_is_written_rebuilds_next_time(self):
        t1, t2, t3 = (datetime(2025, 4, day, tzinfo=timezone.utc) for day in (1, 2, 3))
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            self._sync(folder, t1)
            # Player 3 sat out every game after all; the sync is killed between its writes.
            zero = {k: 0 for k in ('minutes', 'fgm', 'fga', 'fg3m', 'fg3a', 'ftm', 'fta', 'tov', 'pts')}
            for i in range(6):
                _final(folder, i, pid='3', status='dnp', **zero)
            real = player_sync.atomic_json
            def killed(path, value):
                if Path(path).name == 'index.json':
                    raise Stopped()
                return real(path, value)
            with patch('nba_forecast.player_sync.atomic_json', side_effect=killed), self.assertRaises(Stopped):
                self._sync(folder, t2)
            third = self._sync(folder, t3)
            page = json.loads((folder/'players'/'3.json').read_text())
            self.assertEqual(third['player_summaries']['3']['games'], page['current_season']['gp'])
            self.assertEqual(third['rotation_roles']['3'], page['rotation_role'])
            self.assertEqual(json.loads((folder/'players'/'index.json').read_text())['rotation_roles']['3'], page['rotation_role'])

    def test_the_sync_reports_a_mismatch_once_as_main_did(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            _shifted_final(folder, 1)
            memo = tick_memo.HistoryMemo(folder, _history(), 'k')
            merged, _, tick_errors, _, _ = memo.merged()
            self.assertEqual([e['game_id'] for e in tick_errors], ['g1'])
            _shifted_final(folder, 2)  # captured after the tick's merge, before the sync
            main = []
            _merge_finals(merged, folder/'finals', errors=main)  # main's sync merged the tick's merge again
            roster = dict(observed_at='2025-03-01T00:00:00Z', players=[])
            with patch('nba_forecast.player_sync.datetime') as clock:
                clock.now.return_value = datetime(2025, 4, 1, tzinfo=timezone.utc)
                summary = sync_player_data(folder, merged, roster=roster, schedule=dict(season='2024-25', games=[]),
                                           max_shot_games=0, memo=memo)
            self.assertEqual([e['game_id'] for e in main], ['g2'])
            self.assertEqual(summary['skipped_sources'], main)


class PersistentReplayTests(unittest.TestCase):
    def setUp(self):
        replay_cache.clear()
        self.addCleanup(replay_cache.clear)

    def test_a_later_process_reads_the_replay_instead_of_computing_it(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            compute = lambda: calls.append(1) or ({'shots': [1.5, 2]}, [('a', 1)], 'known')
            cold = replay_cache.replay(('shot', d, 'x'), compute, d)
            replay_cache.clear()  # a new tick process
            warm = replay_cache.replay(('shot', d, 'x'), compute, d)
            self.assertEqual(calls, [1])
            self.assertEqual(warm, cold)
            self.assertEqual(type(warm[1][0]), tuple)
            replay_cache.clear()
            replay_cache.replay(('shot', d, 'y'), compute, d)
            self.assertEqual(calls, [1, 1])

    def test_a_corrupt_entry_is_replayed_and_written_again(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            compute = lambda: calls.append(1) or 'value'
            replay_cache.replay(('k',), compute, d)
            entries = list((Path(d)/'cache'/'replay').rglob('*.pickle'))
            self.assertEqual(len(entries), 1)
            entries[0].write_bytes(b'\x80\x05garbage')
            replay_cache.clear()
            self.assertEqual(replay_cache.replay(('k',), compute, d), 'value')
            replay_cache.clear()
            self.assertEqual(replay_cache.replay(('k',), compute, d), 'value')
            self.assertEqual(calls, [1, 1])

    def test_an_entry_stored_by_other_code_is_not_used_and_its_store_is_removed(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            compute = lambda: calls.append(1) or 'value'
            replay_cache.replay(('k',), compute, d)
            old = list((Path(d)/'cache'/'replay').iterdir())
            replay_cache.clear()
            with patch.object(replay_cache, '_code', ['f'*64]):
                replay_cache.replay(('k',), compute, d)
                self.assertEqual(calls, [1, 1])
                self.assertEqual([p.name for p in (Path(d)/'cache'/'replay').iterdir()], ['f'*16])
            self.assertFalse(old[0].exists())

    def test_the_disk_store_is_bounded(self):
        with tempfile.TemporaryDirectory() as d, patch.object(replay_cache, 'DISK_ENTRIES', 3):
            for i in range(5):
                replay_cache.replay(('k', i), lambda i=i: i, d)
            self.assertEqual(len(list((Path(d)/'cache'/'replay').rglob('*.pickle'))), 3)

    def test_without_a_folder_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                replay_cache.replay(('k',), lambda: 1)
                self.assertFalse((Path(d)/'cache').exists())
            finally:
                os.chdir(Path(__file__).resolve().parents[1])


if __name__ == '__main__':
    unittest.main()
