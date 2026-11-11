"""Save the snapshots of the newest live runs in a data folder's ledger, one per game, for tick_memo_prepare_identity.py.

Usage: python3 tick_memo_snapshots.py <data-dir> <out-dir> [count=4]
Reads run_records only (never the ``runs`` view, which joins each run's 59 MB history).
"""
import sqlite3, json, sys
from pathlib import Path
folder, out = Path(sys.argv[1]), Path(sys.argv[2]); count = int(sys.argv[3]) if len(sys.argv) > 3 else 4
out.mkdir(parents=True, exist_ok=True)
conn = sqlite3.connect((folder/'forecast.sqlite').resolve().as_uri()+'?mode=ro', uri=True)
seen = set()
for run_id, created, gid in conn.execute("SELECT run_id, created_at, json_extract(snapshot_json,'$.games[0].game_id') FROM run_records "
                                         "WHERE mode='live' ORDER BY rowid DESC LIMIT 400"):
    if gid in seen:
        continue
    seen.add(gid)
    snapshot = conn.execute('SELECT snapshot_json FROM run_records WHERE run_id=?', (run_id,)).fetchone()[0]
    (out/(gid+'.json')).write_text(snapshot)
    print(gid, created, run_id)
    if len(seen) == count:
        break
