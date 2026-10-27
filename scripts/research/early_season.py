"""How the model does in a season's first weeks, the dates the gate keeps as history only.

    python3 scripts/research/early_season.py --run <seeded replay> [<seeded replay> ...]

Reads replays run with ``--warmup-fraction 0`` and the previous season seeded
(``--previous-history``, ``--previous-team-games``), so opening night is
forecast from last season's box scores and the ratings carry last season's
prior, as live forecasts are. For each window of days since opening night it
prints the model's and the close's margin MAE, team-points bias and the player
errors; the last window is the gate's own.

Then the WAS @ CHA mechanism (2026-10-26): "uncounted last-season minutes" are
the minutes per game, for this team last season, of players the game's
T-minus-30 report listed out who average 15 or more for it and have not yet
played for it this season. The live absence adjustment reads only this
season's minutes, so it counts them as zero while the rating still carries
last season's prior they helped earn. The model's and the close's misses are
regressed on the home-minus-away uncounted minutes with date-clustered errors.
"""
import argparse
from collections import defaultdict
import datetime
from pathlib import Path
from statistics import fmean
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gap_breakdown import SEASONS, load, read  # noqa: E402
from stats_lite import slope  # noqa: E402

HISTORY = {'2023-24': 'espn-season-2023-24', '2024-25': 'espn-season-2024-25', '2025-26': 'regular-season-expanded-2026-10-22'}
# The seeded replays' --previous-history; 2025-26's carries NBA IDs (previous_ids.py).
PREVIOUS = {'2023-24': 'espn-season-2022-23/history.json.gz', '2024-25': 'espn-season-2023-24/history.json.gz',
            '2025-26': 'espn-season-2024-25/history-nba-ids.json.gz'}
REGULAR_MINUTES = 15.
WINDOWS = (('weeks 1-2', 0, 14), ('weeks 3-4', 14, 28), ('weeks 5-8', 28, 56), ('week 9 to the gate', 56, None),
           ('gate window', None, None))


def windows(datasets, seasons):
    """game_id -> window name, by days since the season's opening night; the gate window starts at 30% of dates."""
    out = {}
    for season in seasons:
        records = read(Path(datasets) / SEASONS[season] / 'team-games.json.gz')
        dates = sorted({datetime.date.fromisoformat(r['game_time'][:10]) for r in records})
        opening, gate = dates[0], dates[int(len(dates) * .3)]
        for r in records:
            day = datetime.date.fromisoformat(r['game_time'][:10])
            days = (day - opening).days
            out[r['game_id']] = 'gate window' if day >= gate else next(
                name for name, lo, hi in WINDOWS[:-1] if days >= lo and (hi is None or days < hi))
    return out


def uncounted_minutes(datasets, seasons):
    """(game_id, team_id) -> last-season minutes per game of reported-out regulars yet to play for the team."""
    result = {}
    for season in seasons:
        current = [r for r in read(Path(datasets) / HISTORY[season] / 'history.json.gz') if r.get('season', season) == season]
        previous = read(Path(datasets) / PREVIOUS[season])
        reports = read(Path(datasets) / f'injury-reports-{season}' / 'statuses.json.gz')['games']
        last = defaultdict(lambda: [0., 0])  # (team, player) -> [minutes, games] last season
        for r in previous:
            if (r.get('minutes') or 0) > 0:
                last[(r['team_id'], r['player_id'])][0] += r['minutes']
                last[(r['team_id'], r['player_id'])][1] += 1
        team_games = defaultdict(set)
        first = {}  # (team, player) -> time of the first appearance for the team this season
        for r in current:
            team_games[r['team_id']].add((r['game_time'], r['game_id']))
            if (r.get('minutes') or 0) > 0:
                key = (r['team_id'], r['player_id'])
                first[key] = min(first.get(key, r['game_time']), r['game_time'])
        for team, games in team_games.items():
            for time, gid in games:
                total = 0.
                for pid, listed in ((reports.get(gid) or {}).get('statuses') or {}).items():
                    minutes, n = last.get((team, pid), (0., 0))
                    if listed[0] == 'out' and n and minutes / n >= REGULAR_MINUTES and first.get((team, pid), '9') >= time:
                        total += minutes / n
                result[(gid, team)] = total
    return result


def summarize(name, games, players, teams):
    if not games:
        return
    model = fmean(abs(g['actual'] - g['model']) for g in games)
    close = fmean(abs(g['actual'] - g['close']) for g in games)
    team = [t for t in teams if t['projected_points'] is not None and t['actual_points_regulation'] is not None]
    played = [p for p in players if p['expected_minutes'] is not None and p['actual_status'] == 'played' and p['actual_minutes'] > 0]
    print(f"  {name:20s} {len(games):5d} {model:6.2f} {close:6.2f} {model - close:+6.2f} "
          f"{fmean(abs(g['model'] - g['close']) for g in games):6.2f} "
          f"{fmean(t['projected_points'] - t['actual_points_regulation'] for t in team):+6.2f} "
          f"{fmean(abs(p['points_mean'] - p['actual_points']) for p in played):6.2f} "
          f"{fmean(abs(p['expected_minutes'] - p['actual_minutes']) for p in played):6.2f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', required=True, nargs='+', help='Seeded replay directories holding rows.json.gz')
    parser.add_argument('--datasets', default='datasets')
    args = parser.parse_args()
    games, players, teams = [], defaultdict(list), defaultdict(list)
    for run in args.run:
        games += load(args.datasets, run)
        board = read(Path(run) / 'rows.json.gz')
        for row in board['players']:
            players[row[0]].append(dict(zip(board['player_fields'], row)))
        for row in board['teams']:
            teams[row[0]].append(dict(zip(board['team_fields'], row)))
    seasons = sorted({g['season'] for g in games})
    window = windows(args.datasets, seasons)
    print('window                   games  model  close    gap  |m-c|  team bias  pts MAE  min MAE')
    for season in seasons + [None]:
        print(season or 'pooled')
        for name, *_ in WINDOWS:
            subset = [g for g in games if (season is None or g['season'] == season) and window[g['game_id']] == name]
            ids = {g['game_id'] for g in subset}
            summarize(name, subset, [p for i in ids for p in players[i]], [t for i in ids for t in teams[i]])
    uncounted = uncounted_minutes(args.datasets, seasons)
    print('\nper 10 uncounted last-season minutes (home minus away), points of home margin, date-clustered:')
    for label, names in (('first 30% of dates', [n for n, *_ in WINDOWS[:-1]]), ('weeks 1-4', ['weeks 1-2', 'weeks 3-4']),
                         ('gate window', ['gate window'])):
        subset = [g for g in games if window[g['game_id']] in names]
        x = [(uncounted.get((g['game_id'], g['home']), 0.) - uncounted.get((g['game_id'], g['away']), 0.)) / 10 for g in subset]
        days = [g['day'] for g in subset]
        hit = sum(1 for v in x if v)
        print(f'  {label} ({len(subset)} games, {hit} with uncounted minutes, {sum(abs(v) for v in x) * 10 / max(hit, 1):.1f} '
              f'minutes per such game):')
        if hit < 3:
            continue
        for name, y in (('model miss', [g['actual'] - g['model'] for g in subset]),
                        ('close miss', [g['actual'] - g['close'] for g in subset]),
                        ('close minus model', [g['close'] - g['model'] for g in subset])):
            b, se = slope(x, y, days)
            print(f'    {name:18s} {b:+.2f} ± {se:.2f}')
        hit_games = [g for g, v in zip(subset, x) if v]
        if hit_games:
            print(f"    those games: model MAE {fmean(abs(g['actual'] - g['model']) for g in hit_games):.2f}, "
                  f"close {fmean(abs(g['actual'] - g['close']) for g in hit_games):.2f}")


if __name__ == '__main__':
    main()
