"""Each distinct input history is stored once; the logical ledger rows are unchanged."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from nba_forecast import store
from tests.test_store import inputs, actual, NOW, LATER


def history(name='Previous'):
    return [dict(actual(), game_id='previous', game_time='2026-10-20T18:00:00Z',
                 known_at='2026-10-20T21:00:00Z', name=name)]


def ledger_rows(db):
    with sqlite3.connect(db) as conn:
        return conn.execute(f'SELECT rowid,{",".join(store._RUN_COLUMNS)} FROM runs ORDER BY rowid').fetchall()


def evaluation_rows(db):
    with sqlite3.connect(db) as conn:
        return conn.execute('SELECT rowid,* FROM evaluations ORDER BY rowid').fetchall()


class HistoryDedupeTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db = self.folder / 'forecast.sqlite'

    def save(self, past, db=None):
        snapshot, _, forecast = inputs()
        with patch('nba_forecast.store._now', return_value=NOW):
            return store.save_run(db or self.db, snapshot, past, forecast)

    def legacy(self):
        with sqlite3.connect(self.db) as conn:
            conn.executescript(store._LEGACY_SCHEMA)

    def test_shared_history_is_stored_once_and_rows_read_back_complete(self):
        first = self.save(history())
        self.save(history())
        self.save(history('Other'))
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM histories').fetchone()[0], 2)
        rows = ledger_rows(self.db)
        self.assertEqual([r[0] for r in rows], [1, 2, 3])
        self.assertEqual(rows[0][1], first)
        encoded = json.dumps(history(), sort_keys=True, separators=(',', ':'))
        self.assertEqual(rows[0][7], encoded)
        self.assertEqual(rows[0][10], hashlib.sha256(encoded.encode()).hexdigest())

    def test_runs_and_histories_stay_immutable(self):
        self.save(history())
        with sqlite3.connect(self.db) as conn:
            for statement in ("UPDATE run_records SET forecast_json='{}'", 'DELETE FROM run_records',
                              "UPDATE histories SET history_json='[]'", 'DELETE FROM histories',
                              "INSERT INTO histories SELECT history_sha256,'[]' FROM histories"):
                with self.assertRaises(sqlite3.IntegrityError, msg=statement):
                    conn.execute(statement)

    def test_a_different_history_cannot_reuse_a_stored_digest(self):
        self.save(history())
        with sqlite3.connect(self.db) as conn:
            row = list(conn.execute(f'SELECT {",".join(store._RUN_COLUMNS)} FROM runs').fetchone())
            row[0], row[6] = 'forged', '[]'
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(f'INSERT INTO runs ({",".join(store._RUN_COLUMNS)}) VALUES ({",".join("?" * 12)})', row)

    def test_legacy_ledger_keeps_working_until_rebuilt(self):
        self.legacy()
        run_id = self.save(history())
        with patch('nba_forecast.store._now', return_value=LATER):
            self.assertEqual(store.evaluate_run(self.db, run_id, [actual()])['revision'], 1)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT type FROM sqlite_master WHERE name='runs'").fetchone()[0], 'table')

    def test_rebuild_preserves_every_run_and_evaluation_value(self):
        self.legacy()
        ids = [self.save(history()), self.save(history()), self.save(history('Other'))]
        with patch('nba_forecast.store._now', return_value=LATER):
            store.evaluate_run(self.db, ids[0], [actual()])
            store.evaluate_run(self.db, ids[0], [dict(actual(), known_at='2026-10-22T22:00:00Z', pts=26, fgm=11)])
            store.evaluate_run(self.db, ids[2], [actual('P2')])
        runs, evaluations = ledger_rows(self.db), evaluation_rows(self.db)
        result = store.dedupe_histories(self.db)
        self.assertEqual((result['runs'], result['histories'], result['evaluations']), (3, 2, 3))
        self.assertEqual(result['history_digest_mismatches'], 0)
        self.assertEqual(ledger_rows(self.db), runs)
        self.assertEqual(evaluation_rows(self.db), evaluations)
        backup = Path(result['backup'])
        self.assertEqual(ledger_rows(backup), runs)
        self.assertFalse(self.db.with_name(self.db.name + '.dedupe-tmp').exists())
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT type FROM sqlite_master WHERE name='runs'").fetchone()[0], 'view')
        # The rebuilt ledger accepts new runs and evaluations as before.
        added = self.save(history())
        with patch('nba_forecast.store._now', return_value=LATER):
            self.assertEqual(store.evaluate_run(self.db, added, [actual()])['revision'], 1)
        # Equal clocks: insertion order through the view's rowid selects the newest run.
        selected = store.performance_report(self.db)['groups'][0]['selected_forecasts']
        self.assertEqual({row['run_id'] for row in selected}, {added})
        with self.assertRaisesRegex(ValueError, 'already'):
            store.dedupe_histories(self.db, backup_suffix='.again')

    def test_rebuild_refuses_two_histories_under_one_digest(self):
        self.legacy()
        self.save(history())
        with sqlite3.connect(self.db) as conn:
            row = list(conn.execute(f'SELECT {",".join(store._RUN_COLUMNS)} FROM runs').fetchone())
            row[0], row[6] = 'conflict', '[]'
            conn.execute(f'INSERT INTO runs VALUES ({",".join("?" * 12)})', row)
        before = self.db.read_bytes()
        with self.assertRaisesRegex(ValueError, 'different content'):
            store.dedupe_histories(self.db)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ['forecast.sqlite'])

    def test_scoring_never_loads_the_input_history(self):
        run_id = self.save(history('X' * (4 * 1024 * 1024)))
        tracemalloc.start()
        try:
            with patch('nba_forecast.store._now', return_value=LATER):
                store.evaluate_run(self.db, run_id, [actual()])
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
