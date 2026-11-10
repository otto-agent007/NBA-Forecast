"""Kalshi's NBA player prop ladders, captured as a market benchmark for the player forecasts.

Kalshi lists, for each game, binary markets per player and statistic: "Stephen
Curry: 25+ points" resolves Yes when he scores more than its ``floor_strike``
(24.5). A player's markets form a ladder of thresholds (points 9.5, 14.5, ...,
rebounds and assists 1.5, 3.5, ..., threes 0.5, 1.5, ...). The bid-ask mid of
each rung is the market's probability of clearing it, and where the ladder's
mids cross one half (``kalshi_lines.crossing``) is its median line.

Settlement (each market's ``rules_secondary``, read on 2026-11-09): "If a player
is inactive, or active but never takes the court, the market settles to a fair
price. Once a player enters the game the market settles based on the player's
points recorded." A price is therefore conditional on the player entering the
game, like the model's points, minutes and interval, which are conditional on
playing; ``props_report`` scores both on players who entered the game only.

Each capture runs at the props cadence (every 15 minutes within a day of tip,
every 5 in the last 30): the four series of a game (``SERIES``) are fetched, each
market is matched to a roster player of its team (``match_players``, exact name,
then without a generational suffix, then a first-name prefix; each only when it
names one player of that team, and never two market names to one player), and a
record is appended to ``market/kalshi-props/<game_id>.jsonl`` when a quote or a
match changed. The responses behind an appended record are archived gzipped by
the digest of their bytes under ``market/kalshi-props/raw/``; the record lists
each one's URL, retrieval time, digest and size. Volume and open interest are
recorded but a change in them alone appends nothing. This records what the
market said; it is not a quote, a recommendation or a bet.
"""
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .files import archive_bytes, atomic_bytes, atomic_json
from .injury_parser import SUFFIXES, normalized, person_key
from .kalshi_lines import _dollars, _download, crossing, event_ticker, mid
from .market_lines import _parse_lines
from .validation import parse_time

VERSION = 'kalshi-props-1'
SERIES = dict(points='KXNBAPTS', rebounds='KXNBAREB', assists='KXNBAAST', threes='KXNBA3PT')
API = 'https://api.elections.kalshi.com/trade-api/v2/markets?event_ticker={}&limit=1000'
URL_PATTERN = (r'https://api\.elections\.kalshi\.com/trade-api/v2/markets\?event_ticker=KXNBA(PTS|REB|AST|3PT)-[0-9A-Z]+'
               r'&limit=1000(&cursor=[A-Za-z0-9%_.=-]+)?')
MAX_PAGES = 3
# Between requests: Kalshi asks clients for restraint. 42 requests in 6.6 s (props and
# game lines side by side) drew HTTP 429 on 2026-11-10; 24 in 8.8 s did not.
PAUSE = .25
# Within a day of tip every 15 minutes, every 5 in the last 30; the props need no
# one-minute close, and a slate's prices change in nearly every 15-minute slot.
CADENCE = ((1800, 300), (float('inf'), 900))
LADDER_FIELDS = ('threshold', 'bid', 'ask', 'mid', 'width', 'volume', 'open_interest', 'status')
PACIFIC = ZoneInfo('America/Los_Angeles')
Z80 = 1.2815515655446004  # the standard normal's 90th percentile: an 80% interval is the mean +- Z80 sd


def _root(folder):
    return Path(folder) / 'market' / 'kalshi-props'


def fetch_event(url, fetch=None):
    """One page of an event's markets: ``(markets, cursor, raw bytes, source)``; archives nothing."""
    if not re.fullmatch(URL_PATTERN, url):
        raise ValueError('not a Kalshi NBA player-prop markets URL')
    raw = (fetch or _download)(url)
    retrieved = datetime.now(timezone.utc).isoformat()
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ValueError('Kalshi response is not JSON') from exc
    if not isinstance(payload, dict) or not isinstance(payload.get('markets'), list):
        raise ValueError('Kalshi response is not a market list')
    digest = hashlib.sha256(raw).hexdigest()
    source = dict(source_url=url, retrieved_at=retrieved, sha256=digest, bytes=len(raw), archive=f'raw/{digest}.json.gz')
    return payload['markets'], payload.get('cursor') or '', raw, source


def _archive(folder, raw, source):
    """Store a response gzipped under the digest of its bytes; identical responses are stored once."""
    archive_bytes(_root(folder) / source['archive'], gzip.compress(raw, mtime=0))


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value):
    return round(value, 4) if value is not None else None


def _words(name):
    words = [w for w in re.split(r'[\s,.]+', name) if w]
    while len(words) > 2 and normalized(words[-1]) in SUFFIXES:
        words.pop()
    return words


def match_players(names, players):
    """Match market names to roster players of the same team: ``{(name, team_id): (player_id, basis) or (None, reason)}``.

    ``names`` are ``(name, team_id)`` pairs and ``players`` the roster entries of
    the game's two teams. A name matches only within its own team, by the first
    rule that names exactly one player: the exact name (case, punctuation and
    diacritics aside), the name without a generational suffix (Jr., III), or the
    same surname with one first name a prefix of the other ("Nic" for "Nicolas").
    Two market names that would match one player match neither.
    """
    by_team = {}
    for p in players:
        by_team.setdefault(p['team_id'], []).append(p)
    rules = (('exact', lambda n: normalized(n)), ('suffix', person_key))
    out = {}
    for name, team in names:
        pool = by_team.get(team, [])
        found, basis = [], None
        for basis, key in rules:
            found = [p for p in pool if key(p['name']) == key(name)]
            if found:
                break
        if not found:
            words = _words(name)
            if len(words) >= 2:
                first, last = normalized(words[0]), normalized(' '.join(words[1:]))
                def prefix(p):
                    other = _words(p['name'])
                    if len(other) < 2 or normalized(' '.join(other[1:])) != last:
                        return False
                    a, b = sorted((first, normalized(other[0])), key=len)
                    return len(a) >= 3 and b.startswith(a)
                found, basis = [p for p in pool if prefix(p)], 'first_name_prefix'
        if len(found) == 1:
            out[(name, team)] = (found[0]['player_id'], basis)
        else:
            out[(name, team)] = (None, 'no roster player of this team has this name' if not found
                                 else f'{len(found)} roster players of this team match ({basis})')
    claimed = {}
    for key, (pid, _) in out.items():
        if pid is not None:
            claimed.setdefault(pid, []).append(key)
    for pid, keys in claimed.items():
        if len(keys) > 1:
            for key in keys:
                out[key] = (None, 'two market names match one roster player')
    return out


def extract_props(game, markets, players, captured_at, sources):
    """One capture: each player's ladder per statistic, matched to the roster where it is unambiguous.

    ``markets`` maps a statistic (``SERIES``) to its event's markets; ``players``
    is the saved roster's player list.
    """
    teams = {game['home_tricode']: game['home_team_id'], game['away_tricode']: game['away_team_id']}
    grouped, unmatched, skipped = {}, [], 0
    for stat, items in markets.items():
        event = event_ticker(SERIES[stat], game)
        for m in items:
            ticker, strike = m.get('ticker'), m.get('floor_strike')
            if (not isinstance(ticker, str) or not ticker.startswith(event + '-') or m.get('strike_type') != 'greater'
                    or isinstance(strike, bool) or not isinstance(strike, (int, float))):
                skipped += 1
                continue
            key = ticker[len(event) + 1:].rsplit('-', 1)[0]
            title = m.get('title') if isinstance(m.get('title'), str) and ':' in m['title'] else m.get('yes_sub_title') or ''
            name = title.rsplit(':', 1)[0].strip()
            ident = ((m.get('custom_strike') or {}).get('basketball_player') or key)
            entry = grouped.setdefault(ident, dict(kalshi_player=ident, name=name, market_key=key, team=key[:3], stats={}))
            bid, ask = _dollars(m.get('yes_bid_dollars')), _dollars(m.get('yes_ask_dollars'))
            entry['stats'].setdefault(stat, []).append([
                strike, bid, ask, _round(mid(m)) if m.get('status') == 'active' else None,
                round(ask - bid, 4) if bid is not None and ask is not None else None,
                _number(m.get('volume_fp')), _number(m.get('open_interest_fp')), m.get('status')])
    names = [(e['name'], teams[e['team']]) for e in grouped.values() if e['team'] in teams and e['name']]
    matched = match_players(names, [p for p in players if p.get('team_id') in teams.values()])
    out = []
    for entry in sorted(grouped.values(), key=lambda e: (e['team'], e['name'])):
        team_id = teams.get(entry['team'])
        pid, basis = matched.get((entry['name'], team_id), (None, 'market team is not one of this game\'s teams'
                                                                  if team_id is None else 'no player name in the market'))
        stats = {}
        for stat, ladder in sorted(entry['stats'].items()):
            ladder.sort(key=lambda r: r[0])
            stats[stat] = dict(ladder=ladder, implied_median=crossing((r[0], r[3]) for r in ladder))
        out.append(dict(kalshi_player=entry['kalshi_player'], name=entry['name'], market_key=entry['market_key'],
                        team=entry['team'], team_id=team_id, player_id=pid, match=basis if pid else None,
                        stats=stats))
        if pid is None:
            unmatched.append(dict(name=entry['name'], team=entry['team'], reason=basis))
    return dict(schema_version=1, version=VERSION, game_id=game['game_id'], start_time=game['start_time'],
                home_team_id=game['home_team_id'], away_team_id=game['away_team_id'], provider='Kalshi',
                captured_at=captured_at, sources=sources, ladder_fields=list(LADDER_FIELDS), players=out,
                unmatched=unmatched, skipped_markets=skipped,
                basis='Each rung is a binary market "more than threshold"; mid is the bid-ask mean of an active market '
                      'with a spread of at most kalshi_lines.MAX_WIDTH, and implied_median is where the mids cross one half. '
                      'A player who does not enter the game settles at a fair price.')


def _lines_path(folder, game_id):
    return _root(folder) / (game_id + '.jsonl')


def read_props(folder, game_id):
    path = _lines_path(folder, game_id)
    return _parse_lines(path.read_bytes(), path.name)[0] if path.exists() else []


def _signature(record):
    """What a new record must differ in: each player's identity and match, and each rung's quote and status."""
    return sorted([p['kalshi_player'], p['player_id'], stat, [[r[0], r[1], r[2], r[7]] for r in item['ladder']]]
                  for p in record['players'] for stat, item in p['stats'].items())


def record_props(folder, record):
    """Append the capture when a quote or a match changed; the file is rewritten through a rename like the line files."""
    path = _lines_path(folder, record['game_id'])
    raw = path.read_bytes() if path.exists() else b''
    existing, clean, partial = _parse_lines(raw, path.name) if raw else ([], 0, False)
    if existing and _signature(existing[-1]) == _signature(record):
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
    """The capture budget ran out between a game's requests; nothing was recorded."""


def capture_kalshi_props(folder, games, as_of, fetch=None, deadline=None):
    """Capture Kalshi's player prop ladders for the upcoming games, once per cadence slot each; failures are reported, never raised.

    ``deadline`` is a ``time.monotonic()`` instant: a game not finished by then is
    listed under ``deferred`` and its slot stays open for the next run
    (``kalshi_lines.capture_kalshi_lines`` has the same contract). A game whose
    events list no markets yet (they open on game day) is ``unlisted``.
    """
    folder = Path(folder)
    now = parse_time(as_of)
    state_path = _root(folder) / 'capture-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    captured = {k: v for k, v in state.get('captured', {}).items() if now.timestamp() - v < 172800}
    checked = {k: v for k, v in state.get('checked', {}).items() if now.timestamp() - parse_time(v).timestamp() < 172800}
    report = dict(captured=[], unchanged=[], unlisted=[], skipped=[], deferred=[], errors=[], unmatched=[])
    late = lambda: deadline is not None and time.monotonic() > deadline  # noqa: E731
    started = time.monotonic()
    players = None
    requests = 0
    for game in games:
        # A game that tips while this call works through the slate is not captured: its record would pass for pregame.
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
            if players is None:
                roster = folder / 'roster.json'
                players = json.loads(roster.read_text()).get('players', []) if roster.exists() else []
            markets, pages = {}, []
            for stat, series in SERIES.items():
                url, items = API.format(event_ticker(series, game)), []
                for _ in range(MAX_PAGES):
                    if (pages or markets) and late():
                        raise _Deferred()
                    if requests and fetch is None:
                        time.sleep(PAUSE)
                    requests += 1
                    page, cursor, raw, source = fetch_event(url, fetch)
                    items.extend(page)
                    pages.append((raw, source))
                    if not cursor:
                        break
                    url = API.format(event_ticker(series, game)) + '&cursor=' + quote(cursor, safe='')
                else:
                    raise ValueError(f'{series}: more than {MAX_PAGES} pages of markets')
                markets[stat] = items
            checked[game['game_id']] = as_of
            if not any(markets.values()):
                captured[slot] = now.timestamp()
                report['unlisted'].append(game['game_id'])
                continue
            if not players:
                report['errors'].append(dict(game_id=game['game_id'], error='no saved roster: markets recorded unmatched'))
            record = extract_props(game, markets, players, as_of, [source for _, source in pages])
            appended = record_props(folder, record)
            if appended:
                for raw, source in pages:
                    _archive(folder, raw, source)
            captured[slot] = now.timestamp()
            (report['captured'] if appended else report['unchanged']).append(game['game_id'])
            report['unmatched'].extend(dict(game_id=game['game_id'], **u) for u in record['unmatched'])
        except _Deferred:
            report['deferred'].append(game['game_id'])
        except HTTPError as exc:
            report['errors'].append(dict(game_id=game['game_id'], error=str(exc)))
            if exc.code == 429:  # throttled: leave the other games' slots open for the next run
                report['throttled'] = True
                deadline = time.monotonic() - 1
        except (ValueError, OSError, KeyError, TypeError) as exc:
            report['errors'].append(dict(game_id=game['game_id'], error=str(exc)))
    saved = dict(version=VERSION, captured=captured, checked=checked)
    if saved != state:  # a call with nothing due writes nothing
        atomic_json(state_path, saved)
    return report


def closing_props(records, start_time):
    """The last capture before tipoff, or None."""
    tip = parse_time(start_time)
    pregame = [r for r in records if parse_time(r['captured_at']) < tip]
    return pregame[-1] if pregame else None


def _cdf(z):
    return .5 * math.erfc(-z / math.sqrt(2.))


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def model_probability(prediction, threshold, method='interval'):
    """The forecast's probability that the player scores more than ``threshold``, given that he plays.

    ``interval``: a two-piece normal centred on ``points_mean`` whose halves put
    the published 80% interval's ends at the 10th and 90th percentiles (the lower
    half takes the upper half's scale when the interval is cut at zero). ``sd``: a
    normal with ``points_sd``. None when the forecast has no numeric points.
    """
    mean = prediction.get('points_mean')
    if prediction.get('status') != 'projected' or not _finite(mean):
        return None
    if method == 'sd':
        sd = prediction.get('points_sd')
        return 1 - _cdf((threshold - mean) / sd) if _finite(sd) and sd > 0 else None
    low, high = prediction.get('points_lower80'), prediction.get('points_upper80')
    if not (_finite(low) and _finite(high)) or not high > mean:
        return None
    upper = (high - mean) / Z80
    lower = (mean - low) / Z80 if low > 0 and mean > low else upper
    return 1 - _cdf((threshold - mean) / (upper if threshold >= mean else lower))


def paired(rows, a, b):
    """Mean of ``a``, of ``b`` and of ``a - b`` over rows carrying both, with the difference's standard error clustered by date.

    The error is one standard error, cluster-robust over Pacific game dates with
    the G/(G-1) factor (``diagnostic_stats``); None with fewer than two dates.
    """
    use = [r for r in rows if r.get(a) is not None and r.get(b) is not None]
    if not use:
        return dict(n=0)
    diffs = [r[a] - r[b] for r in use]
    mean = sum(diffs) / len(diffs)
    sums = {}
    for r, d in zip(use, diffs):
        sums[r['date']] = sums.get(r['date'], 0.) + d - mean
    groups = len(sums)
    se = math.sqrt(groups / (groups - 1) * sum(s * s for s in sums.values())) / len(use) if groups > 1 else None
    return {'n': len(use), 'dates': groups, a: sum(r[a] for r in use) / len(use), b: sum(r[b] for r in use) / len(use),
            'difference': mean, 'difference_se': se}


def _log_loss(p, outcome):
    p = min(max(p, 1e-3), 1 - 1e-3)
    return -math.log(p if outcome else 1 - p)


def props_report(folder, schedule, as_of=None, *, selected=None):
    """Compare each finished game's last pre-tip prop capture with the final pregame forecast and the box score.

    Points only: (1) for every rung of a matched player the model projected, the
    market mid against the model's probability of clearing the threshold, scored
    by Brier and log loss on whether he did; (2) the ladder's implied median line
    against the model's points mean, by absolute error against his points. Both
    count players who entered the game only (the markets of a player who did not
    settle at a fair price); excluded rungs and players are counted by reason.
    """
    folder = Path(folder)
    as_of = as_of or datetime.now(timezone.utc).isoformat()
    cutoff = parse_time(as_of)
    games = {}
    closes = {}
    errors = []
    for game in schedule.get('games', []):
        gid = game['game_id']
        if not game.get('start_time') or game.get('status') in ('postponed', 'cancelled', 'tbd') or parse_time(game['start_time']) > cutoff:
            continue
        if not (folder / 'finals' / (gid + '.json')).exists() or not _lines_path(folder, gid).exists():
            continue
        try:
            close = closing_props(read_props(folder, gid), game['start_time'])
        except ValueError as exc:
            errors.append(dict(game_id=gid, error=str(exc)))
            continue
        if close is not None:
            games[gid], closes[gid] = game, close
    if games and selected is None:
        from .daily_review import _selected
        selected = _selected(folder, games, as_of, schedule.get('season'))
    selected = selected or {}
    rungs, medians, games_out = [], [], []
    excluded = dict(unmatched_players=0, players_without_forecast=0, players_not_entered=0,
                    rungs_without_mid=0, medians_not_bracketed=0, games_without_forecast=0)
    for gid, game in sorted(games.items(), key=lambda item: item[1]['start_time']):
        choice = selected.get((gid, 'final')) or selected.get((gid, 'preliminary'))
        if not choice:
            excluded['games_without_forecast'] += 1
            continue
        close = closes[gid]
        finals = {r['player_id']: r for r in json.loads((folder / 'finals' / (gid + '.json')).read_text())}
        predictions = {p['player_id']: p for p in choice.get('predictions', [])}
        date = parse_time(game['start_time']).astimezone(PACIFIC).date().isoformat()
        counted = 0
        for player in close['players']:
            ladder = (player['stats'].get('points') or {})
            if not ladder:
                continue
            if player['player_id'] is None:
                excluded['unmatched_players'] += 1
                continue
            prediction = predictions.get(player['player_id'])
            if not prediction or model_probability(prediction, 0.) is None:
                excluded['players_without_forecast'] += 1
                continue
            actual = finals.get(player['player_id'])
            minutes = (actual or {}).get('minutes')
            if not actual or not _finite(minutes) or minutes <= 0:
                excluded['players_not_entered'] += 1
                continue
            counted += 1
            points = actual['pts']
            base = dict(game_id=gid, date=date, player_id=player['player_id'], name=player['name'])
            for rung in ladder['ladder']:
                threshold, market = rung[0], rung[3]
                if market is None:
                    excluded['rungs_without_mid'] += 1
                    continue
                outcome = points > threshold
                row = dict(base, threshold=threshold, market=market, model=model_probability(prediction, threshold),
                           model_sd=model_probability(prediction, threshold, 'sd'), outcome=outcome)
                for name in ('market', 'model', 'model_sd'):
                    if row[name] is not None:
                        row[name + '_brier'] = (row[name] - outcome) ** 2
                        row[name + '_log_loss'] = _log_loss(row[name], outcome)
                rungs.append(row)
            if ladder.get('implied_median') is None:
                excluded['medians_not_bracketed'] += 1
            else:
                medians.append(dict(base, market_line=ladder['implied_median'], model_points=prediction['points_mean'],
                                    actual_points=points, market_abs_error=abs(ladder['implied_median'] - points),
                                    model_abs_error=abs(prediction['points_mean'] - points)))
        games_out.append(dict(game_id=gid, date=date, start_time=game['start_time'], run_id=choice.get('run_id'),
                              stage='final' if (gid, 'final') in selected else 'preliminary',
                              close_captured_at=close['captured_at'], players_scored=counted))
    summary = dict(games=len(games_out), rungs=len(rungs), median_players=len(medians), excluded=excluded)
    if not games_out:
        summary.update(status='no_data', reason='No finished game has a pre-tip prop capture, a saved pregame forecast '
                                                'and a final box score yet.')
    else:
        summary.update(status='scored',
                       brier=dict(interval=paired(rungs, 'model_brier', 'market_brier'), sd=paired(rungs, 'model_sd_brier', 'market_brier')),
                       log_loss=dict(interval=paired(rungs, 'model_log_loss', 'market_log_loss'),
                                     sd=paired(rungs, 'model_sd_log_loss', 'market_log_loss')),
                       median_abs_error=paired(medians, 'model_abs_error', 'market_abs_error'))
    return dict(version=VERSION, generated_at=as_of, summary=summary, games=games_out, rungs=rungs, medians=medians, errors=errors,
                conventions='difference = model minus market (negative favours the model); difference_se is one standard error '
                            'clustered by Pacific game date. Brier and log loss (probabilities clipped to [0.001, 0.999]) score each rung '
                            'on whether the player scored more than its threshold. The model probability is conditional on playing, as '
                            'the market is: "interval" puts the published 80% interval at the 10th and 90th percentiles, "sd" uses '
                            'points_sd. The market line is a median and the model points a mean, which favours the market on absolute error '
                            'when scoring is skewed.',
                policy='Recorded public Kalshi prices, last capture before tipoff, against the final pregame forecast. Not a quote, '
                       'a recommendation or a bet; a small sample is not an edge.')
