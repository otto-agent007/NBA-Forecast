"""Re-score a backtest reference's margins under a different absence rule, without a replay.

The replay locates each margin as (1 - w) * player_sum + w * (edge + R_home - R_away + absence),
absence = -per_minute * (home - away counted minutes of reported-out regulars), w the rating
weight (`margins.blended_location`, `margins.absence_adjustment`). Changing who counts, or for
how much, moves the published margin by exactly w * (absence' - absence), so the reference's
rows can be re-scored for any rule in seconds once each game's reported-out players and their
game logs are known. They are rebuilt here from the gate's own files with the replay's own
functions (`backtest._roster`, `backtest._listed_absentees`, `model._absence`).

    python3 scripts/research/absence_harness.py --reference datasets/backtest-2026-11-07-market-strength
    python3 scripts/research/absence_harness.py --seeded --reference datasets/backtest-2026-11-07-market-strength-early-season
    python3 scripts/research/absence_harness.py --midday --reference datasets/backtest-2026-11-07-market-strength-midday

Proof that the reconstruction is the replay's: `--components RUN/components.json.gz`, the file
`replay_components.py` writes beside an unchanged-code replay, holds each game's player sum,
ratings and absence term; with it the harness prints the largest difference between its own
out players, counted minutes, rating margin and rebuilt location and the replay's (all must
be ~1e-9 or less). `--check-run RUN --variant NAME` compares a replay of an implemented rule
with the harness's prediction for it, game by game.

The reference must have been built with the rule named by `--built-with` (default `current`:
0.1 a minute, 15+ minutes a game played, one of the team's last ten games, the rule of every
reference up to backtest-2026-11-07-market-strength).

The +- printed is ONE date-clustered standard error of the paired change (never below the
independent one, as the gate computes it); the `gate` column is the gate's own verdict for
that metric (two standard errors and the 0.5% materiality floor, `backtest._paired`).
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import fmean

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))
from stats_lite import ols, slope  # noqa: E402
from nba_forecast import backtest as B  # noqa: E402
from nba_forecast import model as M  # noqa: E402
from nba_forecast.injury_history import read_statuses  # noqa: E402
from nba_forecast.margins import PARAMETERS, team_ratings  # noqa: E402
from nba_forecast.market_history import read_lines  # noqa: E402
from nba_forecast.validation import parse_time  # noqa: E402

W = PARAMETERS['rating_weight']
# v4's market-strength half-life and share, for the A3 rule on a v4 reference only: from margin
# method v5 (2026-11-12) the rating reads no market line, so `strength_active` is always False.
HALF_LIFE = PARAMETERS.get('market_strength_half_life_days', 7.)
STRENGTH_SHARE = PARAMETERS.get('market_strength_weight', .7)
MINIMUM_MINUTES = 15.  # the rule every reference so far was built with
# The gate command's files (AGENTS.md), and the seeds of the early-season replay (docs/backtest.md).
SEASONS = (
    dict(label='2023-24', history='espn-season-2023-24/history.json.gz', team_games='espn-season-2023-24/team-games.json.gz',
         previous_history='espn-season-2022-23/history.json.gz', previous_team_games='espn-season-2022-23/team-games.json.gz'),
    dict(label='2024-25', history='espn-season-2024-25/history.json.gz', team_games='espn-season-2024-25/team-games.json.gz',
         previous_history='espn-season-2023-24/history.json.gz', previous_team_games='espn-season-2023-24/team-games.json.gz'),
    dict(label='2025-26', history='regular-season-expanded-2026-10-22/history.json.gz',
         team_games='model-upgrade-2026-10-23/team-games.json.gz',
         previous_history='espn-season-2024-25/history-nba-ids.json.gz',
         previous_team_games='espn-season-2024-25/team-games.json.gz'))
WINDOWS = (('weeks 1-2', 0, 14), ('weeks 3-4', 14, 28), ('weeks 5-8', 28, 56), ('week 9 on', 56, 10 ** 6))


def season_games(datasets, season, *, seeded=False, suffix=''):
    """Every replayed game of one season with its reported-out players' facts, as the replay would see them.

    Mirrors `backtest.replay`: games in order, the history of earlier Eastern days only, the roster
    and statuses of `_roster`, the ratings of the day's first game. Each out player carries what
    `model._absence` records plus his whole log against the team's games (`flags`, oldest first)
    and their ages in days, so any rule can be computed from it.
    """
    datasets = Path(datasets)
    history = B.read_json(datasets / season['history'])
    records = B.read_json(datasets / season['team_games'])
    injuries = read_statuses(datasets / f"injury-reports-{season['label']}{suffix}" / 'statuses.json.gz')
    market = read_lines(datasets / f"market-closing-{season['label']}" / 'closing-lines.json.gz')
    previous_history = B.read_json(datasets / season['previous_history']) if seeded else ()
    previous_records = B.read_json(datasets / season['previous_team_games']) if seeded else ()
    # The closes score the margins (the `gap` column and `worth`); they reach the rating only on v4 code.
    closes = {gid: -line['home_spread'] for gid, line in market.items()
              if isinstance(line, dict) and isinstance(line.get('home_spread'), (int, float))}
    rating_closes = (closes,) if 'market_strength_weight' in PARAMETERS else ()
    labels = {r['season'] for r in history}
    if any(r['season'] in labels for r in previous_history):
        raise ValueError('the seed season shares a season label with the replayed one')
    names = {r['player_id']: r['name'] for r in (*previous_history, *history)}
    by_game = defaultdict(list)
    for r in history:
        by_game[r['game_id']].append(r)
    ordered, evaluated = B.evaluation_games(records, 0. if seeded else .3)
    targets = {g['game_id'] for g in evaluated}
    opening = min(B._day(g['game_time']) for g in ordered)
    finals = [(d, B._final_sample(g)) for d, batch in B._by_day(previous_records, lambda g: g['game_time']) for g in batch]
    player_rows, team_games = defaultdict(list), defaultdict(set)  # the model's `current` and `team_games`, earlier days only
    pending, pending_day, rating_cache, games = [], None, {}, []
    for game in ordered:
        rows, day = by_game.get(game['game_id'], []), B._day(game['game_time'])
        if day != pending_day:
            for r in pending:
                if r['season_type'] == 'Regular Season':
                    player_rows[(r['season'], r['player_id'])].append(r)
                    team_games[(r['season'], r['team_id'])].add((M._time(r['game_time']), r['game_id']))
            pending, pending_day = [], day
        if game['game_id'] in targets and rows:
            report = injuries.get(game['game_id'])
            scored = rows + B._listed_absentees(rows, report, names)
            if day not in rating_cache:
                window = [s for d, s in finals if d < day][-B.MARGIN_WINDOW:]
                edge = fmean(s['home_points'] - s['away_points'] for s in window) if window else None
                rating_cache = {day: (edge, team_ratings(window, game['game_time'], *rating_closes))}
            edge, ratings = rating_cache[day]
            home, away = game['home_team_id'], game['away_team_id']
            rated = home in ratings and away in ratings and edge is not None
            tip = M._time(game['game_time'])
            out = []
            for p in B._roster(scored, report):
                if p['status'] != 'out':
                    continue
                team = p['team_id']
                current = player_rows.get((game['season'], p['player_id']), [])
                schedule = sorted(team_games.get((game['season'], team), ()))
                played = {r['game_id'] for r in current if r['team_id'] == team and r['status'] == 'played' and r['minutes'] > 0}
                out.append(dict(M._absence(current, team, schedule), player_id=p['player_id'], team_id=team,
                                flags=[gid in played for _, gid in schedule],
                                ages=[(tip - t).total_seconds() / 86400. for t, _ in schedule],
                                strength_active=bool(rated and ratings[team].get('market_strength') is not None)))
            games.append(dict(game_id=game['game_id'], season=season['label'], day=day.isoformat(), home=home, away=away,
                              week_days=(day - opening).days, rated=rated, out=out, close=closes.get(game['game_id']),
                              rating_margin=edge + ratings[home]['rating'] - ratings[away]['rating'] if rated else None))
        finals.append((day, B._final_sample(game)))
        pending.extend(rows)
    return games


def eligible(p, minimum=MINIMUM_MINUTES):
    """Today's rule: 15+ minutes a game played for the team this season and one of its last ten games."""
    return bool(p['played_recent_team_game'] and p['minutes_per_game'] is not None and p['minutes_per_game'] >= minimum)


def season_share(p):
    return sum(p['flags']) / len(p['flags']) if p['flags'] else 0.


def recent_share(p, games=10):
    recent = p['flags'][-games:]
    return sum(recent) / len(recent) if recent else 0.


def weighted_share(p, half_life=HALF_LIFE):
    weights = [.5 ** (age / half_life) for age in p['ages']]
    return sum(w for w, f in zip(weights, p['flags']) if f) / sum(weights) if weights else 0.


def rating_share(p):
    """A3: the share of the rating he helped earn; the results part by games, the market part by recency."""
    if not p['strength_active']:
        return season_share(p)
    return (1 - STRENGTH_SHARE) * season_share(p) + STRENGTH_SHARE * weighted_share(p)


def long_absent(p, minimum=MINIMUM_MINUTES):
    """A regular by his minutes who has missed all of the team's last ten games: not counted today."""
    return bool(not p['played_recent_team_game'] and p['minutes_per_game'] is not None and p['minutes_per_game'] >= minimum)


def rule(share=None, per_minute=.1, long_value=0.):
    """(per-minute value, minutes one out player counts for) for a share rule on today's eligibility.

    `long_value` is the per-minute value of a regular who missed the last ten games, whom today's rule leaves out.
    """
    def minutes(p):
        if eligible(p):
            return p['minutes_per_game'] * (share(p) if share else 1.)
        return p['minutes_per_game'] * long_value / per_minute if long_value and long_absent(p) else 0.
    return per_minute, minutes


VARIANTS = {
    'current': rule(),
    'none (term off)': rule(per_minute=0.),
    'A1 season share': rule(season_share),
    'A2 share of last ten': rule(recent_share),
    'A3 rating share': rule(rating_share),
    'A4 0.075 a minute': rule(per_minute=.075),
    'A4 0.05 a minute': rule(per_minute=.05),
}
# Added after the eligibility table (who_counts) showed regulars out for more than ten games still cost
# about a third of a counted minute, on outcomes and on the close: the two variants of our own.
VARIANTS['own: 0.075, long absences 0.035'] = rule(per_minute=.075, long_value=.035)
VARIANTS['own: 0.1, long absences 0.035'] = rule(per_minute=.1, long_value=.035)
# Sizes of today's rule, for re-sizing the term on margin method v5 (no market strength).
for _value in (.025, .125, .15, .2):
    VARIANTS[f'size {_value:g} a minute'] = rule(per_minute=_value)
for _name, _share in (('A1 season share', season_share), ('A2 share of last ten', recent_share), ('A3 rating share', rating_share)):
    for _value in (.075, .125, .15):
        VARIANTS[f'A5 {_name[3:]}, {_value:g}'] = rule(_share, _value)


def counted(game, variant):
    """Home and away counted minutes of one game under a variant."""
    minutes = VARIANTS[variant][1]
    return tuple(sum(minutes(p) for p in game['out'] if p['team_id'] == game[side]) for side in ('home', 'away'))


def adjustment(game, variant):
    home, away = counted(game, variant)
    return -VARIANTS[variant][0] * (home - away)


def margins_for(games, reference, variant, built_with):
    """game_id -> the margin the replay would publish under `variant`, from the reference's margin."""
    return {g['game_id']: reference[g['game_id']]['projected_margin']
            + (W * (adjustment(g, variant) - adjustment(g, built_with)) if g['rated'] else 0.)
            for g in games if g['game_id'] in reference}


def paired(new, old, day, unit=False):
    """The gate's paired comparison: by date for games, two-way by date and team for team rows."""
    cluster = (lambda key: (day[key.partition(':')[0]], key.partition(':')[2])) if unit else (lambda key: (day[key],))
    item = B._paired(new, old, cluster)
    return item['mean'], item['se'], item['verdict']


def bias(new, old, day, unit=False):
    cluster = (lambda key: (day[key.partition(':')[0]], key.partition(':')[2])) if unit else (lambda key: (day[key],))
    item = B._bias(new, old, cluster)
    return item['reference'], item['current'], item['verdict']


def team_points(games, reference, variant, built_with):
    """'game:team' -> (new projected, old projected, actual regulation points) under `variant`.

    The replay scales each team's players toward the location (`scoreline.reconcile`): each
    total moves half the gap, so a location change of d moves the home total by d/2 and the
    away total by -d/2 whenever reconcile applied in the reference (`prove` counts the games
    where it did not: their team totals do not differ by the location).
    """
    out = {}
    for g in games:
        teams = reference[g['game_id']].get('teams') or {}
        if set(teams) != {'home', 'away'}:
            continue
        moved = W * (adjustment(g, variant) - adjustment(g, built_with)) if g['rated'] else 0.
        if abs(teams['home']['projected_points'] - teams['away']['projected_points'] - reference[g['game_id']]['projected_margin']) > 1e-6:
            moved = 0.  # reconcile did not apply in the reference; assumed not to apply under the variant either
        for side, sign in (('home', 1.), ('away', -1.)):
            t = teams[side]
            out[f"{g['game_id']}:{t['team_id']}"] = (t['projected_points'] + sign * moved / 2, t['projected_points'],
                                                    t['actual_points_regulation'])
    return out


def score(games, reference, variant, built_with):
    """One variant against the reference rule on a set of games."""
    games = [g for g in games if g['game_id'] in reference]
    if len(games) < 3:
        return None
    day = {g['game_id']: g['day'] for g in games}
    new, old = margins_for(games, reference, variant, built_with), {g['game_id']: reference[g['game_id']]['projected_margin'] for g in games}
    actual = {g['game_id']: reference[g['game_id']]['actual_margin'] for g in games}
    sd = {g['game_id']: reference[g['game_id']]['prior_sd'] for g in games}
    error = lambda m: {k: abs(m[k] - actual[k]) for k in m}
    brier = lambda m: {k: (B._home_win_probability(m[k], sd[k]) - (actual[k] > 0)) ** 2 for k in m if sd[k]}
    mae = paired(error(new), error(old), day)
    win = paired(brier(new), brier(old), day)
    x = [(counted(g, built_with)[0] - counted(g, built_with)[1]) / 10 for g in games]
    days = [g['day'] for g in games]
    miss = slope(x, [actual[g['game_id']] - new[g['game_id']] for g in games], days) if any(x) else (float('nan'),) * 2
    lined = [g for g in games if g['close'] is not None]
    teams = team_points(games, reference, variant, built_with)
    team_change = paired({k: abs(v[0] - v[2]) for k, v in teams.items()}, {k: abs(v[1] - v[2]) for k, v in teams.items()}, day, unit=True)
    team_bias = bias({k: v[0] - v[2] for k, v in teams.items()}, {k: v[1] - v[2] for k, v in teams.items()}, day, unit=True)
    margin_bias = bias({k: new[k] - actual[k] for k in new}, {k: old[k] - actual[k] for k in old}, day)
    return dict(n=len(games), moved=sum(abs(new[k] - old[k]) > 1e-12 for k in new), mae=fmean(error(new).values()),
                mae_change=mae, brier=fmean(brier(new).values()), brier_change=win, team_change=team_change,
                team_bias=team_bias, margin_bias=margin_bias,
                bias=fmean(new[k] - actual[k] for k in new), slope=miss,
                gap=fmean(abs(new[g['game_id']] - actual[g['game_id']]) - abs(g['close'] - actual[g['game_id']]) for g in lined) if lined else None)


def table(title, games, reference, built_with, variants):
    print(f'\n{title}')
    print(f"  {'rule':34s} {'games':>5s} {'moved':>5s} {'MAE':>7s} {'change':>7s} {'1 se':>6s} {'gate':>12s} "
          f"{'Brier':>7s} {'change':>8s} {'1 se':>7s} {'gate':>12s} {'bias':>6s} {'gap':>6s}  {'miss/10 min':>13s} "
          f"| {'team pts':>8s} {'1 se':>6s} {'gate':>12s} | biases' gate: margin, team points")
    for variant in variants:
        s = score(games, reference, variant, built_with)
        if s is None:
            continue
        print(f"  {variant:34s} {s['n']:5d} {s['moved']:5d} {s['mae']:7.3f} {s['mae_change'][0]:+7.3f} {s['mae_change'][1]:6.3f} "
              f"{s['mae_change'][2]:>12s} {s['brier']:7.4f} {s['brier_change'][0]:+8.4f} {s['brier_change'][1]:7.4f} "
              f"{s['brier_change'][2]:>12s} {s['bias']:+6.2f} {s['gap']:+6.2f}  {s['slope'][0]:+.2f} ± {s['slope'][1]:.2f} "
              f"| {s['team_change'][0]:+8.4f} {s['team_change'][1]:6.4f} {s['team_change'][2]:>12s} "
              f"| {s['margin_bias'][2]}, {s['team_bias'][2]} ({s['team_bias'][0]:+.2f} -> {s['team_bias'][1]:+.2f})")


def worth(title, groups, games, reference, built_with):
    """What ten minutes a game of reported-out players are worth on the published margin, by group of players.

    The margin with the absence term taken out is regressed, home minus away, on each group's
    minutes per game (per ten), all groups in one regression. Close minus that margin is the
    low-noise teacher (docs/beating-the-close-2026-11-06.md), actual minus it the outcome. The
    rule a reference was built with charges every counted group -10 * per-minute * rating weight.
    """
    games = [g for g in games if g['game_id'] in reference and g['rated'] and g['close'] is not None]
    rows, minutes = [], [0.] * len(groups)
    for g in games:
        x = [0.] * len(groups)
        for p in g['out']:
            i = next((i for i, (_, test) in enumerate(groups) if p['minutes_per_game'] is not None and test(p)), None)
            if i is not None:
                x[i] += (1 if p['team_id'] == g['home'] else -1) * p['minutes_per_game'] / 10
                minutes[i] += p['minutes_per_game']
        rows.append(x)
    keep = [i for i, m in enumerate(minutes) if m]
    days = [g['day'] for g in games]
    bare = [reference[g['game_id']]['projected_margin'] - W * adjustment(g, built_with) for g in games]
    targets = (('close - model without the term', [g['close'] - b for g, b in zip(games, bare)]),
               ('actual - model without the term', [reference[g['game_id']]['actual_margin'] - b for g, b in zip(games, bare)]),
               ('actual - close', [reference[g['game_id']]['actual_margin'] - g['close'] for g in games]))
    print(f"\n{title}: points of published margin per ten minutes a game, {len(games)} games with a close "
          f"(the reference's rule charges {-10 * VARIANTS[built_with][0] * W:+.2f} per ten counted minutes)")
    print(f"  {'group':38s} {'min/game':>8s} " + ' '.join(f'{name:>32s}' for name, _ in targets))
    fits = [ols([[1.] + [r[i] for i in keep] for r in rows], y, days) for _, y in targets]
    for column, i in enumerate(keep):
        print(f"  {groups[i][0]:38s} {minutes[i] / len(games):8.2f} "
              + ' '.join(f'{beta[column + 1]:+25.2f} ± {se[column + 1]:.2f}' for beta, se in fits))


def missed_in_a_row(p):
    """Team games he has missed since he last played for it (the whole season for one who has not)."""
    return next((n for n, played in enumerate(reversed(p['flags'])) if played), len(p['flags']))


def who_counts(games, reference, built_with):
    """The diagnostic tables: one group, by share of the season played, by current streak, and who is left out today."""
    counted_and = lambda test: (lambda p: eligible(p) and test(p))
    worth('Every counted minute', [('counted today', eligible)], games, reference, built_with)
    worth("By the player's share of the team's games so far (counted players)",
          [(name, counted_and(lambda p, lo=lo, hi=hi: lo <= season_share(p) < hi)) for name, lo, hi in (
              ('played every team game', 1., 9.), ('75% up to all', .75, 1.), ('50-75%', .5, .75), ('25-50%', .25, .5),
              ('under 25%', 0., .25))], games, reference, built_with)
    worth('By team games missed in a row before tonight (counted players)',
          [(name, counted_and(lambda p, lo=lo, hi=hi: lo <= missed_in_a_row(p) <= hi)) for name, lo, hi in (
              ('played the last game', 0, 0), ('missed 1-2', 1, 2), ('missed 3-5', 3, 5), ('missed 6-9', 6, 9))],
          games, reference, built_with)
    worth('By minutes per game (counted players)',
          [(f'{lo}-{hi} minutes', counted_and(lambda p, lo=lo, hi=hi: lo <= p['minutes_per_game'] < hi))
           for lo, hi in ((15, 20), (20, 25), (25, 30), (30, 34), (34, 60))], games, reference, built_with)
    worth('Who is left out today',
          [('counted today', eligible), ('15+ minutes, missed the last ten', long_absent),
           ('10-15 minutes, one of the last ten', lambda p: p['played_recent_team_game'] and 10 <= p['minutes_per_game'] < 15),
           ('under 10 minutes, one of the last ten', lambda p: p['played_recent_team_game'] and p['minutes_per_game'] < 10)],
          games, reference, built_with)


def prove(games, reference, components, built_with):
    """Largest differences between the harness's reconstruction and what the replay itself computed."""
    dumped = {g['game_id']: g for g in B.read_json(components)['games']}
    worst = defaultdict(float)
    mismatched = unreconciled = 0
    for g in games:
        d = dumped.get(g['game_id'])
        if d is None or g['game_id'] not in reference:
            mismatched += 1
            continue
        mine = {p['player_id']: p for p in g['out']}
        theirs = {p['player_id']: p for p in d['out']}
        if set(mine) != set(theirs):
            mismatched += 1
            continue
        for pid, p in mine.items():
            q = theirs[pid]
            if (p['team_games_played'], p['played_recent_team_game'], p['recent_team_games'], p['team_id']) != (
                    q['team_games_played'], q['played_recent_team_game'], q['recent_team_games'], q['team_id']):
                mismatched += 1
            if p['minutes_per_game'] is not None or q['minutes_per_game'] is not None:
                worst['minutes per game'] = max(worst['minutes per game'], abs((p['minutes_per_game'] or 0) - (q['minutes_per_game'] or 0)))
        home, away = counted(g, built_with)
        worst['counted minutes'] = max(worst['counted minutes'], abs(home - d['absent_minutes']['home']), abs(away - d['absent_minutes']['away']))
        worst['absence adjustment'] = max(worst['absence adjustment'], abs(adjustment(g, built_with) - d['absence_adjustment']))
        if (d['rating_margin'] is None) != (not g['rated']):
            mismatched += 1
            continue
        location = d['player_sum']
        if g['rated']:
            rating = g['rating_margin'] + adjustment(g, built_with)
            worst['rating margin'] = max(worst['rating margin'], abs(rating - d['rating_margin']))
            location = (1 - W) * d['player_sum'] + W * rating
        worst['location vs the replay'] = max(worst['location vs the replay'], abs(location - d['location']))
        worst['location vs the reference rows'] = max(worst['location vs the reference rows'],
                                                      abs(location - reference[g['game_id']]['projected_margin']))
        teams = reference[g['game_id']].get('teams') or {}
        if set(teams) == {'home', 'away'} and g['rated']:
            gap = abs(teams['home']['projected_points'] - teams['away']['projected_points'] - location)
            unreconciled += gap > 1e-6
            if gap <= 1e-6:
                worst['team totals vs the location (reconciled games)'] = max(
                    worst['team totals vs the location (reconciled games)'], gap)
    print(f"\nProof against {components}: {len(games)} harness games, {len(dumped)} replayed, "
          f"{len(reference)} reference rows; games or players that do not match: {mismatched}")
    for name in ('minutes per game', 'counted minutes', 'absence adjustment', 'rating margin', 'location vs the replay',
                 'location vs the reference rows', 'team totals vs the location (reconciled games)'):
        print(f'  largest absolute difference, {name}: {worst[name]:.3e}')
    print(f'  rated games whose team totals do not differ by the location (reconcile did not apply; their team '
          f'points are left unchanged by the harness, a small approximation): {unreconciled}')
    return mismatched, max(worst.values()) if worst else float('nan')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--datasets', type=Path, default=HERE.parent.parent / 'datasets')
    parser.add_argument('--seeded', action='store_true', help='the early-season replay: previous season seeded, no warm-up')
    parser.add_argument('--midday', action='store_true', help='the preliminary replay: midday injury reports')
    parser.add_argument('--built-with', default='current', help='the rule the reference was built with')
    parser.add_argument('--components', type=Path, help='components.json.gz of an unchanged-code replay (replay_components.py)')
    parser.add_argument('--check-run', type=Path, help='a replay of an implemented rule to compare with the prediction')
    parser.add_argument('--variant', help='the rule --check-run implements')
    parser.add_argument('--only', nargs='+', help='print these rules only')
    parser.add_argument('--facts', type=Path, help='write every game\'s out players and counted minutes here (json.gz)')
    args = parser.parse_args()
    board = B.read_json(args.reference / 'rows.json.gz')
    reference = {g[0]: dict(zip(board['game_fields'], g)) for g in board['games']}
    for row in board['teams']:
        t = dict(zip(board['team_fields'], row))
        if t['game_id'] in reference and t['projected_players'] > 0 and t['actual_points_regulation'] is not None:
            reference[t['game_id']].setdefault('teams', {})['home' if t['home'] else 'away'] = t
    games = [g for season in SEASONS for g in season_games(args.datasets, season, seeded=args.seeded,
                                                           suffix='-midday' if args.midday else '')]
    variants = args.only or list(VARIANTS)
    scored = [g for g in games if g['game_id'] in reference]
    print(f"{len(games)} games rebuilt, {len(scored)} of the reference's {len(reference)} rows; rating weight {W}; "
          f"reference built with '{args.built_with}'")
    out = [p for g in scored for p in g['out']]
    counting = [p for p in out if eligible(p)]
    total = sum(p['minutes_per_game'] for p in counting)
    print(f"reported-out players {len(out)}, counted today {len(counting)} ({total / len(scored):.1f} minutes a game); "
          f"share of the counted minutes from players who played every team game so far "
          f"{sum(p['minutes_per_game'] for p in counting if all(p['flags'])) / total:.0%}, "
          f"at least three quarters {sum(p['minutes_per_game'] for p in counting if season_share(p) >= .75) / total:.0%}, "
          f"under half {sum(p['minutes_per_game'] for p in counting if season_share(p) < .5) / total:.0%}")
    if args.components:
        prove(games, reference, args.components, args.built_with)
    if args.check_run:
        run = B.read_json(args.check_run / 'rows.json.gz')
        theirs = {g[0]: dict(zip(run['game_fields'], g))['projected_margin'] for g in run['games']}
        mine = margins_for(games, reference, args.variant, args.built_with)
        shared = sorted(set(mine) & set(theirs))
        print(f"\n{args.check_run} against the harness's '{args.variant}': {len(shared)} games in both "
              f"({len(theirs)} replayed, {len(mine)} predicted); largest absolute margin difference "
              f"{max(abs(mine[k] - theirs[k]) for k in shared):.3e}")
    table('Pooled', games, reference, args.built_with, variants)
    for season in SEASONS:
        table(season['label'], [g for g in games if g['season'] == season['label']], reference, args.built_with, variants)
    if args.seeded:
        for name, lo, hi in WINDOWS:
            table(f'{name} since opening night', [g for g in games if lo <= g['week_days'] < hi], reference, args.built_with, variants)
    who_counts(games, reference, args.built_with)
    if args.seeded:
        for name, lo, hi in (('Weeks 1-8', 0, 56), ('Week 9 on', 56, 10 ** 6)):
            subset = [g for g in games if lo <= g['week_days'] < hi]
            worth(f'{name} since opening night, every counted minute', [('counted today', eligible)], subset, reference, args.built_with)
            worth(f'{name}, by team games missed in a row (counted players)',
                  [(label, lambda p, a=a, b=b: eligible(p) and a <= missed_in_a_row(p) <= b)
                   for label, a, b in (('played the last game', 0, 0), ('missed 1-2', 1, 2), ('missed 3-9', 3, 9))],
                  subset, reference, args.built_with)
    if args.facts:
        import gzip
        args.facts.write_bytes(gzip.compress(json.dumps(dict(games=[dict(g, counted={v: counted(g, v) for v in VARIANTS})
                                                                     for g in scored]), separators=(',', ':')).encode(), mtime=0))


if __name__ == '__main__':
    main()
