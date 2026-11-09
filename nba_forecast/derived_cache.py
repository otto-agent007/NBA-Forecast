"""What the derived reports read from each immutable forecast run, extracted once per run.

Ledger runs are append-only: SQLite triggers refuse to update or delete them. The
derived reports nonetheless parsed, and some hashed, every run's frozen JSON on
every rebuild, so each rebuild grew with every run ever saved. This sidecar keeps,
per run, what each report takes from it, extracted in one pass when the run is
first seen; a rebuild reads only the runs saved since the last one.

Each report's facts are that report's own per-run code recording what the run
contributes before any cutoff, model key or schedule is applied (the ``*_ops``
and ``*_facts`` functions beside each report), so reading the facts gives the
same result as reading the run again. Results that depend on one chosen run (a
daily review's frozen selection, a benchmark's baselines, a postgame's forecast
view, an evaluation's matched rows) are kept the same way, keyed by the run or
game they were computed from.

Integrity. A run's stored digests are checked against its bytes when its facts
are extracted, exactly where each report checked them before. Afterwards the
facts stand for the bytes: the triggers forbid changes through SQLite, and each
process that opens the sidecar (one derived rebuild) re-verifies the
``max(REVERIFY_MIN, ceil(runs / REVERIFY_REBUILDS))`` runs verified longest ago,
so at the derived cadence every run is checked again in about a day; a run
whose bytes or stored digests changed is extracted again and reported the way
the report always reported it. A promotion does not rely on the sidecar at all:
``direct_rows`` extracts the facts from the ledger bytes again.

The file is disposable, and each code version keeps its own
(``forecast.<version>.derived-cache.sqlite``): a report run from another
checkout never writes into the service's file, and files unused for
``PRUNE_SECONDS`` are removed. A sidecar that does not describe this ledger (a
rebuilt or replaced ledger) is recognized by its (rowid, run_id, created_at)
list and emptied. Every change is checked against the generation it was read
under, so a reader whose sidecar was emptied by another process meanwhile
catches up again instead of reading a partial one. An unreadable sidecar is
replaced; one that cannot be written is kept in memory for the rest of the
process.
"""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
import zlib


VERSION = 2
KINDS = ('perf', 'prospective', 'margin', 'review', 'summary')
REVERIFY_MIN = 32
REVERIFY_REBUILDS = 48   # derived rebuilds in a day, about one every half hour
PRUNE_SECONDS = 2 * 24 * 3600
_CODE = None
_LOCK = threading.Lock()
_MEMO = {}
_MEMORY = {}          # (ledger, thread, version) -> this process's in-memory sidecar
_FILE_FAILED = set()  # sidecar files this process could not use
_REVERIFIED = set()   # sidecar files this process has re-verified a batch of
_PRUNED = set()       # folders this process has pruned

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
    ledger_rowid INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    created_at TEXT,
    as_of TEXT,
    mode TEXT,
    scope TEXT,
    digests TEXT,
    verified_at REAL,
    perf BLOB, prospective BLOB, margin BLOB, review BLOB, summary BLOB
);
CREATE INDEX IF NOT EXISTS runs_verified ON runs (verified_at) WHERE digests IS NOT NULL;
CREATE INDEX IF NOT EXISTS runs_id ON runs (run_id);
CREATE TABLE IF NOT EXISTS extras (
    kind TEXT NOT NULL, key TEXT NOT NULL, ledger_rowid INTEGER, value BLOB NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE INDEX IF NOT EXISTS extras_run ON extras (ledger_rowid);
"""

def _clock():
    return time.time()


def _code_digest():
    """This package's source: facts extracted by other code are extracted again."""
    global _CODE
    if _CODE is None:
        package = Path(__file__).resolve().parent
        digest = hashlib.sha256()
        for path in sorted(package.rglob('*.py')):
            digest.update(path.relative_to(package).as_posix().encode() + b'\0' + path.read_bytes())
        _CODE = digest.hexdigest()
    return _CODE


def _version():
    return f'{VERSION}:{_code_digest()}'


def cache_path(db):
    """This code version's sidecar beside a ledger: ``forecast.sqlite`` keeps ``forecast.<version>.derived-cache.sqlite``."""
    db = Path(db)
    return db.with_name(f'{db.stem}.{hashlib.sha256(_version().encode()).hexdigest()[:16]}.derived-cache.sqlite')


def _pack(value):
    return zlib.compress(json.dumps(value, allow_nan=True, separators=(',', ':')).encode(), 3)


def _unpack(blob):
    return None if blob is None else json.loads(zlib.decompress(blob))


def _sha(text):
    return hashlib.sha256(text.encode() if isinstance(text, str) else bytes(text)).hexdigest()


class Parsed:
    """One ledger row, its JSON decoded at most once whichever reports read it.

    A decoding error is kept and raised again to each reader, as each would have
    met it decoding the row itself. Readers must not change the decoded values.
    """

    def __init__(self, row):
        self.row = row
        self._values = {}

    def _get(self, name, make):
        if name not in self._values:
            try:
                self._values[name] = (True, make())
            except Exception as exc:
                self._values[name] = (False, exc)
        ok, value = self._values[name]
        if not ok:
            raise value
        return value

    def forecast(self):
        return self._get('forecast', lambda: json.loads(self.row['forecast_json']))

    def snapshot(self):
        return self._get('snapshot', lambda: json.loads(self.row['snapshot_json']))

    def digest(self, kind):
        return self._get(kind + '_sha', lambda: _sha(self.row[kind + '_json']))


def _extractor(kind):
    if kind == 'perf':
        from .store import performance_ops as extract
    elif kind == 'prospective':
        from .prospective_calibration import run_ops as extract
    elif kind == 'margin':
        from .prospective_margin_calibration import run_ops as extract
    elif kind == 'review':
        from .daily_review import review_facts as extract
    elif kind == 'summary':
        extract = summary_facts
    else:
        raise ValueError('unknown derived fact kind: ' + kind)
    return extract


def _extract(row):
    """Every report's facts for one live league run, from one decoding of its JSON."""
    parsed = Parsed(row)
    return {kind: _extractor(kind)(parsed) for kind in KINDS}


def summary_facts(parsed):
    """The dashboard's run summary (``dashboard._run_summaries``): its JSON fields as SQLite's json_extract returns them."""
    try:
        snapshot = parsed.snapshot()
    except ValueError:
        return dict(malformed=True)
    if not isinstance(snapshot, dict):
        return dict(malformed=False, season=_sql_json(snapshot, ('season',)), roster_observed_at=None, stage=None, games=None)
    return dict(malformed=False, season=_sql_json(snapshot, ('season',)),
                roster_observed_at=_sql_json(snapshot, ('roster', 'observed_at')),
                stage=_sql_json(snapshot, ('forecast_stage',)), games=_game_identities(_sql_value(snapshot, ('games',))))


_MISSING = object()


def _sql_value(value, path):
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


def _sql_json(value, path):
    """``json_extract(text, '$.a.b')`` as Python's sqlite3 returns it: a scalar, None, or minified JSON text for a container."""
    value = _sql_value(value, path)
    if value is _MISSING or value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(',', ':'), ensure_ascii=False)
    return value


def _game_identities(games):
    """The saved games as the selectors read them: only the four identity fields, a missing one left missing.

    Anything but a list (never saved by ``save_run``) is kept as ``{'raw': value}``.
    """
    if games is _MISSING or games is None:
        return None
    if not isinstance(games, list):
        return dict(raw=games)
    keys = ('game_id', 'start_time', 'home_team_id', 'away_team_id')
    return [{k: g[k] for k in keys if k in g} if isinstance(g, dict) else g for g in games]


def saved_games(value):
    """What ``json.loads(json_extract(snapshot_json, '$.games'))`` gave the selectors."""
    if value is None:
        raise TypeError('the JSON object must be str, bytes or bytearray, not NoneType')
    return value['raw'] if isinstance(value, dict) else value


def game_ids(value):
    """The game IDs ``json_each(snapshot_json, '$.games')`` matches against a requested list."""
    if not isinstance(value, list):
        return []
    return [g['game_id'] for g in value if isinstance(g, dict) and isinstance(g.get('game_id'), str)]


# --- the sidecar ---------------------------------------------------------------------------


_COLUMNS = 'rowid AS insertion_order,run_id,created_at,as_of,mode,scope'
_BODY = 'snapshot_json,forecast_json,snapshot_sha256,forecast_sha256'


def _ledger(db):
    conn = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


def _table(conn):
    """``run_records`` holds each run once; a legacy ledger keeps the original ``runs`` table."""
    found = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_records'").fetchone()
    return 'run_records' if found else 'runs'


def direct_rows(db, kind):
    """Every live league run with ``kind``'s facts extracted now from its ledger bytes, never from the sidecar.

    What a promotion reads: the same facts ``Cache.rows`` gives, each run's
    digests checked against its bytes on this call, as the reports did before.
    """
    extract = _extractor(kind)
    with closing(_ledger(db)) as ledger:
        for row in ledger.execute(f"SELECT {_COLUMNS},{_BODY} FROM runs WHERE mode='live' AND scope='league' ORDER BY rowid"):
            row = dict(row)
            yield dict(insertion_order=row['insertion_order'], run_id=row['run_id'], created_at=row['created_at'],
                       as_of=row['as_of'], mode=row['mode'], scope=row['scope'], value=extract(Parsed(row)))


def _connect(path):
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    try:
        conn.execute('PRAGMA busy_timeout = 30000')
        try:
            conn.execute('PRAGMA journal_mode = WAL')
        except sqlite3.OperationalError:
            pass
        conn.executescript(_SCHEMA)
    except BaseException:
        conn.close()
        raise
    return conn


def _meta(conn, key, default=None):
    row = conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def _remove(path):
    for suffix in ('', '-wal', '-shm', '-journal'):
        try:
            Path(str(path) + suffix).unlink()
        except FileNotFoundError:
            pass


class SidecarError(sqlite3.Error):
    """The sidecar file failed (unreadable, unwritable, full); the ledger is fine.

    A ``sqlite3.Error``, so a report that already handles a failing ledger handles
    this too.
    """


class _Stale(Exception):
    """The sidecar changed generation (another process emptied it) since this reader brought it up to date."""


class _Busy(Exception):
    """The sidecar kept changing while this reader brought it up to date."""


def _sidecar(statement):
    def wrapped(*args, **kwargs):
        try:
            return statement(*args, **kwargs)
        except sqlite3.Error as exc:
            raise SidecarError(str(exc)) from exc
    return wrapped


def _transaction(conn, immediate=False):
    """A read (snapshot) or write transaction on the sidecar, rolled back on any error."""
    class _Txn:
        def __enter__(self):
            conn.execute('BEGIN IMMEDIATE' if immediate else 'BEGIN')
            return conn

        def __exit__(self, kind, value, tb):
            conn.execute('ROLLBACK' if kind else 'COMMIT')
            return False
    return _Txn()


def _corrupt(exc):
    """Bytes the sidecar cannot give back (a damaged page or value), as opposed to a busy or full one."""
    cause = exc.__cause__ if isinstance(exc, SidecarError) else exc
    return isinstance(cause, (zlib.error, ValueError)) or (
        isinstance(cause, sqlite3.DatabaseError) and not isinstance(cause, sqlite3.OperationalError))


class Cache:
    """This code version's sidecar of one ledger, brought up to date when opened.

    An unusable sidecar file is replaced once; if that fails too, the facts are
    kept in memory for the rest of the process. Errors reading the ledger itself
    propagate exactly as the reports met them before.
    """

    def __init__(self, db):
        self.db = Path(db)
        self.path = cache_path(self.db)
        self.conn, self.memory, self.generation = None, False, None
        self._open()

    # -- opening ------------------------------------------------------------------------

    def _open(self, fresh=False):
        attempts = () if str(self.path) in _FILE_FAILED else (('fresh file',) if fresh else ('file', 'fresh file'))
        for attempt in attempts:
            try:
                if attempt == 'fresh file':
                    _remove(self.path)
                self.conn, self.memory = _sidecar(_connect)(self.path), False
                self._update()
            except _Busy:
                self._drop()
                break
            except (SidecarError, OSError):
                self._drop()
                continue
            except BaseException:
                self._drop()
                raise
            self._housekeep()
            return
        else:
            _FILE_FAILED.add(str(self.path))
        self._use_memory()

    def _use_memory(self):
        """This process's in-memory sidecar of the ledger: extracted once, then only new runs."""
        key = (str(self.db.resolve()), threading.get_ident(), _version())
        with _LOCK:
            conn = _MEMORY.get(key)
            if conn is None:
                conn = _MEMORY[key] = _sidecar(_connect)(':memory:')
        self.conn, self.memory = conn, True
        try:
            self._update()
        except _Busy as exc:
            raise SidecarError(str(exc)) from exc

    def _drop(self):
        """Close this reader's sidecar file; the process's in-memory copy stays open."""
        if self.conn is not None and not self.memory:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
        self.conn, self.memory, self.generation = None, False, None

    def _housekeep(self):
        """Mark this file used and, once per process, remove other versions' files unused for ``PRUNE_SECONDS``."""
        try:
            os.utime(self.path)
        except OSError:
            pass
        folder = str(self.path.parent.resolve())
        if folder in _PRUNED:
            return
        _PRUNED.add(folder)
        cutoff = time.time() - PRUNE_SECONDS
        candidates = list(self.path.parent.glob(f'{self.db.stem}.*.derived-cache.sqlite'))
        candidates.append(self.path.parent / f'{self.db.stem}.derived-cache.sqlite')  # before versioned names
        for path in candidates:
            if path == self.path:
                continue
            try:
                used = max(Path(str(path) + suffix).stat().st_mtime for suffix in ('', '-wal') if Path(str(path) + suffix).exists())
            except (OSError, ValueError):
                continue
            if used < cutoff:
                try:
                    _remove(path)
                except OSError:
                    pass

    # -- bringing it up to date ---------------------------------------------------------

    def _update(self):
        for _ in range(3):
            try:
                self._catch_up()
                return
            except _Stale:
                continue
        raise _Busy('the sidecar kept changing while it was brought up to date')

    def _catch_up(self):
        with closing(_ledger(self.db)) as ledger:
            table = _table(ledger)
            version, generation, last, held = self._snapshot()
            theirs = [tuple(r) for r in ledger.execute(f'SELECT rowid,run_id,created_at FROM {table} WHERE rowid<=? ORDER BY rowid', (last,))]
            if version != _version() or generation is None or held != theirs:
                generation, last = self._reset(generation), 0
            self.generation = generation
            self._fold(ledger, table, last)
            if not self.memory and str(self.path) not in _REVERIFIED:
                self._reverify(ledger, table)
                _REVERIFIED.add(str(self.path))

    @_sidecar
    def _snapshot(self):
        """Version, generation, last folded rowid and every held run, read in one snapshot."""
        with _transaction(self.conn) as conn:
            version, generation = _meta(conn, 'version'), _meta(conn, 'generation')
            last = _meta(conn, 'last_rowid', '0')
            held = [tuple(r) for r in conn.execute('SELECT ledger_rowid,run_id,created_at FROM runs ORDER BY ledger_rowid')]
        try:
            return version, generation, int(last), held
        except ValueError:
            return None, generation, 0, held  # an unreadable mark: empty it like a sidecar of other code

    @_sidecar
    def _reset(self, expected):
        """Empty the sidecar for this ledger and code, unless another process already did; a new generation."""
        generation = uuid.uuid4().hex
        with _transaction(self.conn, immediate=True) as conn:
            if _meta(conn, 'generation') != expected:
                raise _Stale()
            conn.execute('DELETE FROM runs')
            conn.execute('DELETE FROM extras')
            conn.execute('DELETE FROM meta')
            conn.executemany('INSERT INTO meta VALUES (?,?)', [('version', _version()), ('last_rowid', '0'),
                                                               ('generation', generation)])
        return generation

    def _check(self, conn):
        if _meta(conn, 'generation') != self.generation or _meta(conn, 'version') != _version():
            raise _Stale()

    def _fold(self, ledger, table, last):
        """Extract the runs saved since the last fold, committing as it goes."""
        heads = ledger.execute(f'SELECT {_COLUMNS} FROM {table} WHERE rowid>? ORDER BY rowid', (last,)).fetchall()
        batch = []
        for head in heads:
            batch.append(self._record(ledger, table, dict(head)))
            if len(batch) >= 64:
                self._store(batch)
                batch = []
        if batch:
            self._store(batch)

    def _record(self, ledger, table, head):
        live = head['mode'] == 'live' and head['scope'] == 'league'
        values, digests = {}, None
        if live:
            body = ledger.execute(f'SELECT {_BODY} FROM {table} WHERE rowid=?', (head['insertion_order'],)).fetchone()
            row = dict(head, **dict(body))
            values = _extract(row)
            digests = json.dumps(_digests(row))
        return (head['insertion_order'], head['run_id'], head['created_at'], head['as_of'], head['mode'], head['scope'],
                digests, _clock() if live else None, *(_pack(values[k]) if live else None for k in KINDS))

    @_sidecar
    def _store(self, batch, replace=False):
        """Add runs under this reader's generation; ``replace`` (a re-extracted run) also drops its kept results."""
        generation = uuid.uuid4().hex if replace else self.generation
        with _transaction(self.conn, immediate=True) as conn:
            self._check(conn)
            conn.executemany(f'INSERT OR REPLACE INTO runs VALUES ({",".join("?" * (8 + len(KINDS)))})', batch)
            last = max(int(_meta(conn, 'last_rowid', '0')), max(r[0] for r in batch))
            conn.execute("UPDATE meta SET value=? WHERE key='last_rowid'", (str(last),))
            if replace:
                conn.executemany('DELETE FROM extras WHERE ledger_rowid=?', [(r[0],) for r in batch])
                conn.execute("UPDATE meta SET value=? WHERE key='generation'", (generation,))
        self.generation = generation

    def _reverify(self, ledger, table):
        """Check again the runs verified longest ago, enough that every run is checked in about a day of rebuilds."""
        @_sidecar
        def due():
            with _transaction(self.conn) as conn:
                total = conn.execute('SELECT COUNT(*) FROM runs WHERE digests IS NOT NULL').fetchone()[0]
                size = max(REVERIFY_MIN, math.ceil(total / REVERIFY_REBUILDS))
                return conn.execute('SELECT ledger_rowid,digests FROM runs WHERE digests IS NOT NULL '
                                    'ORDER BY verified_at,ledger_rowid LIMIT ?', (size,)).fetchall()
        verified = []
        for rowid, saved in due():
            head = ledger.execute(f'SELECT {_COLUMNS},{_BODY} FROM {table} WHERE rowid=?', (rowid,)).fetchone()
            if head is None:
                continue
            if _digests(dict(head)) == json.loads(saved):
                verified.append(rowid)
                continue
            keys = ('insertion_order', 'run_id', 'created_at', 'as_of', 'mode', 'scope')
            self._store([self._record(ledger, table, {k: head[k] for k in keys})], replace=True)
        if verified:
            @_sidecar
            def mark():
                with _transaction(self.conn, immediate=True) as conn:
                    self._check(conn)
                    now = _clock()
                    conn.executemany('UPDATE runs SET verified_at=? WHERE ledger_rowid=?', [(now, r) for r in verified])
            mark()

    # -- reading ------------------------------------------------------------------------

    def _recover(self, exc):
        """After a failure on an open sidecar: catch up again if it was emptied meanwhile, else replace it or use memory."""
        if isinstance(exc, _Stale):
            try:
                self._update()
                return
            except _Busy:
                pass
            except (SidecarError, OSError):
                exc = SidecarError('the sidecar failed while catching up')
        if self.memory:
            if isinstance(exc, _Stale):
                raise SidecarError('the in-memory sidecar kept changing')
            raise exc
        corrupt = not isinstance(exc, _Stale) and _corrupt(exc)
        self._drop()
        if corrupt:
            self._open(fresh=True)
        else:
            self._use_memory()

    def rows(self, kind):
        """Every run in insertion order: its ledger columns and this report's facts (None unless live league)."""
        if kind not in KINDS:
            raise ValueError('unknown derived fact kind: ' + kind)
        for _ in range(4):
            try:
                return self._rows(kind)
            except (_Stale, sqlite3.Error, zlib.error, ValueError) as exc:
                self._recover(exc)
        raise SidecarError('the derived sidecar could not be read')

    def _rows(self, kind):
        with _transaction(self.conn) as conn:
            try:
                generation, last = _meta(conn, 'generation'), _meta(conn, 'last_rowid')
                if generation != self.generation:
                    raise _Stale()
                key = (id(conn) if self.memory else str(self.path.resolve()), generation, kind)
                with _LOCK:
                    memo = _MEMO.get(key)
                if memo is not None and memo[0] == last:
                    return memo[1]
                raw = conn.execute(f'SELECT ledger_rowid,run_id,created_at,as_of,mode,scope,{kind} FROM runs ORDER BY ledger_rowid').fetchall()
            except sqlite3.Error as exc:
                raise SidecarError(str(exc)) from exc
        result = [dict(insertion_order=r[0], run_id=r[1], created_at=r[2], as_of=r[3], mode=r[4], scope=r[5], value=_unpack(r[6]))
                  for r in raw]
        with _LOCK:
            if len(_MEMO) > 16:
                _MEMO.clear()
            _MEMO[key] = (last, result)
        return result

    def _read(self, query, args):
        """One row of a read under this reader's generation, or None when it changed or failed (the caller computes it again)."""
        try:
            with _transaction(self.conn) as conn:
                if _meta(conn, 'generation') != self.generation:
                    return None
                return conn.execute(query, args).fetchone()
        except sqlite3.Error:
            return None

    def rowid_of(self, run_id):
        row = self._read('SELECT ledger_rowid FROM runs WHERE run_id=?', (run_id,))
        return row[0] if row else None

    def get(self, kind, key):
        row = self._read('SELECT value FROM extras WHERE kind=? AND key=?', (kind, key))
        if row is None:
            return None
        try:
            return (_unpack(row[0]),)
        except (zlib.error, ValueError):
            return None

    def put(self, kind, key, value, rowid=None):
        """Keep a result under this reader's generation; one computed for a sidecar emptied meanwhile is not kept."""
        try:
            with _transaction(self.conn, immediate=True) as conn:
                self._check(conn)
                conn.execute('INSERT OR REPLACE INTO extras VALUES (?,?,?,?)', (kind, key, rowid, _pack(value)))
        except (sqlite3.Error, _Stale):
            pass  # Kept or not, the value is the same; a failed write only costs a later recomputation.

    def extra(self, kind, key, compute, rowid=None):
        """``compute()`` once per (kind, key) for this ledger; later readers get the saved value."""
        saved = self.get(kind, key)
        if saved is not None:
            return saved[0]
        value = compute()
        self.put(kind, key, value, rowid)
        return value

    def close(self):
        self._drop()


def _digests(row):
    return [row['snapshot_sha256'], row['forecast_sha256'], _sha(row['snapshot_json']), _sha(row['forecast_json'])]


def open_cache(db):
    """The up-to-date sidecar of ``db`` (an existing ledger); close it after use."""
    return Cache(db)


def rows(db, kind):
    with closing(open_cache(db)) as cache:
        return cache.rows(kind)


def run_summaries(cache, season):
    """``dashboard._run_summaries`` from the sidecar: one row per live run of the season, oldest first."""
    found = []
    for run in cache.rows('summary'):
        if run['mode'] != 'live' or run['scope'] != 'league':
            continue
        facts = run['value']
        if facts['malformed']:
            raise sqlite3.OperationalError('malformed JSON')
        if season is None or facts['season'] is None or facts['season'] != season:
            continue
        found.append(dict(insertion_order=run['insertion_order'], run_id=run['run_id'], created_at=run['created_at'],
                          roster_observed_at=facts['roster_observed_at'], stage=facts['stage'],
                          games=saved_games(facts['games'])))
    return sorted(found, key=lambda s: (s['created_at'], s['insertion_order']))


def forget_after(db, rowid):
    """Drop every fact of runs after ``rowid``, as if they had not been seen yet (a test and benchmarking aid)."""
    with closing(_connect(cache_path(db))) as conn:
        with _transaction(conn, immediate=True):
            conn.execute('DELETE FROM runs WHERE ledger_rowid>?', (rowid,))
            conn.execute('DELETE FROM extras WHERE ledger_rowid IS NULL OR ledger_rowid>?', (rowid,))
            conn.execute("UPDATE meta SET value=? WHERE key='last_rowid'", (str(rowid),))
            conn.execute("UPDATE meta SET value=? WHERE key='generation'", (uuid.uuid4().hex,))
    with _LOCK:
        _MEMO.clear()


def freeze_exception(exc):
    """An exception as data, so a reader can raise the same one later."""
    if isinstance(exc, json.JSONDecodeError):
        return dict(type='JSONDecodeError', msg=exc.msg, pos=exc.pos, lineno=exc.lineno, colno=exc.colno, text=str(exc))
    args = list(exc.args)
    try:
        json.dumps(args, allow_nan=True)
    except (TypeError, ValueError):
        args = [str(exc)]
    return dict(type=type(exc).__name__, args=args, text=str(exc))


_BUILTIN = {cls.__name__: cls for cls in (KeyError, ValueError, TypeError, AttributeError, OverflowError,
                                           IndexError, ZeroDivisionError, LookupError, ArithmeticError)}


def thaw_exception(frozen):
    if frozen['type'] == 'JSONDecodeError':
        exc = json.JSONDecodeError.__new__(json.JSONDecodeError)
        ValueError.__init__(exc, frozen['text'])
        exc.msg, exc.doc, exc.pos, exc.lineno, exc.colno = frozen['msg'], '', frozen['pos'], frozen['lineno'], frozen['colno']
        return exc
    if frozen['type'] == 'OperationalError':
        return sqlite3.OperationalError(*frozen['args'])
    cls = _BUILTIN.get(frozen['type'])
    return cls(*frozen['args']) if cls else ValueError(frozen['text'])
