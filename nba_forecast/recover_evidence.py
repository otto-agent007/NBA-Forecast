"""Recover what an outage left uncaptured, as labeled late captures.

Most of what a tick captures live can be fetched later from sources that keep
history: every official injury report edition stays online at its dated URL,
ESPN's odds endpoint records a finished event's closing line, and Kalshi's
candlesticks hold each market's minute-by-minute prices. What cannot be
recovered is ESPN's injury list and game summaries as they stood at the time,
so a team the official report left uncovered stays unresolved.

Nothing here creates a forecast or a final checkpoint. A recovered record
carries ``late_capture`` (or ``recovered_close`` / ``recovered_from_candlesticks``)
with its real retrieval time; an existing live record is never replaced, and
the tick's missed-checkpoint record is untouched.

- T-30 report records (``injuries/games/<game>/final.json``): the edition
  published at or before the checkpoint. ``injury_capture.reported_unavailable``
  reads it, so a player's reported-out games leave his participation window.
- Closing lines (``market/game-lines/<game>.jsonl``): ESPN's recorded close,
  which ``market_lines.closing_lines`` prefers to a stale pregame capture.
- Kalshi (``market/kalshi/<game>.jsonl``): the last pre-tip bid-ask mids from
  the candlesticks, timed by the exchange, so the market report's second close
  exists for the game.
- Daily reports (``injuries/daily/<season>-<date>.json``): the edition the
  9 AM Pacific capture would have taken.

The derived service runs the three per-game recoveries by itself
(``auto_recover``) for every game the tick recorded as a missed final, once the
game is over; the command line remains for daily reports and for re-running.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

from .data import _fetch
from .files import archive_bytes, atomic_json
from .injuries import checkpoints, report_time
from .injury_capture import record_check
from .injury_history import candidates
from .kalshi_lines import API, SERIES, _download, closing_kalshi_line, event_ticker, fetch_markets, read_kalshi_lines, record_kalshi_line
from .kalshi_lines import extract as extract_kalshi
from .market_history import ODDS_URL, closing_line
from .market_lines import read_game_lines, record_game_lines
from .reference import fetch_reference, scoreboard_event
from .validation import parse_time

VERSION = 'recover-evidence-1'
INDEX_URL = 'https://official.nba.com/nba-injury-report-{}-season/'
CANDLE_URL = 'https://api.elections.kalshi.com/trade-api/v2/series/{}/markets/{}/candlesticks?start_ts={}&end_ts={}&period_interval=1'
CANDLE_PATTERN = (r'https://api\.elections\.kalshi\.com/trade-api/v2/series/KXNBA(GAME|SPREAD|TOTAL)/markets/[0-9A-Z-]+/candlesticks'
                  r'\?start_ts=\d+&end_ts=\d+&period_interval=1')
CANDLE_HOURS = 1      # the hour before tipoff
PAUSE = 2.            # the NBA report CDN blocks bursts; Kalshi asks for restraint too
RETRY_PAUSE = 30.     # a throttled request answers like an absent file; one retry tells them apart
PACIFIC = ZoneInfo('America/Los_Angeles')
EASTERN = ZoneInfo('America/New_York')
AUTO_FILE = 'recovered-evidence.json'
AUTO_SOURCES = ('reports', 'closing_lines', 'kalshi')
AUTO_DONE = ('recorded', 'exists', 'unchanged')
AUTO_RETRY_MINUTES = (30, 120, 360)  # waits after the first, second and third failed attempts; the fourth gives up
AUTO_OVER_HOURS = 6                  # a game the calendar has not marked final is over this long after tipoff
AUTO_GAMES_PER_RUN = 2               # bounds one derived run: a game whose report editions all fail costs about six minutes


def _now():
    return datetime.now(timezone.utc).isoformat()


def _late(now, reason):
    return dict(version=VERSION, recovered_at=now, reason=reason)


def _games(schedule, game_ids):
    by_id = {g['game_id']: g for g in schedule['games']}
    for gid in game_ids:
        yield gid, by_id.get(gid)


def _fetch_edition(urls, raw, fetch, sleep, cache=None):
    """The first candidate edition that downloads as a PDF, archived; (report, failures).

    An edition already fetched in this run (``cache``) is reused: the same
    edition anchors every game of a slot, and the CDN throttles repeats. A slot
    that fails is retried once after a pause before the previous slot is tried.
    """
    failures = []
    cache = {} if cache is None else cache
    for url in urls:
        if url in cache:
            return cache[url], failures
        for attempt in range(2):
            try:
                body, retrieved = fetch(url, raw, timeout=20)
                if not body.startswith(b'%PDF-'):
                    raise ValueError('official injury report response is not a PDF')
                cache[url] = dict(source_url=url, published_at=report_time(url).isoformat(),
                                  sha256=hashlib.sha256(body).hexdigest(), retrieved_at=retrieved)
                return cache[url], failures
            except (ValueError, OSError) as exc:
                if attempt == 0:
                    sleep(RETRY_PAUSE)
                else:
                    failures.append(dict(source_url=url, error=str(exc), attempts=2))
        sleep(PAUSE)
    return None, failures


def recover_report_records(directory, schedule, game_ids, *, now=None, fetch=_fetch, sleep=time.sleep):
    """Each game's T-30 anchor edition, archived and recorded as a late final-checkpoint record.

    The record is what ``reported_unavailable`` reads for later forecasts; it
    is not a final forecast. A game with a live ``final.json`` is left alone.
    """
    folder = Path(directory)
    now = now or _now()
    out = []
    editions = {}
    for gid, game in _games(schedule, game_ids):
        if game is None or not game.get('start_time'):
            out.append(dict(game_id=gid, status='unknown_game'))
            continue
        root = folder / 'injuries' / 'games' / gid
        if (root / 'final.json').exists():
            out.append(dict(game_id=gid, status='exists'))
            continue
        if parse_time(game['start_time']) > parse_time(now):
            out.append(dict(game_id=gid, status='not_tipped'))
            continue
        anchor, failures = _fetch_edition(candidates(game['start_time']), folder / 'raw', fetch, sleep, editions)
        if anchor is None:
            out.append(dict(game_id=gid, status='unavailable', failures=failures))
            continue
        check = dict(schema_version=1, policy='per-game-t-minus-30', index_url=INDEX_URL.format(schedule['season']),
                     index_observed_at=None, index_sha256=None, checked_at=now,
                     games=checkpoints([game], [anchor], now), availability_applied=False,
                     late_capture=_late(now, 'The checkpoint never ran (host outage); the edition published by T-30 '
                                             'was fetched later from the NBA archive.'))
        record_check(folder, check, 'final')  # the archive and latest.json; final.json is only written live
        atomic_json(root / 'final.json', dict(check, forecast_stage='final'))
        out.append(dict(game_id=gid, status='recorded', anchor=anchor['source_url'], published_at=anchor['published_at'],
                        retrieved_at=anchor['retrieved_at'], report_status=check['games'][0]['report_status'], failures=failures))
        sleep(PAUSE)
    return out


def _odds(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def recover_closing_lines(directory, schedule, game_ids, *, now=None, fetch=fetch_reference):
    """ESPN's recorded closing line of each finished event, appended as a late game-lines capture."""
    folder = Path(directory)
    refs = folder / 'references'
    now = now or _now()
    out = []
    for gid, game in _games(schedule, game_ids):
        if game is None or not game.get('start_time'):
            out.append(dict(game_id=gid, status='unknown_game'))
            continue
        if parse_time(game['start_time']) > parse_time(now):
            out.append(dict(game_id=gid, status='not_tipped'))
            continue
        existing = read_game_lines(folder, gid)
        if any(r.get('recovered_close') for r in existing):
            out.append(dict(game_id=gid, status='exists'))
            continue
        event = next((r['event_id'] for r in existing if r.get('event_id')), None)
        if event is None:
            try:
                matched, _ = scoreboard_event(game, refs)
                event = matched and matched.get('provider_event_id')
            except (ValueError, OSError, KeyError, TypeError) as exc:
                out.append(dict(game_id=gid, status='unavailable', error=f'scoreboard: {exc}'))
                continue
        if not event:
            out.append(dict(game_id=gid, status='no_event'))
            continue
        try:
            payload, meta = fetch(ODDS_URL.format(event), refs, max_age=10 ** 10)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            out.append(dict(game_id=gid, status='unavailable', error=f'odds: {exc}'))
            continue
        line = closing_line(payload)
        if line is None:
            out.append(dict(game_id=gid, status='no_close', event_id=str(event)))
            continue
        provider = next((i.get('provider') or {} for i in payload.get('items') or []
                         if (i.get('provider') or {}).get('name') == line['provider']), {})
        spread, total = _odds(line['home_spread']), _odds(line['total'])
        side = lambda value: dict(current_line=value, current_odds=None, open_line=None, open_odds=None)  # noqa: E731
        record = dict(schema_version=1, game_id=gid, start_time=game['start_time'], event_id=str(event),
                      home_team_id=game['home_team_id'], away_team_id=game['away_team_id'],
                      provider_id=str(provider['id']) if provider.get('id') is not None else 'close',
                      provider=line['provider'], details=None,
                      lines=dict(spread=dict(home=side(spread), away=side(-spread if spread is not None else None)),
                                 total=dict(over=side(total), under=side(total)),
                                 moneyline=dict(home=dict(side(None), current_odds=_odds(line['home_moneyline'])),
                                                away=dict(side(None), current_odds=_odds(line['away_moneyline'])))),
                      source_url=meta['source_url'], retrieved_at=meta['retrieved_at'], sha256=meta['sha256'],
                      recovered_close=_late(now, "ESPN's recorded close for the event, fetched after the game; "
                                                 'no pregame capture ran at the close.'))
        appended = record_game_lines(folder, [record], now)
        out.append(dict(game_id=gid, status='recorded' if appended else 'unchanged', event_id=str(event),
                        provider=line['provider'], home_spread=spread, total=total))
    return out


def fetch_candlesticks(series, ticker, start, end, directory, fetch=None):
    """One market's one-minute candlesticks over [start, end], archived like the market lists."""
    url = CANDLE_URL.format(series, ticker, int(parse_time(start).timestamp()), int(parse_time(end).timestamp()))
    if not re.fullmatch(CANDLE_PATTERN, url):
        raise ValueError('not a Kalshi NBA candlestick URL')
    raw = (fetch or _download)(url)
    retrieved = _now()
    digest = hashlib.sha256(raw).hexdigest()
    folder = Path(directory) / 'market' / 'kalshi' / 'raw'
    archive_bytes(folder / (digest + '.json'), raw)
    meta = folder / (digest + '.provenance.json')
    if not meta.exists():
        atomic_json(meta, dict(source_url=url, retrieved_at=retrieved, sha256=digest, bytes=len(raw)))
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ValueError('Kalshi response is not JSON') from exc
    if not isinstance(payload, dict) or not isinstance(payload.get('candlesticks'), list):
        raise ValueError('Kalshi response is not a candlestick list')
    return payload['candlesticks'], dict(source_url=url, retrieved_at=retrieved, sha256=digest)


def _last_quote(candles, tip):
    """(end time, bid, ask) of the last candle closing before tipoff with both sides quoted, or None.

    A candle ending at the tipoff instant is not pregame (``closing_kalshi_line``
    takes captures strictly before tipoff), so the close is the candle before it.
    """
    best = None
    for c in candles:
        end = c.get('end_period_ts')
        bid, ask = (c.get('yes_bid') or {}).get('close_dollars'), (c.get('yes_ask') or {}).get('close_dollars')
        if not isinstance(end, (int, float)) or end >= tip.timestamp() or bid is None or ask is None:
            continue
        if best is None or end > best[0]:
            best = (end, bid, ask)
    return best


def recover_kalshi_lines(directory, schedule, game_ids, *, now=None, fetch=None, sleep=time.sleep):
    """Kalshi's last pre-tip prices, from the candlesticks, as a late capture timed by the exchange."""
    folder = Path(directory)
    now = now or _now()
    out = []
    for gid, game in _games(schedule, game_ids):
        if game is None or not game.get('start_time'):
            out.append(dict(game_id=gid, status='unknown_game'))
            continue
        tip = parse_time(game['start_time'])
        if tip > parse_time(now):
            out.append(dict(game_id=gid, status='not_tipped'))
            continue
        existing = read_kalshi_lines(folder, gid)
        if any(r.get('recovered_from_candlesticks') for r in existing):
            out.append(dict(game_id=gid, status='exists'))
            continue
        start = (tip - timedelta(hours=CANDLE_HOURS)).isoformat()
        markets, sources, failures, ends = {}, [], [], []
        for key, series in SERIES.items():
            try:
                items, meta = fetch_markets(API.format(event_ticker(series, game)), folder, fetch=fetch)
            except (ValueError, OSError) as exc:
                failures.append(dict(series=series, error=str(exc)))
                continue
            sources.append(meta)
            priced = []
            for m in items:
                ticker = m.get('ticker')
                if not isinstance(ticker, str):
                    continue
                try:
                    candles, cmeta = fetch_candlesticks(series, ticker, start, game['start_time'], folder, fetch=fetch)
                except (ValueError, OSError) as exc:
                    failures.append(dict(ticker=ticker, error=str(exc)))
                    sleep(PAUSE / 10)
                    continue
                sources.append(cmeta)
                quote = _last_quote(candles, tip)
                if quote:
                    priced.append(dict(m, yes_bid_dollars=quote[1], yes_ask_dollars=quote[2], status='recovered'))
                    ends.append(quote[0])
                sleep(PAUSE / 10)
            markets[key] = priced
        if not ends:
            out.append(dict(game_id=gid, status='unavailable', failures=failures))
            continue
        captured_at = datetime.fromtimestamp(max(ends), timezone.utc).isoformat()
        try:
            record = extract_kalshi(game, markets, captured_at, sources)
        except ValueError as exc:
            out.append(dict(game_id=gid, status='unavailable', error=str(exc), failures=failures))
            continue
        record['recovered_from_candlesticks'] = _late(now, "The last pre-tip bid-ask mids from Kalshi's candlesticks; "
                                                           'captured_at is the exchange time of that candle.')
        appended = record_kalshi_line(folder, record)
        close = closing_kalshi_line(read_kalshi_lines(folder, gid), game['start_time'])
        out.append(dict(game_id=gid, status='recorded' if appended else 'unchanged', captured_at=captured_at,
                        home_win_probability=record['home_win_probability'], implied_home_margin=record['implied_home_margin'],
                        implied_total=record['implied_total'], is_close=bool(close and close.get('captured_at') == captured_at),
                        failures=failures))
    return out


def recover_daily_report(directory, season, date, *, now=None, fetch=_fetch, sleep=time.sleep):
    """The edition the 9 AM Pacific daily capture would have taken on ``date``, recorded as a late capture."""
    folder = Path(directory)
    now = now or _now()
    path = folder / 'injuries' / 'daily' / f'{season}-{date}.json'
    if path.exists():
        return dict(date=date, status='exists')
    scheduled = datetime.fromisoformat(date).replace(hour=9, tzinfo=PACIFIC).astimezone(timezone.utc)
    # candidates() anchors on an Eastern clock time of a game day; two hours past the capture time keeps
    # the T-30 fallback later than the anchor, so the anchor is the checkpoint.
    anchor = scheduled.astimezone(EASTERN).strftime('%H:%M')
    report, failures = _fetch_edition(candidates((scheduled + timedelta(hours=2)).isoformat(), anchor=anchor), folder / 'raw', fetch, sleep)
    if report is None:
        return dict(date=date, status='unavailable', failures=failures)
    capture = dict(schema_version=1, season=season, index_url=INDEX_URL.format(season), index_observed_at=None, index_sha256=None,
                   checked_at=report['retrieved_at'], report=report, capture_date=date, scheduled_at=scheduled.isoformat(),
                   late_capture=_late(now, 'The daily capture never ran (host outage); the edition published by 9 AM Pacific '
                                           'was fetched later from the NBA archive.'))
    atomic_json(path, capture)
    return dict(date=date, status='recorded', anchor=report['source_url'], published_at=report['published_at'], failures=failures)


def recover(directory, game_ids, *, reports=True, closing_lines=True, kalshi=True, daily_reports=(), wait=900, now=None):
    """Run the selected recoveries under the tick's locks; the result lists every item's status."""
    from .deploy import _locks
    folder = Path(directory)
    schedule = json.loads((folder / 'schedule' / 'latest.json').read_text())
    now = now or _now()
    result = dict(version=VERSION, started_at=now, directory=str(folder), games=list(game_ids))
    with _locks(folder, wait):
        if game_ids and reports:
            result['reports'] = recover_report_records(folder, schedule, game_ids, now=now)
        if game_ids and closing_lines:
            result['closing_lines'] = recover_closing_lines(folder, schedule, game_ids, now=now)
        if game_ids and kalshi:
            result['kalshi'] = recover_kalshi_lines(folder, schedule, game_ids, now=now)
        if daily_reports:
            result['daily_reports'] = [recover_daily_report(folder, schedule['season'], date, now=now) for date in daily_reports]
    result['finished_at'] = _now()
    result['unresolved'] = sorted({item.get('game_id') or item.get('date') for key in ('reports', 'closing_lines', 'kalshi', 'daily_reports')
                                   for item in result.get(key, []) if item['status'] in ('unavailable', 'no_close', 'no_event', 'unknown_game')})
    return result


def _auto_due(source, now):
    """Whether one source of one game should be tried now."""
    if source is None:
        return True
    if source['status'] in AUTO_DONE or source.get('gave_up'):
        return False
    return parse_time(source['next_attempt_at']) <= now


def _over(folder, game, now):
    """Whether a game has finished, so no tick writes its files and ESPN has recorded its close."""
    if game is None or not game.get('start_time'):
        return True  # the recoveries report an unknown game, which ends its attempts
    if game.get('status') == 'final' or (folder / 'finals' / (game['game_id'] + '.json')).exists():
        return True
    return now - parse_time(game['start_time']) >= timedelta(hours=AUTO_OVER_HOURS)


def _auto_result(item):
    result = {k: v for k, v in item.items() if k not in ('game_id', 'failures')}
    if item.get('failures'):
        result.update(failures=item['failures'][:5], failure_count=len(item['failures']))
    return result


def auto_recover(directory, schedule, *, now=None, run=None):
    """Recover each missed game's evidence once the game is over; the derived service calls this every run.

    The games are the tick's missed-final record (``missed-finals.json``). Each
    source (the T-30 report edition, ESPN's close, Kalshi's close) is tried
    until it is recorded or found already there; a failed source is tried
    again after each wait in ``AUTO_RETRY_MINUTES`` and given up after the last.
    Progress is kept in ``recovered-evidence.json``. Only files of finished
    games are written, which no tick touches after tipoff, so this runs under
    the derived service's lock alone and never holds up a tick. Returns a
    summary, or None when nothing was due.
    """
    folder = Path(directory)
    now = now or _now()
    clock = parse_time(now)
    run = run or dict(reports=recover_report_records, closing_lines=recover_closing_lines, kalshi=recover_kalshi_lines)
    missed_path = folder / 'missed-finals.json'
    if not missed_path.exists():
        return None
    missed = {g['game_id']: g['start_time'] for g in json.loads(missed_path.read_text()).get('games', [])}
    path = folder / AUTO_FILE
    state = json.loads(path.read_text()) if path.exists() else dict(games={})
    by_id = {g['game_id']: g for g in schedule['games']}
    due = []
    for gid, start in sorted(missed.items(), key=lambda item: (item[1] or '', item[0])):
        entry = state['games'].get(gid)
        if entry is None or entry.get('start_time') != start:  # new, or rescheduled: start over
            entry = state['games'][gid] = dict(start_time=start, sources={})
        sources = [s for s in AUTO_SOURCES if _auto_due(entry['sources'].get(s), clock)]
        if sources and _over(folder, by_id.get(gid), clock):
            due.append((gid, sources))
    due = due[:AUTO_GAMES_PER_RUN]
    if not due:
        return None
    summary = dict(started_at=now, games=[gid for gid, _ in due], recorded=[], retrying=[], gave_up=[])
    for source in AUTO_SOURCES:
        ids = [gid for gid, sources in due if source in sources]
        if not ids:
            continue
        try:
            results = {item['game_id']: item for item in run[source](folder, schedule, ids, now=now)}
        except Exception as exc:  # one source failing leaves the others and the derived reports running
            results = {gid: dict(game_id=gid, status='error', error=f'{type(exc).__name__}: {exc}') for gid in ids}
        for gid in ids:
            item = results.get(gid) or dict(game_id=gid, status='error', error='no result')
            attempts = (state['games'][gid]['sources'].get(source) or {}).get('attempts', 0) + 1
            entry = dict(status=item['status'], attempts=attempts, last_attempt_at=now, result=_auto_result(item))
            label = f'{gid}:{source}'
            if item['status'] in AUTO_DONE:
                if item['status'] == 'recorded':
                    summary['recorded'].append(label)
            elif item['status'] == 'unknown_game' or attempts > len(AUTO_RETRY_MINUTES):
                entry['gave_up'] = True
                summary['gave_up'].append(label)
            else:
                entry['next_attempt_at'] = (clock + timedelta(minutes=AUTO_RETRY_MINUTES[attempts - 1])).isoformat()
                summary['retrying'].append(label)
            state['games'][gid]['sources'][source] = entry
    state.update(schema_version=1, version=VERSION, updated_at=_now(),
                 policy='Each missed final game gets its T-30 report edition, ESPN close and Kalshi close fetched once it is over, '
                        'as labeled late captures; a failed source is retried after 30 min, 2 h and 6 h, then given up.')
    atomic_json(path, state)
    summary['finished_at'] = _now()
    return summary
