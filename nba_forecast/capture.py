"""Market-line capture on its own one-minute timer, beside the forecast tick.

``python -m nba_forecast capture --directory DIR`` (``nba-capture.service`` and
``.timer``, every minute at :45) captures, for the games the tick treats as
upcoming (``schedule.upcoming_games`` of the saved calendar: scheduled, tipping
within 24 hours), ESPN's sportsbook lines, Kalshi's game markets and Kalshi's
player prop ladders, each at its own cadence slot. The three providers run side
by side, one lane per host (ESPN; Kalshi's game lines, then its props), until
one deadline, so a slow ESPN cannot starve Kalshi or the reverse. Every request
has a 12-second timeout and each lane checks the deadline before each request
(``capture_game_lines`` between a game's scoreboard and summary too), so a run
ends within ``RUN_SECONDS`` plus one request timeout, 52 seconds; name
resolution is not covered by the timeouts, and the unit's start limit ends a run
stuck there.

Every capture of upcoming games, the service's and the tick's, holds
``capture.lock``, so no two processes write ``market/capture-state.json``,
``market/index.json``, the line files or the Kalshi states at once. The derived
service's ``recover_evidence.auto_recover`` and the ``recover-evidence`` command
also append to ``market/game-lines/`` and ``market/kalshi/`` without it; they
write only files of tipped games, which no capture writes after tipoff. The
tick still captures the game lines itself when this service has not finished a
run within ``HEARTBEAT_SECONDS`` (``last-capture.json``): a host without the
unit, or with it stopped, keeps its lines. Each run that did something appends
one line to ``capture.jsonl``.

This module is light on purpose: it runs 1,440 times a day and imports neither
the model nor the ledger. What it records is what the markets said; it is not a
quote, a recommendation or a bet.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from .files import atomic_json
from .schedule import saved_calendar, upcoming_games
from .validation import parse_time

VERSION = 'capture-service-1'
LOCK = 'capture.lock'
# Each lane stops starting requests this long after the run starts; the request in
# flight can add its own timeout (12 s), so a run ends within 52 s, inside the minute.
RUN_SECONDS = 40
# The tick captures the game lines itself when no capture run has finished this
# recently. A healthy service finishes a run every minute (an idle run in about a
# second, a slate in under RUN_SECONDS + 12), so a finished run is at most about
# two minutes old when a tick looks; three minutes allows timer jitter and one slow
# run, and loses at most three one-minute Kalshi slots when the service is down.
HEARTBEAT_SECONDS = 180
# A run that found the lock held (busy) captured nothing and does not count: when the
# service fails and the tick's fallback spans :45, the next tick must capture again.
# Whoever holds the lock is seen by the tick's own non-blocking lock attempt.
ALIVE = ('complete', 'partial', 'waiting')
PROVIDERS = ('market_lines', 'kalshi_lines', 'kalshi_props')


def _now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def capture_lock(folder):
    """Hold ``capture.lock`` without waiting; yields False when another process holds it."""
    import fcntl
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    handle = (folder / LOCK).open('a')
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def heartbeat(folder, now):
    """Whether the capture service finished a run within ``HEARTBEAT_SECONDS``: ``(alive, detail)``.

    A run that failed as a whole (``error``) does not count, nor does a ``busy``
    one (busy runs do not replace ``last-capture.json``; ``ALIVE``).
    """
    path = Path(folder) / 'last-capture.json'
    try:
        last = json.loads(path.read_text())
        age = (parse_time(now) - parse_time(last['finished_at'])).total_seconds()
        status = last.get('status')
    except FileNotFoundError:
        return False, dict(reason='no capture service run on record (last-capture.json)')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return False, dict(reason=f'unreadable last-capture.json: {type(exc).__name__}')
    detail = dict(last_finished_at=last['finished_at'], last_status=status, age_seconds=round(age, 1))
    if status not in ALIVE:
        return False, dict(detail, reason='the last capture run failed')
    if not -60 <= age <= HEARTBEAT_SECONDS:
        return False, dict(detail, reason=f'no capture run finished in the last {HEARTBEAT_SECONDS} seconds')
    return True, detail


def _lane(calls, deadline):
    results = {}
    for name, call in calls:
        try:
            results[name] = (call(deadline), None)
        except Exception as exc:  # reported, never raised: one provider never fails the others
            results[name] = (None, exc)
    return results


def run_providers(lanes, seconds):
    """Run each lane of provider calls side by side, the calls of a lane in order, all until one deadline.

    A lane is a list of ``(name, call)``, a call a function of the deadline (a
    ``time.monotonic()`` instant ``seconds`` from now). One lane per host: a slow
    ESPN cannot hold up Kalshi, and Kalshi's two series run one after the other
    (game lines first), because side by side they drew HTTP 429 on 2026-11-10.
    Returns ``{name: (report, None) or (None, exception)}``.
    """
    deadline = time.monotonic() + max(0., seconds)
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, len(lanes)), thread_name_prefix='capture') as pool:
        for future in [pool.submit(_lane, lane, deadline) for lane in lanes]:
            results.update(future.result())
    return results


def tick_fallback(folder, calls, seconds, now):
    """The tick's capture when the service is not running: ``(summary, results)``.

    ``calls`` maps report names to functions of a deadline, run in sequence with
    half of ``seconds`` each from when each starts, so a slow first provider
    cannot starve the second. ``results`` is empty when the tick does not capture:
    the service is alive, or holds the lock right now.
    """
    try:
        alive, detail = heartbeat(folder, now)
        if alive:
            return dict(detail, by='nba-capture'), {}
        with capture_lock(folder) as held:
            if not held:
                return dict(detail, by='nba-capture', reason='capture.lock held: another process is capturing'), {}
            results = {}
            share = max(0., seconds) / max(1, len(calls))
            for name, call in calls.items():
                try:
                    results[name] = (call(time.monotonic() + share), None)
                except Exception as exc:
                    results[name] = (None, exc)
        return dict(detail, by='tick'), results
    except Exception as exc:  # the lock file could not be opened: never a reason to fail the tick
        return dict(by='none', error=f'{type(exc).__name__}: {exc}'), {}


def _counts(report):
    """A provider report reduced to counts and errors, for the one-line log."""
    if not isinstance(report, dict):
        return report
    out = {k: len(v) for k, v in report.items() if isinstance(v, list) and k != 'errors'}
    out['errors'] = report.get('errors', [])[:5]
    if len(report.get('errors', [])) > 5:
        out['error_count'] = len(report['errors'])
    return out


def summary_line(report):
    """The run as one line: provider reports reduced to counts, the game list to its length."""
    line = {k: v for k, v in report.items() if k not in PROVIDERS + ('games', 'schedule_version')}
    line['games'] = len(report.get('games') or [])
    line.update({name: _counts(report[name]) for name in PROVIDERS if name in report})
    return line


def _idle(line):
    """A run that found every game's slots already taken: it changes nothing worth a log line."""
    return line['status'] == 'complete' and not line.get('index_rebuilt') and all(
        not count for name in PROVIDERS for key, count in (line.get(name) or {}).items() if key != 'skipped')


def run_capture(directory, *, fetch=None, now=None):
    """One run of the capture service; returns its report.

    A run that finds ``capture.lock`` held reports ``busy``, captures nothing and
    leaves ``last-capture.json`` to the last run that captured. Otherwise the report
    is saved as ``last-capture.json``, and its counts appended to
    ``capture.jsonl`` after every run that did something (an idle run, every slot
    already taken, updates ``last-capture.json`` alone).
    """
    folder = Path(directory)
    started = time.monotonic()
    report = dict(version=VERSION, started_at=now or _now(), status='running', errors=[], warnings=[])
    with capture_lock(folder) as held:
        if not held:
            report['status'] = 'busy'
        else:
            try:
                _capture(folder, report, fetch, now)
                if report['status'] == 'running':
                    report['status'] = 'partial' if report['errors'] or any(
                        (report.get(name) or {}).get('errors') for name in PROVIDERS) else 'complete'
            except Exception as exc:
                report['errors'].append(dict(task='capture', error=f'{type(exc).__name__}: {exc}'))
                report['status'] = 'error'
        report['finished_at'] = _now()
        report['seconds'] = round(time.monotonic() - started, 3)
        if report['status'] != 'busy':
            atomic_json(folder / 'last-capture.json', report)
        line = summary_line(report)
        if not _idle(line):
            with (folder / 'capture.jsonl').open('a') as stream:
                stream.write(json.dumps(line, sort_keys=True) + '\n')
    return report


def _capture(folder, report, fetch, now):
    """``now`` fixes the clock (tests); otherwise each provider takes the time it starts, as the tick's calls did."""
    from .kalshi_lines import capture_kalshi_lines
    from .kalshi_props import capture_kalshi_props
    from .market_lines import capture_game_lines, refresh_index
    clock = (lambda: now) if now else _now
    schedule, source = saved_calendar(folder)
    if schedule is None:
        report.update(status='waiting', reason='No saved calendar yet; the first tick saves one.')
        return
    games = upcoming_games(schedule, now or report['started_at'])
    report.update(schedule_source=source, schedule_version=schedule.get('version'), games=[g['game_id'] for g in games])
    if games:
        season = schedule['season']
        results = run_providers([
            [('market_lines', lambda deadline: capture_game_lines(folder, games, season, clock(), deadline=deadline, index=False))],
            [('kalshi_lines', lambda deadline: capture_kalshi_lines(folder, games, clock(), fetch=fetch, deadline=deadline)),
             ('kalshi_props', lambda deadline: capture_kalshi_props(folder, games, clock(), fetch=fetch, deadline=deadline))]], RUN_SECONDS)
        for name, (value, error) in results.items():
            if error is None:
                report[name] = value
            else:
                report['errors'].append(dict(task=name, error=f'{type(error).__name__}: {error}'))
    # Once per run, games or not (a close recovered after an outage lands in a line file), and only when a line file changed.
    try:
        report['index_rebuilt'] = refresh_index(folder)
    except Exception as exc:
        report['errors'].append(dict(task='market_index', error=f'{type(exc).__name__}: {exc}'))
