"""Evidence replays and league-wide history maps are computed once per process and keyed on what they read."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from nba_forecast import replay_cache, role_context, shot_profiles
from nba_forecast.profiles import eligible_history


def _row(game_id, player_id, game_time, known_at, season='2025-26'):
    return dict(game_id=game_id, player_id=player_id, name='P'+player_id, team_id='1', opponent_id='2', season=season,
                season_type='Regular Season', game_time=game_time, known_at=known_at, status='played', minutes=30,
                pts=10, reb=1, ast=1, stl=0, blk=0, tov=0, fgm=4, fga=8, fg3m=0, fg3a=0, ftm=2, fta=2, oreb=0, dreb=1, pf=0,
                source_url='https://cdn.nba.com/static/json/liveData/boxscore/boxscore_'+game_id+'.json')


class ReplayCacheTests(unittest.TestCase):
    def setUp(self):
        replay_cache.clear()
        self.addCleanup(replay_cache.clear)

    def test_replay_runs_once_per_key_and_shares_the_result(self):
        calls = []
        compute = lambda: calls.append(1) or {'value': len(calls)}
        first = replay_cache.replay(('k', 1), compute)
        second = replay_cache.replay(('k', 1), compute)
        self.assertIs(first, second)
        self.assertEqual(calls, [1])
        replay_cache.replay(('k', 2), compute)
        self.assertEqual(calls, [1, 1])

    def test_replay_does_not_keep_a_failure(self):
        attempts = []
        def compute():
            attempts.append(1)
            if len(attempts) == 1:
                raise ValueError('first time only')
            return 'ok'
        with self.assertRaises(ValueError):
            replay_cache.replay(('k',), compute)
        self.assertEqual(replay_cache.replay(('k',), compute), 'ok')
        self.assertEqual(len(attempts), 2)

    def test_replay_store_is_bounded(self):
        with mock.patch.object(replay_cache, 'REPLAYS', 3):
            for i in range(5):
                replay_cache.replay(('k', i), lambda i=i: i)
            self.assertEqual(len(replay_cache._replays), 3)
            self.assertNotIn(('k', 0), replay_cache._replays)
            self.assertIn(('k', 4), replay_cache._replays)

    def test_league_is_keyed_on_the_history_object(self):
        history = [_row('0022600001', '1', '2026-10-20T00:00:00+00:00', '2026-10-20T03:00:00+00:00')]
        same = list(history)
        calls = []
        compute = lambda: calls.append(1) or {'built': len(calls)}
        self.assertIs(replay_cache.league(history, 'map', compute), replay_cache.league(history, 'map', compute))
        self.assertEqual(calls, [1])
        replay_cache.league(same, 'map', compute)
        self.assertEqual(calls, [1, 1], 'an equal list that is a different object is built again')
        replay_cache.league(history, 'other', compute)
        self.assertEqual(calls, [1, 1, 1])

    def test_cutoffs_after_the_horizon_share_a_key(self):
        history = [_row('0022600001', '1', '2026-10-20T00:00:00+00:00', '2026-10-20T03:00:00+00:00'),
                   _row('0022600002', '1', '2026-10-21T00:00:00+00:00', '2026-10-21T03:00:00+00:00')]
        later = datetime(2026, 10, 22, tzinfo=timezone.utc)
        much_later = datetime(2026, 11, 2, tzinfo=timezone.utc)
        self.assertEqual(replay_cache.cutoff_key(history, later), 'beyond')
        self.assertEqual(replay_cache.cutoff_key(history, later), replay_cache.cutoff_key(history, much_later))
        at_horizon = datetime(2026, 10, 21, 3, tzinfo=timezone.utc)
        self.assertEqual(replay_cache.cutoff_key(history, at_horizon), at_horizon.isoformat())
        self.assertNotEqual(replay_cache.cutoff_key(history, at_horizon), replay_cache.cutoff_key(history, later))
        self.assertEqual(replay_cache.cutoff_key([], later), later.isoformat())

    def test_stamp_changes_when_an_archive_is_rewritten_or_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'a.html'
            path.write_bytes(b'one')
            before = replay_cache.stamp([path, Path(folder)/'missing'])
            self.assertEqual(before[1], (str(Path(folder)/'missing'), None, None))
            path.write_bytes(b'one ')
            self.assertNotEqual(before, replay_cache.stamp([path, Path(folder)/'missing']))

    def test_eligible_history_is_filtered_once_per_history_and_cutoff(self):
        history = [_row('0022600002', '1', '2026-10-21T00:00:00+00:00', '2026-10-21T03:00:00+00:00'),
                   _row('0022600001', '1', '2026-10-20T00:00:00+00:00', '2026-10-20T03:00:00+00:00')]
        first = eligible_history(dict(as_of='2026-11-02T18:00:00+00:00', season='2025-26'), history)
        second = eligible_history(dict(as_of='2026-11-02T19:00:00+00:00', season='2025-26'), history)
        self.assertIs(first, second)
        self.assertEqual([r['game_id'] for r in first], ['0022600001', '0022600002'])
        earlier = eligible_history(dict(as_of='2026-10-21T00:00:00+00:00', season='2025-26'), history)
        self.assertEqual([r['game_id'] for r in earlier], ['0022600001'])

    def test_shot_replay_is_done_once_per_source_rows_and_archive(self):
        rows = [_row('0022600001', '1', '2026-10-20T00:00:00+00:00', '2026-10-20T03:00:00+00:00')]
        context = dict(cutoff=datetime(2026, 11, 2, tzinfo=timezone.utc), seasons={'2025-26'}, excluded=set(), future=set())
        source = dict(game_id='0022600001', season='2025-26', game_time='2026-10-20T00:00:00+00:00',
                      known_at='2026-10-20T04:00:00+00:00', provenance=[])
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(shot_profiles, '_replay_source') as replayed:
            replayed.return_value = ('replay', [], 'known')
            shot_profiles._replay(folder, source, rows, context, digest='a'*64)
            shot_profiles._replay(folder, source, rows, context, digest='a'*64)
            self.assertEqual(replayed.call_count, 1)
            shot_profiles._replay(folder, source, rows+[_row('0022600001', '2', '2026-10-20T00:00:00+00:00', '2026-10-20T03:00:00+00:00')], context, digest='a'*64)
            shot_profiles._replay(folder, source, rows, context, digest='b'*64)
            self.assertEqual(replayed.call_count, 3)

    def test_recap_replay_is_done_once_per_source_rows_and_archive(self):
        rows = [_row('0022600001', '1', '2026-10-20T00:00:00+00:00', '2026-10-20T03:00:00+00:00')]
        metadata = dict(source_url='https://www.nba.com/game/0022600001', sha256='c'*64, retrieved_at='2026-10-20T05:00:00+00:00', bytes=10)
        envelope = dict(metadata=metadata, game=dict(game_id='0022600001'))
        snapshot = dict(as_of='2026-11-02T18:00:00+00:00')
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(role_context, '_replay_source') as replayed, \
                mock.patch.object(role_context, '_metadata', side_effect=lambda value: dict(value)):
            replayed.return_value = ([], 'recap_unavailable')
            role_context._replay(folder, envelope, rows, snapshot)
            role_context._replay(folder, envelope, rows, snapshot)
            self.assertEqual(replayed.call_count, 1)
            (Path(folder)/'raw').mkdir()
            (Path(folder)/'raw'/('c'*64+'.html')).write_bytes(b'rewritten')
            role_context._replay(folder, envelope, rows, snapshot)
            self.assertEqual(replayed.call_count, 2, 'a changed archive file is a different key')


if __name__ == '__main__':
    unittest.main()
