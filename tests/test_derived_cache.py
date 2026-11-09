"""The derived sidecar must give exactly what reading the ledger gives, whoever else opens it."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from nba_forecast import derived_cache as dc
from nba_forecast.store import _SCHEMA, performance_report

REPO = Path(__file__).resolve().parents[1]
SEASON = '2026-27'


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class DerivedCacheTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db = self.folder / 'forecast.sqlite'
        self.ledger(self.db)

    def ledger(self, db):
        with sqlite3.connect(db) as conn:
            conn.executescript(_SCHEMA)

    def save(self, run_id, index, *, db=None, game='0022600100', forecast_json=None):
        """One live league run of one game, saved through the store's own view and triggers."""
        created = f'2026-11-01T18:{index // 60:02d}:{index % 60:02d}+00:00'
        start = '2026-11-02T00:00:00+00:00'
        identity = dict(game_id=game, start_time=start, home_team_id='H', away_team_id='A', season_type='Regular Season')
        snapshot = json.dumps(dict(season=SEASON, as_of=created, games=[identity], roster=dict(observed_at=created, players=[])))
        forecast = json.dumps(dict(season=SEASON, as_of=created, model_key='k', model_version='v', margin_forecasts=[],
                                   predictions=[dict(game_id=game, player_id='p1', team_id='H', start_time=start,
                                                     status='projected', points_mean=10.0, points_sd=3.0,
                                                     components={'availability': {}})]))
        stored = forecast if forecast_json is None else forecast_json
        with sqlite3.connect(db or self.db) as conn:
            conn.execute('INSERT INTO runs (run_id,created_at,as_of,mode,scope,snapshot_json,history_json,forecast_json,'
                         'snapshot_sha256,history_sha256,forecast_sha256,inputs_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (run_id, created, created, 'live', 'league', snapshot, '[]', stored, _sha(snapshot), 'h',
                          _sha(forecast), 'i'))

    def runs(self, count, start=0, **kwargs):
        for i in range(start, start + count):
            self.save(f'r{i}', i, **kwargs)

    def generation(self):
        with sqlite3.connect(dc.cache_path(self.db)) as conn:
            return conn.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0]

    def change_forecast_bytes(self, run_id, text):
        """A change made outside the store: the triggers that refuse it are bypassed."""
        with sqlite3.connect(self.db) as conn:
            conn.execute('DROP TRIGGER runs_no_update')
            conn.execute('UPDATE run_records SET forecast_json=? WHERE run_id=?', (text, run_id))

    # -- concurrency ---------------------------------------------------------------------

    def test_reader_catches_up_when_another_process_empties_the_sidecar(self):
        self.runs(10)
        cache = dc.open_cache(self.db)
        self.addCleanup(cache.close)
        # Another process emptied the sidecar and has re-extracted only two runs so far.
        dc.forget_after(self.db, 2)
        self.assertEqual([r['run_id'] for r in cache.rows('perf')], [f'r{i}' for i in range(10)])

    def test_commit_between_a_readers_reads_does_not_empty_the_sidecar(self):
        self.runs(30)
        dc.open_cache(self.db).close()
        self.runs(70, start=30)
        before = self.generation()
        real = dc._meta
        armed = [True]

        def racing(conn, key, default=None):
            value = real(conn, key, default)
            if key == 'last_rowid' and armed[0]:
                armed[0] = False
                dc.open_cache(self.db).close()  # another process folds the new runs between this reader's reads
            return value

        with patch.object(dc, '_meta', racing):
            cache = dc.open_cache(self.db)
        self.addCleanup(cache.close)
        self.assertEqual(self.generation(), before, 'a consistent reader has no reason to empty the sidecar')
        self.assertEqual(len(cache.rows('perf')), 100)

    def test_other_code_version_never_writes_into_this_versions_sidecar(self):
        self.runs(300)

        def code(tag):
            return [patch.object(dc, '_version', lambda: f'test:{tag}'),
                    patch.object(dc, '_extract', lambda row: {k: tag for k in dc.KINDS})]

        stores = [0]
        real = dc.Cache._store

        def interleaved(cache, batch, *args, **kwargs):
            out = real(cache, batch, *args, **kwargs)
            stores[0] += 1
            if stores[0] == 1:  # checkout X is mid-extraction when release Y's rebuild opens the sidecar
                y = code('Y')
                with y[0], y[1]:
                    dc.open_cache(self.db).close()
            return out

        x = code('X')
        with x[0], x[1], patch.object(dc.Cache, '_store', interleaved):
            dc.open_cache(self.db).close()
        y = code('Y')
        with y[0], y[1]:
            seen = Counter(r['value'] for r in dc.rows(self.db, 'perf'))
        self.assertEqual(seen, Counter(Y=300))

    def test_replaced_ledger_drops_results_computed_from_the_old_one(self):
        self.runs(5)
        early = dc.open_cache(self.db)
        self.addCleanup(early.close)
        # The ledger is replaced (another copy); the next reader empties the sidecar for it.
        os.remove(self.db)
        self.ledger(self.db)
        for i in range(5):
            self.save(f'new{i}', i)
        dc.open_cache(self.db).close()
        # The reader that opened before the replacement finishes a result from the old ledger.
        early.put('baselines', 'r0', 'from the old ledger')
        fresh = dc.open_cache(self.db)
        self.addCleanup(fresh.close)
        self.assertIsNone(fresh.get('baselines', 'r0'))
        self.assertEqual([r['run_id'] for r in fresh.rows('perf')], [f'new{i}' for i in range(5)])

    # -- integrity -----------------------------------------------------------------------

    def test_changed_run_is_found_by_the_next_rebuild(self):
        self.runs(3)
        self.assertEqual([r['value']['pre_error'] for r in dc.rows(self.db, 'prospective')], [None] * 3)
        self.change_forecast_bytes('r1', json.dumps(dict(season=SEASON, predictions=[])))
        # The next derived rebuild is a new process.
        script = ('import json,sys\nfrom nba_forecast import derived_cache as dc\n'
                  "print(json.dumps([r['value']['pre_error'] for r in dc.rows(sys.argv[1],'prospective')]))")
        done = subprocess.run([sys.executable, '-c', script, str(self.db)], cwd=REPO, capture_output=True, text=True,
                              env=dict(os.environ, PYTHONPATH=str(REPO)), check=True)
        self.assertEqual(json.loads(done.stdout), [None, 'immutable forecast hash mismatch', None])

    def test_cached_error_is_raised_again_as_the_ledger_raised_it(self):
        from nba_forecast.daily_review import _later
        reviewed = dict(game_id='0022600099', start_time='2026-11-01T00:00:00+00:00', home_team_id='H', away_team_id='A')
        following = dict(game_id='0022600100', start_time='2026-11-02T00:00:00+00:00', home_team_id='H', away_team_id='B')
        schedule = dict(season=SEASON, games=[reviewed, following])
        # The next game's run lost its forecast bytes; main's query never read them, its verification did.
        self.save('r0', 0, forecast_json='{"predictions": [')
        raised = []
        for _ in range(2):  # extracted, then read from the sidecar
            with self.assertRaises(ValueError) as caught:
                _later(self.folder, {reviewed['game_id']: reviewed}, schedule, '2026-11-03T00:00:00+00:00')
            raised.append((type(caught.exception), str(caught.exception)))
        self.assertEqual(raised, [(ValueError, 'Frozen forecast hash mismatch: r0')] * 2)

    # -- failures ------------------------------------------------------------------------

    def test_unwritable_sidecar_is_extracted_once_per_process(self):
        self.runs(3)
        dc.cache_path(self.db).mkdir()  # nothing can open or replace it
        real = dc._extract
        calls = []

        def counted(row):
            calls.append(row['run_id'])
            return real(row)

        with patch.object(dc, '_extract', counted):
            first = [r['run_id'] for r in dc.rows(self.db, 'perf')]
            second = [r['run_id'] for r in dc.rows(self.db, 'perf')]
        self.assertEqual(first, ['r0', 'r1', 'r2'])
        self.assertEqual(second, first)
        self.assertEqual(calls, ['r0', 'r1', 'r2'])

    def test_damaged_value_after_open_is_replaced_not_raised(self):
        self.runs(3)
        expected = performance_report(self.db)
        with sqlite3.connect(dc.cache_path(self.db)) as conn:
            conn.execute("UPDATE runs SET perf=x'00' WHERE ledger_rowid=2")
        dc._MEMO.clear()
        again = performance_report(self.db)
        self.assertEqual({k: v for k, v in again.items() if k != 'generated_at'},
                         {k: v for k, v in expected.items() if k != 'generated_at'})


if __name__ == '__main__':
    unittest.main()
