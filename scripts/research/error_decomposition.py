"""Where the margin, points and totals errors against actual outcomes come from, live and in the backtest.

Two steps.

``replay`` runs ``python -m nba_forecast backtest`` (arguments after ``--``) with two read-only
hooks on ``backtest.blended_location`` and ``backtest.absence_adjustment`` that record, for every
scored game, the player-sum margin, the home edge, both team ratings (results rating, prior,
market strength, games played), the absence adjustment and its minutes. Nothing the replay
computes changes; the hooks only copy their arguments. The records are written to
``<output>/halves.json.gz`` in the order of ``rows.json.gz``'s games, checked team by team.

    python3 scripts/research/error_decomposition.py replay -- backtest --history ... --output DIR ...

``tables`` prints the analyses (every table and number in the 2026-11-10 report comes from it):

    python3 scripts/research/error_decomposition.py tables --live LIVE.json [--oracle-o3 O3.json] \
        --production-path REF_DIR --seeded DIR [DIR ...] [--datasets datasets]

Run on 2026-11-10 (seeded replays one season at a time, under the shared heavy lock; 2025-26 shown, the
other seasons take their own files and the season before as ``--previous-*``):

    python3 scripts/research/error_decomposition.py replay -- backtest \
      --history datasets/regular-season-expanded-2026-10-22/history.json.gz --team-games datasets/model-upgrade-2026-10-23/team-games.json.gz \
      --injuries datasets/injury-reports-2025-26/statuses.json.gz --market datasets/market-closing-2025-26/closing-lines.json.gz \
      --previous-history datasets/espn-season-2024-25/history-nba-ids.json.gz --previous-team-games datasets/espn-season-2024-25/team-games.json.gz \
      --warmup-fraction 0 --production-path datasets/production-artifact-2026-11-09/artifact.json.gz --output SEEDED-2025-26
    python3 scripts/research/error_decomposition.py tables \
      --live scratch/2026-11-09-items/livereplay/main-rebuilt.json --oracle-o3 scratch/2026-11-10-news/oracle/o3-rebuilt.json \
      --production-path datasets/backtest-2026-11-09-production-path --seeded SEEDED-2023-24 SEEDED-2024-25 SEEDED-2025-26

(the artifact and the production-path reference were on branch gate/production-path's datasets).

Everything is scored against what happened (final scores, box scores). The closing line
appears only as the bar: its own error against the same outcomes. ± is one standard error
clustered by Eastern game date (``backtest._mean_se``), paired on the same games, unless a
line says otherwise.
"""
import argparse
import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))


# ---------------------------------------------------------------- replay with hooks

def replay(argv):
    from nba_forecast import backtest as B
    from nba_forecast import __main__ as cli
    from nba_forecast.margins import RATING_WEIGHT
    records, pending = [], {}
    original_blend, original_absence = B.blended_location, B.absence_adjustment

    def absence(predictions, game):
        value, minutes = original_absence(predictions, game)
        pending['absence'] = (game['game_id'], value, dict(minutes))
        return value, minutes

    def blend(player_sum, home_edge, ratings, home, away, absence=0.):
        location, rating = original_blend(player_sum, home_edge, ratings, home, away, absence)
        game_id, value, minutes = pending.pop('absence', (None, None, None))

        def side(team):
            r = ratings.get(team)
            return None if r is None else {k: r.get(k) for k in ('rating', 'games', 'prior', 'results_rating', 'market_strength')}
        records.append(dict(game_id=game_id, home_team_id=home, away_team_id=away, player_sum_margin=player_sum,
                            home_edge=home_edge, home_rating=side(home), away_rating=side(away),
                            absence_rating_adjustment=absence, absence_check=value, absent_minutes=minutes,
                            team_rating_margin=rating, location=location,
                            rating_weight=RATING_WEIGHT if rating is not None else 0.))
        return location, rating

    B.blended_location, B.absence_adjustment = blend, absence
    status = cli.main(argv)
    output = Path(argv[argv.index('--output') + 1])
    with gzip.open(output / 'halves.json.gz', 'wt') as handle:
        json.dump(dict(games=records, argv=argv), handle)
    rows = B.read_json(output / 'rows.json.gz')
    if len(rows['games']) != len(records):
        raise SystemExit(f'{len(records)} hook records for {len(rows["games"])} games')
    for record, game in zip(records, rows['games']):
        if (record['game_id'], record['home_team_id'], record['away_team_id']) != tuple(game[:3]) \
                or abs(record['location'] - game[3]) > 1e-9:
            raise SystemExit(f'hook record {record["game_id"]} does not match the game row {game[:4]}')
    print(f'halves: {len(records)} games matched to rows.json.gz, written to {output / "halves.json.gz"}', file=sys.stderr)
    return status


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'replay':
        argv = sys.argv[2:]
        if argv and argv[0] == '--':
            argv = argv[1:]
        raise SystemExit(replay(argv))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['tables'])
    parser.add_argument('--live', type=Path, required=True)
    parser.add_argument('--live-finals', type=Path, help="the live finals folder (default: the live output's copy/finals)")
    parser.add_argument('--oracle-o3', type=Path)
    parser.add_argument('--production-path', type=Path, required=True)
    parser.add_argument('--seeded', type=Path, nargs='+', default=[])
    parser.add_argument('--datasets', type=Path, default=HERE.parent.parent / 'datasets')
    args = parser.parse_args()
    if args.live_finals is None:
        args.live_finals = Path(_read(args.live)['copy']) / 'finals'
    tables(args)


# ---------------------------------------------------------------- loading

SEASON_FILES = {  # history, team games, market, previous season's history (same player IDs)
    '2023-24': ('espn-season-2023-24/history.json.gz', 'espn-season-2023-24/team-games.json.gz',
                'market-closing-2023-24/closing-lines.json.gz', 'espn-season-2022-23/history.json.gz'),
    '2024-25': ('espn-season-2024-25/history.json.gz', 'espn-season-2024-25/team-games.json.gz',
                'market-closing-2024-25/closing-lines.json.gz', 'espn-season-2023-24/history.json.gz'),
    '2025-26': ('regular-season-expanded-2026-10-22/history.json.gz', 'model-upgrade-2026-10-23/team-games.json.gz',
                'market-closing-2025-26/closing-lines.json.gz', 'espn-season-2024-25/history-nba-ids.json.gz'),
}
LIVE_LAST_SEASON = ('regular-season-expanded-2026-10-22/history.json.gz', 'model-upgrade-2026-10-23/team-games.json.gz', '2025-26')
EARLY_GAMES = 15        # a game is "early" while its two teams average fewer than this many games played
GP_BUCKETS = ((0, 4), (4, 8), (8, 15), (15, 30), (30, 99))
CONTINUITY_BUCKETS = ((0., .55), (.55, .75), (.75, 1.01))
FAV_BUCKETS = ((0, 3), (3, 6), (6, 9), (9, 99))
MINUTE_BUCKETS = ((0, 10), (10, 20), (20, 30), (30, 99))
W_GRID = [i / 100 for i in range(101)]
BOOTSTRAP = 400


def _read(path):
    from nba_forecast.backtest import read_json
    return read_json(path)


def _finite(v):
    import math
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def continuity(season_rows, previous_rows):
    """Share of each team's minutes in ``season_rows`` played by players who played for that team the season before;
    and the (player, team) and player sets of the season before."""
    previous_pairs = {(r['player_id'], r['team_id']) for r in previous_rows if (r.get('minutes') or 0) > 0}
    previous_players = {p for p, _ in previous_pairs}
    total, kept = {}, {}
    for r in season_rows:
        m = r.get('minutes') or 0
        if m > 0:
            total[r['team_id']] = total.get(r['team_id'], 0.) + m
            if (r['player_id'], r['team_id']) in previous_pairs:
                kept[r['team_id']] = kept.get(r['team_id'], 0.) + m
    return {t: kept.get(t, 0.) / v for t, v in total.items()}, previous_pairs, previous_players


def newness(player, team, pairs, players):
    return 'same team' if (player, team) in pairs else 'new team' if player in players else 'no minutes last season'


def games_played(schedule):
    """schedule: [(game_id, date, home, away)] -> game_id -> (home games before the date, away games before)."""
    out = {}
    for gid, day, home, away in schedule:
        out[gid] = tuple(sum(1 for _, d, h, a in schedule if d < day and t in (h, a)) for t in (home, away))
    return out


def _season_prior(team_games):
    """0.6 x each team's average final margin over a season's team games (the rating's prior, approximately)."""
    from nba_forecast.margins import RATING_PRIOR_SHRINK
    acc = {}
    for g in team_games:
        h, a = g['home_team_id'], g['away_team_id']
        m = g['team_stats'][h]['pts'] - g['team_stats'][a]['pts']
        for t, s in ((h, m), (a, -m)):
            acc.setdefault(t, []).append(s)
    return {t: RATING_PRIOR_SHRINK * sum(v) / len(v) for t, v in acc.items()}


def load_live(path, finals_dir, datasets, o3_path=None):
    from nba_forecast.margins import RATING_WEIGHT
    data = _read(path)
    finals = {}
    for f in sorted(Path(finals_dir).glob('*.json')):
        finals[f.stem] = _read(f)
    from nba_forecast.backtest import _day
    schedule = []
    for gid, rows in finals.items():
        teams = sorted({r['team_id'] for r in rows})
        g = next((x for x in data['games'] if x['game_id'] == gid), None)
        if g:
            schedule.append((gid, g['date'], g['home_team_id'], g['away_team_id']))
        elif len(teams) == 2:
            schedule.append((gid, _day(rows[0]['game_time']).isoformat(), teams[0], teams[1]))
    gp = games_played(schedule)
    last_rows = [r for r in _read(datasets / LIVE_LAST_SEASON[0]) if r.get('season') == LIVE_LAST_SEASON[2]]
    season_rows = [r for rows in finals.values() for r in rows if r.get('status') == 'played']
    cont, pairs, players = continuity(season_rows, last_rows)
    prior = _season_prior([r for r in _read(datasets / LIVE_LAST_SEASON[1]) if r.get('season', LIVE_LAST_SEASON[2]) == LIVE_LAST_SEASON[2]])
    o3, o3_games = {}, {}
    if o3_path:
        for g in _read(o3_path)['games']:
            if g.get('replayed'):
                o3_games[g['game_id']] = g['replayed']
            for p in g['players'] if g.get('replayed') else ():
                if p.get('replayed') and p['replayed']['forecast_status'] == 'projected':
                    o3[(g['game_id'], p['player_id'])] = p['replayed']
    games, players_out, teams_out, checks = [], [], [], dict(recomposed=0, games=0)
    for g in data['games']:
        r = g.get('replayed')
        close = next((c for c in g['closes'] if _finite(c.get('market_margin'))), None)
        if not r or not _finite(r.get('margin')) or close is None:
            continue
        a = g['actual']
        w = r['rating_weight']
        checks['games'] += 1
        if _finite(r.get('player_sum_margin')) and _finite(r.get('team_rating_margin')):
            checks['recomposed'] += abs((1 - w) * r['player_sum_margin'] + w * r['team_rating_margin'] - r['margin']) < 1e-6
        h, aw = g['home_team_id'], g['away_team_id']
        fin = finals.get(g['game_id'], [])
        starters = {x['player_id'] for x in fin if x.get('starter') is True}
        minutes = {t: sum((x.get('minutes') or 0) for x in fin if x['team_id'] == t and x.get('status') == 'played') for t in (h, aw)}
        games.append(dict(source='live', season='2026-27', date=g['date'], game_id=g['game_id'], home=h, away=aw,
                          actual=a['margin'], close=close['market_margin'], close_total=close.get('total_line'),
                          blend=r['margin'], ps=r.get('player_sum_margin'), rating=r.get('team_rating_margin'),
                          absence=r.get('absence_rating_adjustment') or 0., w=w if w is not None else RATING_WEIGHT,
                          gp=gp.get(g['game_id']), prior=(prior.get(h), prior.get(aw)), cont=(cont.get(h), cont.get(aw)),
                          total=r.get('total'), actual_total=a['total'],
                          o3_ps=(o3_games.get(g['game_id']) or {}).get('player_sum_margin'),
                          o3_blend=(o3_games.get(g['game_id']) or {}).get('margin')))
        for t, side in ((h, 'home'), (aw, 'away')):
            m = minutes[t]
            teams_out.append(dict(date=g['date'], game_id=g['game_id'], team=t, season='2026-27', opp=aw if t == h else h,
                                  projected=r.get(side + '_points'),
                                  actual=a[side + '_points'], actual_regulation=a[side + '_points'] * min(1., 240. / m) if m else None))
        for p in g['players']:
            s = p.get('replayed')
            if not s or s['forecast_status'] != 'projected' or not (p.get('actual_minutes') or 0) > 0:
                continue
            q = o3.get((g['game_id'], p['player_id']))
            players_out.append(dict(date=g['date'], game_id=g['game_id'], player=p['player_id'], team=p['team_id'],
                                    pred_min=s['predicted_minutes'], pred_pts=s['predicted_points'], act_min=p['actual_minutes'],
                                    act_pts=p['actual_points'], starter=p['player_id'] in starters,
                                    new=newness(p['player_id'], p['team_id'], pairs, players),
                                    o3_pts=q['predicted_points'] if q else None))
    return dict(games=games, players=players_out, teams=teams_out, checks=checks, label=f'live ({path.name})')


def load_season_inputs(datasets):
    from nba_forecast.backtest import _day
    from nba_forecast.market_history import read_lines
    out = {}
    for season, (hist, tg, market, prev) in SEASON_FILES.items():
        # The 2025-26 history also carries 385 rows of 2026-27's opening nights; each season keeps its own rows.
        rows = [r for r in _read(datasets / hist) if r.get('season') == season]
        records = [r for r in _read(datasets / tg) if r.get('season', season) == season]
        cont, pairs, players = continuity([r for r in rows if r.get('status') == 'played'], _read(datasets / prev))
        schedule = [(r['game_id'], _day(r['game_time']).isoformat(), r['home_team_id'], r['away_team_id']) for r in records]
        lines = read_lines(datasets / market)
        out[season] = dict(
            date={gid: d for gid, d, _, _ in schedule}, gp=games_played(schedule), cont=cont, pairs=pairs, players=players,
            starter={(r['game_id'], r['player_id']): r.get('starter') is True for r in rows},
            close={gid: -v['home_spread'] for gid, v in lines.items() if _finite(v.get('home_spread'))},
            close_total={gid: v['total'] for gid, v in lines.items() if _finite(v.get('total'))},
            actual_total={r['game_id']: sum(s['pts'] for s in r['team_stats'].values()) for r in records})
    return out


def load_backtest(directory, inputs, label):
    rows = _read(directory / 'rows.json.gz')
    halves = {}
    if (directory / 'halves.json.gz').exists():
        halves = {h['game_id']: h for h in _read(directory / 'halves.json.gz')['games']}
    season_of = {gid: s for s, v in inputs.items() for gid in v['date']}
    games, players, teams = [], [], []
    outside = {r[0] for r in rows['games'] if r[0] not in season_of}
    rows = dict(rows, games=[r for r in rows['games'] if r[0] in season_of], teams=[r for r in rows['teams'] if r[0] in season_of],
                players=[r for r in rows['players'] if r[0] in season_of])
    for gid, home, away, margin, actual, sd, _ in rows['games']:
        s = season_of[gid]
        v = inputs[s]
        h = halves.get(gid)
        rating = h['team_rating_margin'] if h else None
        games.append(dict(source=label, season=s, date=v['date'][gid], game_id=gid, home=home, away=away, actual=actual,
                          close=v['close'].get(gid), close_total=v['close_total'].get(gid), blend=margin,
                          ps=h['player_sum_margin'] if h else None, rating=rating,
                          absence=(h['absence_rating_adjustment'] or 0.) if h else None, w=h['rating_weight'] if h else None,
                          gp=v['gp'][gid], prior=((h['home_rating'] or {}).get('prior'), (h['away_rating'] or {}).get('prior')) if h else None,
                          cont=(v['cont'].get(home), v['cont'].get(away)), actual_total=v['actual_total'].get(gid)))
    sides = {g[0]: (g[1], g[2]) for g in rows['games']}
    for gid, tid, home, projected, actual_reg, actual, *_ in rows['teams']:
        pair = sides.get(gid)
        teams.append(dict(date=inputs[season_of[gid]]['date'][gid], game_id=gid, team=tid, season=season_of[gid],
                          opp=None if pair is None else pair[1] if tid == pair[0] else pair[0], projected=projected,
                          actual=actual, actual_regulation=actual_reg))
    for gid, pid, tid, em, am, pm, *_rest in rows['players']:
        if em is None or pm is None or not (am or 0) > 0:
            continue
        v = inputs[season_of[gid]]
        players.append(dict(date=v['date'][gid], game_id=gid, player=pid, team=tid, pred_min=em, pred_pts=pm, act_min=am,
                            act_pts=_rest[3], starter=v['starter'].get((gid, pid), False),
                            new=newness(pid, tid, v['pairs'], v['players']), o3_pts=None))
    return dict(games=games, players=players, teams=teams, label=label, outside=len(outside))


SURPRISE_PROJECTED_MINUTES = 20.
SURPRISE_PLAYED_MINUTES = 15.


def surprise_flags(live_path, finals_dir):
    """The lineup oracle's material availability surprise (measure/lineup-oracle, lineup_oracle.surprise_of): a player
    the replay projected for >= 20 minutes who did not play, or a player it did not project who played >= 15."""
    flags = {}
    for g in _read(live_path)['games']:
        if not g.get('replayed'):
            continue
        rows = _read(Path(finals_dir) / (g['game_id'] + '.json'))
        teams = (g['home_team_id'], g['away_team_id'])
        actual = {r['player_id']: (r['minutes'] or 0) if r['status'] == 'played' else 0. for r in rows if r['team_id'] in teams}
        projected = {p['player_id']: p['replayed'] for p in g['players']
                     if p.get('replayed') and p['replayed']['forecast_status'] == 'projected'}
        missed = [pid for pid, side in projected.items()
                  if (side['predicted_minutes'] or 0) >= SURPRISE_PROJECTED_MINUTES and actual.get(pid, 0) <= 0]
        unexpected = [pid for pid, m in actual.items() if m >= SURPRISE_PLAYED_MINUTES and pid not in projected]
        flags[g['game_id']] = bool(missed or unexpected)
    return flags


# ---------------------------------------------------------------- statistics

def mse(values, dates):
    """Mean and one standard error clustered by Eastern date (backtest._mean_se)."""
    from statistics import fmean
    from nba_forecast.backtest import _mean_se
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], None
    return fmean(values), _mean_se(values, [(d,) for d in dates])[0]


def pm(mean, se, digits=3):
    if mean is None:
        return 'n/a'
    return f'{mean:+.{digits}f} ± {se:.{digits}f}' if se is not None else f'{mean:+.{digits}f}'


def mae(values):
    return sum(abs(v) for v in values) / len(values) if values else float('nan')


def ols_line(x, y, dates, names):
    from stats_lite import ols
    beta, se = ols([[1., *row] for row in x], y, dates)
    return '  '.join(f'{n} {b:+.3f} ± {s:.3f}' for n, b, s in zip(names, beta, se))


def best_weight(rows, ps_key='ps', rating_key='rating'):
    def loss(w):
        return sum(abs((1 - w) * r[ps_key] + w * r[rating_key] - r['actual']) for r in rows)
    return min(W_GRID, key=loss)


def bootstrap_weight(rows, seed=20261110, **kw):
    import random
    from statistics import pstdev
    rng = random.Random(seed)
    by_date = {}
    for r in rows:
        by_date.setdefault(r['date'], []).append(r)
    dates = sorted(by_date)
    draws = []
    for _ in range(BOOTSTRAP):
        sample = [r for d in (rng.choice(dates) for _ in dates) for r in by_date[d]]
        draws.append(best_weight(sample, **kw))
    return pstdev(draws), sorted(draws)[int(.05 * BOOTSTRAP)], sorted(draws)[int(.95 * BOOTSTRAP) - 1]


def halves_rows(rows):
    out = []
    for r in rows:
        if _finite(r.get('ps')) and _finite(r.get('rating')):
            out.append(dict(r, rating_noabs=r['rating'] - r['absence'], blend_noabs=r['blend'] - r['w'] * r['absence']))
    return out


def subset(rows, early=None, ids=None):
    out = rows
    if early is not None:
        out = [r for r in out if (sum(r['gp']) / 2 < EARLY_GAMES) == early]
    if ids is not None:
        out = [r for r in out if r['game_id'] in ids]
    return out


# ---------------------------------------------------------------- the analyses

def margin_halves(samples):
    print('\n=== 1. Margin by half, against the actual margin (MAE; paired differences vs the blend, ± one SE by date) ===')
    print('  rating half = edge + R_home - R_away + absence term; "no absence" removes the term from the rating half / blend.')
    head = f'  {"sample":40s} {"n":>5s} {"dates":>5s} {"close":>6s} {"blend":>6s} {"p-sum":>6s} {"rating":>6s} {"rat-noA":>7s} {"bl-noA":>6s}'
    print(head)
    for name, rows in samples:
        rows = [r for r in rows if r['close'] is not None]
        if not rows:
            continue
        print(f'  {name:40s} {len(rows):5d} {len({r["date"] for r in rows}):5d} {mae([r["close"] - r["actual"] for r in rows]):6.2f} '
              f'{mae([r["blend"] - r["actual"] for r in rows]):6.2f} {mae([r["ps"] - r["actual"] for r in rows]):6.2f} '
              f'{mae([r["rating"] - r["actual"] for r in rows]):6.2f} {mae([r["rating_noabs"] - r["actual"] for r in rows]):7.2f} '
              f'{mae([r["blend_noabs"] - r["actual"] for r in rows]):6.2f}')
    print('  paired |error| differences:')
    for name, rows in samples:
        rows = [r for r in rows if r['close'] is not None]
        if not rows:
            continue
        d = [r['date'] for r in rows]

        def diff(k):
            return mse([abs(r[k] - r['actual']) - abs(r['blend'] - r['actual']) for r in rows], d)
        print(f'  {name:40s} p-sum − blend {pm(*diff("ps"))}   rating − blend {pm(*diff("rating"))}   '
              f'blend-noA − blend {pm(*diff("blend_noabs"))}   blend − close {pm(*mse([abs(r["blend"] - r["actual"]) - abs(r["close"] - r["actual"]) for r in rows], d))}')
    for name, rows in samples:
        rows = [r for r in rows if r['close'] is not None and _finite(r.get('o3_ps'))]
        if rows:
            d = [r['date'] for r in rows]
            print(f'  {name:40s} with who played, starters and actual minutes (O3, {len(rows)} games): p-sum MAE '
                  f'{mae([r["o3_ps"] - r["actual"] for r in rows]):.2f} vs {mae([r["ps"] - r["actual"] for r in rows]):.2f}, '
                  f'O3 − main p-sum {pm(*mse([abs(r["o3_ps"] - r["actual"]) - abs(r["ps"] - r["actual"]) for r in rows], d))}')
    print('  mean |absence term| (points of rating margin) and its contribution to the blend (w x term):')
    for name, rows in samples:
        rows = [r for r in rows if r['close'] is not None]
        if rows:
            print(f'  {name:40s} |term| {sum(abs(r["absence"]) for r in rows) / len(rows):.2f}   '
                  f'nonzero in {sum(1 for r in rows if abs(r["absence"]) > 1e-9)} of {len(rows)}')
    print('\n  Blend weight on the rating half that minimises margin MAE against actual (grid 0.00..1.00; SE and 90% range by'
          f' {BOOTSTRAP} bootstrap draws of dates; in-sample, so the gain at w* is optimistic):')
    print(f'  {"sample":40s} {"w*":>5s} {"SE":>5s} {"90% range":>11s}  {"MAE@0.65":>8s} {"MAE@w*":>7s}  w* without absence term')
    fitted = {}
    for name, rows in samples:
        rows = [r for r in rows if r['close'] is not None]
        if len(rows) < 30:
            continue
        w = best_weight(rows)
        se, lo, hi = bootstrap_weight(rows)
        w2 = best_weight(rows, rating_key='rating_noabs')
        se2, _, _ = bootstrap_weight(rows, rating_key='rating_noabs')
        fitted[name] = (w, w2)
        print(f'  {name:40s} {w:5.2f} {se:5.2f} {lo:5.2f}-{hi:4.2f}  {mae([r["blend"] - r["actual"] for r in rows]):8.2f} '
              f'{mae([(1 - w) * r["ps"] + w * r["rating"] - r["actual"] for r in rows]):7.2f}  {w2:.2f} ± {se2:.2f}')
    print('\n  w* by games played (the two teams\' average), same method:')
    for name, rows in samples:
        rows = [r for r in rows if r['close'] is not None and r['gp'] is not None]
        if name.endswith('surprise') or name.endswith('window') or name.endswith('(gp >= 15)') or name.endswith('(gp < 15)'):
            continue
        for lo, hi in GP_BUCKETS:
            b = [r for r in rows if lo <= sum(r['gp']) / 2 < hi]
            if len(b) < 30:
                continue
            w = best_weight(b)
            se, lo90, hi90 = bootstrap_weight(b)
            print(f'  {name:40s} gp {lo:>2d}-{hi - 1:<2d} n {len(b):4d}  w* {w:.2f} ± {se:.2f} ({lo90:.2f}-{hi90:.2f})  '
                  f'MAE@0.65 {mae([r["blend"] - r["actual"] for r in b]):5.2f}  MAE@w* {mae([(1 - w) * r["ps"] + w * r["rating"] - r["actual"] for r in b]):5.2f}')
    return fitted


def calibration(samples):
    print('\n=== 2. Calibration against reality: actual margin = a + b x forecast (OLS, SE clustered by date) ===')
    print('  b < 1: the forecast is over-confident (too spread out); b > 1: under-confident. a: home-court bias of the forecast.')
    for name, rows in samples:
        rows = [r for r in rows if r['close'] is not None]
        if not rows:
            continue
        d = [r['date'] for r in rows]
        y = [r['actual'] for r in rows]
        print(f'  {name}  (n {len(rows)})')
        for label, key in (('blend', 'blend'), ('player sum', 'ps'), ('rating half', 'rating'), ('close (bar)', 'close')):
            if all(_finite(r.get(key)) for r in rows):
                print(f'    {label:12s} {ols_line([[r[key]] for r in rows], y, d, ("a", "b"))}')
        if all(_finite(r.get('ps')) for r in rows):
            print(f'    both halves  {ols_line([[r["ps"], r["rating"]] for r in rows], y, d, ("a", "b_psum", "b_rating"))}')
        bias = mse([r['actual'] - r['blend'] for r in rows], d)
        cbias = mse([r['actual'] - r['close'] for r in rows], d)
        print(f'    home bias (actual − forecast): blend {pm(*bias)}  close {pm(*cbias)}  mean actual {sum(y) / len(y):+.2f}'
              f'  mean blend {sum(r["blend"] for r in rows) / len(rows):+.2f}  mean close {sum(r["close"] for r in rows) / len(rows):+.2f}')
    print('\n  By favourite size (the blend\'s favourite; margins signed toward it): mean forecast, mean actual,'
          ' actual − forecast (> 0: favourites win by more than forecast), MAE blend and close')
    for name, rows in samples:
        rows = [r for r in rows if r['close'] is not None]
        if not rows:
            continue
        print(f'  {name}')
        for lo, hi in FAV_BUCKETS:
            b = [r for r in rows if lo <= abs(r['blend']) < hi]
            if not b:
                continue
            sign = [1 if r['blend'] >= 0 else -1 for r in b]
            gap = mse([s * (r['actual'] - r['blend']) for s, r in zip(sign, b)], [r['date'] for r in b])
            print(f'    |blend| {lo:>2d}-{hi:<2d} n {len(b):4d}  forecast {sum(s * r["blend"] for s, r in zip(sign, b)) / len(b):5.2f}  '
                  f'actual {sum(s * r["actual"] for s, r in zip(sign, b)) / len(b):+6.2f}  actual − forecast {pm(*gap, 2):>14s}  '
                  f'MAE blend {mae([r["blend"] - r["actual"] for r in b]):5.2f} close {mae([r["close"] - r["actual"] for r in b]):5.2f}')


def early_and_turnover(samples):
    print('\n=== 3. Early season and roster turnover ===')
    print('  games played = the two teams\' average number of earlier games this season. MAE against actual.')
    for name, rows in samples:
        rows = [r for r in rows if r['close'] is not None and r['gp'] is not None]
        if not rows:
            continue
        print(f'  {name}')
        for lo, hi in GP_BUCKETS:
            b = [r for r in rows if lo <= sum(r['gp']) / 2 < hi]
            if not b:
                continue
            d = [r['date'] for r in b]
            gap = mse([abs(r['blend'] - r['actual']) - abs(r['close'] - r['actual']) for r in b], d)
            rp = mse([abs(r['rating'] - r['actual']) - abs(r['ps'] - r['actual']) for r in b], d)
            print(f'    gp {lo:>2d}-{hi - 1:<2d} n {len(b):4d}  close {mae([r["close"] - r["actual"] for r in b]):5.2f}  '
                  f'blend {mae([r["blend"] - r["actual"] for r in b]):5.2f}  p-sum {mae([r["ps"] - r["actual"] for r in b]):5.2f}  '
                  f'rating {mae([r["rating"] - r["actual"] for r in b]):5.2f}  blend − close {pm(*gap, 2):>13s}  rating − p-sum {pm(*rp, 2):>13s}')
    print('\n  Roster continuity = share of the team\'s minutes this season (live: to date; backtest: the whole season) by players who'
          ' played for it last season; a game takes the mean of its two teams.')
    for name, rows in samples:
        rows = [r for r in rows if r['close'] is not None and None not in r['cont']]
        if not rows:
            continue
        print(f'  {name}  (mean continuity {sum(sum(r["cont"]) / 2 for r in rows) / len(rows):.2f})')
        for early in (True, False):
            sub = [r for r in rows if (sum(r['gp']) / 2 < EARLY_GAMES) == early]
            if not sub:
                continue
            for lo, hi in CONTINUITY_BUCKETS:
                b = [r for r in sub if lo <= sum(r['cont']) / 2 < hi]
                if len(b) < 5:
                    continue
                d = [r['date'] for r in b]
                rp = mse([abs(r['rating'] - r['actual']) - abs(r['ps'] - r['actual']) for r in b], d)
                gap = mse([abs(r['blend'] - r['actual']) - abs(r['close'] - r['actual']) for r in b], d)
                print(f'    {"early" if early else "later"} continuity {lo:.2f}-{min(hi, 1):.2f} n {len(b):4d}  close {mae([r["close"] - r["actual"] for r in b]):5.2f}  '
                      f'blend {mae([r["blend"] - r["actual"] for r in b]):5.2f}  p-sum {mae([r["ps"] - r["actual"] for r in b]):5.2f}  '
                      f'rating {mae([r["rating"] - r["actual"] for r in b]):5.2f}  rating − p-sum {pm(*rp, 2):>13s}  blend − close {pm(*gap, 2):>13s}')
            d = [r['date'] for r in sub]
            turnover = [[1 - sum(r['cont']) / 2] for r in sub]
            print(f'    {"early" if early else "later"}: |error| on turnover (1 − continuity), slope per unit:  '
                  f'rating {ols_line(turnover, [abs(r["rating"] - r["actual"]) for r in sub], d, ("a", "b"))}')
            print(f'    {"":5s}  p-sum  {ols_line(turnover, [abs(r["ps"] - r["actual"]) for r in sub], d, ("a", "b"))}')
            print(f'    {"":5s}  close  {ols_line(turnover, [abs(r["close"] - r["actual"]) for r in sub], d, ("a", "b"))}')
        # Is last season's prior over-trusted for changed rosters? Residual of the blend on the prior difference.
        sub = [r for r in rows if sum(r['gp']) / 2 < EARLY_GAMES and r.get('prior') and None not in r['prior']]
        if len(sub) >= 30:
            print('    early games: (actual − blend) on the prior difference (0.6 x last season\'s margin, home − away),'
                  ' by continuity; slope < 0 = the prior is over-trusted')
            for lo, hi in CONTINUITY_BUCKETS:
                b = [r for r in sub if lo <= sum(r['cont']) / 2 < hi]
                if len(b) >= 20:
                    print(f'      continuity {lo:.2f}-{min(hi, 1):.2f} n {len(b):4d}  '
                          f'{ols_line([[r["prior"][0] - r["prior"][1]] for r in b], [r["actual"] - r["blend"] for r in b], [r["date"] for r in b], ("a", "slope"))}')


PERSISTENCE_PSEUDO = 5.


def persistence_feature(rows, key):
    """For each game, the home team's earlier mean residual (actual − forecast, from its side) minus the away team's,
    each as sum / (games + 5), from earlier Eastern dates only, within the sample's season."""
    out, acc = {}, {}
    for day in sorted({r['date'] for r in rows}):
        today = [r for r in rows if r['date'] == day]
        for r in today:
            def mean(team):
                s, n = acc.get((r['season'], team), (0., 0))
                return s / (n + PERSISTENCE_PSEUDO)
            out[r['game_id']] = mean(r['home']) - mean(r['away'])
        for r in today:
            e = r['actual'] - r[key]
            for team, sign in ((r['home'], 1), (r['away'], -1)):
                s, n = acc.get((r['season'], team), (0., 0))
                acc[(r['season'], team)] = (s + sign * e, n + 1)
    return out


def team_persistence(samples):
    print('\n=== 3b. Do the errors persist by team? (actual − forecast) on the teams\' earlier residuals, home − away,'
          ' each sum / (games + 5), earlier dates only; slope > 0 = team-strength errors the forecast keeps repeating ===')
    fits = {}
    for name, rows in samples:
        rows = [r for r in rows if r['close'] is not None]
        if len(rows) < 30:
            continue
        d = [r['date'] for r in rows]
        parts = []
        for label, key in (('blend', 'blend'), ('p-sum', 'ps'), ('rating', 'rating'), ('close (bar)', 'close')):
            x = persistence_feature(rows, key)
            from stats_lite import ols
            beta, se = ols([[1., x[r['game_id']]] for r in rows], [r['actual'] - r[key] for r in rows], d)
            parts.append(f'{label} {beta[1]:+.2f} ± {se[1]:.2f}')
            if key == 'blend':
                fits[name] = beta[1]
        print(f'  {name:40s} n {len(rows):4d}  ' + '   '.join(parts))
    return fits


def persistence_transfer(live, fits, items):
    rows = [r for r in live if r['close'] is not None]
    x = persistence_feature(rows, 'blend')
    for name, k in fits.items():
        def change(sample):
            return mse([abs(r['blend'] + k * x[r['game_id']] - r['actual']) - abs(r['blend'] - r['actual']) for r in sample],
                       [r['date'] for r in sample])
        c, o = change(rows), change([r for r in rows if not r['surprise']])
        print(f'  {"team-residual correction k " + format(k, "+.2f") + " (fitted on " + name + ")":72s} {pm(*c)} | {pm(*o)}')
        items.append(dict(kind='margin', label=f'learn from own team residuals, k {k:+.2f} from {name}', live=c, ordinary=o,
                          insample=None, backtest=None))


def player_level(samples):
    print('\n=== 4. Players (projected and played): points and minutes against actual ===')
    print('  bias = forecast − actual. "pts@actual min" scales the forecast by actual/projected minutes (the rate kept);'
          ' O3 = the live model re-run with who played, starters and actual minutes (lineup oracle).')
    for name, rows in samples:
        if not rows:
            continue
        print(f'  {name}  (n {len(rows)}, {len({r["date"] for r in rows})} dates)')
        groups = [('all', rows)] + [(f'proj {lo}-{hi} min', [r for r in rows if lo <= r['pred_min'] < hi]) for lo, hi in MINUTE_BUCKETS] + \
                 [('starter', [r for r in rows if r['starter']]), ('bench', [r for r in rows if not r['starter']])] + \
                 [(k, [r for r in rows if r['new'] == k]) for k in ('same team', 'new team', 'no minutes last season')]
        for label, b in groups:
            if not b:
                continue
            d = [r['date'] for r in b]
            pb = mse([r['pred_pts'] - r['act_pts'] for r in b], d)
            mb = mse([r['pred_min'] - r['act_min'] for r in b], d)
            scaled = [r['pred_pts'] * r['act_min'] / r['pred_min'] - r['act_pts'] if r['pred_min'] > 0 else r['pred_pts'] - r['act_pts'] for r in b]
            gain = mse([abs(s) - abs(r['pred_pts'] - r['act_pts']) for s, r in zip(scaled, b)], d)
            o3 = [r for r in b if r['o3_pts'] is not None]
            o3txt = ''
            if o3:
                og = mse([abs(r['o3_pts'] - r['act_pts']) - abs(r['pred_pts'] - r['act_pts']) for r in o3], [r['date'] for r in o3])
                o3txt = f'  O3 − model {pm(*og, 2)}'
            print(f'    {label:24s} n {len(b):5d}  pts MAE {mae([r["pred_pts"] - r["act_pts"] for r in b]):5.2f} bias {pm(*pb, 2):>13s}  '
                  f'min MAE {mae([r["pred_min"] - r["act_min"] for r in b]):5.2f} bias {pm(*mb, 2):>13s}  '
                  f'pts@actual min MAE {mae(scaled):5.2f} ({pm(*gain, 2)}, {-gain[0] / mae([r["pred_pts"] - r["act_pts"] for r in b]):.0%}){o3txt}')


def team_totals(samples):
    print('\n=== 5. Team points against actual (bias = forecast − actual; regulation-equivalent: points x 240 / team minutes) ===')
    for name, rows in samples:
        rows = [r for r in rows if _finite(r.get('projected'))]
        if not rows:
            continue
        d = [r['date'] for r in rows]
        reg = [r for r in rows if _finite(r.get('actual_regulation'))]
        print(f'  {name:40s} n {len(rows):5d}  bias vs final {pm(*mse([r["projected"] - r["actual"] for r in rows], d), 2)}  '
              f'vs regulation-equivalent {pm(*mse([r["projected"] - r["actual_regulation"] for r in reg], [r["date"] for r in reg]), 2)}  '
              f'MAE (regulation) {mae([r["projected"] - r["actual_regulation"] for r in reg]):5.2f}')


def team_points_persistence(samples):
    print('  Do team-points errors persist? (actual − forecast team points, regulation-equivalent) on the team\'s earlier'
          ' scoring residual (offense) and its opponent\'s earlier allowed residual (defense), each sum / (games + 5);'
          ' residuals centred on the season\'s running league bias:')
    from stats_lite import ols
    for name, rows in samples:
        rows = [r for r in rows if _finite(r.get('projected')) and _finite(r.get('actual_regulation')) and r.get('opp')]
        if len(rows) < 60:
            continue
        acc_off, acc_def, league, x, y, d = {}, {}, {}, [], [], []
        for day in sorted({r['date'] for r in rows}):
            today = [r for r in rows if r['date'] == day]
            centre = {}
            for r in today:
                ls, ln = league.get(r['season'], (0., 0))
                centre[id(r)] = ls / ln if ln else 0.
                so, no = acc_off.get((r['season'], r['team']), (0., 0))
                sd, nd = acc_def.get((r['season'], r['opp']), (0., 0))
                x.append([1., so / (no + PERSISTENCE_PSEUDO), sd / (nd + PERSISTENCE_PSEUDO)])
                y.append(r['actual_regulation'] - r['projected'] - centre[id(r)])
                d.append(day)
            for r in today:
                e = r['actual_regulation'] - r['projected'] - centre[id(r)]
                so, no = acc_off.get((r['season'], r['team']), (0., 0))
                acc_off[(r['season'], r['team'])] = (so + e, no + 1)
                sd, nd = acc_def.get((r['season'], r['opp']), (0., 0))
                acc_def[(r['season'], r['opp'])] = (sd + e, nd + 1)
            for r in today:
                ls, ln = league.get(r['season'], (0., 0))
                league[r['season']] = (ls + r['actual_regulation'] - r['projected'], ln + 1)
        beta, se = ols(x, y, d)
        print(f'  {name:40s} n {len(y):5d}  intercept {beta[0]:+.2f} ± {se[0]:.2f}  offense {beta[1]:+.2f} ± {se[1]:.2f}  '
              f'defense {beta[2]:+.2f} ± {se[2]:.2f}')


def game_totals(samples):
    print('  Game totals against the actual final total (the close\'s total line as the bar; the model projects regulation):')
    for name, rows in samples:
        rows = [r for r in rows if _finite(r.get('close_total')) and _finite(r.get('model_total')) and _finite(r.get('actual_total'))]
        if not rows:
            continue
        d = [r['date'] for r in rows]
        print(f'  {name:40s} n {len(rows):5d}  model bias {pm(*mse([r["model_total"] - r["actual_total"] for r in rows], d), 2)} '
              f'MAE {mae([r["model_total"] - r["actual_total"] for r in rows]):5.2f}   close bias {pm(*mse([r["close_total"] - r["actual_total"] for r in rows], d), 2)} '
              f'MAE {mae([r["close_total"] - r["actual_total"] for r in rows]):5.2f}')


def transfers(live, backtests, items):
    """Corrections fitted on the backtest, applied to the live games: out-of-sample recoverable MAE."""
    from stats_lite import ols
    print('\n=== 6. What each correction would recover, fitted on the backtest and applied to the live games (out of sample) ===')
    print('  change in live margin MAE against actual (negative = better), paired, ± one SE by date: all 122 live games |'
          ' the 99 without an availability surprise; in-sample fits in brackets')
    rows = [r for r in live if r['close'] is not None]
    plain = [r for r in rows if not r['surprise']]

    def change(fn, sample):
        return mse([abs(fn(r) - r['actual']) - abs(r['blend'] - r['actual']) for r in sample], [r['date'] for r in sample])

    def report(label, fn, insample=None, backtest=None, kind='margin'):
        c, o = change(fn, rows), change(fn, plain)
        extra = (f'   [live in-sample: {pm(*insample)}]' if insample else '') + (f'   [backtest in-sample: {pm(*backtest)}]' if backtest else '')
        print(f'  {label:72s} {pm(*c)} | {pm(*o)}{extra}')
        items.append(dict(kind=kind, label=label, live=c, ordinary=o, insample=insample, backtest=backtest))
    print(f'  live blend MAE {mae([r["blend"] - r["actual"] for r in rows]):.2f} | {mae([r["blend"] - r["actual"] for r in plain]):.2f}, '
          f'close {mae([r["close"] - r["actual"] for r in rows]):.2f} | {mae([r["close"] - r["actual"] for r in plain]):.2f}')
    lw = best_weight(rows)
    print(f'  (live in-sample w* {lw:.2f}: {pm(*change(lambda r: (1 - lw) * r["ps"] + lw * r["rating"], rows))}; not a transfer)')
    for name, bt in backtests:
        bt = [r for r in bt if r['close'] is not None]
        if len(bt) < 100:
            continue
        w = best_weight(bt)
        report(f'rating weight {w:.2f} (w* of {name})', lambda r, w=w: (1 - w) * r['ps'] + w * r['rating'],
               backtest=change(lambda r: (1 - w) * r['ps'] + w * r['rating'], bt))
        if name.endswith('all'):
            report(f'rating weight 1.00 (rating half alone; backtest: {name})', lambda r: r['rating'],
                   backtest=change(lambda r: r['rating'], bt))
            by = {}
            for lo, hi in GP_BUCKETS:
                b = [r for r in bt if lo <= sum(r['gp']) / 2 < hi]
                by[(lo, hi)] = best_weight(b) if len(b) >= 30 else RATING_WEIGHT_DEFAULT

            def wg(r):
                return next(v for (lo, hi), v in by.items() if lo <= sum(r['gp']) / 2 < hi)
            report('rating weight by games played (' + ', '.join(f'{lo}-{hi - 1}: {v:.2f}' for (lo, hi), v in by.items()) + f') from {name}',
                   lambda r: (1 - wg(r)) * r['ps'] + wg(r) * r['rating'], backtest=change(lambda r: (1 - wg(r)) * r['ps'] + wg(r) * r['rating'], bt))
        beta, _ = ols([[1., r['blend']] for r in bt], [r['actual'] for r in bt])
        lb, _ = ols([[1., r['blend']] for r in rows], [r['actual'] for r in rows])
        report(f'a + b x blend from {name} (a {beta[0]:+.2f}, b {beta[1]:.3f})', lambda r: beta[0] + beta[1] * r['blend'],
               change(lambda r: lb[0] + lb[1] * r['blend'], rows), change(lambda r: beta[0] + beta[1] * r['blend'], bt))
        beta2, _ = ols([[1., r['ps'], r['rating']] for r in bt], [r['actual'] for r in bt])
        report(f'a + b1 psum + b2 rating from {name} ({beta2[0]:+.2f}, {beta2[1]:.3f}, {beta2[2]:.3f})',
               lambda r: beta2[0] + beta2[1] * r['ps'] + beta2[2] * r['rating'],
               backtest=change(lambda r: beta2[0] + beta2[1] * r['ps'] + beta2[2] * r['rating'], bt))
        shift = sum(r['actual'] - r['blend'] for r in bt) / len(bt)
        report(f'home shift {shift:+.2f} from {name}', lambda r: r['blend'] + shift)
    whole = next((bt for name, bt in backtests if name.endswith('all')), None)
    report('drop the absence term (parked; backtest: seeded, all)', lambda r: r['blend_noabs'],
           backtest=change(lambda r: r['blend_noabs'], [r for r in whole if r['close'] is not None]) if whole else None)
    o3 = [r for r in rows if _finite(r.get('o3_blend'))]
    if o3:
        c = change(lambda r: r['o3_blend'], o3)
        o = change(lambda r: r['o3_blend'], [r for r in o3 if not r['surprise']])
        print(f'  {"lineups known: who played, starters, actual minutes (O3 oracle)":72s} {pm(*c)} | {pm(*o)}')
        items.append(dict(kind='margin', label='perfect lineups and minutes (O3 oracle; upper bound of lineup news)',
                          live=c, ordinary=o, insample=None, backtest=None))
    return rows, plain


RATING_WEIGHT_DEFAULT = .65


def player_transfer(live_players, backtest_players, name, items):
    print(f'\n  Player points: bias by projected-minutes bucket fitted on {name}, subtracted live (change in live points MAE):')
    d = [r['date'] for r in live_players]
    shift = {}
    for lo, hi in MINUTE_BUCKETS:
        b = [r['pred_pts'] - r['act_pts'] for r in backtest_players if lo <= r['pred_min'] < hi]
        shift[(lo, hi)] = sum(b) / len(b) if b else 0.

    def bucket(m):
        return next(k for k in shift if k[0] <= m < k[1])
    change = mse([abs(r['pred_pts'] - shift[bucket(r['pred_min'])] - r['act_pts']) - abs(r['pred_pts'] - r['act_pts'])
                  for r in live_players], d)
    lb = sum(r['pred_pts'] - r['act_pts'] for r in live_players) / len(live_players)
    insample = mse([abs(r['pred_pts'] - lb - r['act_pts']) - abs(r['pred_pts'] - r['act_pts']) for r in live_players], d)
    print('    backtest bias by bucket: ' + ', '.join(f'{lo}-{hi} {v:+.2f}' for (lo, hi), v in shift.items()))
    print(f'    live points MAE change {pm(*change)}   [in-sample: subtract the live mean bias {lb:+.2f}: {pm(*insample)}]')
    items.append(dict(kind='points', label=f'points bias by minutes bucket from {name}', live=change, insample=insample, backtest=None))


def player_upper_bounds(live_players, ref_players, items):
    """Perfect minutes: the O3 oracle live (model re-run) and the rate-kept scaling in the backtest."""
    o3 = [r for r in live_players if r['o3_pts'] is not None]
    if o3:
        c = mse([abs(r['o3_pts'] - r['act_pts']) - abs(r['pred_pts'] - r['act_pts']) for r in o3], [r['date'] for r in o3])
        def scaled(r):
            return r['pred_pts'] * r['act_min'] / r['pred_min'] - r['act_pts'] if r['pred_min'] > 0 else r['pred_pts'] - r['act_pts']
        b = mse([abs(scaled(r)) - abs(r['pred_pts'] - r['act_pts']) for r in ref_players], [r['date'] for r in ref_players])
        items.append(dict(kind='points', label='perfect minutes (O3 oracle live; backtest: forecast scaled to actual minutes)',
                          live=c, insample=None, backtest=b))


def totals_item(live_teams, ref_teams, items):
    """What the production-path backtest's team-points shortfall, added back as a constant, does to live team points."""
    ref = [t for t in ref_teams if _finite(t.get('projected')) and _finite(t.get('actual_regulation'))]
    live = [t for t in live_teams if _finite(t.get('projected')) and _finite(t.get('actual_regulation'))]
    shift = -sum(t['projected'] - t['actual_regulation'] for t in ref) / len(ref)
    c = mse([abs(t['projected'] + shift - t['actual_regulation']) - abs(t['projected'] - t['actual_regulation']) for t in live],
            [t['date'] for t in live])
    b = mse([abs(t['projected'] + shift - t['actual_regulation']) - abs(t['projected'] - t['actual_regulation']) for t in ref],
            [t['date'] for t in ref])
    print(f'\n  Team points: adding the production-path backtest\'s shortfall ({shift:+.2f} per team, a constant stand-in for'
          f' exp/totals-shortfall) changes live team-points MAE by {pm(*c)} (backtest in-sample {pm(*b)})')
    items.append(dict(kind='team points', label=f'add {shift:+.2f} per team (the production-path backtest bias; exp/totals-shortfall stand-in)',
                      live=c, insample=None, backtest=b))


def ranking(items):
    print('\n=== 7. Ranked: change in live MAE against actual each source could recover (negative = better; ± one SE by date) ===')
    print('  columns: all live games | live games without an availability surprise (margin rows only); fits from the backtest'
          ' applied live (out of sample); oracle rows use the outcome itself (upper bounds)')
    order = sorted(items, key=lambda i: (i['kind'], i['live'][0]))
    for i in order:
        ordinary = f' | {pm(*i["ordinary"]):>16s}' if i.get('ordinary') else f' | {"":16s}'
        extra = (f'  live in-sample {pm(*i["insample"])}' if i.get('insample') else '') + \
                (f'  backtest {pm(*i["backtest"])}' if i.get('backtest') else '')
        print(f'  [{i["kind"]:11s}] {pm(*i["live"]):>16s}{ordinary}  {i["label"]}{extra}')


def tables(args):
    from nba_forecast.backtest import _day  # noqa: F401  (package import check)
    live = load_live(args.live, args.live_finals, args.datasets, args.oracle_o3)
    inputs = load_season_inputs(args.datasets)
    ref = load_backtest(args.production_path, inputs, 'production-path reference')
    seeded = {'games': [], 'players': [], 'teams': []}
    for directory in args.seeded:
        part = load_backtest(directory, inputs, 'seeded')
        for k in seeded:
            seeded[k].extend(part[k])
    print(f'live: {args.live} ({live["checks"]["games"]} games with a replayed margin and a close; '
          f'(1 − w) x player sum + w x rating reproduces the margin in {live["checks"]["recomposed"]})')
    print(f'production-path reference: {args.production_path} ({len(ref["games"])} games, the gate window: first 30% of dates excluded;'
          f' {ref["outside"]} games of 2026-27 in the 2025-26 files left out)')
    print(f'seeded replays (production path, previous season seeded, warmup 0, halves recorded): '
          f'{", ".join(str(p) for p in args.seeded) or "none"} ({len(seeded["games"])} games)')
    ref_ids = {r['game_id'] for r in ref['games']}
    if seeded['games']:
        refm = {r['game_id']: r for r in ref['games']}
        pairs = [(refm[s['game_id']], s) for s in seeded['games'] if s['game_id'] in refm]
        if pairs:
            d = [s['date'] for _, s in pairs]
            print(f'  seeded on the reference\'s games ({len(pairs)}): margin MAE seeded {mae([s["blend"] - s["actual"] for _, s in pairs]):.3f} '
                  f'reference {mae([r["blend"] - r["actual"] for r, _ in pairs]):.3f}; seeded − reference '
                  f'{pm(*mse([abs(s["blend"] - s["actual"]) - abs(r["blend"] - r["actual"]) for r, s in pairs], d))}; '
                  f'mean |seeded − reference margin| {sum(abs(s["blend"] - r["blend"]) for r, s in pairs) / len(pairs):.2f}')
    lh = halves_rows(live['games'])
    sh = halves_rows(seeded['games'])
    samples = [('live, all 122', lh)]
    flags = surprise_flags(args.live, args.live_finals)
    for r in lh:
        r['surprise'] = flags.get(r['game_id'], False)
    samples.append(('live, no availability surprise', [r for r in lh if not flags.get(r['game_id'])]))
    if sh:
        samples += [('seeded backtest, early (gp < 15)', subset(sh, early=True)),
                    ('seeded backtest, later (gp >= 15)', subset(sh, early=False)),
                    ('seeded backtest, gate window', subset(sh, ids=ref_ids)),
                    ('seeded backtest, all', sh)]
    margin_halves(samples)
    short = [x for x in samples if not x[0].endswith(('window', 'all'))]
    calibration(short + [('production-path reference (blend only)', [dict(r, ps=None, rating=None) for r in ref['games']])])
    early_and_turnover([samples[0]] + [x for x in samples if x[0] == 'seeded backtest, all'])
    persistence = team_persistence(short + [x for x in samples if x[0] == 'seeded backtest, all'])
    seeded_players = seeded['players']
    early_ids = {r['game_id'] for r in seeded['games'] if sum(r['gp']) / 2 < EARLY_GAMES}
    player_level([('live (replayed main)', live['players']),
                  ('production-path reference', ref['players'])] +
                 ([('seeded backtest, early (gp < 15)', [p for p in seeded_players if p['game_id'] in early_ids])] if seeded_players else []))
    team_totals([('live (replayed main)', live['teams']), ('production-path reference', ref['teams'])] +
                ([('seeded backtest, early (gp < 15)', [t for t in seeded['teams'] if t['game_id'] in early_ids]),
                  ('seeded backtest, all', seeded['teams'])] if seeded['teams'] else []))
    team_points_persistence([('live (replayed main)', live['teams']), ('production-path reference', ref['teams'])] +
                            ([('seeded backtest, all', seeded['teams'])] if seeded['teams'] else []))
    game_totals([('live (replayed main)', [dict(r, model_total=r['total']) for r in live['games']]),
                 ('production-path reference', _with_model_total(ref))] +
                ([('seeded backtest, early (gp < 15)', [r for r in _with_model_total(seeded) if r['game_id'] in early_ids])]
                 if seeded['games'] else []))
    items = []
    transfers(lh, [(n, rows) for n, rows in samples if n.startswith('seeded') and n.endswith(('all', '(gp < 15)'))], items)
    player_transfer(live['players'], ref['players'], 'the production-path reference', items)
    if seeded_players:
        player_transfer(live['players'], [p for p in seeded_players if p['game_id'] in early_ids], 'the seeded early games', items)
    persistence_transfer(lh, {k: v for k, v in persistence.items() if k.endswith(('all', '(gp < 15)'))}, items)
    player_upper_bounds(live['players'], ref['players'], items)
    totals_item(live['teams'], ref['teams'], items)
    ranking(items)


def _with_model_total(part):
    total = {}
    for t in part['teams']:
        if _finite(t.get('projected')):
            total[t['game_id']] = total.get(t['game_id'], 0.) + t['projected']
    return [dict(r, model_total=total.get(r['game_id'])) for r in part['games']]


if __name__ == '__main__':
    main()
