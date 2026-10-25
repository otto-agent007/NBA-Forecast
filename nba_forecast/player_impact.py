"""Player-impact ratings: adjusted plus-minus over a season's box scores, shrunk toward a box-score rating.

Each player-game's plus-minus is modeled as his minutes/48 times his rating, plus
the expected ratings of the teammates and opponents on the floor with him, plus
the home edge. The box score has no stints, so lineups are mixed in proportion
to minutes. The fit weights each observation by 1/minutes (on-court noise grows
with time on the floor) and shrinks every rating toward a box-score estimate:
per-minute rates, shrunk toward league average, weighted by a ridge fit of team
margins on the teams' rate totals. Ratings are points per 48 minutes above an
average player. Fitted on a completed season, they give the next season's team
rating its prior (``backtest.replay``), so a team's starting point follows the
players it is projected to use, not the team that played last season.

Extract plus-minus from an ESPN summary archive (``espn_season``):

    python -m nba_forecast.player_impact --directory data/espn-history/2024-25 \\
        --output datasets/espn-plus-minus-2024-25
"""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
import math
from operator import mul
from pathlib import Path

from .injury_parser import person_key
from .reference import BASE
from .schedule import atomic_json, canonical

VERSION = 'player-impact-1'
STATS = ('pts', 'ast', 'reb', 'stl', 'blk', 'tov', 'fga', 'fta', 'fg3a')
BOX_SHRINK_MINUTES = 250.  # pseudo-minutes of league-average rates
BOX_RIDGE = 50.            # on standardized team rate totals
APM_RIDGE = 2.             # chosen on 2024-25 alone: fit on odd days, team margins of even days
MIN_COVERAGE = .9          # share of a season's played player-games that must carry plus-minus


def _solve(matrix, vector):
    """Solve a symmetric positive-definite system by Cholesky decomposition."""
    n = len(vector)
    lower = [[0.] * n for _ in range(n)]
    for j in range(n):
        row_j = lower[j]
        diagonal = matrix[j][j] - sum(map(mul, row_j[:j], row_j[:j]))
        if diagonal <= 0:
            raise ValueError('system is not positive definite')
        row_j[j] = math.sqrt(diagonal)
        for i in range(j + 1, n):
            row_i = lower[i]
            row_i[j] = (matrix[i][j] - sum(map(mul, row_i[:j], row_j[:j]))) / row_j[j]
    forward = [0.] * n
    for i in range(n):
        forward[i] = (vector[i] - sum(map(mul, lower[i][:i], forward[:i]))) / lower[i][i]
    solution = [0.] * n
    for i in reversed(range(n)):
        solution[i] = (forward[i] - math.fsum(lower[k][i] * solution[k] for k in range(i + 1, n))) / lower[i][i]
    return solution


def _played(history, records):
    """Played rows by game and team, for games with a final record."""
    games = {g['game_id']: g for g in records}
    by_game = defaultdict(lambda: defaultdict(list))
    for r in history:
        if r['game_id'] in games and (r.get('minutes') or 0) > 0:
            by_game[r['game_id']][r['team_id']].append(r)
    return games, {gid: teams for gid, teams in by_game.items()
                   if set(teams) == {games[gid]['home_team_id'], games[gid]['away_team_id']}}


def _margin(game):
    return game['team_stats'][game['home_team_id']]['pts'] - game['team_stats'][game['away_team_id']]['pts']


def box_ratings(history, records):
    """Points per 48 above average from per-minute box rates, weighted by a ridge fit to team margins."""
    games, played = _played(history, records)
    totals, league = defaultdict(lambda: defaultdict(float)), defaultdict(float)
    for teams in played.values():
        for rows in teams.values():
            for r in rows:
                t = totals[person_key(r['name'])]
                t['minutes'] += r['minutes']; league['minutes'] += r['minutes']
                for s in STATS:
                    t[s] += r[s]; league[s] += r[s]
    if not league['minutes']:
        return {}
    average = [league[s] / league['minutes'] for s in STATS]
    rates = {k: [(t[s] + BOX_SHRINK_MINUTES * a) / (t['minutes'] + BOX_SHRINK_MINUTES) - a for s, a in zip(STATS, average)]
             for k, t in totals.items()}
    features, margins = [], []
    for gid in sorted(played):
        g, teams = games[gid], played[gid]
        side = {tid: [math.fsum(r['minutes'] * rates[person_key(r['name'])][j] for r in rows) for j in range(len(STATS))]
                for tid, rows in teams.items()}
        features.append([h - a for h, a in zip(side[g['home_team_id']], side[g['away_team_id']])])
        margins.append(_margin(g))
    n = len(features)
    if n < 2 * len(STATS):
        return {}
    means = [math.fsum(f[j] for f in features) / n for j in range(len(STATS))]
    scale = [math.sqrt(math.fsum((f[j] - means[j]) ** 2 for f in features) / n) or 1. for j in range(len(STATS))]
    z = [[1.] + [f[j] / scale[j] for j in range(len(STATS))] for f in features]
    size = len(STATS) + 1
    matrix = [[math.fsum(row[i] * row[j] for row in z) + (BOX_RIDGE if i == j and i else 0.) for j in range(size)]
              for i in range(size)]
    coefficients = _solve(matrix, [math.fsum(row[i] * y for row, y in zip(z, margins)) for i in range(size)])
    weights = [c / s for c, s in zip(coefficients[1:], scale)]
    return {k: 48 * math.fsum(w * v for w, v in zip(weights, r)) for k, r in rates.items()}


def ratings(history, records, plus_minus):
    """Adjusted plus-minus per player (name key -> points per 48), or {} when plus-minus is too sparse.

    ``plus_minus`` maps (game ID, player ID) to the player's plus-minus.
    """
    games, played = _played(history, records)
    rows = [r for teams in played.values() for side in teams.values() for r in side]
    if not rows or sum((r['game_id'], r['player_id']) in plus_minus for r in rows) < MIN_COVERAGE * len(rows):
        return {}
    played = {gid: teams for gid, teams in played.items()
              if all((r['game_id'], r['player_id']) in plus_minus for side in teams.values() for r in side)}
    prior = box_ratings(history, records)
    keys = sorted({person_key(r['name']) for teams in played.values() for side in teams.values() for r in side})
    index = {k: i for i, k in enumerate(keys)}
    home = len(keys)
    size = home + 1
    matrix = [[0.] * size for _ in range(size)]
    vector = [0.] * size
    for gid in sorted(played):
        g, teams = games[gid], played[gid]
        for tid, mine in teams.items():
            opponents = teams[g['away_team_id'] if tid == g['home_team_id'] else g['home_team_id']]
            team_minutes = math.fsum(r['minutes'] for r in mine)
            opponent_minutes = math.fsum(r['minutes'] for r in opponents)
            for r in mine:
                share = r['minutes'] / 48
                x = defaultdict(float)
                x[index[person_key(r['name'])]] += share
                for other in mine:
                    if other is not r and team_minutes > r['minutes']:
                        x[index[person_key(other['name'])]] += share * 4 * other['minutes'] / (team_minutes - r['minutes'])
                for other in opponents:
                    x[index[person_key(other['name'])]] -= share * 5 * other['minutes'] / opponent_minutes
                x[home] = share * (1. if tid == g['home_team_id'] else -1.)
                weight = 1. / max(r['minutes'], 1.)
                y = plus_minus[(r['game_id'], r['player_id'])]
                items = list(x.items())
                for i, xi in items:
                    vector[i] += weight * xi * y
                    row = matrix[i]
                    for j, xj in items:
                        row[j] += weight * xi * xj
    for k, i in index.items():
        matrix[i][i] += APM_RIDGE
        vector[i] += APM_RIDGE * prior.get(k, 0.)
    solution = _solve(matrix, vector)
    return {k: solution[i] for k, i in index.items()}


def team_strength(player_ratings, minutes):
    """Points per game above average for ``minutes`` (name key -> minutes); unrated players count as average."""
    return math.fsum(m / 48 * player_ratings.get(k, 0.) for k, m in minutes.items())


def summary_plus_minus(summary):
    """(ESPN game ID, ESPN player ID, plus-minus) for every player with a recorded value."""
    event = 'espn-' + str(summary['header']['id'])
    out = []
    for box in summary['boxscore']['players']:
        for group in box['statistics']:
            for entry in group['athletes']:
                value = dict(zip(group['keys'], entry.get('stats') or [])).get('plusMinus')
                if value not in (None, '', '--'):
                    out.append((event, 'espn-' + str(entry['athlete']['id']), int(value.replace('+', ''))))
    return out


def extract(directory):
    """Plus-minus rows from an ``espn_season`` archive, read offline; each row cites its summary's hash."""
    folder = Path(directory) / 'references'
    rows = []
    for pointer in sorted(folder.glob('*.json')):
        meta = json.loads(pointer.read_text())
        if not meta['source_url'].startswith(BASE + 'summary?event='):
            continue
        raw = (folder / 'raw' / (meta['sha256'] + '.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != meta['sha256']:
            raise ValueError(f"archive hash mismatch for {meta['source_url']}")
        rows.extend(dict(game_id=g, player_id=p, plus_minus=v, source_sha256=meta['sha256'])
                    for g, p, v in summary_plus_minus(json.loads(raw)))
    return sorted(rows, key=lambda r: (r['game_id'], r['player_id']))


def read_plus_minus(paths):
    """(game ID, player ID) -> plus-minus from extracted datasets."""
    out = {}
    for path in paths:
        for r in json.loads(gzip.decompress(Path(path).read_bytes())):
            out[(r['game_id'], r['player_id'])] = r['plus_minus']
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--directory', required=True, help='espn_season raw archive')
    parser.add_argument('--output', required=True, help='Folder for plus-minus.json.gz and manifest.json')
    args = parser.parse_args()
    rows = extract(args.directory)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'plus-minus.json.gz').write_bytes(gzip.compress(canonical(rows), mtime=0))
    manifest = dict(version=VERSION, rows=len(rows), games=len({r['game_id'] for r in rows}),
                    files={'plus-minus.json.gz': hashlib.sha256((out / 'plus-minus.json.gz').read_bytes()).hexdigest()})
    atomic_json(out / 'manifest.json', manifest)
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
