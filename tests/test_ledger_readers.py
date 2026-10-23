"""Reader memory must not grow with copies of immutable input history."""

import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from nba_forecast import store
from nba_forecast.benchmarks import benchmark_report
from nba_forecast.operations import ledger_runs
from tests.test_store import inputs, actual, NOW, LATER


class LedgerReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.db = Path(cls.folder.name) / 'forecast.sqlite'
        previous = dict(actual(), game_id='previous', game_time='2026-10-20T18:00:00Z',
                        known_at='2026-10-20T21:00:00Z', name='X' * (2 * 1024 * 1024))
        cls.run_ids = []
        for i in range(6):
            snapshot, _, forecast = inputs()
            gid = f'game-{i}'
            snapshot['games'][0]['game_id'] = gid
            for p in forecast['predictions']:
                if p['game_id'] is not None:
                    p['game_id'] = gid
            with patch('nba_forecast.store._now', return_value=NOW):
                rid = store.save_run(cls.db, snapshot, [previous], forecast)
            with patch('nba_forecast.store._now', return_value=LATER):
                store.evaluate_run(cls.db, rid, [dict(actual(), game_id=gid)])
            cls.run_ids.append(rid)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def peak_bytes(self, action):
        tracemalloc.start()
        try:
            value = action()
            return value, tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    def test_operations_reader_uses_metadata_memory_budget(self):
        runs, peak = self.peak_bytes(lambda: list(ledger_runs(self.db)))
        self.assertEqual(len(runs), 6)
        self.assertLess(peak, 1024 * 1024)
        self.assertTrue(all('history_json' not in r for r in runs))

    def test_performance_report_does_not_load_input_histories(self):
        report, peak = self.peak_bytes(lambda: store.performance_report(self.db))
        self.assertEqual(report['sample_counts']['evaluated'], 6)
        self.assertLess(peak, 1024 * 1024)

    def test_benchmarks_keep_only_one_selected_run_history(self):
        report, peak = self.peak_bytes(lambda: benchmark_report(self.db))
        self.assertEqual(report['groups'][0]['metrics']['paired_n'], 6)
        self.assertEqual(report['groups'][0]['metrics']['model']['mae'], 4)
        self.assertLess(peak, 7 * 1024 * 1024)

    def test_evaluations_can_write_between_iterator_yields(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'forecast.sqlite'
            shutil.copyfile(self.db, db)
            runs = iter(ledger_runs(db))
            first = next(runs)
            snapshot = json.loads(first['snapshot_json'])
            observed = dict(actual(), game_id=snapshot['games'][0]['game_id'],
                            known_at='2026-10-22T21:01:00Z', fgm=11, pts=26)
            with patch('nba_forecast.store._now', return_value=LATER):
                result = store.evaluate_run(db, first['run_id'], [observed])
            self.assertEqual(result['revision'], 2)
            self.assertEqual(result['matched'][0]['actual_points'], 26)
            with sqlite3.connect(db) as conn:
                revisions = conn.execute('SELECT COUNT(*) FROM evaluations WHERE run_id=?',
                                         (first['run_id'],)).fetchone()[0]
            self.assertEqual(revisions, 2)
            self.assertEqual(len(list(runs)), 5)
