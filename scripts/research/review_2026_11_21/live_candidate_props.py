"""A candidate's points on the live Kalshi medians, from two live replays of the same games.

    python3 live_candidate_props.py BASE-rebuilt.json CANDIDATE-rebuilt.json

The two files are ``scripts/research/live_replay.py`` outputs (rebuilt mode) of
the base code and the candidate code on one production copy. For every Kalshi
points median the props report scored (``market/kalshi-props/scored`` under
``NBA_OPERATIONS``), the points each code projected for that player in that
game are scored against what he scored, next to Kalshi's implied median and
what production published. Also: every played, projected player of the
replayed games (points and minutes), and the medians by the base's projected
minutes. ± one standard error clustered by Eastern game date. Reads only.
"""
import collections, glob, json, math, os, pathlib, sys

REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only


def clustered(rows, value):
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r['date']].append(value(r))
    n = sum(len(v) for v in groups.values())
    mean = sum(sum(v) for v in groups.values()) / n
    g = len(groups)
    se = math.sqrt(g / (g - 1) * sum((sum(v) - mean * len(v)) ** 2 for v in groups.values())) / n if g > 1 else float('nan')
    return mean, se, n, g


def players(path):
    out = {}
    for game in json.load(open(path))['games']:
        for p in game.get('players') or ():
            r = p.get('replayed') or {}
            if r.get('predicted_points') is not None:
                out[(game['game_id'], p['player_id'])] = dict(date=game['date'], points=r['predicted_points'], minutes=r['predicted_minutes'],
                    actual_points=p['actual_points'], actual_minutes=p['actual_minutes'], played=p['played'])
    return out


def main(base_path, candidate_path):
    base, cand = players(base_path), players(candidate_path)
    both = [dict(b, c=cand[k]) for k, b in base.items() if k in cand and b['played']]
    show = lambda t: f'{t[0]:+.4f} ± {t[1]:.4f}'
    print(f'played and projected by both codes: {len(both)} player-games, {len({r["date"] for r in both})} dates')
    print('  points abs error, candidate - base ', show(clustered(both, lambda r: abs(r['c']['points'] - r['actual_points']) - abs(r['points'] - r['actual_points']))),
          '| level, base %.3f' % clustered(both, lambda r: abs(r['points'] - r['actual_points']))[0])
    print('  minutes abs error, candidate - base', show(clustered(both, lambda r: abs(r['c']['minutes'] - r['actual_minutes']) - abs(r['minutes'] - r['actual_minutes']))),
          '| level, base %.3f' % clustered(both, lambda r: abs(r['minutes'] - r['actual_minutes']))[0])
    print('  points bias: base %+.3f, candidate %+.3f' % (clustered(both, lambda r: r['points'] - r['actual_points'])[0],
                                                          clustered(both, lambda r: r['c']['points'] - r['actual_points'])[0]))
    medians = []
    for f in sorted(glob.glob(OPERATIONS + '/market/kalshi-props/scored/*.json')):
        d = json.load(open(f))
        if d['entry'].get('scored'):
            medians += d['medians']
    rows = []
    for m in medians:
        k = (m['game_id'], m['player_id'])
        if k in base and k in cand:
            rows.append(dict(m, b=base[k], c=cand[k], date=base[k]['date']))
    print(f'\nKalshi points medians scored: {len(medians)}; replayed by both codes: {len(rows)} on {len({r["date"] for r in rows})} dates')
    if not rows:
        return
    err = lambda r, key: abs(r[key]['points'] - r['actual_points'])

    def table(part, label):
        n = len(part)
        market = clustered(part, lambda r: r['market_abs_error'])[0]
        print(f'  {label} (n={n}): Kalshi median MAE {market:.3f}; published {clustered(part, lambda r: r["model_abs_error"])[0]:.3f}; '
              f'base {clustered(part, lambda r: err(r, "b"))[0]:.3f}; candidate {clustered(part, lambda r: err(r, "c"))[0]:.3f}')
        print('     base - Kalshi      ', show(clustered(part, lambda r: err(r, 'b') - r['market_abs_error'])))
        print('     candidate - Kalshi ', show(clustered(part, lambda r: err(r, 'c') - r['market_abs_error'])))
        print('     candidate - base   ', show(clustered(part, lambda r: err(r, 'c') - err(r, 'b'))))
        side = [r for r in part if r['actual_points'] != r['market_line']]
        right = lambda key: clustered([r for r in side if r[key]['points'] != r['market_line']],
                                      lambda r: float((r[key]['points'] > r['market_line']) == (r['actual_points'] > r['market_line'])))
        print('     side of the median right: base %.1f%% ± %.1f, candidate %.1f%% ± %.1f' % (
            100 * right('b')[0], 100 * right('b')[1], 100 * right('c')[0], 100 * right('c')[1]))
    table(rows, 'all')
    for label, low, high in (('base minutes under 28', 0, 28), ('28-34', 28, 34), ('34 and over', 34, 99)):
        part = [r for r in rows if low <= r['b']['minutes'] < high]
        if part:
            table(part, label)
    print('± one standard error clustered by Eastern game date')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
