"""Kalshi's NBA markets as a second live line source, captured at the forecast cadence and every minute near tip.

Kalshi is an exchange, so its price is a traded probability rather than a quoted
line: the home winner market's bid-ask mid is a win probability, and the spread
and total are the strikes where the market's probability crosses one half,
interpolated over the quoted strikes. Every response is archived with its hash
and retrieval time under ``market/kalshi/raw/``; a game's capture is appended to
``market/kalshi/<game_id>.jsonl`` when its numbers change, and the last capture
before tipoff is its closing price. Team codes are the NBA tricodes. This
records what the market said; it is not a quote, a recommendation or a bet.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.request
from zoneinfo import ZoneInfo

from .data import _NoRedirects
from .files import archive_bytes, atomic_bytes, atomic_json
from .market_lines import _parse_lines
from .validation import parse_time

VERSION = 'kalshi-lines-1'
API = 'https://api.elections.kalshi.com/trade-api/v2/markets?event_ticker={}&limit=100'
SERIES = dict(game='KXNBAGAME', spread='KXNBASPREAD', total='KXNBATOTAL')
EASTERN = ZoneInfo('America/New_York')
MAX_BYTES = 2 * 1024 * 1024
# A bid-ask wider than this is an empty book, not a price.
MAX_WIDTH = .12
# Capture cadence by time to tip: every minute in the last 5, every 5 in the last 30,
# every 15 within a day, hourly before that.
CADENCE = ((300, 60), (1800, 300), (86400, 900), (float('inf'), 3600))


def event_ticker(series, game):
    """``KXNBAGAME-26OCT26WASCHA``: the Eastern game date as YYMONDD, then the away and home tricodes."""
    day = parse_time(game['start_time']).astimezone(EASTERN)
    return f"{series}-{day.strftime('%y%b%d').upper()}{game['away_tricode']}{game['home_tricode']}"


def _download(url):
    opener = urllib.request.build_opener(_NoRedirects())
    request = urllib.request.Request(url, headers={'User-Agent': 'NBA-Forecast/0.1', 'Accept': 'application/json'})
    with opener.open(request, timeout=12) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Kalshi response exceeds size limit')
    return raw


def fetch_markets(url, directory, fetch=None):
    """The markets of one Kalshi event, archived by digest with the retrieval time."""
    if not re.fullmatch(r'https://api\.elections\.kalshi\.com/trade-api/v2/markets\?event_ticker=KXNBA(GAME|SPREAD|TOTAL)-[0-9A-Z]+&limit=100', url):
        raise ValueError('not a Kalshi NBA markets URL')
    raw = (fetch or _download)(url)
    retrieved = datetime.now(timezone.utc).isoformat()
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
    if not isinstance(payload, dict) or not isinstance(payload.get('markets'), list):
        raise ValueError('Kalshi response is not a market list')
    return payload['markets'], dict(source_url=url, retrieved_at=retrieved, sha256=digest)


def _dollars(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 1 else None


def mid(market):
    """The bid-ask mid of a market's yes price, or None when the book is empty or wide."""
    bid, ask = _dollars(market.get('yes_bid_dollars')), _dollars(market.get('yes_ask_dollars'))
    if bid is None or ask is None or ask < bid or ask - bid > MAX_WIDTH or ask <= 0 or bid >= 1:
        return None
    return (bid + ask) / 2


def crossing(points):
    """The x where a probability curve over strikes crosses one half, by linear interpolation; None without a bracket."""
    quoted = sorted((x, p) for x, p in points if p is not None)
    for (x0, p0), (x1, p1) in zip(quoted, quoted[1:]):
        if (p0 - .5) * (p1 - .5) <= 0 and p0 != p1:
            return x0 + (p0 - .5) / (p0 - p1) * (x1 - x0)
    return None


def extract(game, markets, captured_at, sources):
    """One capture: the home win probability, the implied home margin and total, and every quote behind them."""
    home, away = game['home_tricode'], game['away_tricode']
    winner = next((m for m in markets.get('game', []) if m.get('ticker', '').endswith('-' + home)), None)
    if winner is None:
        raise ValueError('Kalshi has no winner market for this game yet')
    spreads = []
    for m in markets.get('spread', []):
        match = re.search(r'-([A-Z]+)\d+$', m.get('ticker', ''))
        strike = m.get('floor_strike')
        if not match or isinstance(strike, bool) or not isinstance(strike, (int, float)):
            continue
        p = mid(m)
        if match.group(1) == home:
            spreads.append(dict(home_margin_over=strike, probability=p, ticker=m['ticker']))
        elif match.group(1) == away:  # 'away wins by over s' is the home margin below -s
            spreads.append(dict(home_margin_over=-strike, probability=1 - p if p is not None else None, ticker=m['ticker']))
    totals = [dict(total_over=m['floor_strike'], probability=mid(m), ticker=m['ticker'])
              for m in markets.get('total', []) if isinstance(m.get('floor_strike'), (int, float)) and not isinstance(m.get('floor_strike'), bool)]
    margin = crossing((s['home_margin_over'], s['probability']) for s in spreads)
    total = crossing((t['total_over'], t['probability']) for t in totals)
    return dict(schema_version=1, game_id=game['game_id'], start_time=game['start_time'], home_team_id=game['home_team_id'],
                away_team_id=game['away_team_id'], provider='Kalshi', captured_at=captured_at, sources=sources,
                home_win_probability=mid(winner), implied_home_margin=margin, implied_total=total,
                lines=dict(spread=dict(home=dict(current_line=-margin if margin is not None else None), away=dict(current_line=margin)),
                           total=dict(over=dict(current_line=total), under=dict(current_line=total)),
                           moneyline=dict(home=dict(current_odds=None), away=dict(current_odds=None))),
                spread_quotes=sorted(spreads, key=lambda s: s['home_margin_over']),
                total_quotes=sorted(totals, key=lambda t: t['total_over']),
                basis='bid-ask mids; the margin and total are where the quoted strikes cross one half, interpolated')


def _lines_path(folder, game_id):
    return Path(folder) / 'market' / 'kalshi' / (game_id + '.jsonl')


def read_kalshi_lines(folder, game_id):
    path = _lines_path(folder, game_id)
    return _parse_lines(path.read_bytes(), path.name)[0] if path.exists() else []


def _same(a, b):
    for key in ('home_win_probability', 'implied_home_margin', 'implied_total'):
        if (a.get(key) is None) != (b.get(key) is None) or (a.get(key) is not None and abs(a[key] - b[key]) >= 5e-4):
            return False
    return True


def record_kalshi_line(folder, record):
    """Append the capture when its numbers changed; the file is rewritten through a rename like the ESPN lines."""
    path = _lines_path(folder, record['game_id'])
    raw = path.read_bytes() if path.exists() else b''
    existing, clean, partial = _parse_lines(raw, path.name) if raw else ([], 0, False)
    if existing and _same(existing[-1], record):
        if partial:
            atomic_bytes(path, raw[:clean])
        return False
    prefix = raw[:clean]
    if prefix and not prefix.endswith(b'\n'):
        prefix += b'\n'
    atomic_bytes(path, prefix + json.dumps(record, sort_keys=True).encode() + b'\n')
    return True


def _slot(game, now):
    remaining = (parse_time(game['start_time']) - now).total_seconds()
    seconds = next(step for limit, step in CADENCE if remaining <= limit)
    return f"{game['game_id']}:{int(now.timestamp()) // seconds}:{seconds}"


class _Deferred(Exception):
    """The capture budget ran out between a game's market requests; nothing was recorded."""


def capture_kalshi_lines(folder, games, as_of, fetch=None, deadline=None):
    """Capture Kalshi's prices for the upcoming games, once per cadence slot each; failures are reported, never raised.

    ``deadline`` is a ``time.monotonic()`` instant: a game not finished by then is
    listed under ``deferred`` for the next tick (``capture_game_lines`` has the same contract).
    """
    folder = Path(folder)
    now = parse_time(as_of)
    state_path = folder / 'market' / 'kalshi' / 'capture-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    captured = {k: v for k, v in state.get('captured', {}).items() if now.timestamp() - v < 172800}
    report = dict(captured=[], unchanged=[], skipped=[], deferred=[], errors=[])
    late = lambda: deadline is not None and time.monotonic() > deadline
    for game in games:
        if not game.get('start_time') or parse_time(game['start_time']) <= now or not (game.get('home_tricode') and game.get('away_tricode')):
            continue
        slot = _slot(game, now)
        if slot in captured:
            report['skipped'].append(game['game_id'])
            continue
        if late():
            report['deferred'].append(game['game_id'])
            continue
        try:
            markets, sources = {}, []
            for kind, series in SERIES.items():
                if markets and late():
                    raise _Deferred()
                items, meta = fetch_markets(API.format(event_ticker(series, game)), folder, fetch)
                markets[kind] = items
                sources.append(meta)
            record = extract(game, markets, as_of, sources)
            appended = record_kalshi_line(folder, record)
            captured[slot] = now.timestamp()
            (report['captured'] if appended else report['unchanged']).append(game['game_id'])
        except _Deferred:
            report['deferred'].append(game['game_id'])
        except (ValueError, OSError, KeyError, TypeError) as exc:
            report['errors'].append(dict(game_id=game['game_id'], error=str(exc)))
    saved = dict(version=VERSION, captured=captured)
    if saved != state:  # a call with nothing due writes nothing
        atomic_json(state_path, saved)
    return report


def closing_kalshi_line(records, start_time):
    """The last capture before tipoff, or None."""
    tip = parse_time(start_time)
    pregame = [r for r in records if parse_time(r['captured_at']) < tip]
    return pregame[-1] if pregame else None
