"""Can the model beat the close, and what does the close say it is missing?

    python3 scripts/research/close_teacher.py --reference datasets/backtest-2026-11-06-rating-weight \
        [--live data/operations/production/market/report.json] [--archive ../research_data/.../all_games.json]

On a backtest reference's games with a closing line it prints: whether the
model's disagreements with the close predict the close's misses (the slope of
actual - close on model - close, and the best blend weight), by size of the
disagreement and by month; the "teacher" regressions of close - model,
actual - model and actual - close on schedule and standings features the
model can see (the first has a sixteenth of the outcome noise); how much of
the disagreement is a persistent per-team view and whether that view is
informative ahead of time; and a team rating fitted to prior closing spreads
only, as a strength backbone, alone and blended with the model. With --live it
repeats the headline figures on the production market report, and with
--archive, the 2007-2026 closing-line archive of the research notes, it asks
whether any of the same situations beat the close over nineteen seasons and
how much the close improves on the opener. These are the numbers behind
docs/beating-the-close-2026-11-06.md.
"""
import argparse
from collections import defaultdict
import datetime
import gzip
import json
import math
from pathlib import Path
from statistics import fmean
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats_lite import corr, ols, sd, solve  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
# Team games and histories per replayed season, as AGENTS.md's backtest command names them.
SEASONS = {'2023-24': ('espn-season-2023-24', 'espn-season-2023-24'),
           '2024-25': ('espn-season-2024-25', 'espn-season-2024-25'),
           '2025-26': ('model-upgrade-2026-10-23', 'regular-season-expanded-2026-10-22')}
ARCHIVE = ROOT.parent / 'research_data' / 'NBA forecasting methods and lessons' / 'all_games.json'
LATE = (3, 4)  # March and April


def read(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)


def mae(errors):
    return fmean(abs(e) for e in errors)


def when(text):
    return datetime.datetime.fromisoformat(text.replace('Z', '+00:00'))


def situations(schedule):
    """Rest gaps and standings for every scheduled game, in place: ``rest`` (days per side) and ``rank`` (0 = worst record)."""
    last = {}
    for g in schedule:  # chronological
        g['rest'] = tuple((g['time'] - last[t]).total_seconds() / 86400 if t in last else 9. for t in (g['home'], g['away']))
        last[g['home']] = last[g['away']] = g['time']
    record = defaultdict(lambda: [0, 0])
    pending, day = [], None
    for g in schedule:
        if g['time'].date() != day:
            for p in pending:
                win = p['actual'] > 0
                record[(p['season'], p['home'])][0 if win else 1] += 1
                record[(p['season'], p['away'])][1 if win else 0] += 1
            pending, day = [], g['time'].date()
        teams = [t for s, t in record if s == g['season']]
        order = sorted(teams, key=lambda t: record[(g['season'], t)][0] / max(1, sum(record[(g['season'], t)])))
        g['rank'] = {t: i for i, t in enumerate(order)}
        g['teams'] = len(order)
        pending.append(g)


def load(datasets, reference):
    board = read(Path(reference) / 'rows.json.gz')
    rows = {g[0]: dict(zip(board['game_fields'], g)) for g in board['games']}
    schedule = []
    for season, (team_games, history) in SEASONS.items():
        lines = {}
        for l in read(Path(datasets) / f'market-closing-{season}' / 'closing-lines.json.gz')['lines']:
            if l.get('home_spread') is not None:
                lines[l['game_id']] = lines[f"espn-{l['provider_event_id']}"] = -l['home_spread']
        points = defaultdict(float)
        for p in read(Path(datasets) / history / 'history.json.gz'):
            if p.get('pts') is not None:
                points[(p['game_id'], p['team_id'])] += p['pts']
        for r in read(Path(datasets) / team_games / 'team-games.json.gz'):
            gid, home, away = r['game_id'], r['home_team_id'], r['away_team_id']
            if gid in lines and (gid, home) in points and (gid, away) in points:
                g = rows.get(gid)
                schedule.append(dict(game_id=gid, season=season, time=when(r['game_time']), home=home, away=away,
                                     close=lines[gid], actual=points[(gid, home)] - points[(gid, away)],
                                     model=g['projected_margin'] if g else None))
    schedule.sort(key=lambda g: g['time'])
    situations(schedule)
    return schedule


def informative(label, games):
    """One line: both errors, the slope of the close's miss on the disagreement, and how often the model's side covers."""
    if len(games) < 20:
        return print(f'  {label:>22}  n {len(games):4d}  (too few)')
    em, ec = [g['model'] - g['actual'] for g in games], [g['close'] - g['actual'] for g in games]
    dev, miss = [g['model'] - g['close'] for g in games], [g['actual'] - g['close'] for g in games]
    beta, se = ols([[1., d] for d in dev], miss, [g['time'].date() for g in games])
    sides = [d * m > 0 for d, m in zip(dev, miss) if d and m]
    print(f"  {label:>22}  n {len(games):4d}  model {mae(em):6.2f}  close {mae(ec):6.2f}  gap {mae(em) - mae(ec):+5.2f}  "
          f"slope {beta[1]:+.3f} ± {se[1]:.3f}  model's side covers {fmean(sides):.1%}  |model - close| {mae(dev):.2f}")


def features(g):
    late = g['time'].month in LATE
    bottom = [g['rank'].get(t, 15) < 8 for t in (g['home'], g['away'])]
    top = [g['rank'].get(t, 15) >= g['teams'] - 4 for t in (g['home'], g['away'])]
    return [1., g['rest'][0] < 1.5, g['rest'][1] < 1.5, g['rest'][0] >= 2.5, g['rest'][1] >= 2.5, late,
            late and bottom[0], late and bottom[1], late and top[0], late and top[1],
            g['season'] == '2024-25', g['season'] == '2025-26']


NAMES = ['constant', 'home back-to-back', 'away back-to-back', 'home 3+ days', 'away 3+ days', 'March-April',
         'Mar-Apr home bottom-8', 'Mar-Apr away bottom-8', 'Mar-Apr home top-4', 'Mar-Apr away top-4', '2024-25', '2025-26']


def teacher(games):
    design, clusters = [[float(v) for v in features(g)] for g in games], [g['time'].date() for g in games]
    targets = {'close - model': [g['close'] - g['model'] for g in games], 'actual - model': [g['actual'] - g['model'] for g in games],
               'actual - close': [g['actual'] - g['close'] for g in games]}
    fits = {k: ols(design, y, clusters) for k, y in targets.items()}
    print('\nthe close as teacher: the same features against three targets, date-clustered errors')
    print(f"  {'':>22}  " + '  '.join(f'{k:>16}' for k in fits))
    for i, name in enumerate(NAMES):
        print(f'  {name:>22}  ' + '  '.join(f'{fits[k][0][i]:+6.2f} ± {fits[k][1][i]:4.2f}'.rjust(16) for k in fits))
    print('  residual sd            ' + '  '.join(f'{sd(y):16.2f}' for y in targets.values()))
    b = fits['close - model'][0]
    fixed = [g['model'] + b[1] * (g['rest'][0] < 1.5) + b[2] * (g['rest'][1] < 1.5) for g in games]
    print(f"  margin MAE with the two back-to-back terms added (in sample): {mae([f - g['actual'] for f, g in zip(fixed, games)]):.3f}")


def persistent(games):
    """How much of the disagreement is a standing view of each team, and whether that view is informative ahead of time."""
    by_team = defaultdict(list)
    for g in games:
        by_team[(g['season'], g['home'])].append(g['model'] - g['close'])
        by_team[(g['season'], g['away'])].append(g['close'] - g['model'])
    print(f'\npersistent per-team view: sd of team-season mean (model - close) {sd([fmean(v) for v in by_team.values()]):.2f} '
          f'over {len(by_team)} team-seasons')
    trailing, design, y, clusters = defaultdict(list), [], [], []
    for g in games:
        h, a = trailing[(g['season'], g['home'])], trailing[(g['season'], g['away'])]
        if len(h) >= 10 and len(a) >= 10:
            view = fmean(h) - fmean(a)
            design.append([1., view, g['model'] - g['close'] - view]); y.append(g['actual'] - g['close']); clusters.append(g['time'].date())
        h.append(g['model'] - g['close']); a.append(g['close'] - g['model'])
    beta, se = ols(design, y, clusters)
    views, rest = [r[1] for r in design], [r[2] for r in design]
    print(f'  ahead of time (each team\'s mean over its 10+ earlier games), n {len(y)}: actual - close = {beta[0]:+.2f} '
          f'{beta[1]:+.3f} ± {se[1]:.3f} x standing view {beta[2]:+.3f} ± {se[2]:.3f} x game-day part')
    print(f'  share of the disagreement variance that is the standing view {sd(views) ** 2 / (sd(views) ** 2 + sd(rest) ** 2):.0%}; '
          f"standing views of 1.5+ points cover the close {fmean([v * m > 0 for v, m in zip(views, y) if abs(v) >= 1.5 and m]):.1%}")


def backbone(schedule, target, half_life, ridge):
    """Each game's home margin from a ridge rating of both teams and a home edge, fitted to earlier games of the season only.

    ``target`` is 'close' (prior closing spreads) or 'actual' (prior results); weights halve every ``half_life`` days.
    """
    teams = sorted({g['home'] for g in schedule} | {g['away'] for g in schedule})
    index = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    predictions = {}
    for season in SEASONS:
        games = [g for g in schedule if g['season'] == season]
        prior = []
        for day in sorted({g['time'].date() for g in games}):
            today = [g for g in games if g['time'].date() == day]
            if len(prior) >= 60:
                a = [[ridge if i == j else 0. for j in range(n + 1)] for i in range(n + 1)]
                a[n][n] = 1e-6
                b = [0.] * (n + 1)
                for g in prior:
                    w = .5 ** ((datetime.datetime.combine(day, datetime.time(), g['time'].tzinfo) - g['time']).total_seconds() / 86400 / half_life)
                    cols = ((index[g['home']], 1.), (index[g['away']], -1.), (n, 1.))
                    for i, vi in cols:
                        b[i] += w * vi * g[target]
                        for j, vj in cols:
                            a[i][j] += w * vi * vj
                rating = solve(a, b)
                for g in today:
                    predictions[g['game_id']] = rating[index[g['home']]] - rating[index[g['away']]] + rating[n]
            prior.extend(today)
    return predictions


def backbones(schedule, games):
    print('\na strength backbone from earlier games only (no same-game market information), on the reference games')
    for target, half_life, ridge in (('close', 7, .1), ('close', 14, 1.), ('actual', 30, 1.)):
        p = backbone(schedule, target, half_life, ridge)
        scored = [g for g in games if g['game_id'] in p]
        r = [p[g['game_id']] for g in scored]
        m, c, y = [g['model'] for g in scored], [g['close'] for g in scored], [g['actual'] for g in scored]
        blends = [(mae([w * a + (1 - w) * b - v for a, b, v in zip(m, r, y)]), w) for w in [i / 10 for i in range(11)]]
        best = min(blends)
        beta, se = ols([[1., a, b, d] for a, b, d in zip(m, c, r)], y, [g['time'].date() for g in scored])
        print(f"  fitted to prior {target:>6} spreads, half-life {half_life:2d} d, ridge {ridge}: n {len(scored)}  rating alone {mae([a - v for a, v in zip(r, y)]):.3f}"
              f"  model {mae([a - v for a, v in zip(m, y)]):.3f}  close {mae([a - v for a, v in zip(c, y)]):.3f}  best blend {best[0]:.3f} at {best[1]:.1f} model"
              f"  sd {sd(r):.2f}  |rating - close| {mae([a - b for a, b in zip(r, c)]):.2f}")
        print(f'      actual on model, close and rating together: {beta[1]:+.3f} ± {se[1]:.3f}, {beta[2]:+.3f} ± {se[2]:.3f}, {beta[3]:+.3f} ± {se[3]:.3f}')


def live(path):
    rows = [g for g in read(path)['games'] if all(g.get(k) is not None for k in ('model_margin', 'market_margin', 'actual_margin'))]
    print(f"\nlive market report, {len(rows)} games {min(g['start_time'][:10] for g in rows)} to {max(g['start_time'][:10] for g in rows)}")
    for g in rows:
        g['time'] = when(g['start_time']); g['model'] = g['model_margin']; g['close'] = g['market_margin']; g['actual'] = g['actual_margin']
    informative('published margin', rows)
    halves = [g for g in rows if g.get('player_sum_margin') is not None]
    for label, key in (('player-sum half', lambda g: g['player_sum_margin']), ('rating half (implied)', lambda g: 2 * g['model_margin'] - g['player_sum_margin'])):
        informative(label, [dict(g, model=key(g)) for g in halves])
    for lo, hi in ((0, 2), (2, 4), (4, 6), (6, 99)):
        informative(f'|model - close| {lo}-{hi}', [g for g in rows if lo <= abs(g['model'] - g['close']) < hi])
    totals = [g for g in rows if all(g.get(k) is not None for k in ('model_total', 'total_line', 'actual_total'))]
    informative('totals', [dict(g, model=g['model_total'], close=g['total_line'], actual=g['actual_total']) for g in totals])


def archive(path):
    games = []
    for g in read(path):
        if g.get('close_spread') is None or g.get('neutral'):
            continue
        text = str(g['date']).zfill(4)
        month, day = int(text[:2]), int(text[2:])
        year = int(g['season'][:4]) + (month < 9)
        try:
            time = datetime.datetime(year, month, day, tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
        games.append(dict(game_id=len(games), season=g['season'], time=time, home=g['home'], away=g['vis'],
                          actual=g['hpts'] - g['vpts'], close=g['close_spread'], open=g.get('open_spread')))
    games.sort(key=lambda g: g['time'])
    situations(games)
    print(f"\nclosing-line archive, {len(games)} games {games[0]['season']} to {games[-1]['season']}: close MAE {mae([g['close'] - g['actual'] for g in games]):.2f}")
    print('  actual - close on the situations, date-clustered errors (the archive has dates only: back-to-back = next day, 3+ = two days off)')
    eras = [('all seasons', games)] + [(f'{a}-{b}', [g for g in games if a <= g['season'][:4] < b]) for a, b in (('2007', '2015'), ('2015', '2022'), ('2022', '2027'))]
    fits = {}
    for label, era in eras:
        design = [[1., g['rest'][0] <= 1, g['rest'][1] <= 1, g['rest'][0] >= 3, g['rest'][1] >= 3, g['time'].month in LATE and g['teams'] >= 28]
                  + [float(v) for v in features(g)[6:10]] for g in era]
        fits[label] = ols(design, [g['actual'] - g['close'] for g in era], [g['time'].date() for g in era])
    print(f"  {'':>22}  " + '  '.join(f'{k:>16}' for k in fits))
    for i, name in enumerate(NAMES[:10]):
        print(f'  {name:>22}  ' + '  '.join(f'{fits[k][0][i]:+6.2f} ± {fits[k][1][i]:4.2f}'.rjust(16) for k in fits))
    for side, label in ((0, 'home'), (1, 'away')):
        faded = [g for g in games if g['time'].month in LATE and g['teams'] >= 28 and g['rank'].get((g['home'], g['away'])[side], 15) < 8]
        sign = -1 if side == 0 else 1
        covers = [(g['actual'] - g['close']) * sign > 0 for g in faded if g['actual'] != g['close']]
        print(f'  fading a March-April bottom-8 {label} team: n {len(faded)}, covers {fmean(covers):.1%} ± {math.sqrt(.25 / len(covers)):.1%}')
    both = [g for g in games if g['open'] is not None]
    o, c, y = [g['open'] for g in both], [g['close'] for g in both], [g['actual'] for g in both]
    beta, _ = ols([[1., b - a] for a, b in zip(o, c)], [v - a for a, v in zip(o, y)])
    moved = [(a, b, v) for a, b, v in zip(o, c, y) if abs(b - a) >= 1.5]
    print(f'\n  opener against close, {len(both)} games with both ({both[0]["season"]} to {both[-1]["season"]}): MAE open {mae([a - v for a, v in zip(o, y)]):.3f}'
          f' close {mae([b - v for b, v in zip(c, y)]):.3f}; mean |close - open| {fmean(abs(b - a) for a, b in zip(o, c)):.2f}; actual - open on close - open {beta[1]:.2f}')
    print(f"  lines that moved 1.5+ points ({len(moved) / len(both):.0%}): the side they moved toward covers the opener "
          f"{fmean([(v - a) * (b - a) > 0 for a, b, v in moved if v != a]):.1%} and the close {fmean([(v - b) * (b - a) > 0 for a, b, v in moved if v != b]):.1%}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--reference', required=True, help='Backtest directory holding rows.json.gz')
    parser.add_argument('--datasets', default=str(ROOT / 'datasets'))
    parser.add_argument('--live', help="A production market/report.json")
    parser.add_argument('--archive', default=str(ARCHIVE), help='The research notes\' 2007-2026 closing-line archive (all_games.json)')
    args = parser.parse_args()
    schedule = load(args.datasets, args.reference)
    games = [g for g in schedule if g['model'] is not None]
    print(f'{len(games)} reference games with a model margin, a closing line and a result, of {len(schedule)} scheduled')
    print('\ndoes the disagreement with the close carry information?  (slope: actual - close on model - close; 1 would mean the model is right and the close wrong)')
    informative('all', games)
    for season in SEASONS:
        informative(season, [g for g in games if g['season'] == season])
    m, c, y = [g['model'] for g in games], [g['close'] for g in games], [g['actual'] for g in games]
    print(f'  sd model {sd(m):.2f} close {sd(c):.2f}; corr(model, close) {corr(m, c):.3f}')
    print('  blend w x model + (1 - w) x close: ' + '  '.join(f'{w:.1f} {mae([w * a + (1 - w) * b - v for a, b, v in zip(m, c, y)]):.3f}' for w in [i / 10 for i in range(11)]))
    print('\nby size of the disagreement')
    for lo, hi in ((0, 2), (2, 4), (4, 6), (6, 99)):
        informative(f'|model - close| {lo}-{hi}', [g for g in games if lo <= abs(g['model'] - g['close']) < hi])
    big = [g for g in games if abs(g['model'] - g['close']) >= 6]
    total = sum(abs(g['model'] - g['actual']) - abs(g['close'] - g['actual']) for g in games)
    print(f"  6+ points: {len(big) / len(games):.1%} of games, {sum(abs(g['model'] - g['actual']) - abs(g['close'] - g['actual']) for g in big) / total:.0%} of the gap, "
          f"model toward the underdog in {fmean([(g['model'] - g['close']) * g['close'] < 0 for g in big]):.0%}")
    print('\nby month')
    for month in (12, 1, 2, 3, 4):
        informative(datetime.date(2000, month, 1).strftime('%B'), [g for g in games if g['time'].month == month])
    teacher(games)
    persistent(games)
    backbones(schedule, games)
    if args.live:
        live(args.live)
    if Path(args.archive).exists():
        archive(args.archive)
    else:
        print(f'\n(no closing-line archive at {args.archive}; the nineteen-season section is skipped)')


if __name__ == '__main__':
    main()
