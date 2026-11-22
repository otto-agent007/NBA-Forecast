"""Read gate runs side by side, and pair two of them player by player.

    python3 gate_variants.py table NAME=OUT/scoreboard.json [NAME=...]
    python3 gate_variants.py pair BASE/rows.json.gz CANDIDATE/rows.json.gz

``table`` prints each scoreboard's own comparison with its reference: the mean
paired change, ± TWO clustered standard errors as the gate prints them, and the
gate's verdict. ``pair`` compares two replays of the same games directly
(candidate minus base, players who played and were projected in both): points
and minutes absolute error and the points bias, overall and by the base's
projected minutes, ± ONE standard error clustered by game; team points too.
Standard library only; reads the files given and writes nothing.
"""
import collections, gzip, json, math, sys


def table(specs):
    names = ('points', 'minutes', 'team_points', 'margin', 'winner', 'interval_score')
    print(f"{'run':22s} {'verdict':13s} " + ' '.join(f'{n:>22s}' for n in names) + '   points bias      team-points bias')
    for spec in specs:
        name, path = spec.split('=', 1)
        c = json.load(open(path))['comparison']
        cells = []
        for n in names:
            m = c[n]
            mark = {'improved': '+', 'worse': '!', 'within_noise': ' '}[m['verdict']]
            cells.append(f"{m['mean']:+9.4f} ± {2 * m['se']:.4f}{mark}".rjust(22))
        bias = lambda k: f"{c[k]['reference']:+.2f} -> {c[k]['current']:+.2f}"
        print(f"{name:22s} {c['verdict']:13s} " + ' '.join(cells) + f"   {bias('points_bias'):16s} {bias('team_points_bias')}")
    print('+ improved, ! worse (two standard errors and the 0.5% floor); the reference is the one each run names')


def clustered(rows, value):
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r['game_id']].append(value(r))
    n = sum(len(v) for v in groups.values())
    mean = sum(sum(v) for v in groups.values()) / n
    g = len(groups)
    return mean, math.sqrt(g / (g - 1) * sum((sum(v) - mean * len(v)) ** 2 for v in groups.values())) / n, n


def load(path):
    d = json.load(gzip.open(path))
    players = {(r[0], r[1]): dict(zip(d['player_fields'], r)) for r in d['players']}
    teams = {(r[0], r[1]): dict(zip(d['team_fields'], r)) for r in d['teams']}
    return players, teams


def pair(base_path, candidate_path):
    base, base_teams = load(base_path)
    cand, cand_teams = load(candidate_path)
    rows = []
    for key, b in base.items():
        c = cand.get(key)
        if c and b['actual_status'] == 'played' and b['points_mean'] is not None and c['points_mean'] is not None:
            rows.append(dict(game_id=key[0], b=b, c=c))
    print(f'players who played, projected in both: {len(rows)} (base {len(base)}, candidate {len(cand)} rows)')
    ae = lambda r, k, a: abs(r['c'][k] - r['c'][a]) - abs(r['b'][k] - r['b'][a])
    buckets = [('all', 0, 99), ('0-10', 0, 10), ('10-20', 10, 20), ('20-30', 20, 30), ('30-34', 30, 34), ('34+', 34, 99)]
    print(f"{'base minutes':12s} {'n':>6s} {'points abs error':>18s} {'minutes abs error':>18s} {'points bias, base -> candidate':>32s}")
    for name, low, high in buckets:
        part = [r for r in rows if low <= r['b']['expected_minutes'] < high]
        if not part:
            continue
        p = clustered(part, lambda r: ae(r, 'points_mean', 'actual_points'))
        m = clustered(part, lambda r: ae(r, 'expected_minutes', 'actual_minutes'))
        bb = clustered(part, lambda r: r['b']['points_mean'] - r['b']['actual_points'])[0]
        cb = clustered(part, lambda r: r['c']['points_mean'] - r['c']['actual_points'])[0]
        print(f'{name:12s} {p[2]:6d} {p[0]:+9.4f} ± {p[1]:.4f} {m[0]:+9.4f} ± {m[1]:.4f} {bb:+15.2f} -> {cb:+.2f}')
    teams = [dict(game_id=k[0], b=b, c=cand_teams[k]) for k, b in base_teams.items() if k in cand_teams]
    t = clustered(teams, lambda r: abs(r['c']['projected_points'] - r['c']['actual_points_regulation'])
                  - abs(r['b']['projected_points'] - r['b']['actual_points_regulation']))
    tb = clustered(teams, lambda r: r['b']['projected_points'] - r['b']['actual_points_regulation'])[0]
    tc = clustered(teams, lambda r: r['c']['projected_points'] - r['c']['actual_points_regulation'])[0]
    print(f'team points abs error {t[0]:+.3f} ± {t[1]:.3f} (n={t[2]}); bias {tb:+.2f} -> {tc:+.2f}')
    print('candidate minus base, ± one standard error clustered by game')


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'table':
        table(sys.argv[2:])
    elif len(sys.argv) == 4 and sys.argv[1] == 'pair':
        pair(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
