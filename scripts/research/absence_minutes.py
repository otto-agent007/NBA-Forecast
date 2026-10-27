"""Does the margin under-adjust for reported-out regulars? The measurement behind margins.absence_adjustment.

    python3 scripts/research/absence_minutes.py --reference datasets/backtest-2026-10-26-three-seasons

For every replayed game, the "absent regular minutes" of each side: the
season-to-date minutes per game, for this team, of players who played one of
the team's last ten games, average 15 or more minutes for it, and do not play
tonight; a player whose next appearance is for another team was traded or
released and is not counted. The reported part is those the game's T-minus-30
official report listed out. The model's miss (actual minus model) and the
close's miss are regressed on the home-minus-away reported-out minutes with
date-clustered errors. On the three-seasons reference the model missed by 0.49
points per ten minutes (se 0.06) against the close's 0.17; on the absence
reference, which lowers the rating by 0.1 a minute, the slope should be near zero.
"""
import argparse
import bisect
from collections import defaultdict, deque
from pathlib import Path
from statistics import fmean
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gap_breakdown import load, read  # noqa: E402
from stats_lite import sd, slope  # noqa: E402

HISTORY = {'2023-24': 'espn-season-2023-24', '2024-25': 'espn-season-2024-25', '2025-26': 'regular-season-expanded-2026-10-22'}
ROTATION_GAMES, REGULAR_MINUTES = 10, 15.


def absent_minutes(datasets, seasons):
    """(game_id, team_id) -> (missing regular minutes, the reported-out part of them)."""
    missing, known = {}, {}
    for season in seasons:
        history = [r for r in read(Path(datasets) / HISTORY[season] / 'history.json.gz') if r.get('season', season) == season]
        reports = read(Path(datasets) / f'injury-reports-{season}' / 'statuses.json.gz')['games']
        by_game, team_games, appearances = defaultdict(list), defaultdict(set), defaultdict(list)
        for r in history:
            by_game[(r['game_time'], r['game_id'])].append(r)
            team_games[r['team_id']].add((r['game_time'], r['game_id']))
            if (r.get('minutes') or 0) > 0:
                appearances[r['player_id']].append((r['game_time'], r['team_id']))
        played = {(r['game_id'], r['player_id']) for r in history if (r.get('minutes') or 0) > 0}
        for v in appearances.values():
            v.sort()
        for team, games in team_games.items():
            stats = defaultdict(lambda: [0., 0])  # player -> [minutes, games] for this team so far
            recent = deque(maxlen=ROTATION_GAMES)
            for time, gid in sorted(games):
                statuses = (reports.get(gid) or {}).get('statuses') or {}
                miss = reported = 0.
                for p in {p for g in recent for p in g}:
                    minutes, n = stats[p]
                    if not n or minutes / n < REGULAR_MINUTES or (gid, p) in played:
                        continue
                    later = appearances[p]
                    i = bisect.bisect_right(later, (time, '￿'))
                    if i < len(later) and later[i][1] != team:
                        continue  # next appears for another team: traded or released, not absent
                    miss += minutes / n
                    if (statuses.get(p) or [None])[0] == 'out':
                        reported += minutes / n
                missing[(gid, team)], known[(gid, team)] = miss, reported
                here = [r for r in by_game[(time, gid)] if r['team_id'] == team and (r.get('minutes') or 0) > 0]
                for r in here:
                    stats[r['player_id']][0] += r['minutes']
                    stats[r['player_id']][1] += 1
                recent.append({r['player_id'] for r in here})
    return missing, known


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--reference', required=True, help='Backtest directory holding rows.json.gz')
    parser.add_argument('--datasets', default='datasets')
    args = parser.parse_args()
    games = load(args.datasets, args.reference)
    missing, known = absent_minutes(args.datasets, sorted({g['season'] for g in games}))
    games = [g for g in games if (g['game_id'], g['home']) in missing and (g['game_id'], g['away']) in missing]
    x = [(known[(g['game_id'], g['home'])] - known[(g['game_id'], g['away'])]) / 10 for g in games]
    unreported = [(missing[(g['game_id'], g['home'])] - missing[(g['game_id'], g['away'])]) / 10 - v for g, v in zip(games, x)]
    days = [g['day'] for g in games]
    print(f"{len(games)} games; sd of the reported-out minutes difference {sd(x) * 10:.1f} min; "
          f"games with 30+ more reported-out minutes on one side {fmean(abs(v) >= 3 for v in x):.0%}")
    total = sum(abs(v) for v in unreported) + sum(abs(v) for v in x)
    print(f"share of absent regular minutes no report listed out: {sum(abs(v) for v in unreported) / total:.0%}")
    targets = {'model miss (actual - model)': [g['actual'] - g['model'] for g in games],
               'close miss (actual - close)': [g['actual'] - g['close'] for g in games],
               'close minus model': [g['close'] - g['model'] for g in games]}
    print('per 10 reported-out regular minutes (home minus away), points of home margin, date-clustered errors:')
    for name, y in targets.items():
        b, se = slope(x, y, days)
        print(f'  {name:28s} {b:+.2f} ± {se:.2f}')
    for season in sorted({g['season'] for g in games}):
        idx = [i for i, g in enumerate(games) if g['season'] == season]
        parts = []
        for name, y in targets.items():
            b, se = slope([x[i] for i in idx], [y[i] for i in idx], [days[i] for i in idx])
            parts.append(f'{name.split(" (")[0]} {b:+.2f} ± {se:.2f}')
        print(f'  {season}: ' + '; '.join(parts))
    big = [i for i, v in enumerate(x) if abs(v) >= 3]
    rest = [i for i, v in enumerate(x) if abs(v) < 3]
    err = targets['model miss (actual - model)']; close = targets['close miss (actual - close)']
    print(f"30+ minute imbalance ({len(big)} games): model MAE {fmean(abs(err[i]) for i in big):.2f}, close {fmean(abs(close[i]) for i in big):.2f}; "
          f"others: model {fmean(abs(err[i]) for i in rest):.2f}, close {fmean(abs(close[i]) for i in rest):.2f}")


if __name__ == '__main__':
    main()
