"""Descriptive close-as-teacher diagnostics from the November 6, 2026 study.

No coefficients in this module feed forecasts, picks, model selection or gates.
Run ``python -m nba_forecast.close_teacher --help`` to refresh a saved reference
without replaying or modifying any forecast.
"""
import argparse
from collections import defaultdict
from datetime import datetime
from itertools import groupby
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean, pstdev

from .diagnostic_stats import ols
from .schedule import atomic_json

VERSION = 'close-teacher-1'
STUDY = 'docs/beating-the-close-2026-11-06.md'
REFERENCE = 'backtest-2026-11-06-rating-weight'
SEASONS = {'2023-24': ('espn-season-2023-24', 'espn-season-2023-24'),
           '2024-25': ('espn-season-2024-25', 'espn-season-2024-25'),
           '2025-26': ('model-upgrade-2026-10-23', 'regular-season-expanded-2026-10-22')}
FEATURE_NAMES = ['constant', 'home back-to-back', 'away back-to-back', 'home 3+ days', 'away 3+ days',
                 'March-April', 'Mar-Apr home bottom-8', 'Mar-Apr away bottom-8',
                 'Mar-Apr home top-4', 'Mar-Apr away top-4', '2024-25', '2025-26']
BUCKETS = ((0, 2), (2, 4), (4, 6), (6, 99))
UNCERTAINTY = 'One date-clustered standard error; sandwich covariance with G/(G-1), without an OLS floor (PR #31).'
POLICY = ('Descriptive research diagnostics, not betting signals or a model-change gate. '
          'Coefficients are fitted in sample. Outcome agreement motivates a prospective test; it does not validate a feature. '
          'Existing accuracy gates still apply unchanged.')


def when(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def clustered_fit(design, targets, clusters, names):
    """The study's OLS and clustered covariance, or an explicit unavailable state."""
    n, k, groups = len(targets), len(names), len(set(clusters))
    result = dict(n=n, clusters=groups, coefficients=[], uncertainty=UNCERTAINTY)
    if n < 20 or n <= k:
        return dict(result, status='insufficient_games')
    if groups < 2:
        return dict(result, status='insufficient_dates')
    try:
        beta, se = ols(design, targets, clusters)
    except ValueError:
        return dict(result, status='singular_design')
    return dict(result, status='estimated', coefficients=[dict(feature=name, estimate=b, se=s)
                                                         for name, b, s in zip(names, beta, se)])


def informative(games):
    """Model/close errors and encompassing slope; pushes and zero disagreements excluded from cover rate."""
    dev = [g['model'] - g['close'] for g in games]
    miss = [g['actual'] - g['close'] for g in games]
    sides = [d * m > 0 for d, m in zip(dev, miss) if d and m]
    model_mae = fmean(abs(g['model'] - g['actual']) for g in games) if games else None
    close_mae = fmean(abs(m) for m in miss) if games else None
    return dict(n=len(games), clusters=len({g['time'].date() for g in games}),
                model_mae=model_mae, close_mae=close_mae,
                mae_gap=model_mae - close_mae if games else None,
                mean_abs_disagreement=fmean(abs(d) for d in dev) if dev else None,
                cover_n=len(sides), cover_rate=fmean(sides) if sides else None,
                slope=clustered_fit([[1., d] for d in dev], miss, [g['time'].date() for g in games],
                                    ['constant', 'model_minus_close']))


def situations(schedule):
    """Study's chronological rest gaps and prior-date win-percentage ranks (0 = worst).

    Stable input order breaks equal-record ties, exactly as the research loader.
    Schedule includes warmup games with both final points and closing spreads.
    Dates follow source timestamps (UTC in the reference), not the gate's Eastern dates.
    """
    last = {}
    for g in schedule:
        g['rest'] = tuple((g['time'] - last[t]).total_seconds() / 86400 if t in last else 9.
                          for t in (g['home'], g['away']))
        last[g['home']] = last[g['away']] = g['time']
    record = defaultdict(lambda: [0, 0])
    pending, day = [], None
    for g in schedule:
        if g['time'].date() != day:
            for p in pending:
                win = p['actual'] > 0
                record[(p['season'], p['home'])][0 if win else 1] += 1
                record[(p['season'], p['away'])][1 if win else 0] += 1
            pending, day = [], g['time'].date()
        teams = [t for s, t in record if s == g['season']]
        order = sorted(teams, key=lambda t: record[(g['season'], t)][0] / max(1, sum(record[(g['season'], t)])))
        g['rank'] = {t: i for i, t in enumerate(order)}
        g['teams'] = len(order)
        pending.append(g)


def features(g):
    late = g['time'].month in (3, 4)
    bottom = [g['rank'].get(t, 15) < 8 for t in (g['home'], g['away'])]
    top = [g['rank'].get(t, 15) >= g['teams'] - 4 for t in (g['home'], g['away'])]
    return [1., g['rest'][0] < 1.5, g['rest'][1] < 1.5, g['rest'][0] >= 2.5, g['rest'][1] >= 2.5, late,
            late and bottom[0], late and bottom[1], late and top[0], late and top[1],
            g['season'] == '2024-25', g['season'] == '2025-26']


def teacher_regression(games):
    design = [[float(v) for v in features(g)] for g in games]
    clusters = [g['time'].date() for g in games]
    targets = {'close_minus_model': [g['close'] - g['model'] for g in games],
               'actual_minus_model': [g['actual'] - g['model'] for g in games],
               'actual_minus_close': [g['actual'] - g['close'] for g in games]}
    return dict(feature_names=FEATURE_NAMES, fits={key: clustered_fit(design, y, clusters, FEATURE_NAMES)
                                                  for key, y in targets.items()},
                target_sd={key: pstdev(y) if y else None for key, y in targets.items()})


def standing_rows(games):
    """Prior team-season means after 10+ games per team, signed toward that team.

    The standing view is home mean minus away mean (no factor of one half).
    The remainder is model - close - standing view. Same-time games cannot enter
    one another's histories. This is identical on the study's NBA schedule.
    """
    trailing, result = defaultdict(list), []
    for _, batch in groupby(sorted(games, key=lambda g: (g['time'], str(g['game_id']))), key=lambda g: g['time']):
        today = list(batch)
        for g in today:
            h, a = trailing[(g['season'], g['home'])], trailing[(g['season'], g['away'])]
            if len(h) >= 10 and len(a) >= 10:
                view = fmean(h) - fmean(a)
                result.append(dict(game_id=g['game_id'], time=g['time'], standing_view=view,
                                   game_day=g['model'] - g['close'] - view, miss=g['actual'] - g['close']))
        for g in today:
            trailing[(g['season'], g['home'])].append(g['model'] - g['close'])
            trailing[(g['season'], g['away'])].append(g['close'] - g['model'])
    return result


def persistent(games):
    by_team = defaultdict(list)
    for g in games:
        by_team[(g['season'], g['home'])].append(g['model'] - g['close'])
        by_team[(g['season'], g['away'])].append(g['close'] - g['model'])
    rows = standing_rows(games)
    views, rest = [r['standing_view'] for r in rows], [r['game_day'] for r in rows]
    v, r = pstdev(views) ** 2 if views else 0., pstdev(rest) ** 2 if rest else 0.
    covers = [row['standing_view'] * row['miss'] > 0 for row in rows if abs(row['standing_view']) >= 1.5 and row['miss']]
    return dict(team_seasons=len(by_team), team_season_mean_sd=pstdev([fmean(v) for v in by_team.values()]) if by_team else None,
                n=len(rows), minimum_prior_games=10, standing_variance_share=v / (v + r) if v + r else None,
                cover_n=len(covers), cover_rate=fmean(covers) if covers else None,
                fit=clustered_fit([[1., row['standing_view'], row['game_day']] for row in rows],
                                  [row['miss'] for row in rows], [row['time'].date() for row in rows],
                                  ['constant', 'standing_view', 'game_day']),
                variance_definition='Var(standing view) / [Var(standing view) + Var(game-day remainder)]; not R-squared.',
                mean_sd_scope='Full-sample team-season means, descriptive only; regression uses earlier games exclusively.')


def diagnostics(games, *, historical=False):
    rows = [dict(g, time=when(g['time'])) for g in games
            if all(finite(g.get(key)) for key in ('model', 'close', 'actual')) and g.get('time')]
    rows.sort(key=lambda g: g['time'])
    overall = informative(rows)
    buckets = [dict(lower=lo, upper=hi, label=f'{lo}–<{hi}', **informative(
        [g for g in rows if lo <= abs(g['model'] - g['close']) < hi])) for lo, hi in BUCKETS]
    result = dict(version=VERSION, study=STUDY, policy=POLICY, uncertainty=UNCERTAINTY,
                  date_basis='Source timestamp calendar date (UTC in November 6 reference)',
                  input_rows=len(games), excluded_rows=len(games) - len(rows), overall=overall, buckets=buckets,
                  outside_buckets=sum(abs(g['model'] - g['close']) >= 99 for g in rows),
                  by_season={season: informative([g for g in rows if g.get('season') == season])
                             for season in sorted({g['season'] for g in rows if g.get('season')})})
    if rows:
        result['date_range'] = [rows[0]['time'].date().isoformat(), rows[-1]['time'].date().isoformat()]
    if historical and all('rest' in g and 'rank' in g for g in rows):
        result['teacher'] = teacher_regression(rows)
    else:
        result['teacher'] = dict(status='unavailable', reason='Fixed historical regression requires the study schedule and standings context.')
    if all(g.get('home') and g.get('away') and g.get('season') for g in rows):
        result['persistent'] = persistent(rows)
    else:
        result['persistent'] = dict(status='unavailable', reason='Team and season identities are required.')
    return result


def replay_schedule(game_rows, records, lines, history=None):
    """Join the study's same source rows; warmup schedule supplies rest and standings."""
    forecasts = {g['game_id']: g for g in game_rows}
    indexed = dict(lines)
    for line in lines.values():
        if line.get('provider_event_id'):
            indexed['espn-' + str(line['provider_event_id'])] = line
    points = defaultdict(float)
    if history is not None:
        for p in history:
            if p.get('pts') is not None:
                points[(p['game_id'], p['team_id'])] += p['pts']
    else:
        for r in records:
            for team, stats in (r.get('team_stats') or {}).items():
                if stats.get('pts') is not None:
                    points[(r['game_id'], team)] = stats['pts']
    schedule = []
    for r in records:
        gid, home, away = r['game_id'], r['home_team_id'], r['away_team_id']
        line = indexed.get(gid)
        if not line or not finite(line.get('home_spread')) or (gid, home) not in points or (gid, away) not in points:
            continue
        g = forecasts.get(gid)
        schedule.append(dict(game_id=gid, season=r['season'], time=when(r['game_time']), home=home, away=away,
                             close=-line['home_spread'], actual=points[(gid, home)] - points[(gid, away)],
                             model=g['projected_margin'] if g else None))
    schedule.sort(key=lambda g: g['time'])
    situations(schedule)
    return schedule


def replay_diagnostics(game_rows, records, lines, history=None):
    schedule = replay_schedule(game_rows, records, lines, history)
    games = [g for g in schedule if g['model'] is not None]
    result = diagnostics(games, historical=True)
    result['schedule_games'] = len(schedule)
    result['unmatched_forecast_games'] = len(game_rows) - len(games)
    return result


def live_diagnostics(rows, season=None):
    """Keep providers separate: repeated sportsbook rows are not independent games."""
    providers = defaultdict(list)
    for g in rows:
        providers[str(g['provider_id'])].append(dict(game_id=g['game_id'], time=g['start_time'],
            season=g.get('season', season), home=g.get('home_team_id'), away=g.get('away_team_id'),
            model=g.get('model_margin'), close=g.get('market_margin'), actual=g.get('actual_margin')))
    return {pid: dict(provider=next(r.get('provider') for r in rows if str(r['provider_id']) == pid),
                      diagnostics=diagnostics(games)) for pid, games in sorted(providers.items())}


def read(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)


def reference_report(reference, datasets):
    reference, datasets = Path(reference), Path(datasets)
    sources = []
    def source(path):
        raw = path.read_bytes()
        sources.append(dict(path=str(path.relative_to(datasets)) if path.is_relative_to(datasets) else str(path),
                            sha256=hashlib.sha256(raw).hexdigest()))
        return read(path)
    board = source(reference / 'rows.json.gz')
    games = [dict(zip(board['game_fields'], row)) for row in board['games']]
    records, history, lines = [], [], {}
    for season, (team_games, player_history) in SEASONS.items():
        records.extend(source(datasets / team_games / 'team-games.json.gz'))
        history.extend(source(datasets / player_history / 'history.json.gz'))
        lines.update({r['game_id']: r for r in source(datasets / f'market-closing-{season}' / 'closing-lines.json.gz')['lines']})
    return dict(version=VERSION, reference=reference.name, sources=sources,
                diagnostics=replay_diagnostics(games, records, lines, history))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, default=Path('datasets') / REFERENCE)
    parser.add_argument('--datasets', type=Path, default=Path('datasets'))
    parser.add_argument('--output', type=Path, required=True, help='Derived report, e.g. OPERATIONS/market/close-teacher.json')
    args = parser.parse_args()
    report = reference_report(args.reference, args.datasets)
    atomic_json(args.output, report)
    print(json.dumps(report['diagnostics']['overall'], sort_keys=True))


if __name__ == '__main__':
    main()
