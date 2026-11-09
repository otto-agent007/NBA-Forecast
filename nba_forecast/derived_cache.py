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
view, an evaluation's matched rows, a verified final page) are kept the same
way, keyed by the run, game or file digest they were computed from.

Integrity. A run's stored digests are checked against its bytes when its facts
are extracted, exactly where each report checked them before. Afterwards the
facts stand for the bytes: the triggers forbid changes through SQLite, and every
reader re-verifies a bounded batch of the runs verified longest ago (all of them
within ``REVERIFY_SECONDS`` at the rate the reports run), re-extracting any run
whose bytes or stored digests no longer match, so a change made outside SQLite
is still found and reported the way the report always reported it.

The file is disposable. Deleting it, or any change to this package's code (the
version includes the package digest), means the next reader extracts everything
again and gets the same facts. A sidecar that does not describe this ledger (a
rebuilt or replaced ledger, a copy from elsewhere) is recognized by its
(rowid, run_id, created_at) list and replaced; an unreadable one is replaced; one
that cannot be written is replaced by an in-memory copy for that call.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid
import zlib


VERSION = 1
KINDS = ('perf', 'prospective', 'margin', 'review', 'summary')
REVERIFY_SECONDS = 7 * 24 * 3600
REVERIFY_BATCH = 32
_CODE = None
_LOCK = threading.Lock()
_MEMO = {}

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
    """The sidecar beside a ledger: ``forecast.sqlite`` keeps ``forecast.derived-cache.sqlite``."""
    db = Path(db)
    return db.with_name(db.stem + '.derived-cache.sqlite')


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


def _extract(row):
    """Every report's facts for one live league run, from one decoding of its JSON."""
    from .store import performance_ops
    from .prospective_calibration import run_ops as prospective_ops
    from .prospective_margin_calibration import run_ops as margin_ops
    from .daily_review import review_facts
    parsed = Parsed(row)
    return dict(perf=performance_ops(parsed), prospective=prospective_ops(parsed), margin=margin_ops(parsed),
                review=review_facts(parsed), summary=summary_facts(parsed))


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


def _ledger(db):
    conn = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


def _table(conn):
    """``run_records`` holds each run once; a legacy ledger keeps the original ``runs`` table."""
    found = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_records'").fetchone()
    return 'run_records' if found else 'runs'


def _connect(path):
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.execute('PRAGMA busy_timeout = 30000')
    try:
        conn.execute('PRAGMA journal_mode = WAL')
    except sqlite3.OperationalError:
        pass
    conn.executescript(_SCHEMA)
    return conn


def _meta(conn, key, default=None):
    row = conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def _reset(conn):
    """Empty the sidecar for this code version; a new generation invalidates every in-process copy."""
    conn.execute('BEGIN IMMEDIATE')
    conn.execute('DELETE FROM runs')
    conn.execute('DELETE FROM extras')
    conn.execute('DELETE FROM meta')
    conn.executemany('INSERT INTO meta VALUES (?,?)', [('version', _version()), ('last_rowid', '0'),
                                                       ('generation', uuid.uuid4().hex)])
    conn.execute('COMMIT')


def _remove(path):
    for suffix in ('', '-wal', '-shm', '-journal'):
        try:
            Path(str(path) + suffix).unlink()
        except FileNotFoundError:
            pass


class SidecarError(Exception):
    """The sidecar file failed (unreadable, unwritable, full); the ledger is fine."""


def _sidecar(statement):
    def wrapped(*args, **kwargs):
        try:
            return statement(*args, **kwargs)
        except sqlite3.Error as exc:
            raise SidecarError(str(exc)) from exc
    return wrapped


class Cache:
    """The sidecar of one ledger, brought up to date when opened.

    An unusable sidecar file is replaced once; if that fails too, the facts are
    extracted into memory for this call only. Errors reading the ledger itself
    propagate exactly as the reports met them before.
    """

    def __init__(self, db):
        self.db = Path(db)
        path = cache_path(self.db)
        for attempt in ('file', 'fresh file', 'memory'):
            try:
                if attempt == 'fresh file':
                    _remove(path)
                self.conn = _sidecar(_connect)(':memory:' if attempt == 'memory' else path)
            except (SidecarError, OSError):
                continue
            try:
                self._update()
                return
            except SidecarError:
                self.conn.close()
                if attempt == 'memory':
                    raise
        raise SidecarError('no sidecar could be opened')

    def _update(self):
        conn = self.conn
        with closing(_ledger(self.db)) as ledger:
            table = _table(ledger)
            mine = _sidecar(lambda: (_meta(conn, 'version'), int(_meta(conn, 'last_rowid', '0')),
                [tuple(r) for r in conn.execute('SELECT ledger_rowid,run_id,created_at FROM runs ORDER BY ledger_rowid')]))()
            version, last, held = mine
            theirs = [tuple(r) for r in ledger.execute(f'SELECT rowid,run_id,created_at FROM {table} WHERE rowid<=? ORDER BY rowid', (last,))]
            if version != _version() or held != theirs:
                _sidecar(_reset)(conn)
                last = 0
            self._fold(ledger, table, last)
            self._reverify(ledger, table)

    def _fold(self, ledger, table, last):
        """Extract the runs saved since the last fold, committing as it goes."""
        heads = ledger.execute(f'SELECT rowid AS insertion_order,run_id,created_at,as_of,mode,scope FROM {table} '
                               'WHERE rowid>? ORDER BY rowid', (last,)).fetchall()
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
            body = ledger.execute(f'SELECT snapshot_json,forecast_json,snapshot_sha256,forecast_sha256 FROM {table} '
                                  'WHERE rowid=?', (head['insertion_order'],)).fetchone()
            row = dict(head, **dict(body))
            values = _extract(row)
            digests = json.dumps(_digests(row))
        return (head['insertion_order'], head['run_id'], head['created_at'], head['as_of'], head['mode'], head['scope'],
                digests, _clock() if live else None, *(_pack(values[k]) if live else None for k in KINDS))

    @_sidecar
    def _store(self, batch, generation=False):
        conn = self.conn
        conn.execute('BEGIN IMMEDIATE')
        try:
            conn.executemany(f'INSERT OR REPLACE INTO runs VALUES ({",".join("?" * (8 + len(KINDS)))})', batch)
            last = max(int(_meta(conn, 'last_rowid', '0')), max(r[0] for r in batch))
            conn.execute("UPDATE meta SET value=? WHERE key='last_rowid'", (str(last),))
            if generation:
                conn.executemany('DELETE FROM extras WHERE ledger_rowid=?', [(r[0],) for r in batch])
                conn.execute("UPDATE meta SET value=? WHERE key='generation'", (uuid.uuid4().hex,))
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise

    def _reverify(self, ledger, table):
        """Check again the runs verified longest ago; one whose bytes or digests changed is extracted again."""
        due = _sidecar(lambda: self.conn.execute('SELECT ledger_rowid,digests FROM runs WHERE digests IS NOT NULL AND verified_at<? '
                       'ORDER BY verified_at LIMIT ?', (_clock() - REVERIFY_SECONDS, REVERIFY_BATCH)).fetchall())()
        for rowid, saved in due:
            head = ledger.execute(f'SELECT rowid AS insertion_order,run_id,created_at,as_of,mode,scope,snapshot_json,'
                                  f'forecast_json,snapshot_sha256,forecast_sha256 FROM {table} WHERE rowid=?', (rowid,)).fetchone()
            if head is None:
                continue
            if _digests(dict(head)) == json.loads(saved):
                _sidecar(self.conn.execute)('UPDATE runs SET verified_at=? WHERE ledger_rowid=?', (_clock(), rowid))
                continue
            keys = ('insertion_order', 'run_id', 'created_at', 'as_of', 'mode', 'scope')
            self._store([self._record(ledger, table, {k: head[k] for k in keys})], generation=True)

    def rows(self, kind):
        """Every run in insertion order: its ledger columns and this report's facts (None unless live league)."""
        if kind not in KINDS:
            raise ValueError('unknown derived fact kind: ' + kind)
        state = _sidecar(lambda: (_meta(self.conn, 'generation'), _meta(self.conn, 'last_rowid')))()
        key = (id(self.conn) if self.memory else str(cache_path(self.db).resolve()), state[0], kind)
        with _LOCK:
            memo = _MEMO.get(key)
            if memo is not None and memo[0] == state[1]:
                return memo[1]
        result = _sidecar(lambda: [dict(insertion_order=r[0], run_id=r[1], created_at=r[2], as_of=r[3], mode=r[4], scope=r[5],
                                        value=_unpack(r[6]))
            for r in self.conn.execute(f'SELECT ledger_rowid,run_id,created_at,as_of,mode,scope,{kind} FROM runs ORDER BY ledger_rowid')])()
        with _LOCK:
            if len(_MEMO) > 16:
                _MEMO.clear()
            _MEMO[key] = (state[1], result)
        return result

    @property
    def memory(self):
        return _sidecar(lambda: self.conn.execute('PRAGMA database_list').fetchone()[2] == '')()

    def rowid_of(self, run_id):
        row = _sidecar(lambda: self.conn.execute('SELECT ledger_rowid FROM runs WHERE run_id=?', (run_id,)).fetchone())()
        return row[0] if row else None

    def get(self, kind, key):
        row = _sidecar(lambda: self.conn.execute('SELECT value FROM extras WHERE kind=? AND key=?', (kind, key)).fetchone())()
        return None if row is None else (_unpack(row[0]),)

    def put(self, kind, key, value, rowid=None):
        try:
            self.conn.execute('INSERT OR REPLACE INTO extras VALUES (?,?,?,?)', (kind, key, rowid, _pack(value)))
        except sqlite3.Error:
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
        self.conn.close()


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
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('DELETE FROM runs WHERE ledger_rowid>?', (rowid,))
        conn.execute('DELETE FROM extras WHERE ledger_rowid IS NULL OR ledger_rowid>?', (rowid,))
        conn.execute("UPDATE meta SET value=? WHERE key='last_rowid'", (str(rowid),))
        conn.execute("UPDATE meta SET value=? WHERE key='generation'", (uuid.uuid4().hex,))
        conn.execute('COMMIT')
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
