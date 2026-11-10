"""The absence rules of absence_harness.py on the live games: a check of direction, not a fit.

    python3 scripts/research/absence_live.py --copy <production COPY> [--games-from components.json] [--utc]

For every game of the copy's `market/report.json` (the final pregame forecast it selected, the
DraftKings close and the final margin) whose margin forecast recorded an absence term, the run is
read from the copy's ledger by run id: the margin forecast (rating weight, absence adjustment,
counted minutes), the game's reported-out players with `components.absence`, and the team
ratings in the snapshot (whether each team's market strength was active). Each out player's game
log for his team is rebuilt from the copy's finals and schedule, and checked against what the
forecast recorded (games played, minutes per game, one of the last ten, recent team games); the
season share and the other rules use that log. The current rule recomputed from the records must
reproduce each run's recorded absence adjustment.

A rule moves the published margin by rating weight x (its term - the recorded term). Per rule:
the games where its term is at least one rating point, their margin MAE with the term minus
without any term, and how far the model sits past the close toward the adjusted side; and over
every game with a term recorded, its MAE minus the current rule's. +- is ONE standard error
clustered by Eastern date (`--utc`: by UTC date, as scratch/2026-11-09-margin-gap/absence_check.py
did). Live is a few dozen games: read the signs, not the sizes.
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from statistics import fmean

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))
import absence_harness as H  # noqa: E402
from nba_forecast import backtest as B  # noqa: E402
from nba_forecast.validation import parse_time  # noqa: E402

LIVE_VARIANTS = ('current', 'none (term off)', 'A1 season share', 'A2 share of last ten', 'A3 rating share',
                 'A4 0.075 a minute', 'A4 0.05 a minute', 'A5 rating share, 0.125', 'own: 0.075, long absences 0.035',
                 'own: 0.1, long absences 0.035')


def read_schedule(folder):
    """The copy's current season schedule (`schedule/latest.json`, else the newest saved version)."""
    latest = Path(folder) / 'schedule' / 'latest.json'
    if latest.exists():
        return json.loads(latest.read_text())
    return json.loads(Path(sorted(glob.glob(str(Path(folder) / 'schedule' / 'versions' / '*.json')), key=os.path.getmtime)[-1]).read_text())


def team_logs(folder, schedule):
    """team -> [(start, game_id)] of finished regular-season games, and (game_id, player) -> minutes when he played."""
    finals = {Path(f).stem for f in glob.glob(str(Path(folder) / 'finals' / '*.json'))}
    games = defaultdict(list)
    for g in schedule['games']:
        if g['game_id'] in finals and g.get('season_type', 'Regular Season') == 'Regular Season':
            for team in (g['home_team_id'], g['away_team_id']):
                games[team].append((parse_time(g['start_time']), g['game_id']))
    minutes = {}
    for gid in finals:
        for r in json.loads((Path(folder) / 'finals' / f'{gid}.json').read_text()):
            if r.get('status') == 'played' and (r.get('minutes') or 0) > 0:
                minutes[(gid, r['player_id'], r['team_id'])] = r['minutes']
    return {t: sorted(v) for t, v in games.items()}, minutes


def clustered(values, days):
    se, _ = B._mean_se(values, [(d,) for d in days])
    return fmean(values), se


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--copy', type=Path, required=True, help='a production COPY, never production itself')
    parser.add_argument('--games-from', type=Path, help="restrict to these games (a list of {'game_id', 'run_id'} records)")
    parser.add_argument('--utc', action='store_true', help='cluster by UTC date instead of Eastern')
    parser.add_argument('--json', type=Path, help='write the per-game records here')
    args = parser.parse_args()
    if 'operations/production' in str(args.copy.resolve()):
        parser.error('--copy takes a copy; production is never read by research scripts')
    report = json.loads((args.copy / 'market' / 'report.json').read_text())
    rows = {r['game_id']: r for r in report['games'] if 'spread_edge' in r}
    if args.games_from:
        wanted = {c['game_id']: c['run_id'] for c in json.loads(args.games_from.read_text())}
        mismatched = [g for g in wanted if g in rows and rows[g]['run_id'] != wanted[g]]
        print(f'restricted to {len(wanted)} games of {args.games_from}; in the copy\'s report {sum(g in rows for g in wanted)}, '
              f'with a different selected run {len(mismatched)}')
        rows = {g: r for g, r in rows.items() if g in wanted}
    calendar = read_schedule(args.copy)
    sides = {g['game_id']: (g['home_team_id'], g['away_team_id']) for g in calendar['games']}
    schedule, minutes = team_logs(args.copy, calendar)
    conn = sqlite3.connect((args.copy / 'forecast.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    query = ("SELECT as_of, json_extract(forecast_json, '$.margin_forecasts'), json_extract(forecast_json, '$.predictions'), "
             "json_extract(snapshot_json, '$.model_enhancements.margin_inputs.team_ratings') FROM run_records WHERE run_id = ?")
    games, checks = [], defaultdict(int)
    worst = defaultdict(float)
    for gid, r in sorted(rows.items(), key=lambda item: item[1]['start_time']):
        as_of, margins, predictions, ratings = conn.execute(query, (r['run_id'],)).fetchone()
        margin = next((m for m in json.loads(margins or '[]') if m.get('game_id') == gid), {})
        if margin.get('absence_rating_adjustment') is None or margin.get('rating_weight') is None:
            checks['no absence term recorded (method before v3 or no rating)'] += 1
            continue
        ratings = json.loads(ratings or '{}')
        cutoff = parse_time(as_of)
        home, away = sides[gid]
        out = []
        for p in json.loads(predictions):
            a = (p.get('components') or {}).get('absence')
            if p.get('game_id') != gid or p['status'] != 'out' or not a:
                continue
            team = p['team_id']
            log = [(t, g) for t, g in schedule.get(team, []) if t < cutoff]
            played = [(t, g) for t, g in log if (g, p['player_id'], team) in minutes]
            rebuilt = dict(team_games_played=len(played),
                           minutes_per_game=fmean(minutes[(g, p['player_id'], team)] for _, g in played) if played else None,
                           played_recent_team_game=any((g, p['player_id'], team) in minutes for _, g in log[-10:]),
                           recent_team_games=len(log[-10:]))
            checks['out players with a record'] += 1
            same = all(rebuilt[k] == a[k] for k in ('team_games_played', 'played_recent_team_game', 'recent_team_games'))
            if a['minutes_per_game'] is not None and rebuilt['minutes_per_game'] is not None:
                gap = abs(a['minutes_per_game'] - rebuilt['minutes_per_game'])
                worst['minutes per game'] = max(worst['minutes per game'], gap)
                same = same and gap < .02
            elif (a['minutes_per_game'] is None) != (rebuilt['minutes_per_game'] is None):
                same = False
            checks['log rebuilt exactly' if same else 'log differs from the record'] += 1
            checks['recent_team_games equals the team\'s games so far' if a['recent_team_games'] == len(log)
                   else 'recent_team_games below the team\'s games so far (more than ten played)'] += 1
            strength = (ratings.get(team) or {}).get('market_strength')
            out.append(dict(a, player_id=p['player_id'], team_id=team, rebuilt=same,
                            flags=[(g, p['player_id'], team) in minutes for _, g in log],
                            ages=[(cutoff - t).total_seconds() / 86400. for t, _ in log], strength_active=strength is not None))
        game = dict(game_id=gid, home=home, away=away, out=out, rated=margin.get('team_rating_margin') is not None,
                    day=(r['start_time'][:10] if args.utc else B._day(r['start_time']).isoformat()), weight=margin['rating_weight'],
                    recorded=margin['absence_rating_adjustment'], model=r['model_margin'], close=r['market_margin'],
                    actual=r['actual_margin'], method=margin.get('method'))
        recomputed = H.adjustment(game, 'current') if game['rated'] else 0.
        worst['current rule vs the recorded adjustment'] = max(worst['current rule vs the recorded adjustment'],
                                                               abs(recomputed - game['recorded']))
        games.append(game)
    print(f"{len(rows)} games in the report; {len(games)} with an absence term recorded; checks: {dict(checks)}")
    for name, value in worst.items():
        print(f'  largest absolute difference, {name}: {value:.3e}')
    if args.json:
        args.json.write_text(json.dumps(games, default=str))
    bare = {g['game_id']: g['model'] - g['weight'] * g['recorded'] for g in games}

    def published(g, variant):
        return bare[g['game_id']] + (g['weight'] * H.adjustment(g, variant) if g['rated'] else 0.)

    days = [g['day'] for g in games]
    print(f"\nPer rule ({'UTC' if args.utc else 'Eastern'} dates; one clustered standard error). 'with - without': MAE of the "
          f'published margin with the rule\'s term minus with no term, on the games where the term is 1+ rating points. '
          f"'past the close': (model - close) toward the side the term moves, with and without the term.")
    print(f"  {'rule':34s} {'games':>5s} {'with - without':>16s} {'past the close, with':>22s} {'without':>14s} {'result - close':>15s}"
          f" | all {len(games)} games: MAE vs current")
    for variant in LIVE_VARIANTS:
        hit = [g for g in games if g['rated'] and abs(H.adjustment(g, variant)) >= 1]
        change, se = clustered([abs(published(g, variant) - g['actual']) - abs(published(g, 'current') - g['actual']) for g in games], days)
        line = f'  {variant:34s} {len(hit):5d} '
        if len(hit) >= 5:
            sign = lambda g: 1. if H.adjustment(g, variant) > 0 else -1.
            d = [g['day'] for g in hit]
            w, wse = clustered([abs(published(g, variant) - g['actual']) - abs(bare[g['game_id']] - g['actual']) for g in hit], d)
            o, ose = clustered([(published(g, variant) - g['close']) * sign(g) for g in hit], d)
            u, use = clustered([(bare[g['game_id']] - g['close']) * sign(g) for g in hit], d)
            a, ase = clustered([(g['actual'] - g['close']) * sign(g) for g in hit], d)
            line += f'{w:+8.2f} ± {wse:.2f} {o:+14.2f} ± {ose:.2f} {u:+7.2f} ± {use:.2f} {a:+8.2f} ± {ase:.2f}'
        else:
            line += f"{'(too few)':>16s} {'':>22s} {'':>14s} {'':>15s}"
        print(line + f' | {change:+.3f} ± {se:.3f}')
    for label, test in (('3+ rating points, current rule', lambda g: abs(g['recorded']) >= 3),):
        hit = [g for g in games if test(g)]
        if len(hit) >= 5:
            w, wse = clustered([abs(g['model'] - g['actual']) - abs(bare[g['game_id']] - g['actual']) for g in hit], [g['day'] for g in hit])
            print(f'  {label}: {len(hit)} games, with - without {w:+.2f} ± {wse:.2f}')
    counted = [p for g in games for p in g['out'] if H.eligible(p)]
    total = sum(p['minutes_per_game'] for p in counted)
    if total:
        print(f"\nCounted today: {len(counted)} out players, {total:.0f} minutes; share of those minutes from players who played every "
              f"team game so far {sum(p['minutes_per_game'] for p in counted if all(p['flags'])) / total:.0%}; "
              f"regulars left out as absent for ten or more games: {sum(1 for g in games for p in g['out'] if H.long_absent(p))}")


if __name__ == '__main__':
    main()
