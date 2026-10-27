"""Where the model's margin error is lost against the close, on a backtest reference's own games.

    python3 scripts/research/gap_breakdown.py --reference datasets/backtest-2026-10-26-absence

Reads the reference's game rows, each season's team games and closing lines, and
prints: the overall gap in margin MAE, whether the margins are compressed, and
the gap by month, by back-to-backs, by the size of the disagreement with the
close, by the size of the spread, by underdog versus favourite lean, and by
team-season. These are the numbers behind "Where the remaining gap is" in
docs/research-lessons-2026-10.md.
"""
import argparse
from collections import Counter, defaultdict
import datetime
import gzip
import json
from pathlib import Path
from statistics import fmean
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats_lite import corr, ols, sd  # noqa: E402

# Team games per replayed season, as AGENTS.md's backtest command names them.
SEASONS = {'2023-24': 'espn-season-2023-24', '2024-25': 'espn-season-2024-25', '2025-26': 'model-upgrade-2026-10-23'}
MONTH_ORDER = {'10': 0, '11': 1, '12': 2, '01': 3, '02': 4, '03': 5, '04': 6}


def read(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)


def load(datasets, reference):
    board = read(Path(reference) / 'rows.json.gz')
    rows = {g[0]: dict(zip(board['game_fields'], g)) for g in board['games']}
    games = []
    for season, folder in SEASONS.items():
        records = read(Path(datasets) / folder / 'team-games.json.gz')
        lines = {l['game_id']: l for l in read(Path(datasets) / f'market-closing-{season}' / 'closing-lines.json.gz')['lines']}
        last = {}  # team -> date of its previous game
        for r in sorted(records, key=lambda r: r['game_time']):
            day = datetime.date.fromisoformat(r['game_time'][:10])  # UTC dates suffice for whole-day rest gaps
            home, away = r['home_team_id'], r['away_team_id']
            rest = {t: (day - last[t]).days if t in last else 9 for t in (home, away)}
            last[home] = last[away] = day
            g, line = rows.get(r['game_id']), lines.get(r['game_id'])
            if g and line and line.get('home_spread') is not None:
                games.append(dict(game_id=r['game_id'], season=season, day=r['game_time'][:10], month=r['game_time'][5:7],
                                  home=home, away=away, model=g['projected_margin'], close=-line['home_spread'],
                                  actual=g['actual_margin'], b2b=(rest[home] == 1) + (rest[away] == 1)))
    return games


def gap(games):
    return fmean(abs(g['actual'] - g['model']) for g in games) - fmean(abs(g['actual'] - g['close']) for g in games)


def table(games, title, key):
    print('\n' + title)
    by = defaultdict(list)
    for g in games:
        by[key(g)].append(g)
    total = gap(games) * len(games)
    for k in sorted(by):
        u = by[k]
        print(f"  {str(k):>12} n {len(u):4d}  model {fmean(abs(g['actual'] - g['model']) for g in u):.2f}  "
              f"close {fmean(abs(g['actual'] - g['close']) for g in u):.2f}  gap {gap(u):+.2f}  share of total {gap(u) * len(u) / total:.0%}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--reference', required=True, help='Backtest directory holding rows.json.gz')
    parser.add_argument('--datasets', default='datasets')
    parser.add_argument('--calendar', help='A schedule calendar JSON naming teams (tricodes in the team table)')
    args = parser.parse_args()
    games = load(args.datasets, args.reference)
    print(len(games), 'games with a model margin and a closing line')
    print(f'overall gap in margin MAE, model minus close: {gap(games):+.3f}')
    m, c, y = [g['model'] for g in games], [g['close'] for g in games], [g['actual'] for g in games]
    print('\ncompression: the scale on the actual margin that would minimise squared error')
    for name, x in (('model', m), ('close', c)):
        k = sum(a * b for a, b in zip(x, y)) / sum(a * a for a in x)
        print(f"  {name}: sd {sd(x):.2f}; best stretch {k:.3f}; MAE {fmean(abs(a - b) for a, b in zip(y, x)):.3f}"
              f" -> stretched {fmean(abs(a - k * b) for a, b in zip(y, x)):.3f}")
    beta, _ = ols([[1., a, b] for a, b in zip(m, c)], y)
    print(f"  actual sd {sd(y):.2f}; corr(model, close) {corr(m, c):.3f}; sd of (model - close) {sd([a - b for a, b in zip(m, c)]):.2f}")
    print(f"  actual on model and close together: model {beta[1]:+.3f}, close {beta[2]:+.3f}")
    table(games, 'by month', lambda g: f"{MONTH_ORDER.get(g['month'], 9)}-{g['month']}")
    table(games, 'by season', lambda g: g['season'])
    table(games, 'by teams on the second night of a back-to-back', lambda g: g['b2b'])
    table(games, 'by |model - close| (points, capped at 6)', lambda g: min(int(abs(g['model'] - g['close'])), 6))
    table(games, 'by size of the closing spread (3-point bins, capped at 12)', lambda g: min(int(abs(g['close']) // 3) * 3, 12))
    table(games, 'model further toward the underdog (U) or the favourite (F) than the close',
          lambda g: 'U' if abs(g['model']) < abs(g['close']) or g['model'] * g['close'] < 0 else 'F')
    names = {}
    if args.calendar:
        for g in read(args.calendar)['games']:
            for side in ('home', 'away'):
                if g.get(side + '_team_id'):
                    names[g[side + '_team_id']] = g.get(side + '_tricode', g[side + '_team_id'])
    big = [g for g in games if abs(g['model'] - g['close']) >= 6]
    print(f"\n|model - close| >= 6: {len(big)} games ({len(big) / len(games):.0%}), {gap(big) * len(big) / (gap(games) * len(games)):.0%} of the gap;"
          f" by month {Counter(g['month'] for g in big).most_common()}")
    tilt, seen = Counter(), Counter()
    for g in games:
        loss = abs(g['actual'] - g['model']) - abs(g['actual'] - g['close'])
        for t in (g['home'], g['away']):
            tilt[(g['season'], t)] += loss
            seen[(g['season'], t)] += 1
    print('\nteam-seasons contributing the most gap (model abs error minus close abs error, per game):')
    for (season, team), value in sorted(tilt.items(), key=lambda kv: -kv[1])[:12]:
        u = [g for g in games if g['season'] == season and team in (g['home'], g['away'])]
        view = lambda g: (g['close'] - g['model']) * (1 if g['home'] == team else -1)
        late = [g for g in u if g['month'] in ('03', '04')]
        print(f"  {season} {names.get(team, team):>4}: {value / len(u):+.2f}/game over {len(u)}; the close rates them {fmean(map(view, u)):+.2f}"
              f" vs the model" + (f" (Mar-Apr {fmean(map(view, late)):+.2f})" if late else ''))


if __name__ == '__main__':
    main()
