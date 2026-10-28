"""Is clutch shooting a skill? Year-to-year persistence of clutch true shooting.

    python3 scripts/research/clutch.py [--min-attempts 40] [--players "Luka Dončić" "Damian Lillard"]

Clutch is the NBA's definition, the last five minutes with the margin within
five (``datasets/clutch-2026-10-22``); season true shooting comes from the
gate's histories, matched by name key (``injury_parser.person_key``). For
players with ``--min-attempts`` clutch shooting attempts (FGA + 0.44 FTA) in
two consecutive seasons it prints the year-to-year correlation of clutch TS%,
of season TS% and of clutch minus season (the clutch "extra"), with a
bootstrap interval for the last. The spread chance alone gives the extra is
the binomial variance of each player's clutch attempts at that season's
league clutch percentages. Then the share of games that finish within five
points, and the named players' seasons.
"""
import argparse
import math
from pathlib import Path
import random
from statistics import fmean
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stats_lite import corr, sd  # noqa: E402
from nba_forecast.backtest import read_json  # noqa: E402
from nba_forecast.injury_parser import person_key  # noqa: E402

# The gate's histories, by season.
HISTORIES = {'2023-24': 'espn-season-2023-24', '2024-25': 'espn-season-2024-25', '2025-26': 'regular-season-expanded-2026-10-22'}


def attempts(pts, fga, fta):
    return fga + .44 * fta


def true_shooting(pts, fga, fta):
    return pts / (2 * attempts(pts, fga, fta)) if attempts(pts, fga, fta) else None


def chance_variance(row, league):
    """Sampling variance of a clutch TS% on these attempts at the league's clutch percentages."""
    p2, p3, pft = league
    twos = row['FGA'] - row['FG3A']
    points = twos * 4 * p2 * (1 - p2) + row['FG3A'] * 9 * p3 * (1 - p3) + row['FTA'] * pft * (1 - pft)
    return points / (2 * attempts(row['PTS'], row['FGA'], row['FTA'])) ** 2


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--datasets', default=str(Path(__file__).resolve().parents[2] / 'datasets'))
    parser.add_argument('--min-attempts', type=float, default=40)
    parser.add_argument('--players', nargs='*', default=['Luka Dončić', 'Damian Lillard'])
    args = parser.parse_args()
    datasets = Path(args.datasets)
    season, scores = {}, {}
    for label, folder in HISTORIES.items():
        for r in read_json(datasets / folder / 'history.json.gz'):
            if r['season'] != label or r.get('season_type') not in (None, 'Regular Season'):
                continue
            t = season.setdefault((label, person_key(r['name'])), [0, 0, 0])
            t[0] += r['pts'] or 0
            t[1] += r['fga'] or 0
            t[2] += r['fta'] or 0
            game = scores.setdefault(label, {}).setdefault(r['game_id'], {})
            game[r['team_id']] = game.get(r['team_id'], 0) + (r['pts'] or 0)
    clutch, league = {}, {}
    for label in HISTORIES:
        rows = read_json(datasets / 'clutch-2026-10-22' / f'{label}.json.gz')['rows']
        clutch.update(((label, person_key(r['PLAYER_NAME'])), r) for r in rows)
        total = {k: sum(r[k] for r in rows) for k in ('FGA', 'FGM', 'FG3A', 'FG3M', 'FTA', 'FTM')}
        league[label] = ((total['FGM'] - total['FG3M']) / (total['FGA'] - total['FG3A']),
                         total['FG3M'] / total['FG3A'], total['FTM'] / total['FTA'])

    def ts(label, key):
        r = clutch[(label, key)]
        return true_shooting(r['PTS'], r['FGA'], r['FTA']), attempts(r['PTS'], r['FGA'], r['FTA'])

    pairs, noise = [], []
    labels = list(HISTORIES)
    for before, after in zip(labels, labels[1:]):
        for (label, key) in clutch:
            if label != before or not all(k in clutch and k in season for k in ((before, key), (after, key))):
                continue
            (c1, a1), (c2, a2) = ts(before, key), ts(after, key)
            if min(a1, a2) < args.min_attempts:
                continue
            s1, s2 = (true_shooting(*season[(s, key)]) for s in (before, after))
            pairs.append((c1, c2, s1, s2, a1, a2))
            noise += [chance_variance(clutch[(before, key)], league[before]),
                      chance_variance(clutch[(after, key)], league[after])]
    extra = [(p[0] - p[2], p[1] - p[3]) for p in pairs]
    random.seed(1)
    draws = []
    for _ in range(2000):
        sample = [random.choice(extra) for _ in extra]
        draws.append(corr([a for a, _ in sample], [b for _, b in sample]))
    draws.sort()
    observed = sd([e for pair in extra for e in pair])
    chance = math.sqrt(fmean(noise))
    print(f'{len(pairs)} consecutive-season pairs with {args.min_attempts:g}+ clutch shooting attempts in both; '
          f'median {sorted(p[4] for p in pairs)[len(pairs) // 2]:.0f} attempts a season')
    print(f'year-to-year correlation: season TS% {corr([p[2] for p in pairs], [p[3] for p in pairs]):.2f}, '
          f'clutch TS% {corr([p[0] for p in pairs], [p[1] for p in pairs]):.2f}, '
          f'clutch minus season {corr([a for a, _ in extra], [b for _, b in extra]):.2f} '
          f'(95% bootstrap {draws[50]:.2f} to {draws[1949]:.2f})')
    print(f'clutch minus season, SD across player-seasons {observed:.3f}; chance alone {chance:.3f}; '
          f'left for skill {math.sqrt(max(0., observed ** 2 - chance ** 2)):.3f}')
    close = []
    for label in labels:
        margins = [abs(a - b) for game in scores[label].values() if len(game) == 2 for a, b in [tuple(game.values())]]
        close.append(f'{label} {sum(m <= 5 for m in margins) / len(margins):.0%} of {len(margins)}')
    print('games finishing within five points: ' + ', '.join(close))
    for name in args.players:
        key = person_key(name)
        cells = []
        for label in labels:
            if (label, key) in clutch and (label, key) in season:
                c, a = ts(label, key)
                s = true_shooting(*season[(label, key)])
                cells.append(f'{label} clutch {c:.3f} on {a:.0f}, season {s:.3f} ({c - s:+.3f})')
            else:
                cells.append(f'{label} no row')
        print(f'{name}: ' + '; '.join(cells))


if __name__ == '__main__':
    main()
