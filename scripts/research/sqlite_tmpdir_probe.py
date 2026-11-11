"""Where does this host's SQLite put a sorter's temporary file? One mechanism per run.

Usage: for m in default env-before-import env-after-import pragma; do
           python3 scripts/research/sqlite_tmpdir_probe.py $m WORKDIR; done
  MODE: default (no variable), env-before-import (SQLITE_TMPDIR set before ``import sqlite3``),
        env-after-import (set after it), pragma (the deprecated ``PRAGMA temp_store_directory``)

On SQLite 3.45.1 (2026-11-09): default /var/tmp; env-before-import and pragma the chosen
directory; env-after-import /var/tmp, which is why ``nba_forecast.__main__`` sets the variable
before it imports ``sqlite3``.

The sort is forced to spill (about 25 MB of rows, a 200 KB page cache), the
statement is left part-way through its rows, and the temporary files are read
from /proc/self/fd (SQLite unlinks them as it opens them, so a directory
listing does not show them).
"""
import os, sys
mode, work = sys.argv[1], sys.argv[2]
target = os.path.join(work, 'tmp-' + mode)
os.makedirs(target, exist_ok=True)
os.environ.pop('SQLITE_TMPDIR', None)
os.environ.pop('TMPDIR', None)
if mode == 'env-before-import':
    os.environ['SQLITE_TMPDIR'] = target
import sqlite3
if mode == 'env-after-import':
    os.environ['SQLITE_TMPDIR'] = target
db = os.path.join(work, 'probe-' + mode + '.sqlite')
if os.path.exists(db):
    os.remove(db)
conn = sqlite3.connect(db)
readback = None
if mode == 'pragma':
    conn.execute(f"PRAGMA temp_store_directory = '{target}'")
    readback = conn.execute('PRAGMA temp_store_directory').fetchall()
    conn.close()
    conn = sqlite3.connect(db)  # the setting is process-wide: a later connection uses it
conn.execute('PRAGMA cache_size = -200')
conn.execute('CREATE TABLE t (x TEXT)')
with conn:
    conn.executemany('INSERT INTO t VALUES (?)', ((os.urandom(8).hex() * 60,) for _ in range(25000)))
cursor = conn.execute('SELECT x FROM t ORDER BY x')
cursor.fetchone()
found = []
for fd in os.listdir('/proc/self/fd'):
    try:
        link = os.readlink('/proc/self/fd/' + fd)
    except OSError:
        continue
    if 'etilqs_' in link:
        found.append(os.path.dirname(link))
cursor.close(); conn.close(); os.remove(db)
print(f'sqlite {sqlite3.sqlite_version} mode={mode:18s} temp files={len(found)} directories={sorted(set(found))} '
      f'in_target={bool(found) and all(d == target for d in found)} readback={readback}')
