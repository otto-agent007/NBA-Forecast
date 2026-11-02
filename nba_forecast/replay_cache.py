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
"""
from collections import OrderedDict
import hashlib
import os

from .schedule import canonical

REPLAYS = 4096  # archived sources kept; a season has 1,230 games
LEAGUE = 16     # league-wide maps kept; a handful per history object
_replays = OrderedDict()
_league = OrderedDict()


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


def replay(key, compute):
    """``compute()`` once per key; callers share the result and must not change it. A failure is not kept."""
    value = _replays.get(key)
    if value is None:
        value = compute()
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
