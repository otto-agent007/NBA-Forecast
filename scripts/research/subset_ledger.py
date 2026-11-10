"""Copy the last N runs (with their histories and evaluations, original rowids) of a ledger into a new one.

Usage: python3 scripts/research/subset_ledger.py SOURCE.sqlite TARGET.sqlite N

Reads SOURCE read-only, rows in rowid order (no sort over the JSON columns). Point it at a copy: it reads the
last N runs' snapshots, forecasts and histories.
"""
import sqlite3, sys, time
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from nba_forecast.store import _SCHEMA
source, target, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
t = time.time()
src = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
dst = sqlite3.connect(target)
dst.executescript(_SCHEMA)
last = src.execute('SELECT MAX(rowid) FROM run_records').fetchone()[0]
first = last - n + 1
cols = 'run_id,created_at,as_of,mode,scope,snapshot_json,history_sha256,forecast_json,snapshot_sha256,forecast_sha256,inputs_sha256'
digests = set()
with dst:
    for row in src.execute(f'SELECT rowid,{cols} FROM run_records WHERE rowid>=? ORDER BY rowid', (first,)):
        digests.add(row[7])
        dst.execute(f'INSERT INTO run_records (rowid,{cols}) VALUES ({",".join("?"*12)})', row)
    print('runs', time.time() - t, flush=True)
    for d in sorted(digests):
        dst.execute('INSERT INTO histories VALUES (?,?)', src.execute('SELECT history_sha256,history_json FROM histories WHERE history_sha256=?', (d,)).fetchone())
    print('histories', len(digests), time.time() - t, flush=True)
    ids = [r[0] for r in dst.execute('SELECT run_id FROM run_records')]
    for rid in ids:
        for row in src.execute('SELECT rowid,run_id,revision,evaluated_at,actuals_json,actuals_sha256,report_json FROM evaluations WHERE run_id=?', (rid,)):
            dst.execute('INSERT INTO evaluations (rowid,run_id,revision,evaluated_at,actuals_json,actuals_sha256,report_json) VALUES (?,?,?,?,?,?,?)', row)
print('done', dst.execute('SELECT COUNT(*) FROM run_records').fetchone(), dst.execute('SELECT COUNT(*) FROM evaluations').fetchone(), time.time() - t)
dst.execute('PRAGMA journal_mode=WAL')
