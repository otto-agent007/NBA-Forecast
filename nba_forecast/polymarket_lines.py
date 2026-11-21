"""Polymarket's NBA game markets as a third live line source, captured at the Kalshi cadence.

Polymarket is an exchange like Kalshi: the winner market's bid-ask mid is a win
probability, and the spread and total are the lines where the market's
probability crosses one half, interpolated over the listed lines. A game is one
event, ``nba-<away>-<home>-<Eastern date>`` with lowercase NBA tricodes, read
from the Gamma API, which lists its markets (winner, spreads, totals and many
props) with their volume, liquidity and order-book tokens. Gamma sits behind a
five-minute cache, so its own prices can be that old; the prices recorded here
are the best bid and ask of each market's live order book, from one request to
the CLOB API for the game's winner, spread and total tokens. Both responses are
archived as received (gzip) with their hash and retrieval time under
``market/polymarket/raw/``; a game's capture is appended to
``market/polymarket/<game_id>.jsonl`` when its numbers change, and the last
capture before tipoff is its closing price. This records what the market said;
it is not a quote, a recommendation or a bet, and no price here enters a forecast.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError
import urllib.request
import zlib

from .data import _NoRedirects
from .files import archive_bytes, atomic_bytes, atomic_json
from .kalshi_lines import CADENCE, EASTERN, MAX_WIDTH, _Deferred, _same, crossing
from .market_lines import _parse_lines
from .validation import parse_time

VERSION = 'polymarket-lines-1'
EVENT_API = 'https://gamma-api.polymarket.com/events/keyset?slug={}'
BOOKS_API = 'https://clob.polymarket.com/books'
# The event lists some 500 markets (2 MB of JSON, 0.2 MB gzipped); the books of its ~67 game-line tokens are 0.14 MB.
MAX_BYTES = 4 * 1024 * 1024
MAX_JSON = 32 * 1024 * 1024
KINDS = dict(game='moneyline', spread='spreads', total='totals')


def event_slug(game):
    """``nba-por-nyk-2026-11-21``: the away and home tricodes in lowercase, then the Eastern game date."""
    day = parse_time(game['start_time']).astimezone(EASTERN)
    return f"nba-{game['away_tricode'].lower()}-{game['home_tricode'].lower()}-{day.strftime('%Y-%m-%d')}"


def _download(url, body=None):
    """GET, or POST of a JSON ``body``; the bytes as received, gzip when the server compresses."""
    opener = urllib.request.build_opener(_NoRedirects())
    headers = {'User-Agent': 'NBA-Forecast/0.1', 'Accept': 'application/json', 'Accept-Encoding': 'gzip'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    with opener.open(urllib.request.Request(url, data=body, headers=headers), timeout=12) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Polymarket response exceeds size limit')
    return raw


def _fetch(url, directory, fetch=None, body=None):
    """One Polymarket response as JSON, its received bytes archived by digest with the retrieval time."""
    if body is None:
        if not re.fullmatch(r'https://gamma-api\.polymarket\.com/events/keyset\?slug=nba-[a-z]{3}-[a-z]{3}-\d{4}-\d{2}-\d{2}', url):
            raise ValueError('not a Polymarket NBA event URL')
        raw = (fetch or _download)(url)
    else:
        if url != BOOKS_API or not re.fullmatch(rb'\[\{"token_id":"\d{1,80}"\}(,\{"token_id":"\d{1,80}"\})*\]', body):
            raise ValueError('not a Polymarket order book request')
        raw = (fetch or _download)(url, body)
    retrieved = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(raw).hexdigest()
    zipped = raw[:2] == b'\x1f\x8b'
    folder = Path(directory) / 'market' / 'polymarket' / 'raw'
    archive_bytes(folder / (digest + ('.json.gz' if zipped else '.json')), raw)
    source = dict(source_url=url, retrieved_at=retrieved, sha256=digest)
    if body is not None:
        source['request_sha256'] = hashlib.sha256(body).hexdigest()
    meta = folder / (digest + '.provenance.json')
    if not meta.exists():
        atomic_json(meta, dict(source, bytes=len(raw), content_encoding='gzip' if zipped else None))
    try:
        if zipped:
            raw = zlib.decompressobj(31).decompress(raw, MAX_JSON + 1)
            if len(raw) > MAX_JSON:
                raise ValueError('Polymarket response exceeds size limit')
        return json.loads(raw), source
    except (ValueError, zlib.error) as exc:
        raise ValueError('Polymarket response is not JSON') from exc


def _listed(text):
    """Gamma carries a market's outcomes and tokens as JSON text."""
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _whole(value):
    return round(value) if value is not None else None


def game_markets(payload, game):
    """The event of this game and its winner, spread and total markets: ``(event, markets)``.

    The event must carry this game's slug and name its two teams home and away as
    the schedule does. Each market is reduced to the order-book token of its first
    outcome, except the winner market, whose token is the home team's. None when
    Polymarket lists no such event.
    """
    events = payload.get('events') if isinstance(payload, dict) else None
    if not isinstance(events, list):
        raise ValueError('Polymarket response is not an event list')
    event = next((e for e in events if isinstance(e, dict) and e.get('slug') == event_slug(game)), None)
    if event is None:
        return None
    sides = {t.get('ordering'): t for t in event.get('teams') or [] if isinstance(t, dict)}
    codes = {side: str(sides.get(side, {}).get('abbreviation') or '').upper() for side in ('home', 'away')}
    if codes != dict(home=game['home_tricode'], away=game['away_tricode']):
        raise ValueError('Polymarket event does not match this game')
    home, away = sides['home'].get('name'), sides['away'].get('name')
    markets = []
    for m in event.get('markets') or []:
        kind = next((k for k, v in KINDS.items() if isinstance(m, dict) and m.get('sportsMarketType') == v), None)
        outcomes, tokens = (_listed(m.get(key)) for key in ('outcomes', 'clobTokenIds')) if kind else ([], [])
        if not kind or len(outcomes) != 2 or len(tokens) != 2 or m.get('closed') or not all(re.fullmatch(r'\d{1,80}', str(t)) for t in tokens):
            continue
        line = _number(m.get('line'))
        item = dict(kind=kind, slug=m.get('slug'), token=str(tokens[0]), volume=_number(m.get('volumeNum')), liquidity=_number(m.get('liquidityNum')))
        if kind == 'game' and set(outcomes) == {home, away}:
            item['token'] = str(tokens[outcomes.index(home)])
        elif kind == 'spread' and line is not None and set(outcomes) == {home, away}:
            # The first team lays -line points: 'away by over s' is the home margin below -s.
            item.update(side='home' if outcomes[0] == home else 'away', home_margin_over=-line if outcomes[0] == home else line)
        elif kind == 'total' and line is not None and outcomes[0] == 'Over':
            item['total_over'] = line
        else:
            continue
        markets.append(item)
    return event, markets


def quote(book):
    """The best bid and ask of an order book and their mid; the mid is None when the book is empty or wide."""
    def best(side, pick):
        levels = [(p, _number(x.get('size'))) for x in (book or {}).get(side) or [] if isinstance(x, dict)
                  for p in [_number(x.get('price'))] if p is not None and 0 < p < 1]
        return pick(levels, key=lambda level: level[0]) if levels else (None, None)
    (bid, bid_size), (ask, ask_size) = best('bids', max), best('asks', min)
    priced = bid is not None and ask is not None and ask >= bid
    return dict(bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size, width=round(ask - bid, 6) if priced else None,
                mid=(bid + ask) / 2 if priced and ask - bid <= MAX_WIDTH else None)


def _closest(quotes):
    """The listed line priced nearest one half: the market's main line."""
    priced = [q for q in quotes if q['probability'] is not None]
    return min(priced, key=lambda q: (abs(q['probability'] - .5), -(q['liquidity'] or 0))) if priced else None


def extract(game, payload, books, captured_at, sources):
    """One capture: the home win probability, the implied home margin and total, and every quote behind them."""
    found = game_markets(payload, game)
    if found is None:
        raise ValueError('Polymarket has no event for this game yet')
    event, markets = found
    by_token = {b.get('asset_id'): b for b in books if isinstance(b, dict)} if isinstance(books, list) else {}
    winner = next((m for m in markets if m['kind'] == 'game'), None)
    if winner is None:
        raise ValueError('Polymarket has no winner market for this game yet')
    spreads, totals = [], []
    for m in markets:
        if m['kind'] == 'game':
            continue
        q = quote(by_token.get(m['token']))
        p = q['mid'] if m.get('side') != 'away' or q['mid'] is None else round(1 - q['mid'], 6)
        base = dict(probability=p, bid=q['bid'], ask=q['ask'], slug=m['slug'], volume=_whole(m['volume']), liquidity=_whole(m['liquidity']))
        if m['kind'] == 'spread':
            spreads.append(dict(home_margin_over=m['home_margin_over'], side=m['side'], **base))
        else:
            totals.append(dict(total_over=m['total_over'], **base))
    spreads.sort(key=lambda s: s['home_margin_over'])
    totals.sort(key=lambda t: t['total_over'])
    margin = crossing((s['home_margin_over'], s['probability']) for s in spreads)
    total = crossing((t['total_over'], t['probability']) for t in totals)
    book = by_token.get(winner['token']) or {}
    stamp = _number(book.get('timestamp'))
    moneyline = dict(quote(book), slug=winner['slug'], volume=winner['volume'], liquidity=winner['liquidity'],
                     last_trade_price=_number(book.get('last_trade_price')),
                     book_time=datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat() if stamp else None)
    main_spread, main_total = _closest(spreads), _closest(totals)
    return dict(schema_version=1, game_id=game['game_id'], start_time=game['start_time'], home_team_id=game['home_team_id'],
                away_team_id=game['away_team_id'], provider='Polymarket', captured_at=captured_at, sources=sources,
                event=dict(slug=event['slug'], id=event.get('id'), start_time=event.get('startTime'),
                           volume=_number(event.get('volume')), liquidity=_number(event.get('liquidity'))),
                home_win_probability=moneyline['mid'], moneyline=moneyline, implied_home_margin=margin, implied_total=total,
                main_spread=dict(home_line=-main_spread['home_margin_over'], home_cover_probability=main_spread['probability'],
                                 slug=main_spread['slug']) if main_spread else None,
                main_total=dict(line=main_total['total_over'], over_probability=main_total['probability'], slug=main_total['slug']) if main_total else None,
                lines=dict(spread=dict(home=dict(current_line=-margin if margin is not None else None), away=dict(current_line=margin)),
                           total=dict(over=dict(current_line=total), under=dict(current_line=total)),
                           moneyline=dict(home=dict(current_odds=None), away=dict(current_odds=None))),
                spread_quotes=[{k: v for k, v in s.items() if k != 'slug'} for s in spreads],
                total_quotes=[{k: v for k, v in t.items() if k != 'slug'} for t in totals],
                basis='live order-book bid-ask mids; the margin and total are where the listed lines cross one half, interpolated; '
                      'the main line is the listed line priced nearest one half; volume and liquidity are Gamma\'s, up to five minutes old')


def _lines_path(folder, game_id):
    return Path(folder) / 'market' / 'polymarket' / (game_id + '.jsonl')


def read_polymarket_lines(folder, game_id):
    path = _lines_path(folder, game_id)
    return _parse_lines(path.read_bytes(), path.name)[0] if path.exists() else []


def record_polymarket_line(folder, record):
    """Append the capture when its numbers changed; the file is rewritten through a rename like the Kalshi lines."""
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


def capture_polymarket_lines(folder, games, as_of, fetch=None, deadline=None):
    """Capture Polymarket's prices for the upcoming games, once per cadence slot each; failures are reported, never raised.

    Two requests a game: its event, then the order books of its winner, spread and
    total markets. ``deadline`` is a ``time.monotonic()`` instant: a game not
    finished by then is listed under ``deferred`` for the next run
    (``kalshi_lines.capture_kalshi_lines`` has the same contract). A game
    Polymarket lists no event for is ``unlisted`` until its next slot.
    """
    folder = Path(folder)
    now = parse_time(as_of)
    state_path = folder / 'market' / 'polymarket' / 'capture-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    captured = {k: v for k, v in state.get('captured', {}).items() if now.timestamp() - v < 172800}
    report = dict(captured=[], unchanged=[], unlisted=[], skipped=[], deferred=[], errors=[])
    late = lambda: deadline is not None and time.monotonic() > deadline  # noqa: E731
    started = time.monotonic()
    for game in games:
        # A game that tips while this call works through the slate is not captured: its books are cleared at the start.
        elapsed = timedelta(seconds=max(0., time.monotonic() - started))
        if not game.get('start_time') or parse_time(game['start_time']) <= now + elapsed or not (game.get('home_tricode') and game.get('away_tricode')):
            continue
        slot = _slot(game, now)
        if slot in captured:
            report['skipped'].append(game['game_id'])
            continue
        if late():
            report['deferred'].append(game['game_id'])
            continue
        try:
            payload, source = _fetch(EVENT_API.format(event_slug(game)), folder, fetch)
            found = game_markets(payload, game)
            if found is None:
                captured[slot] = now.timestamp()
                report['unlisted'].append(game['game_id'])
                continue
            if late():
                raise _Deferred()
            tokens = [m['token'] for m in found[1]]
            if not tokens:
                raise ValueError('Polymarket has no winner market for this game yet')
            body = json.dumps([dict(token_id=t) for t in tokens], separators=(',', ':')).encode()
            books, book_source = _fetch(BOOKS_API, folder, fetch, body)
            record = extract(game, payload, books, as_of, [source, book_source])
            appended = record_polymarket_line(folder, record)
            captured[slot] = now.timestamp()
            (report['captured'] if appended else report['unchanged']).append(game['game_id'])
        except _Deferred:
            report['deferred'].append(game['game_id'])
        except HTTPError as exc:
            report['errors'].append(dict(game_id=game['game_id'], error=str(exc)))
            if exc.code == 429:  # throttled: leave the other games' slots open for the next run
                report['throttled'] = True
                deadline = time.monotonic() - 1
        except (ValueError, OSError, KeyError, TypeError) as exc:
            report['errors'].append(dict(game_id=game['game_id'], error=str(exc)))
    saved = dict(version=VERSION, captured=captured)
    if saved != state:  # a call with nothing due writes nothing
        atomic_json(state_path, saved)
    return report


def closing_polymarket_line(records, start_time):
    """The last capture before tipoff, or None."""
    tip = parse_time(start_time)
    pregame = [r for r in records if parse_time(r['captured_at']) < tip]
    return pregame[-1] if pregame else None
