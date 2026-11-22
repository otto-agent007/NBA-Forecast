"""A candidate's game totals on the live games, from two live replays of the same games.

    python3 live_candidate_totals.py BASE-rebuilt.json CANDIDATE-rebuilt.json

The two files are ``scripts/research/live_replay.py`` outputs (rebuilt mode) of
the base code and the candidate code on one production copy. For every game
both replayed that has a final and a closing total: the bias (forecast minus
the final total, overtime included) and the absolute error of each code's
total and of the closing total, and the paired differences, overall and before
and from 2026-11-10 (the date the published totals began to run low). ± one
standard error clustered by Eastern game date. Reads only.
"""
import collections, json, math, sys


def clustered(rows, value):
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r['date']].append(value(r))
    n = sum(len(v) for v in groups.values())
    mean = sum(sum(v) for v in groups.values()) / n
    g = len(groups)
    se = math.sqrt(g / (g - 1) * sum((sum(v) - mean * len(v)) ** 2 for v in groups.values())) / n if g > 1 else float('nan')
    return mean, se


def games(path):
    out = {}
    for g in json.load(open(path))['games']:
        total = (g.get('replayed') or {}).get('total')
        actual = (g.get('actual') or {}).get('total')
        line = next((c['total_line'] for c in g.get('closes') or () if c.get('total_line') is not None), None)
        if total is not None and actual is not None and line is not None:
            out[g['game_id']] = dict(date=g['date'], total=total, actual=actual, line=line, overtime=bool(g['actual'].get('overtime')))
    return out


def main(base_path, candidate_path):
    base, cand = games(base_path), games(candidate_path)
    rows = [dict(b, c=cand[k]['total']) for k, b in base.items() if k in cand]
    show = lambda t: f'{t[0]:+.2f} ± {t[1]:.2f}'
    for label, part in (('all', rows), ('before 2026-11-10', [r for r in rows if r['date'] < '2026-11-10']),
                        ('2026-11-10 on', [r for r in rows if r['date'] >= '2026-11-10'])):
        if not part:
            continue
        dates = len({r['date'] for r in part})
        print(f'{label}: {len(part)} games with a final and a closing total, {dates} dates, {sum(r["overtime"] for r in part)} with overtime')
        print('  bias (forecast - final):  base', show(clustered(part, lambda r: r['total'] - r['actual'])),
              '| candidate', show(clustered(part, lambda r: r['c'] - r['actual'])),
              '| close', show(clustered(part, lambda r: r['line'] - r['actual'])))
        print('  absolute error:           base %.2f | candidate %.2f | close %.2f' % (
            clustered(part, lambda r: abs(r['total'] - r['actual']))[0], clustered(part, lambda r: abs(r['c'] - r['actual']))[0],
            clustered(part, lambda r: abs(r['line'] - r['actual']))[0]))
        print('  candidate - base         ', show(clustered(part, lambda r: abs(r['c'] - r['actual']) - abs(r['total'] - r['actual']))))
        print('  base - close             ', show(clustered(part, lambda r: abs(r['total'] - r['actual']) - abs(r['line'] - r['actual']))))
        print('  candidate - close        ', show(clustered(part, lambda r: abs(r['c'] - r['actual']) - abs(r['line'] - r['actual']))))
        print('  forecast - closing total: base', show(clustered(part, lambda r: r['total'] - r['line'])),
              '| candidate', show(clustered(part, lambda r: r['c'] - r['line'])))
    print('± one standard error clustered by Eastern game date')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
