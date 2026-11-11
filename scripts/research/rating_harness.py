"""Re-score a backtest reference's margins under a different team rating, without a replay.

The replay locates each margin as (1 - w) * player_sum + w * (edge + R_home - R_away + absence),
with R from ``margins.team_ratings`` over the finals of earlier Eastern days (the last 1230) and
the edge their mean margin. Replacing the rating by R' moves the published margin by exactly
w * (R' - R), so the reference rows can be re-scored for any candidate rating in seconds. The
``current`` variant must reproduce the reference margins (delta 0.000); it did on 2026-11-07.

    python3 scripts/research/rating_harness.py --reference datasets/backtest-2026-11-06-rating-weight

Standard library plus the package's own rating code. The ± printed is ONE date-clustered
standard error of the paired change in absolute error, not the gate's two.
"""
import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import fmean

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))
import close_teacher as study  # noqa: E402  (the study's loader: reference rows joined to team games and closes)
from stats_lite import solve  # noqa: E402
from nba_forecast import backtest as B  # noqa: E402
from nba_forecast.margins import PARAMETERS, RATING_PRIOR_GAMES, _final_sample, _season, team_ratings  # noqa: E402
from nba_forecast.validation import parse_time  # noqa: E402

WINDOW = B.MARGIN_WINDOW
W = PARAMETERS['rating_weight']


def finals_by_day(datasets, name):
    rows = study.read(datasets / name / 'team-games.json.gz')
    return [(d, _final_sample(g)) for d, batch in B._by_day(rows, lambda g: g['game_time']) for g in batch]


def window_for(finals, day):
    return [s for d, s in finals if d < day][-WINDOW:]


def age_days(sample, as_of):
    return (parse_time(as_of) - parse_time(sample['game_time'])).total_seconds() / 86400.


def rating_mean(window, as_of, half_life=None, cap=None):
    """Season-to-date signed margin average, optionally recency-weighted or capped; prior 0 as in the replay."""
    current = _season(as_of)
    acc = defaultdict(lambda: [0., 0.])
    for s in window:
        if _season(s['game_time']) != current:
            continue
        m = s['home_points'] - s['away_points']
        if cap is not None:
            m = max(-cap, min(cap, m))
        w = .5 ** (age_days(s, as_of) / half_life) if half_life else 1.
        acc[s['home_team_id']][0] += w * m
        acc[s['home_team_id']][1] += w
        acc[s['away_team_id']][0] -= w * m
        acc[s['away_team_id']][1] += w
    return {t: v[0] / (v[1] + RATING_PRIOR_GAMES) for t, v in acc.items()}


def ridge_strengths(rows, as_of, half_life, ridge, target):
    """Weighted ridge of a per-game target (result or closing margin) on a home edge and two team strengths, centred."""
    teams = sorted({t for s in rows for t in (s['home_team_id'], s['away_team_id'])})
    if not teams:
        return {}
    index = {t: i + 1 for i, t in enumerate(teams)}
    k = len(teams) + 1
    normal = [[0.] * k for _ in range(k)]
    rhs = [0.] * k
    for s in rows:
        weight = .5 ** (age_days(s, as_of) / half_life) if half_life else 1.
        columns = ((0, 1.), (index[s['home_team_id']], 1.), (index[s['away_team_id']], -1.))
        for i, xi in columns:
            rhs[i] += weight * xi * target[s['game_id']]
            for j, xj in columns:
                normal[i][j] += weight * xi * xj
    for i in range(1, k):
        normal[i][i] += ridge
    normal[0][0] += 1e-9
    beta = solve(normal, rhs)
    mean = fmean(beta[1:])
    return {t: beta[index[t]] - mean for t in teams}


def rating_ridge(window, as_of, half_life=None, cap=None, ridge=10.):
    """Opponent-adjusted results rating."""
    current = _season(as_of)
    rows = [s for s in window if _season(s['game_time']) == current]
    target = {s['game_id']: max(-cap, min(cap, s['home_points'] - s['away_points'])) if cap is not None
              else s['home_points'] - s['away_points'] for s in rows}
    return ridge_strengths(rows, as_of, half_life, ridge, target)


def rating_market(window, as_of, closes, beta, half_life=7., ridge=.1, minimum=60):
    """The results rating blended with the strength implied by earlier closing margins (margins.market_strengths)."""
    current = _season(as_of)
    rows = [s for s in window if _season(s['game_time']) == current and s['game_id'] in closes]
    results = {t: v['rating'] for t, v in team_ratings(window, as_of).items()}
    if len(rows) < minimum:
        return results
    strengths = ridge_strengths(rows, as_of, half_life, ridge, closes)
    return {t: (1 - beta) * r + beta * strengths[t] if t in strengths else r for t, r in results.items()}


def cluster_se(values, clusters):
    mean = fmean(values)
    sums = defaultdict(float)
    for v, c in zip(values, clusters):
        sums[c] += v - mean
    g = len(sums)
    return math.sqrt(sum(s * s for s in sums.values()) * g / (g - 1)) / len(values) if g > 1 else float('nan')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--datasets', type=Path, default=Path('datasets'))
    args = parser.parse_args()
    games = [g for g in study.load(args.datasets, args.reference) if g['model'] is not None]
    closes = {}
    finals = {}
    for season, (team_games, _) in study.SEASONS.items():
        if team_games not in finals:
            finals[team_games] = finals_by_day(args.datasets, team_games)
        lines = study.read(args.datasets / f'market-closing-{season}' / 'closing-lines.json.gz')['lines']
        for line in lines:
            if line.get('home_spread') is not None:
                closes[line['game_id']] = -line['home_spread']
                if line.get('provider_event_id'):
                    closes['espn-' + str(line['provider_event_id'])] = -line['home_spread']
    for g in games:
        g['set'] = study.SEASONS[g['season']][0]
        g['day'] = B._day(g['time'].isoformat() if hasattr(g['time'], 'isoformat') else g['time'])
        g['as_of'] = g['time'].isoformat() if hasattr(g['time'], 'isoformat') else g['time']
    variants = {
        'current': lambda win, t: {k: v['rating'] for k, v in team_ratings(win, t).items()},
        'mean half-life 20 d': lambda win, t: rating_mean(win, t, half_life=20),
        'mean half-life 40 d': lambda win, t: rating_mean(win, t, half_life=40),
        'mean cap 20': lambda win, t: rating_mean(win, t, cap=20),
        'ridge 5 pseudo-games': lambda win, t: rating_ridge(win, t, ridge=5.),
        'ridge 10 pseudo-games': lambda win, t: rating_ridge(win, t, ridge=10.),
        'ridge 10, half-life 30 d': lambda win, t: rating_ridge(win, t, ridge=10., half_life=30),
        'market HL 7 d, share 0.5': lambda win, t: rating_market(win, t, closes, .5),
        'market HL 7 d, share 0.7': lambda win, t: rating_market(win, t, closes, .7),
        'market HL 7 d, share 1.0': lambda win, t: rating_market(win, t, closes, 1.),
        'market HL 14 d, share 0.7': lambda win, t: rating_market(win, t, closes, .7, half_life=14.),
        'market HL 7 d, ridge 1, share 0.7': lambda win, t: rating_market(win, t, closes, .7, ridge=1.),
    }
    cache = {}

    def ratings_for(variant, g):
        key = (variant, g['set'], g['day'])
        if key not in cache:
            win = window_for(finals[g['set']], g['day'])
            edge = fmean(s['home_points'] - s['away_points'] for s in win) if win else None
            cache[key] = (edge, variants[variant](win, g['as_of']) if win else {})
        return cache[key]
    base = {}
    for g in games:
        edge, r = ratings_for('current', g)
        if edge is not None and g['home'] in r and g['away'] in r:
            base[g['game_id']] = edge + r[g['home']] - r[g['away']]
    print(f"games {len(games)}, with a rating in the reference {len(base)}; rating weight {W}")
    print(f"{'variant':36s} {'MAE':>7s} {'change':>8s} {'1 se':>6s} | " + ' | '.join(f"{s:>7s}" for s in ('2023-24', '2024-25', '2025-26')) + " | slope of actual-close on model-close")
    ref = [abs(g['model'] - g['actual']) for g in games]
    for variant in variants:
        new = {}
        for g in games:
            m = g['model']
            if g['game_id'] in base:
                edge, r = ratings_for(variant, g)
                if g['home'] in r and g['away'] in r:
                    m += W * ((edge + r[g['home']] - r[g['away']]) - base[g['game_id']])
            new[g['game_id']] = m
        errs = [abs(new[g['game_id']] - g['actual']) for g in games]
        diffs = [e - r for e, r in zip(errs, ref)]
        per = [fmean(abs(new[g['game_id']] - g['actual']) for g in games if g['season'] == s) for s in ('2023-24', '2024-25', '2025-26')]
        x = [new[g['game_id']] - g['close'] for g in games]
        y = [g['actual'] - g['close'] for g in games]
        mx, my = fmean(x), fmean(y)
        slope = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sum((a - mx) ** 2 for a in x)
        print(f"{variant:36s} {fmean(errs):7.3f} {fmean(diffs):+8.3f} {cluster_se(diffs, [g['day'] for g in games]):6.3f} | "
              + ' | '.join(f"{p:7.3f}" for p in per) + f" | {slope:+.3f}")


if __name__ == '__main__':
    main()
