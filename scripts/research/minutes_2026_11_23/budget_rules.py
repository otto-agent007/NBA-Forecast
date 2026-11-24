"""Who gives up or takes on minutes when a team's roles do not add to 240, and the budget rule fitted to it.

    python3 budget_rules.py DUMP.jsonl ROWS.json.gz [grid]

DUMP is ``dump_roles.py``'s file and ROWS the ``rows.json.gz`` of the same
replay. Prints, for the players who played: the median of actual minus role
minutes, and of actual minus each rule's minutes, by role level and by the
team's expected minutes before the budget; the
minutes error of the rule used until 2026-11-23 (an excess scales everyone
alike, a shortfall raises everyone in proportion, the rotation by at most a
tenth) and of the share rule in ``model._allocate_team``; and, with ``grid``,
the share rule's constants picked on two seasons and scored on the third.
Differences are paired per player, +- one standard error clustered by game.
Standard library only; reads the two files and writes nothing.
"""
import collections, gzip, json, math, statistics, sys

CEILING, TAPER, FLOOR, FULL_ROLE, MAX_CUT = 42., 30., .15, 8., .5   # model.BUDGET_*


def load(dump_path, rows_path, points=False):
    rows = json.load(gzip.open(rows_path))
    actual = {(r[0], r[1]): dict(zip(rows['player_fields'], r)) for r in rows['players']}
    teams = collections.defaultdict(list)
    for line in open(dump_path):
        r = json.loads(line)
        a = actual[(r['g'], r['pid'])]
        r['played'] = a['actual_status'] == 'played' and a['actual_minutes'] > 0
        r['actual'] = a['actual_minutes']
        if points:
            r['actual_points'] = a['actual_points']
        teams[(r['g'], r['t'])].append(r)
    return teams


def season(game_id):
    return 0 if game_id.startswith('espn-4015') else 1 if game_id.startswith('espn-') else 2


def equal_fill(team):
    """A short bench: the shortfall is shared equally, up to 48 each (both rules)."""
    target = [r['role'] for r in team]
    for _ in range(len(team)):
        shortfall = 240. - sum(r['P'] * t for r, t in zip(team, target))
        open_ = [i for i, r in enumerate(team) if target[i] > 0 and r['P'] > 0 and target[i] < 48. - 1e-9]
        if shortfall <= 1e-9 or not open_:
            break
        share = shortfall / sum(team[i]['P'] for i in open_)
        for i in open_:
            target[i] = min(48., target[i] + share)
    return target


def old_rule(team):
    total = sum(r['P'] * r['role'] for r in team)
    if team[0]['short'] and total < 240.:
        return equal_fill(team)
    scale = min(1., 240. / total) if total else 1.
    target = [r['role'] * scale for r in team]
    for _ in range(4 if len(team) >= 8 else 0):
        shortfall = 240. - sum(r['P'] * t for r, t in zip(team, target))
        ceiling = [min(48., (1.1 if r['core'] else 2.) * r['role']) for r in team]
        open_ = [i for i, r in enumerate(team) if target[i] > 0 and r['P'] > 0 and target[i] < ceiling[i] - 1e-9]
        if shortfall <= 1e-9 or not open_:
            break
        factor = 1. + shortfall / sum(team[i]['P'] * target[i] for i in open_)
        for i in open_:
            target[i] = min(ceiling[i], target[i] * factor)
    return target


def share_rule(team, ceiling=CEILING, taper=TAPER, floor=FLOOR, full_role=FULL_ROLE, max_cut=MAX_CUT):
    total = sum(r['P'] * r['role'] for r in team)
    if team[0]['short'] and total < 240.:
        return equal_fill(team)
    if not total or (total < 240. and len(team) < 8):
        return [r['role'] for r in team]
    sign = -1. if total > 240. else 1.
    share = [min(r['role'] / full_role, 1., max(floor, (ceiling - r['role']) / taper)) for r in team]
    def moved(step):
        return [min(48., max((1. - max_cut) * r['role'], r['role'] + sign * step * s)) if r['role'] > 0 and r['P'] > 0 else r['role']
                for r, s in zip(team, share)]
    expected = lambda step: sum(r['P'] * m for r, m in zip(team, moved(step)))
    low, high = 0., 96.
    if (expected(high) - 240.) * sign >= 0:
        for _ in range(50):
            middle = (low + high) / 2
            if (expected(middle) - 240.) * sign < 0:
                low = middle
            else:
                high = middle
    target = moved(high if sign < 0 or (expected(96.) - 240.) < 0 else low)
    over = sum(r['P'] * t for r, t in zip(team, target))
    return [t * 240. / over for t in target] if over > 240. else target


def paired(teams, rule, base):
    """Per played row: (game, season, abs error under rule - abs error under base, role)."""
    out = []
    for team in teams.values():
        for r, new, old in zip(team, rule(team), base(team)):
            if r['played']:
                out.append((r['g'], season(r['g']), abs(new - r['actual']) - abs(old - r['actual']), r['role']))
    return out


def clustered(rows, keep=lambda s: True):
    total, count = collections.defaultdict(float), collections.Counter()
    for game, s, value, _ in rows:
        if keep(s):
            total[game] += value; count[game] += 1
    n, groups = sum(count.values()), len(total)
    if groups < 2:
        return float('nan'), float('nan'), n
    mean = sum(total.values()) / n
    return mean, math.sqrt(groups / (groups - 1) * sum((total[g] - mean * count[g]) ** 2 for g in total)) / n, n


def main(dump_path, rows_path, grid=False):
    teams = load(dump_path, rows_path)
    before = [sum(r['P'] * r['role'] for r in team) for team in teams.values()]
    print(f'{len(teams)} team-games, {sum(map(len, teams.values()))} projected players; expected minutes before the budget: mean {statistics.fmean(before):.1f}, '
          f'spread {statistics.pstdev(before):.1f}, over 250 in {sum(b > 250 for b in before) / len(before):.0%}, under 230 in {sum(b < 230 for b in before) / len(before):.0%}')
    levels = ((0, 8), (8, 14), (14, 20), (20, 26), (26, 31), (31, 35), (35, 49))
    for title, rule in (('role minutes (what happened to the role)', lambda team: [r['role'] for r in team]),
                        ('the minutes of the rule until 2026-11-23 (its miss)', old_rule), ('the share rule\'s minutes (its miss)', share_rule)):
        print(f'\nmedian of actual - {title}, players who played (n)')
        print('team minutes before the budget | role ' + ' '.join(f'{lo:>3d}-{hi:<8d}' for lo, hi in levels))
        for low, high in ((0, 215), (215, 228), (228, 236), (236, 244), (244, 252), (252, 262), (262, 999)):
            cells = []
            for lo, hi in levels:
                values = [r['actual'] - m for team in teams.values() if low <= sum(x['P'] * x['role'] for x in team) < high
                          for r, m in zip(team, rule(team)) if r['played'] and lo <= r['role'] < hi]
                cells.append(f'{statistics.median(values):+5.1f} ({len(values):4d})' if len(values) >= 40 else ' ' * 12)
            print(f'  {low:3d}-{high:<3d}                     | ' + ' '.join(cells))
    def error(rule):
        return statistics.fmean(abs(m - r['actual']) for team in teams.values() for r, m in zip(team, rule(team)) if r['played'])
    print(f"\nminutes error of players who played: no budget {error(lambda team: [r['role'] for r in team]):.4f}; "
          f'rule until 2026-11-23 {error(old_rule):.4f}; share rule {error(share_rule):.4f}; as replayed {error(lambda team: [r["m"] for r in team]):.4f}')
    rows = paired(teams, share_rule, old_rule)
    mean, se, n = clustered(rows)
    print(f'share rule - old rule: {mean:+.4f} ± {se:.4f} (n={n}); by season ' + ' '.join(f'{clustered(rows, lambda s, k=k: s == k)[0]:+.3f}' for k in range(3)))
    print('  by role minutes: ' + ' '.join(f'[{lo},{hi}) {statistics.fmean(v for _, _, v, role in rows if lo <= role < hi):+.3f}'
                                           for lo, hi in ((0, 10), (10, 20), (20, 28), (28, 34), (34, 60))))
    if grid:
        choices = [(c, t, f) for c in (38., 42., 46.) for t in (20., 30., 40.) for f in (.05, .15, .25)]
        results = {g: paired(teams, lambda team, g=g: share_rule(team, *g), old_rule) for g in choices}
        for held in range(3):
            pick = min(choices, key=lambda g: clustered(results[g], lambda s: s != held)[0])
            mean, se, n = clustered(results[pick], lambda s: s == held)
            print(f'held out season {held}: ceiling, taper, floor picked on the other two = {pick}; held-out change {mean:+.4f} ± {se:.4f} (n={n})')
    print('± one standard error clustered by game')


if __name__ == '__main__':
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], len(sys.argv) == 4)
