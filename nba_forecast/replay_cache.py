"""Evidence replayed once per process, not once per forecast.

A forecast job prepares the league-wide shot, shot-quality and recap evidence
from the immutable archives, and preflight then replays the same archives to
verify what was prepared. Each archived source replays to the same result
whatever game is being forecast, and the league-wide history maps those
preparations start from depend only on the tick's history and a few filters,
so a fifteen-game slate replayed every source of the season thirty times a
tick. The tick is one process: here each source is replayed once per process
and each history map built once per history object, and every caller reads
the shared result. Nothing here changes what a preparation or verification
computes; a different archive digest, row set, filter or archive file (by
size and modification time) is a different key.

The tick is a new process every minute, so a replay given its data folder is
also kept on disk (``cache/replay/<code>/`` under that folder) and the first
forecast of the next tick reads it instead of replaying the season's archives
again. The disk key adds the package's source digest: a deploy starts a new
store, and each process's saves remove the other code's stores until that
succeeds once. An entry that cannot be read, or that was stored under another
key, is replayed and written again. Entries are pickles written by this process
into the operator's own data folder; a temporary file a killed process left is
removed once it is older than any tick can run (``STALE_TMP_SECONDS``).

League-wide history maps stay per process: they are rebuilt in about a second
from the history the tick already holds in memory, and keying them on disk
would mean digesting that history every tick and writing tens of megabytes
whenever a final arrives.
"""
from collections import OrderedDict
import hashlib
import os
from pathlib import Path
import pickle
import time

from .files import atomic_bytes
from .schedule import canonical

REPLAYS = 4096  # archived sources kept; a season has 1,230 games
LEAGUE = 16     # league-wide maps kept; a handful per history object
DISK_ENTRIES = 8192  # replays kept on disk per data folder; about three per game played
DISK_VERSION = 1
# A tick is stopped after 10 minutes; a temporary file older than this has no writer left.
STALE_TMP_SECONDS = 3600
_replays = OrderedDict()
_league = OrderedDict()
_code = []
_cleaned = set()  # stores whose other-code siblings this process has removed
_MISSING = object()


def digest(value):
    """The content key of rows, an envelope or a filter."""
    return hashlib.sha256(canonical(value)).hexdigest()


def stamp(paths):
    """The size and modification time of each archive a replay reads: a rewritten file is a different key."""
    stamps = []
    for path in paths:
        try:
            info = os.stat(path)
            stamps.append((str(path), info.st_size, info.st_mtime_ns))
        except OSError:
            stamps.append((str(path), None, None))
    return tuple(stamps)


def code_digest():
    """The package's source digest, computed once per process: a replay stored by other code is not reused."""
    if not _code:
        package = Path(__file__).resolve().parent
        value = hashlib.sha256()
        for path in sorted(package.rglob('*.py')):
            value.update(path.relative_to(package).as_posix().encode() + b'\0' + path.read_bytes())
        _code.append(value.hexdigest())
    return _code[0]


def _store(directory):
    return Path(directory)/'cache'/'replay'/code_digest()[:16]


def _disk_key(key):
    return hashlib.sha256(canonical([DISK_VERSION, code_digest(), key])).hexdigest()


def _load(path, name):
    """The stored value, or ``_MISSING`` when there is none or it cannot be trusted."""
    try:
        raw = path.read_bytes()
    except OSError:
        return _MISSING
    try:
        version, stored, value = pickle.loads(raw)
        if version != DISK_VERSION or stored != name:
            raise ValueError('replay entry stored under another key')
    except Exception:
        return _MISSING
    try:
        os.utime(path)  # Recently read entries are the last to be pruned.
    except OSError:
        pass
    return value


def _remove_other_stores(store):
    """Remove the stores other code left beside ``store``; a save tries again until this succeeds once per process."""
    if str(store) in _cleaned:
        return
    done = True
    for old in store.parent.iterdir():
        if old == store or not old.is_dir():
            continue
        try:
            for entry in old.iterdir():
                entry.unlink()
            old.rmdir()
        except OSError:
            done = False
    if done:
        _cleaned.add(str(store))


def _save(store, path, name, value):
    """Write the entry; a full or read-only folder costs the next process a replay, never this forecast."""
    try:
        atomic_bytes(path, pickle.dumps((DISK_VERSION, name, value), protocol=pickle.HIGHEST_PROTOCOL))
        _remove_other_stores(store)
        entries = []
        now = time.time()
        for entry in os.scandir(store):
            if entry.name.endswith('.pickle'):
                entries.append(entry)
            elif entry.name.endswith('.tmp') and now - entry.stat().st_mtime > STALE_TMP_SECONDS:
                os.unlink(entry.path)
        if len(entries) > DISK_ENTRIES:
            entries.sort(key=lambda e: e.stat().st_mtime_ns)
            for entry in entries[:len(entries) - DISK_ENTRIES]:
                os.unlink(entry.path)
    except (OSError, pickle.PicklingError, TypeError, AttributeError):
        pass


def replay(key, compute, directory=None):
    """``compute()`` once per key; callers share the result and must not change it. A failure is not kept.

    With ``directory`` (the data folder the replay reads) the result is also kept
    on disk for later processes, and read from there before ``compute()`` runs.
    """
    value = _replays.get(key)
    if value is None:
        value = _MISSING
        if directory is not None:
            store = _store(directory)
            name = _disk_key(key)
            path = store/(name+'.pickle')
            value = _load(path, name)
        if value is _MISSING:
            value = compute()
            if directory is not None:
                _save(store, path, name, value)
        _replays[key] = value
        while len(_replays) > REPLAYS:
            _replays.popitem(last=False)
    else:
        _replays.move_to_end(key)
    return value


def league(history, key, compute):
    """``compute()`` once per history object and key.

    The history is held with its result so that its identity cannot be reused
    by another list while the entry lives; an equal list that is a different
    object is computed again.
    """
    slot = (id(history), key)
    entry = _league.get(slot)
    if entry is not None and entry[0] is history:
        _league.move_to_end(slot)
        return entry[1]
    value = compute()
    _league[slot] = (history, value)
    while len(_league) > LEAGUE:
        _league.popitem(last=False)
    return value


def horizon(history):
    """The latest game or observation time in ``history``: any cutoff after it selects the same rows."""
    from .validation import parse_time
    return league(history, 'horizon', lambda: max((max(parse_time(r['game_time']), parse_time(r['known_at'])) for r in history), default=None))


def cutoff_key(history, cutoff):
    """``cutoff`` as a map key: every cutoff after the history's horizon is the same key."""
    latest = horizon(history)
    return 'beyond' if latest is not None and cutoff > latest else cutoff.isoformat()


def clear():
    _replays.clear()
    _league.clear()
    _cleaned.clear()
