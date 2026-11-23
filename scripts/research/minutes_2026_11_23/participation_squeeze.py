"""Does who plays follow the team's minutes excess or shortfall? Calibration, and a logit shift scored out of season.

    python3 participation_squeeze.py DUMP.jsonl ROWS.json.gz

DUMP is ``dump_roles.py``'s file and ROWS that replay's ``rows.json.gz``. The
probabilities shifted here are the ones before the model's own squeeze
(``P_before_squeeze``). Prints the mean probability beside the share who played by
role level and by the team's expected minutes before the budget; then, for
each shift per twenty minutes of shortfall, the paired change in Brier score
over every projected player and in minutes error (after the share budget) over
those who played, with the shift picked on two seasons and scored on the third.
+- one standard error clustered by game. Standard library only; writes nothing.
"""
import math, statistics, sys

from budget_rules import clustered, load, season, share_rule

SETTLED, LIMIT, MINUTES = .999, 60., 20.   # model.SQUEEZE_*


def shifted(team, logit):
    before = sum(r['P'] * r['role'] for r in team)
    shift = logit * max(-LIMIT, min(LIMIT, 240. - before)) / MINUTES
    out = []
    for r in team:
        p = r['P']
        if 0 < p < SETTLED and (r.get('status_factor') or 1.) >= 1 and not team[0]['short']:
            p = 1. / (1. + math.exp(-(math.log(p / (1. - p)) + shift)))
        out.append(p)
    return out


def main(dump_path, rows_path):
    teams = load(dump_path, rows_path)
    for team in teams.values():
        for r in team:
            r['P'] = r.get('P_before_squeeze', r['P'])
    levels = ((0, 8), (8, 14), (14, 20), (20, 28), (28, 49))
    print('mean probability / share who played (n), by role minutes')
    print('team minutes before the budget | ' + ' | '.join(f'role {lo}-{hi}'.ljust(17) for lo, hi in levels))
    for low, high in ((0, 215), (215, 228), (228, 236), (236, 244), (244, 252), (252, 262), (262, 999)):
        cells = []
        for lo, hi in levels:
            v = [(r['P'], r['played']) for team in teams.values() if low <= sum(x['P'] * x['role'] for x in team) < high
                 for r in team if lo <= r['role'] < hi]
            cells.append(f'{statistics.fmean(p for p, _ in v):.2f} / {statistics.fmean(o for _, o in v):.2f} ({len(v):4d})')
        print(f'  {low:3d}-{high:<3d}                     | ' + ' | '.join(cells))
    base = {key: share_rule(team) for key, team in teams.items()}
    results = {}
    for logit in (.2, .3, .4, .5):
        brier, minutes = [], []
        for key, team in teams.items():
            new_p = shifted(team, logit)
            moved = [dict(r, P=p) for r, p in zip(team, new_p)]
            for r, p, new, old in zip(team, new_p, share_rule(moved), base[key]):
                brier.append((r['g'], season(r['g']), (p - r['played']) ** 2 - (r['P'] - r['played']) ** 2, r['role']))
                if r['played']:
                    minutes.append((r['g'], season(r['g']), abs(new - r['actual']) - abs(old - r['actual']), r['role']))
        results[logit] = (brier, minutes)
        b, m = clustered(brier), clustered(minutes)
        print(f'shift {logit} per {MINUTES:.0f} minutes: Brier {b[0]:+.5f} ± {b[1]:.5f} (n={b[2]}), minutes {m[0]:+.4f} ± {m[1]:.4f} (n={m[2]})')
    for held in range(3):
        pick = min(results, key=lambda k: clustered(results[k][0], lambda s: s != held)[0])
        b, m = clustered(results[pick][0], lambda s: s == held), clustered(results[pick][1], lambda s: s == held)
        print(f'held out season {held}: shift picked on the other two = {pick}; held-out Brier {b[0]:+.5f} ± {b[1]:.5f}, minutes {m[0]:+.4f} ± {m[1]:.4f}')
    level = statistics.fmean((r['P'] - r['played']) ** 2 for team in teams.values() for r in team)
    print(f'Brier level before the shift {level:.4f}; minutes changes are on top of the share budget; ± one standard error clustered by game')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
