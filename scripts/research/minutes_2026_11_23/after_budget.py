"""The terms applied to a player's minutes after the budget: each one's error and bias, and its constants out of season.

    python3 after_budget.py DUMP.jsonl ROWS.json.gz [grid]

DUMP is ``dump_roles.py``'s file from a replay of the current code and ROWS that
replay's ``rows.json.gz``. The budgeted minutes are recomputed from the dump's
role minutes and probabilities (``budget_rules.share_rule``); the three terms of
``model._typical_minutes``, and two that were measured and left out (a pull
toward the season average, a deep-bench cut), are then applied here, and
scored on the players who played: minutes absolute error, points absolute
error (the published points scaled by the minutes, first order) and the mean
points bias, each against the budgeted minutes with no term. With ``grid``
every term's constants are picked on two seasons with the other terms in
place and scored on the third. +- one standard error clustered by game.
Standard library only; reads the two files and writes nothing.
"""
import statistics, sys

from budget_rules import clustered, load, season, share_rule

TOP, RETURN, STARTED = (36., .5), (.4, 2., 20., 3), (.6, -.2, 3)   # model.TYPICAL_*
SEASON = (0., .5, 5)   # a pull toward the season average: measured here, not in the model (it moves the mean, see docs)
BENCH = (0., .95, 10.)   # a cut for deep-bench roles as participation falls under .95: measured here, not in the model


def terms(r, minutes, top=TOP, back=RETURN, mean=SEASON, started=STARTED, bench=BENCH):
    games, missed = r.get('games') or 0, r.get('missed') or 0
    out = {'top': -top[1] * max(0., minutes - top[0]), 'returning': 0., 'season': 0., 'started': 0.,
           'bench': -bench[0] * min(1., max(0., (bench[1] - r['P']) / bench[1])) * min(minutes, bench[2]) / bench[2]}
    if games >= back[3] and missed:
        out['returning'] = -(back[0] if missed == 1 else back[1]) * min(minutes, back[2]) / back[2]
    if mean[0] and r.get('season_mean') is not None and games >= mean[2]:
        out['season'] = max(-mean[1], min(mean[1], mean[0] * (r['season_mean'] - minutes)))
    if games >= started[2]:
        out['started'] = started[0] if r.get('started') is True else started[1]
    return out


def apply(team, budgeted, use, **constants):
    if team[0]['short']:
        return budgeted
    new = [b if r.get('manual') else min(48., max(0., b + sum(v for k, v in terms(r, b, **constants).items() if k in use)))
           for r, b in zip(team, budgeted)]
    over = sum(r['P'] * m for r, m in zip(team, new))
    return [m * 240. / over for m in new] if over > 240. else new


def score(teams, budgets, use, **constants):
    """Per played row: (game, season, minutes change, role), the same for points, and the signed points errors."""
    minutes, points, signed = [], [], []
    for key, team in teams.items():
        for r, b, m in zip(team, budgets[key], apply(team, budgets[key], use, **constants)):
            if not r['played']:
                continue
            rate = r['points'] / r['m'] if r['m'] else 0.
            minutes.append((r['g'], season(r['g']), abs(m - r['actual']) - abs(b - r['actual']), b))
            points.append((r['g'], season(r['g']), abs(rate * m - r['actual_points']) - abs(rate * b - r['actual_points']), b))
            signed.append((rate * b - r['actual_points'], rate * m - r['actual_points'], b))
    return minutes, points, signed


def main(dump_path, rows_path, grid=False):
    teams = load(dump_path, rows_path, points=True)
    budgets = {key: share_rule(team) for key, team in teams.items()}
    names = ('top', 'returning', 'started')
    print('against the budgeted minutes with no term, players who played')
    for label, use, extra in [(n, {n}, {}) for n in names] + [('all three', set(names), {}), ('+ season', set(names) | {'season'}, dict(mean=(.3, .5, 5))),
                              ('+ bench', set(names) | {'bench'}, dict(bench=(4., .95, 10.)))]:
        minutes, points, signed = score(teams, budgets, use, **extra)
        m, p = clustered(minutes), clustered(points)
        high = [(a, b) for a, b, level in signed if level >= 34]
        print(f'{label:10s} minutes {m[0]:+.4f} ± {m[1]:.4f} | points {p[0]:+.4f} ± {p[1]:.4f} | points bias {statistics.fmean(a for a, _, _ in signed):+.3f} -> '
              f'{statistics.fmean(b for _, b, _ in signed):+.3f}; at 34+ budgeted minutes {statistics.fmean(a for a, _ in high):+.2f} -> {statistics.fmean(b for _, b in high):+.2f} (n={len(high)})')
    if grid:
        choices = {'top': [dict(top=(knee, slope)) for knee in (34., 35., 36.) for slope in (.35, .5, .65)],
                   'returning': [dict(back=(one, more, 20., 3)) for one in (0., .4, .8) for more in (1., 1.5, 2., 2.5)],
                   'started': [dict(started=(up, down, 3)) for up in (.3, .5, .7) for down in (0., -.2, -.4)]}
        for name, options in choices.items():
            results = [(option, score(teams, budgets, set(names), **option)[0]) for option in options]
            for held in range(3):
                option, rows = min(results, key=lambda item: clustered(item[1], lambda s: s != held)[0])
                held_out = clustered(rows, lambda s: s == held)
                print(f'{name}: held out season {held}: picked {list(option.values())[0]} on the other two; the terms then score {held_out[0]:+.4f} ± {held_out[1]:.4f} minutes')
    print('± one standard error clustered by game')


if __name__ == '__main__':
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], len(sys.argv) == 4)
