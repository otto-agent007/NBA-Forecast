"""Where a replay's projected team points miss the actual ones: an exact split of the error.

    python3 scripts/research/totals_decomposition.py \
        --run production=<production-path backtest dir> --run baseline=datasets/backtest-2026-11-07-market-strength

For every team row of each backtest's ``rows.json.gz``, the gate's error,
projected minus actual regulation-equivalent points, splits into parts that add
up to it exactly (checked to 1e-6 on every row). Actual points are put on the
regulation scale of the team row, ``k = actual_points_regulation / actual_points``
(240 over the team's player-minutes), so a player's actual is ``k * pts``:

    played          projected players who played: mean when playing minus actual
      of which minutes   (projected - k * actual minutes) * projected points per minute
      of which rate      k * actual minutes * (projected - actual points per minute)
    participation   (probability - played) * mean, over projected players
    unprojected     minus the actual points of players without a projection,
      listed out    those the game's injury report listed out
      withheld      the others (no evidence, cold start, unresolved availability)
    replacement     the credit for unallocated minutes (published total minus the weighted player sum)
    box remainder   k * (sum of player points - team points): box-score rows missing from the history

Overtime is not in the gate's error (it compares regulation-equivalent points)
but is in every live comparison against final scores; its mean is printed
beside the error. The same split is printed for minutes, where the parts add up
to 240 minus the team's regulation-equivalent player-minutes.

Rows are grouped by season and by the number of reported-out regulars: players
the game's injury report listed out who average at least
``margins.ABSENCE_MINIMUM_MINUTES`` minutes a game for the team this season and
played one of its last ten games (``model._absence``). The ± is one standard
error clustered by Eastern game date. A second ``--run`` adds a column of paired
differences (first minus second) over the team rows both share.
"""
import argparse
from collections import defaultdict, deque
import datetime
import gzip
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats_lite import ols  # noqa: E402

# One entry per replayed season, as AGENTS.md's backtest command names them.
SEASONS = (
    ('2023-24', 'espn-season-2023-24/history.json.gz', 'espn-season-2023-24/team-games.json.gz'),
    ('2024-25', 'espn-season-2024-25/history.json.gz', 'espn-season-2024-25/team-games.json.gz'),
    ('2025-26', 'regular-season-expanded-2026-10-22/history.json.gz', 'model-upgrade-2026-10-23/team-games.json.gz'),
)
EASTERN = ZoneInfo('America/New_York')
RECENT_TEAM_GAMES = 10
TOLERANCE = 1e-6
POINT_PARTS = ('played', 'minutes', 'rate', 'participation', 'unprojected', 'listed out', 'withheld', 'replacement', 'box remainder')
MINUTE_PARTS = ('played', 'participation', 'unprojected', 'unallocated', 'remainder')


def read(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)


def mean_se(values, clusters):
    """The mean and its one-way cluster-robust standard error (G/(G-1) factor)."""
    if len(set(clusters)) < 2:
        return (values[0] if values else float('nan')), float('nan')
    beta, se = ols([[1.]] * len(values), values, clusters)
    return beta[0], se[0]


def game_context(datasets):
    """game_id -> (Eastern date, season label), and (game_id, team_id) -> (reported-out regulars, players listed out)."""
    from nba_forecast.margins import ABSENCE_MINIMUM_MINUTES
    games, regulars = {}, {}
    for season, history_path, games_path in SEASONS:
        for r in read(Path(datasets) / games_path):
            day = datetime.datetime.fromisoformat(r['game_time'].replace('Z', '+00:00')).astimezone(EASTERN).date()
            games[r['game_id']] = (day.isoformat(), r['season'])
        statuses = read(Path(datasets) / f'injury-reports-{season}' / 'statuses.json.gz')['games']
        by_team = defaultdict(lambda: defaultdict(list))
        for r in read(Path(datasets) / history_path):
            by_team[(r['season'], r['team_id'])][(r['game_time'], r['game_id'])].append(r)
        for (_, team), team_games in by_team.items():
            stats = defaultdict(lambda: [0., 0])  # player -> [minutes, games] for the team this season
            recent = deque(maxlen=RECENT_TEAM_GAMES)
            for _, gid in sorted(team_games):
                listed = {pid for pid, s in ((statuses.get(gid) or {}).get('statuses') or {}).items()
                          if s[0] == 'out' and (len(s) < 3 or s[2] == team)}
                count = sum(1 for pid in listed if stats[pid][1] and stats[pid][0] / stats[pid][1] >= ABSENCE_MINIMUM_MINUTES
                            and any(pid in g for g in recent))
                regulars[(gid, team)] = (count, listed)
                here = [r for r in team_games[(_, gid)] if r['status'] == 'played' and r['minutes'] > 0]
                for r in here:
                    stats[r['player_id']][0] += r['minutes']
                    stats[r['player_id']][1] += 1
                recent.append({r['player_id'] for r in here})
    return games, regulars


def decompose(run, games, regulars):
    """(game_id, team_id) -> the parts of one backtest's team rows."""
    board = read(Path(run) / 'rows.json.gz')
    players = defaultdict(list)
    for row in board['players']:
        p = dict(zip(board['player_fields'], row))
        players[(p['game_id'], p['team_id'])].append(p)
    out = {}
    for row in board['teams']:
        t = dict(zip(board['team_fields'], row))
        if not t['projected_players'] or t['actual_points_regulation'] is None or not t['actual_points']:
            continue
        key = (t['game_id'], t['team_id'])
        rows, listed = players[key], regulars.get(key, (0, set()))[1]
        k = t['actual_points_regulation'] / t['actual_points']
        weight = lambda p: 1. if p['participation_probability'] is None else p['participation_probability']
        played = lambda p: p['actual_status'] == 'played' and (p['actual_minutes'] or 0) > 0
        projected = [p for p in rows if p['points_mean'] is not None]
        others = [p for p in rows if p['points_mean'] is None]
        parts = dict.fromkeys(POINT_PARTS, 0.)
        minutes = dict.fromkeys(MINUTE_PARTS, 0.)
        for p in projected:
            actual, actual_minutes = k * p['actual_points'], k * (p['actual_minutes'] or 0)
            if played(p):
                rate = p['points_mean'] / p['expected_minutes'] if p['expected_minutes'] else 0.
                parts['minutes'] += (p['expected_minutes'] - actual_minutes) * rate
                parts['rate'] += actual_minutes * rate - actual
                minutes['played'] += p['expected_minutes'] - actual_minutes
            else:  # a projected player who did not play scored nothing; kept exact all the same
                parts['rate'] -= actual
                minutes['played'] -= actual_minutes
            parts['participation'] += (weight(p) - played(p)) * p['points_mean']
            minutes['participation'] += (weight(p) - played(p)) * p['expected_minutes']
        parts['played'] = parts['minutes'] + parts['rate']
        for p in others:
            name = 'listed out' if p['player_id'] in listed else 'withheld'
            parts[name] -= k * p['actual_points']
            minutes['unprojected'] -= k * (p['actual_minutes'] or 0)
        parts['unprojected'] = parts['listed out'] + parts['withheld']
        weighted_points = sum(weight(p) * p['points_mean'] for p in projected)
        weighted_minutes = sum(weight(p) * p['expected_minutes'] for p in projected)
        parts['replacement'] = t['projected_points'] - weighted_points
        parts['box remainder'] = k * (sum(p['actual_points'] for p in rows) - t['actual_points'])
        minutes['unallocated'] = max(0., 240. - weighted_minutes)
        error = t['projected_points'] - t['actual_points_regulation']
        check = parts['played'] + parts['participation'] + parts['unprojected'] + parts['replacement'] + parts['box remainder'] - error
        if abs(check) > TOLERANCE:
            raise ValueError(f'{run}: parts do not add up for {key}: {check}')
        minute_total = 240. - k * sum(p['actual_minutes'] or 0 for p in rows)
        minutes['remainder'] = minute_total - (minutes['played'] + minutes['participation'] + minutes['unprojected'] + minutes['unallocated'])
        date, season = games[t['game_id']]
        out[key] = dict(error=error, overtime=t['actual_points'] - t['actual_points_regulation'], parts=parts, minutes=minutes,
                        date=date, season=season, regulars=regulars.get(key, (0, set()))[0],
                        unallocated_minutes=minutes['unallocated'], projected=t['projected_points'],
                        unprojected_minutes=-minutes['unprojected'])
    return out


def _group(count):
    return '3+' if count >= 3 else str(count)


def _groups(rows):
    groups = [('all', rows)]
    groups += [(season, [r for r in rows if r['season'] == season]) for season in sorted({r['season'] for r in rows})]
    groups += [(f'out regulars {g}', [r for r in rows if _group(r['regulars']) == g]) for g in ('0', '1', '2', '3+')]
    return [(label, subset) for label, subset in groups if subset]


def _cell(values, dates):
    mean, se = mean_se(values, dates)
    return f'{mean:+7.2f} ±{se:4.2f}'


def table(runs, names):
    """Points table per run, then the paired difference of the first two runs."""
    for name in names:
        rows = list(runs[name].values())
        print(f"\n{name}: projected minus actual regulation points per team-game, ± one date-clustered standard error")
        print(f"  {'':16s} {'n':>5s} {'error':>13s} {'overtime':>8s} " + ' '.join(f'{part:>13s}' for part in POINT_PARTS))
        for label, subset in _groups(rows):
            dates = [r['date'] for r in subset]
            overtime, _ = mean_se([r['overtime'] for r in subset], dates)
            print(f"  {label:16s} {len(subset):5d} {_cell([r['error'] for r in subset], dates)} {overtime:+8.2f} "
                  + ' '.join(_cell([r['parts'][part] for r in subset], dates) for part in POINT_PARTS))
        print(f"  minutes (parts sum to 240 minus the regulation-equivalent player-minutes, the remainder)")
        print(f"  {'':16s} {'n':>5s} " + ' '.join(f'{part:>13s}' for part in MINUTE_PARTS))
        for label, subset in _groups(rows):
            dates = [r['date'] for r in subset]
            print(f"  {label:16s} {len(subset):5d} " + ' '.join(_cell([r['minutes'][part] for r in subset], dates) for part in MINUTE_PARTS))
        unprojected = sum(r['unprojected_minutes'] for r in rows)
        points = -sum(r['parts']['unprojected'] for r in rows)
        allocated = sum(r['unallocated_minutes'] for r in rows)
        credit = sum(r['parts']['replacement'] for r in rows)
        print(f"  unprojected players scored {points / unprojected if unprojected else float('nan'):.3f} points per regulation minute "
              f"({unprojected:.0f} minutes); the replacement credit paid {credit / allocated if allocated else float('nan'):.3f} "
              f"per unallocated minute ({allocated:.0f} minutes)")
    if len(names) >= 2:
        first, second = runs[names[0]], runs[names[1]]
        keys = sorted(set(first) & set(second))
        print(f"\n{names[0]} minus {names[1]}, paired over {len(keys)} team rows, ± one date-clustered standard error")
        print(f"  {'':16s} {'n':>5s} {'error':>13s} " + ' '.join(f'{part:>13s}' for part in POINT_PARTS))
        paired = [dict(first[k], other=second[k]) for k in keys]
        for label, subset in _groups(paired):
            dates = [r['date'] for r in subset]
            print(f"  {label:16s} {len(subset):5d} {_cell([r['error'] - r['other']['error'] for r in subset], dates)} "
                  + ' '.join(_cell([r['parts'][part] - r['other']['parts'][part] for r in subset], dates) for part in POINT_PARTS))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', action='append', required=True, metavar='NAME=DIR',
                        help='A backtest output directory holding rows.json.gz, with a label (repeatable)')
    parser.add_argument('--datasets', default=str(ROOT / 'datasets'))
    parser.add_argument('--layers', help="Records of scripts/research/totals_layers.py, to check the reported-out regulars count against the model's")
    args = parser.parse_args()
    games, regulars = game_context(args.datasets)
    names, runs = [], {}
    for item in args.run:
        name, _, path = item.partition('=')
        names.append(name)
        runs[name] = decompose(path, games, regulars)
    if args.layers:
        records = read(args.layers)['records']
        same = sum(regulars[(r['game_id'], r['team_id'])][0] == r['out_regulars'] for r in records)
        print(f"reported-out regulars counted here agree with the model's own count on {same} of {len(records)} "
              f"team-games of {args.layers}")
    print('parts add up to the error on every team row (tolerance 1e-6)')
    table(runs, names)


if __name__ == '__main__':
    main()
