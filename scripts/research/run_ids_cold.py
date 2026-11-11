"""Listing a ledger's run IDs cold: through the ``runs`` view (main's ``_run_ids``) against ``run_records`` directly.

Usage: python3 scripts/research/run_ids_cold.py LEDGER   (a copy; it only reads)

The view joins each run to its history on history_sha256, which is stored after snapshot_json, so
the view query walks every run's snapshot overflow pages.
Each query runs after the ledger's pages are dropped from the page cache (posix_fadvise DONTNEED); prints
seconds, bytes this process read from disk (/proc/self/io read_bytes), row counts, whether the two results
are identical, and the query plans.
"""
import os, sqlite3, sys, time
ledger = sys.argv[1]
def evict():
    fd = os.open(ledger, os.O_RDONLY)
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)
def read_bytes():
    for line in open('/proc/self/io'):
        if line.startswith('read_bytes:'):
            return int(line.split()[1])
results = {}
for name, sql in (('run_records', 'SELECT run_id,mode FROM run_records ORDER BY rowid'),
                  ('runs view', 'SELECT run_id,mode FROM runs ORDER BY rowid')):
    evict()
    conn = sqlite3.connect(f'file:{ledger}?mode=ro', uri=True)
    plan = [row[-1] for row in conn.execute('EXPLAIN QUERY PLAN ' + sql)]
    before, started = read_bytes(), time.monotonic()
    rows = conn.execute(sql).fetchall()
    seconds, read = time.monotonic() - started, read_bytes() - before
    conn.close()
    results[name] = rows
    print(f'{name:12s} rows {len(rows):5d}  {seconds:8.2f} s  read {read / 1e6:9.1f} MB  plan {plan}', flush=True)
print('identical results:', results['run_records'] == results['runs view'])
