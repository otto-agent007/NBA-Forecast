"""Archive sportsbook game lines from ESPN game summaries and compare saved forecasts with them.

ESPN's game summary carries each provider's spread, total and moneyline with
their opening and current values. Lines are captured at the forecast cadence
for a game (hourly while it waits for the morning checkpoint, every 15 minutes
within 24 hours of tipoff, every 5 minutes in the last 30), so the last capture
before tipoff is the closing line. Every capture keeps its source URL, hash and
retrieval time. This records what the market said; it is not a quote, a
recommendation or a bet.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time

from .availability import summary_sides
from .files import atomic_bytes, atomic_json, canonical
from .validation import parse_time

VERSION = 'market-lines-1'
# Spread and total edges, in points, at which a model lean is counted in the report.
EDGE_THRESHOLDS = (0.0, 1.0, 2.0, 3.0)


def _number(text):
    """'+124' -> 124, '-2.5' -> -2.5, 'o220.5' -> 220.5, 'EVEN' -> 100; None when unreadable."""
    if isinstance(text, bool) or text is None:
        return None
    if isinstance(text, (int, float)):
        return text
    cleaned = str(text).strip().upper()
    if cleaned in ('EVEN', 'EV', 'PK', 'PICK'):
        return 100 if cleaned in ('EVEN', 'EV') else 0.0
    cleaned = re.sub(r'^[OU]', '', cleaned)
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return int(value) if value.is_integer() and abs(value) >= 100 else value


def _side(entry, key):
    """Current and opening line/odds for one side of a pickcenter market."""
    item = (entry or {}).get(key) or {}
    current, opening = item.get('close') or {}, item.get('open') or {}
    return dict(current_line=_number(current.get('line')), current_odds=_number(current.get('odds')),
                open_line=_number(opening.get('line')), open_odds=_number(opening.get('odds')))


def extract_game_lines(payload, game, season, evidence):
    """One record per provider from an identity-checked ESPN game summary."""
    event_id, sides = summary_sides(payload, game, season, evidence)
    records = []
    for entry in payload.get('pickcenter') or []:
        if not isinstance(entry, dict):
            continue
        provider = entry.get('provider') or {}
        home_odds, away_odds = entry.get('homeTeamOdds') or {}, entry.get('awayTeamOdds') or {}
        spread = dict(home=_side(entry.get('pointSpread'), 'home'), away=_side(entry.get('pointSpread'), 'away'))
        if spread['home']['current_line'] is None and entry.get('spread') is not None and home_odds.get('favorite') is not None:
            # Older payloads carry only the favorite's spread.
            value = _number(entry['spread'])
            if value is not None:
                home_line = value if home_odds.get('favorite') else -value
                spread['home']['current_line'], spread['away']['current_line'] = home_line, -home_line
                spread['home']['current_odds'], spread['away']['current_odds'] = _number(home_odds.get('spreadOdds')), _number(away_odds.get('spreadOdds'))
        total = dict(over=_side(entry.get('total'), 'over'), under=_side(entry.get('total'), 'under'))
        if total['over']['current_line'] is None and entry.get('overUnder') is not None:
            total['over']['current_line'] = total['under']['current_line'] = _number(entry['overUnder'])
            total['over']['current_odds'], total['under']['current_odds'] = _number(entry.get('overOdds')), _number(entry.get('underOdds'))
        moneyline = dict(home=_side(entry.get('moneyline'), 'home'), away=_side(entry.get('moneyline'), 'away'))
        for side, odds in (('home', home_odds), ('away', away_odds)):
            if moneyline[side]['current_odds'] is None and odds.get('moneyLine') is not None:
                moneyline[side]['current_odds'] = _number(odds['moneyLine'])
        records.append(dict(schema_version=1, game_id=game['game_id'], start_time=game['start_time'], event_id=event_id,
                            home_team_id=game['home_team_id'], away_team_id=game['away_team_id'],
                            provider_id=str(provider.get('id')) if provider.get('id') is not None else None,
                            provider=provider.get('name'), details=entry.get('details'),
                            lines=dict(spread=spread, total=total, moneyline=moneyline),
                            source_url=evidence['source_url'], retrieved_at=evidence['retrieved_at'], sha256=evidence['sha256']))
    return records


def _lines_path(folder, game_id):
    return Path(folder) / 'market' / 'game-lines' / (game_id + '.jsonl')


def _parse_lines(raw, name):
    """(records, clean byte length, partial): a cut-off final line is dropped, damage before it raises."""
    records, clean, offset = [], 0, 0
    for line in raw.splitlines(keepends=True):
        offset += len(line)
        text = line.strip()
        if not text:
            clean = offset
            continue
        try:
            records.append(json.loads(text))
        except ValueError:
            if raw[offset:].strip():
                raise ValueError(f'damaged market line archive: {name}') from None
            return records, clean, True  # a write cut short at the end of the file
        clean = offset
    return records, clean, False


def read_game_lines(folder, game_id):
    path = _lines_path(folder, game_id)
    if not path.exists():
        return []
    return _parse_lines(path.read_bytes(), path.name)[0]


def record_game_lines(folder, records, captured_at):
    """Append each provider's record when its lines changed; return what was appended.

    The file is rewritten through a rename, so a crash mid-write cannot leave a
    partial line, and a partial line an earlier crash left is dropped first.
    """
    appended = []
    by_game = {}
    for record in records:
        by_game.setdefault(record['game_id'], []).append(record)
    for game_id, items in by_game.items():
        path = _lines_path(folder, game_id)
        raw = path.read_bytes() if path.exists() else b''
        existing, clean, partial = _parse_lines(raw, path.name) if raw else ([], 0, False)
        latest = {r['provider_id']: r for r in existing}
        new = []
        for record in items:
            previous = latest.get(record['provider_id'])
            if previous and previous['lines'] == record['lines']:
                continue
            entry = dict(record, captured_at=captured_at, capture_index=len(existing) + len(new))
            new.append(entry)
            latest[record['provider_id']] = entry
        if new or partial:
            prefix = raw[:clean]
            if prefix and not prefix.endswith(b'\n'):
                prefix += b'\n'
            atomic_bytes(path, prefix + b''.join(json.dumps(e, sort_keys=True).encode() + b'\n' for e in new))
        appended.extend(new)
    return appended


def _slot(game, now):
    """Capture cadence: hourly while waiting, 15 minutes within a day, 5 minutes in the last 30."""
    remaining = (parse_time(game['start_time']) - now).total_seconds()
    seconds = 300 if remaining <= 1800 else 900 if remaining <= 86400 else 3600
    return f"{game['game_id']}:{int(now.timestamp()) // seconds}:{seconds}"


def saved_capture_evidence(folder, games):
    """The ESPN injury-list digests the last captures saved for these games; reads no network."""
    state_path = Path(folder) / 'market' / 'capture-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    wanted = {g['game_id'] for g in games}
    return {k: v for k, v in state.get('evidence', {}).items() if k in wanted}


def capture_game_lines(folder, games, season, as_of, deadline=None):
    """Capture lines for the given upcoming games, once per cadence slot each; failures are reported, never raised.

    ``deadline`` is a ``time.monotonic()`` instant: games not started by then are
    listed under ``deferred`` and wait for the next tick, so a slow network (each
    request waiting out its timeout) cannot hold the tick past its budget.
    """
    from .reference import BASE, fetch_reference, scoreboard_event
    folder = Path(folder)
    now = parse_time(as_of)
    state_path = folder / 'market' / 'capture-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    captured = {k: v for k, v in state.get('captured', {}).items() if now.timestamp() - v < 172800}
    evidence = {k: v for k, v in state.get('evidence', {}).items() if k in {g['game_id'] for g in games}}
    report = dict(captured=[], unchanged=[], skipped=[], deferred=[], errors=[], evidence=evidence)
    for game in games:
        if not game.get('start_time') or parse_time(game['start_time']) <= now:
            continue
        slot = _slot(game, now)
        if slot in captured:
            report['skipped'].append(game['game_id'])
            continue
        if deadline is not None and time.monotonic() > deadline:
            report['deferred'].append(game['game_id'])
            continue
        try:
            event, _ = scoreboard_event(game, folder / 'references')
            if event is None:
                raise ValueError('no unique ESPN event for this matchup')
            payload, meta = fetch_reference(BASE + 'summary?event=' + str(event['provider_event_id']), folder / 'references')
            # The summary's injury lists are forecast evidence; their digest tells a tick whether they changed.
            evidence[game['game_id']] = hashlib.sha256(canonical(payload.get('injuries'))).hexdigest()
            records = extract_game_lines(payload, game, season, meta)
            appended = record_game_lines(folder, records, as_of)
            captured[slot] = now.timestamp()
            (report['captured'] if appended else report['unchanged']).append(
                dict(game_id=game['game_id'], providers=sorted({r['provider'] for r in records if r['provider']}),
                     appended=len(appended)))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            report['errors'].append(dict(game_id=game['game_id'], error=str(exc)))
    atomic_json(state_path, dict(version=VERSION, captured=captured, evidence=evidence))
    atomic_json(folder / 'market' / 'index.json', build_index(folder))
    return report


def closing_lines(records, start_time):
    """The last capture per provider before tipoff.

    A provider's recovered close (``recover_evidence``: ESPN's own record of
    the close, fetched after the game) stands in for its pregame captures.
    """
    tip = parse_time(start_time)
    recovered = {r['provider_id']: r for r in records if r.get('recovered_close')}
    names = {r['provider'] for r in recovered.values()}
    latest = dict(recovered)
    for r in records:
        if r.get('recovered_close') or r['provider_id'] in recovered or r['provider'] in names:
            continue
        if parse_time(r['retrieved_at']) < tip:
            latest[r['provider_id']] = r
    return latest


def closing_margins(folder, records, as_of):
    """Each already-tipped game's closing home margin from its saved captures, for the team rating.

    The close is each provider's last pregame capture (``closing_lines``), averaged
    across providers and negated to a home margin. A game not yet tipped by
    ``as_of``, without a capture, or with a damaged file is left out: a missing
    line never blocks a forecast.
    """
    cutoff = parse_time(as_of)
    out = {}
    for record in records:
        start = record.get('game_time')
        if not start or parse_time(start) >= cutoff:
            continue
        try:
            lines = closing_lines(read_game_lines(folder, record['game_id']), start)
        except ValueError:
            continue
        spreads = [((line.get('lines') or {}).get('spread') or {}).get('home', {}).get('current_line') for line in lines.values()]
        spreads = [s for s in spreads if isinstance(s, (int, float)) and math.isfinite(s)]
        if spreads:
            out[record['game_id']] = -sum(spreads) / len(spreads)
    return out


def build_index(folder):
    """Latest and first capture per game and provider, for the dashboard."""
    root = Path(folder) / 'market' / 'game-lines'
    index = {}
    damaged = []
    for path in sorted(root.glob('*.jsonl')) if root.exists() else []:
        try:
            records = read_game_lines(folder, path.stem)
        except ValueError:
            damaged.append(path.stem)  # one damaged file never hides the other games
            continue
        if not records:
            continue
        providers = {}
        for r in records:
            item = providers.setdefault(r['provider_id'], dict(provider=r['provider'], first=r))
            item['latest'] = r
        index[path.stem] = dict(start_time=records[-1]['start_time'], captures=len(records),
                                providers={pid: dict(provider=v['provider'], first_captured_at=v['first']['captured_at'],
                                                     latest_captured_at=v['latest']['captured_at'], latest=v['latest']['lines'],
                                                     first=v['first']['lines']) for pid, v in providers.items()})
    from .kalshi_lines import read_kalshi_lines  # imported here: kalshi_lines uses this module's line parser
    kalshi_root = Path(folder) / 'market' / 'kalshi'
    for path in sorted(kalshi_root.glob('*.jsonl')) if kalshi_root.exists() else []:
        try:
            records = read_kalshi_lines(folder, path.stem)
        except ValueError:
            damaged.append(path.stem)
            continue
        if not records:
            continue
        entry = index.setdefault(path.stem, dict(start_time=records[-1]['start_time'], captures=0, providers={}))
        entry['providers']['kalshi'] = dict(provider='Kalshi', first_captured_at=records[0]['captured_at'],
                                            latest_captured_at=records[-1]['captured_at'], latest=records[-1]['lines'],
                                            first=records[0]['lines'], home_win_probability=records[-1]['home_win_probability'],
                                            captures=len(records))
    return dict(version=VERSION, generated_at=datetime.now(timezone.utc).isoformat(), games=index, damaged=damaged)


def _implied(odds):
    """Win probability implied by American odds, before removing the vig."""
    if odds is None:
        return None
    return 100 / (odds + 100) if odds > 0 else -odds / (-odds + 100)


def _record(picks):
    wins = sum(p == 'win' for p in picks)
    losses = sum(p == 'loss' for p in picks)
    pushes = sum(p == 'push' for p in picks)
    return dict(picks=len(picks), wins=wins, losses=losses, pushes=pushes,
                win_rate=wins / (wins + losses) if wins + losses else None)


def market_report(folder, schedule, as_of=None, *, selected=None):
    """Compare each game's latest pregame forecast with the closing line and the final score.

    For every finished game with a saved forecast, a closing line and a final
    box score: the model's margin and total against the spread and the total,
    the record of model leans at several edge thresholds, and the head-to-head
    error of model versus market against the actual result. The model margin is
    the published one (``model_margin_basis``), with the player-sum difference
    beside it. A lean is 'home' when the model margin exceeds the market margin
    by the threshold.
    """
    from .daily_review import _selected
    from .kalshi_lines import closing_kalshi_line, read_kalshi_lines
    folder = Path(folder)
    as_of = as_of or datetime.now(timezone.utc).isoformat()
    games = {g['game_id']: g for g in schedule.get('games', []) if g.get('start_time') and g.get('status') not in ('postponed', 'cancelled', 'tbd')}
    chosen = selected if selected is not None else _selected(folder, games, as_of, schedule.get('season'))
    rows = []
    errors = []
    for gid, game in sorted(games.items(), key=lambda item: item[1]['start_time']):
        selection = chosen.get((gid, 'final')) or chosen.get((gid, 'preliminary'))
        try:
            lines = closing_lines(read_game_lines(folder, gid), game['start_time'])
        except ValueError as exc:
            errors.append(dict(game_id=gid, error=str(exc)))
            continue
        final_path = folder / 'finals' / (gid + '.json')
        if not selection or not lines or not final_path.exists():
            continue
        finals = json.loads(final_path.read_text())
        home_pts = sum(r['pts'] for r in finals if r['team_id'] == game['home_team_id'])
        away_pts = sum(r['pts'] for r in finals if r['team_id'] == game['away_team_id'])
        totals = selection.get('totals') or {}
        teams = {t.get('side') or ('home' if t['team_id'] == game['home_team_id'] else 'away'): t for t in totals.get('teams', [])}
        if not teams.get('home') or not teams.get('away') or teams['home'].get('points_mean') is None or teams['away'].get('points_mean') is None:
            continue
        model_total = teams['home']['points_mean'] + teams['away']['points_mean']
        margin_forecast = selection.get('margin_forecast') or {}
        # Reconciled team totals already differ by the margin (scoreline.py); the player
        # model's own difference is the one the margin forecast saved.
        saved = margin_forecast.get('player_sum_margin')
        player_sum_margin = (saved if isinstance(saved, (int, float)) and math.isfinite(saved)
                             else teams['home']['points_mean'] - teams['away']['points_mean'])
        # The published margin is the margin forecast's location (the player sum blended with
        # the team rating); a run without an estimated margin forecast published the player sum.
        published = margin_forecast.get('mean_margin') if margin_forecast.get('status') == 'estimated' else None
        basis = 'margin_forecast' if isinstance(published, (int, float)) and math.isfinite(published) else 'player_sum'
        model_margin = published if basis == 'margin_forecast' else player_sum_margin
        model_home_win = margin_forecast.get('home_win_probability')
        if model_home_win is None and margin_forecast.get('status') == 'estimated':
            from .margins import probability_above
            try:
                value = probability_above(margin_forecast, game['home_team_id'], 0)
                model_home_win = value['yes_probability'] if value.get('status') == 'estimated' else None
            except (ValueError, KeyError, TypeError):
                model_home_win = None
        actual_margin, actual_total = home_pts - away_pts, home_pts + away_pts
        try:
            kalshi = closing_kalshi_line(read_kalshi_lines(folder, gid), game['start_time'])
        except ValueError as exc:
            errors.append(dict(game_id=gid, error=str(exc)))
            kalshi = None
        for pid, line in sorted(lines.items()):
            spread = line['lines']['spread']['home']['current_line']
            total = line['lines']['total']['over']['current_line']
            home_ml, away_ml = line['lines']['moneyline']['home']['current_odds'], line['lines']['moneyline']['away']['current_odds']
            market_margin = -spread if spread is not None else None
            row = dict(game_id=gid, start_time=game['start_time'], provider=line['provider'], provider_id=pid,
                       season=schedule.get('season'), home_team_id=game['home_team_id'], away_team_id=game['away_team_id'],
                       run_id=selection['run_id'], stage='final' if (gid, 'final') in chosen else 'preliminary',
                       model_margin=model_margin, model_margin_basis=basis,
                       player_sum_margin=player_sum_margin, model_total=model_total, model_home_win=model_home_win,
                       spread_home=spread, total_line=total, home_moneyline=home_ml, away_moneyline=away_ml,
                       actual_margin=actual_margin, actual_total=actual_total,
                       market_margin=market_margin, line_captured_at=line['captured_at'],
                       close_recovered=bool(line.get('recovered_close')))
            if market_margin is not None:
                row['spread_edge'] = model_margin - market_margin
                cover = actual_margin + spread
                row['home_covered'] = None if cover == 0 else cover > 0
                row['margin_abs_error'] = dict(model=abs(model_margin - actual_margin), market=abs(market_margin - actual_margin))
            if total is not None:
                row['total_edge'] = model_total - total
                row['went_over'] = None if actual_total == total else actual_total > total
                row['total_abs_error'] = dict(model=abs(model_total - actual_total), market=abs(total - actual_total))
            implied_home = _implied(home_ml)
            implied_away = _implied(away_ml)
            if implied_home is not None and implied_away is not None and implied_home + implied_away > 0:
                row['market_home_win'] = implied_home / (implied_home + implied_away)
            if kalshi and kalshi.get('implied_home_margin') is not None:
                # Kalshi's last pre-tip price, a second close beside the sportsbook's.
                row.update(kalshi_margin=kalshi['implied_home_margin'], kalshi_home_win=kalshi.get('home_win_probability'),
                           kalshi_total=kalshi.get('implied_total'), kalshi_captured_at=kalshi['captured_at'],
                           kalshi_abs_error=abs(kalshi['implied_home_margin'] - actual_margin))
            rows.append(row)
    from .close_teacher import live_diagnostics
    summary = dict(games=len({r['game_id'] for r in rows}), rows=len(rows),
                   close_teacher=live_diagnostics(rows, schedule.get('season')))
    spread_rows = [r for r in rows if 'spread_edge' in r]
    total_rows = [r for r in rows if 'total_edge' in r]
    if spread_rows:
        summary['margin_abs_error'] = dict(model=sum(r['margin_abs_error']['model'] for r in spread_rows) / len(spread_rows),
                                           market=sum(r['margin_abs_error']['market'] for r in spread_rows) / len(spread_rows), n=len(spread_rows))
        summary['spread_leans'] = {}
        for threshold in EDGE_THRESHOLDS:
            picks = []
            for r in spread_rows:
                if abs(r['spread_edge']) < threshold or (threshold == 0 and r['spread_edge'] == 0):
                    continue
                if r['home_covered'] is None:
                    picks.append('push')
                else:
                    picks.append('win' if (r['spread_edge'] > 0) == r['home_covered'] else 'loss')
            summary['spread_leans'][f'edge>={threshold:g}'] = _record(picks)
    if total_rows:
        summary['total_abs_error'] = dict(model=sum(r['total_abs_error']['model'] for r in total_rows) / len(total_rows),
                                          market=sum(r['total_abs_error']['market'] for r in total_rows) / len(total_rows), n=len(total_rows))
        summary['total_leans'] = {}
        for threshold in EDGE_THRESHOLDS:
            picks = []
            for r in total_rows:
                if abs(r['total_edge']) < threshold or (threshold == 0 and r['total_edge'] == 0):
                    continue
                if r['went_over'] is None:
                    picks.append('push')
                else:
                    picks.append('win' if (r['total_edge'] > 0) == r['went_over'] else 'loss')
            summary['total_leans'][f'edge>={threshold:g}'] = _record(picks)
    kalshi_rows = [r for r in rows if 'kalshi_abs_error' in r]
    if kalshi_rows:
        summary.setdefault('margin_abs_error', {})
        summary['margin_abs_error'].update(kalshi=sum(r['kalshi_abs_error'] for r in kalshi_rows) / len(kalshi_rows), kalshi_n=len(kalshi_rows))
        kalshi_wins = [r for r in kalshi_rows if r.get('kalshi_home_win') is not None and r['actual_margin'] != 0]
        if kalshi_wins:
            summary['kalshi_winner'] = dict(n=len(kalshi_wins), brier=sum((r['kalshi_home_win'] - (r['actual_margin'] > 0)) ** 2 for r in kalshi_wins) / len(kalshi_wins))
    win_rows = [r for r in rows if r.get('market_home_win') is not None and r['actual_margin'] != 0]
    if win_rows:
        summary['winner'] = dict(n=len(win_rows),
                                 market_brier=sum((r['market_home_win'] - (r['actual_margin'] > 0)) ** 2 for r in win_rows) / len(win_rows),
                                 model_brier=(sum((r['model_home_win'] - (r['actual_margin'] > 0)) ** 2 for r in win_rows) / len(win_rows)
                                              if all(r.get('model_home_win') is not None for r in win_rows) else None),
                                 model_pick_accuracy=sum((r['model_margin'] > 0) == (r['actual_margin'] > 0) for r in win_rows) / len(win_rows),
                                 market_pick_accuracy=sum((r['market_home_win'] > .5) == (r['actual_margin'] > 0) for r in win_rows) / len(win_rows))
    return dict(version=VERSION, generated_at=as_of, summary=summary, games=rows, errors=errors,
                policy='Closing line is the last capture before tipoff, or the provider\'s recorded close recovered after an outage (close_recovered). Leans are descriptive; a win rate on a small sample is not an edge. '
                       'Absolute errors compare the model and the market against the same final scores.')
