"""The two gated paths side by side, from their committed scoreboards.

    python3 scripts/research/path_scoreboards.py \
        --baseline datasets/backtest-2026-11-07-market-strength \
        --production datasets/backtest-2026-11-09-production-path

Prints the headline level of each path (pooled and per season), the model
against the closing line on the same games, and the paired difference
production minus baseline exactly as the gate computed it when the production
scoreboard was replayed against the baseline one (its `comparison` block).
Every figure is read from `scoreboard.json`; `rows.json.gz` is read to
confirm that the two replays scored the same games, teams and player-games,
and for the winner Brier table: the replay scores the home-win probability
P(margin > 0.5) under the normal, while the distribution production publishes
(`margins.build_margin_forecasts`) leaves out a zero margin and renormalises,
so at the same location and scale it always gives the home side more; that
table rescores each path's game rows both ways.
Lower is better for every error, Brier and interval score; a negative
difference favours the production path. ± is two standard errors, clustered as
the gate clusters them (date and player, date and team, date).
"""
import argparse
import gzip
import json
import math
from pathlib import Path

LEVELS = (
    ('player points MAE', lambda m: m['players']['all']['points_mae'], '.3f'),
    ('player points bias', lambda m: m['players']['all']['points_bias'], '+.3f'),
    ('minutes MAE', lambda m: m['players']['all']['minutes_mae'], '.3f'),
    ('minutes bias', lambda m: m['players']['all']['minutes_bias'], '+.3f'),
    ('team points MAE', lambda m: m['teams']['points_mae'], '.3f'),
    ('team points bias', lambda m: m['teams']['points_bias'], '+.3f'),
    ('margin MAE', lambda m: m['games']['margin_mae'], '.3f'),
    ('margin bias (home)', lambda m: m['games']['margin_bias_home'], '+.3f'),
    ('winner Brier', lambda m: m['games']['probabilities']['winner_brier'], '.4f'),
    ('winner accuracy', lambda m: m['games']['winner_accuracy'], '.4f'),
    ('participation Brier', lambda m: m['players']['participation']['brier'], '.4f'),
    ('mean P(plays)', lambda m: m['players']['participation']['mean_probability'], '.4f'),
    ('share who played', lambda m: m['players']['participation']['played_share'], '.4f'),
    ('80% interval score', lambda m: m['players']['all']['interval_score80'], '.3f'),
    ('80% coverage', lambda m: m['players']['all']['coverage80'], '.4f'),
)
MARKET = (
    ('games with a closing line', lambda k: k['n'], 'd'),
    ('model margin MAE', lambda k: k['model']['margin_mae'], '.3f'),
    ('close margin MAE', lambda k: k['market']['margin_mae'], '.3f'),
    ('model minus close', lambda k: k['model']['margin_mae'] - k['market']['margin_mae'], '+.3f'),
    ('model winner Brier', lambda k: k['model']['winner_brier'], '.4f'),
    ('close winner Brier', lambda k: k['market']['winner_brier'], '.4f'),
    ('model team points MAE', lambda k: k['team_points']['model_mae'], '.3f'),
    ('close team points MAE', lambda k: k['team_points']['market_mae'], '.3f'),
)
PAIRED = ('points', 'minutes', 'team_points', 'margin', 'participation', 'winner', 'interval_score')
BIASES = ('points_bias', 'team_points_bias', 'margin_bias')


def normal_cdf(z):
    return .5 * math.erfc(-z / math.sqrt(2.))


def published_home_probability(location, sd):
    """P(home wins) under margins._pmf: the discrete normal without the zero cell, renormalised.

    The pmf's endpoint floors (1e-6 a side) are left out; they move the probability
    by less than 1e-6.
    """
    above = 1. - normal_cdf((.5 - location) / sd)
    zero = normal_cdf((.5 - location) / sd) - normal_cdf((-.5 - location) / sd)
    return above / (1. - zero)


def read(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)


def scopes(board):
    """(label, metrics, games evaluated, comparison) for the pooled seasons and each season."""
    out = [('pooled', board['metrics'], board['games']['evaluated'], board.get('comparison'))]
    for part in board.get('seasons') or []:
        out.append((part['label'], part['metrics'], part['games']['evaluated'], part.get('comparison')))
    return out


def table(header, rows):
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    line = lambda r: '| ' + ' | '.join(str(v).ljust(w) if i == 0 else str(v).rjust(w) for i, (v, w) in enumerate(zip(r, widths))) + ' |'
    print(line(header))
    print('|' + '|'.join('-' * (w + 2) for w in widths) + '|')
    for r in rows:
        print(line(r))
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--baseline', required=True, help='Baseline-path scoreboard directory')
    parser.add_argument('--production', required=True, help='Production-path scoreboard directory')
    args = parser.parse_args()
    boards = {name: read(Path(folder) / 'scoreboard.json') for name, folder in (('baseline', args.baseline), ('production', args.production))}
    for name, board in boards.items():
        path = board.get('model_path', 'baseline')
        if path != name:
            raise SystemExit(f'{name}: the scoreboard is a {path}-path replay')
        artifact = board['parameters'].get('production_artifact') or {}
        print(f"{name:10s} {getattr(args, name)}: model {board['model_version']}, code {board['code_sha256'][:12]}, created {board['created_at'][:19]}, "
              f"games {board['games']['evaluated']} of {board['games']['total']}"
              + (f", artifact model key {artifact['model_key'][:12]} parameters {json.dumps(artifact['parameters'], sort_keys=True)}" if artifact else ''))
    same_sources = boards['baseline']['sources'] == boards['production']['sources']
    rows = {name: read(Path(getattr(args, name)) / 'rows.json.gz') for name in boards}
    keys = {name: dict(players={(r[0], r[1]) for r in rows[name]['players']}, teams={(r[0], r[1]) for r in rows[name]['teams']},
                       games={r[0] for r in rows[name]['games']}) for name in boards}
    same_rows = {kind: keys['baseline'][kind] == keys['production'][kind] for kind in ('players', 'teams', 'games')}
    print(f"same input files (path and sha256 of every history, team-games, injuries and market file): {same_sources}")
    print('same rows scored: ' + ', '.join(f"{kind} {same_rows[kind]} ({len(keys['production'][kind])})" for kind in same_rows))
    print()

    both = {name: scopes(board) for name, board in boards.items()}
    labels = [label for label, *_ in both['baseline']]
    if labels != [label for label, *_ in both['production']]:
        raise SystemExit('the two scoreboards cover different seasons')
    header = ['metric'] + [f'{label} {name}' for label in labels for name in ('baseline', 'production')]
    print('Levels (lower is better for errors, Brier and interval score; bias is forecast minus actual)')
    body = [['games evaluated'] + [str(both[name][i][2]) for i in range(len(labels)) for name in ('baseline', 'production')]]
    for title, get, fmt in LEVELS:
        body.append([title] + [format(get(both[name][i][1]), fmt) for i in range(len(labels)) for name in ('baseline', 'production')])
    table(header, body)

    if all('market' in metrics for name in both for _, metrics, _, _ in both[name]):
        print('Against the closing line, on the replayed games that have one (model minus close: positive = the model trails the close)')
        table(header, [[title] + [format(get(both[name][i][1]['market']), fmt) for i in range(len(labels)) for name in ('baseline', 'production')]
                       for title, get, fmt in MARKET])

    print("Winner Brier with the replay's home-win probability and with the one production publishes, from the game rows")
    repository = Path(__file__).resolve().parents[2]
    body, columns = {}, []
    for label in labels:
        for name in ('baseline', 'production'):
            board = boards[name]
            parts = board.get('seasons') or [dict(label='pooled', sources=board['sources'])]
            wanted = set()
            for part in parts:
                if label in ('pooled', part['label']):
                    wanted |= {r['game_id'] for r in read(repository / part['sources']['team_games']['path'])}
            games = [dict(zip(rows[name]['game_fields'], r)) for r in rows[name]['games']]
            games = [r for r in games if r['game_id'] in wanted and r.get('prior_sd')]
            won = [float(r['actual_margin'] > 0) for r in games]
            replay = [r['home_win_probability'] for r in games]
            published = [published_home_probability(r['projected_margin'], r['prior_sd']) for r in games]
            values = {'games': len(games),
                      "winner Brier, replay's P(margin > 0.5)": sum((p - y) ** 2 for p, y in zip(replay, won)) / len(games),
                      'winner Brier, published (zero excluded)': sum((p - y) ** 2 for p, y in zip(published, won)) / len(games),
                      'published minus replay': (sum((p - y) ** 2 for p, y in zip(published, won))
                                                 - sum((p - y) ** 2 for p, y in zip(replay, won))) / len(games),
                      'mean P(home), replay': sum(replay) / len(games),
                      'mean P(home), published': sum(published) / len(games),
                      'mean P(home), published minus replay': (sum(published) - sum(replay)) / len(games),
                      'home won share': sum(won) / len(won)}
            for key, value in values.items():
                body.setdefault(key, []).append(str(value) if key == 'games' else format(value, '+.4f' if 'minus' in key else '.4f'))
            columns.append(f'{label} {name}')
    table(['metric'] + columns, [[key] + cells for key, cells in body.items()])

    comparison = boards['production'].get('comparison')
    against = comparison and (comparison.get('reference_created_at'), comparison.get('reference_code_sha256'))
    if against != (boards['baseline']['created_at'], boards['baseline']['code_sha256']):
        print('paired differences: not printed, the production scoreboard was not replayed against this baseline scoreboard'
              + (f" (its reference: {comparison['reference']})" if comparison else ''))
        return
    print(f"Production path minus baseline, paired on the same rows (the production scoreboard's comparison with {comparison['reference']})")
    print('mean difference ± two clustered standard errors [materiality floor] verdict; negative = production path better')
    header = ['metric'] + labels
    body = []
    for name in PAIRED:
        cells = []
        for _, _, _, c in both['production']:
            item = c[name]
            direction = 'production better' if item['mean'] < 0 else 'production worse' if item['mean'] > 0 else 'equal'
            cells.append(f"{item['mean']:+.4f} ± {2 * item['se']:.4f} [{item['materiality_floor']:.4f}] {item['verdict']} ({direction}, n={item['n']})")
        body.append([name] + cells)
    for name in BIASES:
        cells = []
        for _, _, _, c in both['production']:
            item = c[name]
            cells.append(f"{item['reference']:+.3f} -> {item['current']:+.3f} (se {item['se']:.3f}) {item['verdict']}")
        body.append([name + ' (baseline -> production)'] + cells)
    body.append(['verdict'] + [c['verdict'] for _, _, _, c in both['production']])
    table(header, body)


if __name__ == '__main__':
    main()
