"""Player-sum margin fallbacks in the live pregame runs: why, when, how wrong, and what the market report scores.

A run without an estimated margin forecast used to publish the difference of its team totals, even partial
ones (Hawks at Pacers, 2026-11-02: -55.7). Reads the per-(run, game) extract of ``news_timing.py extract``
(branch measure/news-timing), never the ledger, plus the copy's schedule, finals and ESPN game lines.

    python3 scripts/research/fallback_margins.py slim EXTRACT.jsonl SLIM.jsonl
    python3 scripts/research/fallback_margins.py saved COPY OUT.json ROWID:GAME_ID ...   # heavy: ledger rows by rowid
    python3 scripts/research/fallback_margins.py diagnose COPY SLIM.jsonl OUT.json [SAVED.json ...]

Team status is inferred from the extract's prediction rows (it has no ``components``): partial when a withheld
player is not a covered bench player, fewer than five are projected, or minutes are unallocated with fewer than
eight projected (``totals.game_totals`` schema 6). Older runs saved stricter schemas, so ``saved`` reads the
run's own saved ``game_totals`` status and margin reason (``run_records`` by rowid, ``json_extract``, no
aggregation over JSON); ``diagnose`` uses them over the inference for every run-game they cover. +/- is one standard error clustered by Eastern game date
(``backtest._mean_se``).
"""
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import fmean
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nba_forecast.backtest import _mean_se as _se  # noqa: E402
from nba_forecast.market_lines import closing_lines, read_game_lines  # noqa: E402

COVERED = {'cold_start', 'no_scoring_opportunity_evidence', 'team_minutes_unresolved', 'availability_questionable'}
ET = ZoneInfo('America/New_York')


def _mean_se(pairs):
    """(mean, one SE clustered by Eastern date) of (date, value) pairs."""
    values = [v for _, v in pairs]
    return fmean(values), _se(values, [(d,) for d, _ in pairs])[0]


def slim(src, out):
    """One pass over the large extract: per (run, game) the margin block and per-team prediction status counts."""
    with open(src) as f, open(out, 'w') as w:
        for line in f:
            r = json.loads(line)
            teams = {}
            for side in ('home', 'away'):
                preds = [p for p in r['predictions'] if p['team_id'] == r[side + '_team_id']]
                weight = lambda p: p.get('participation_probability') if p.get('participation_probability') is not None else 1.
                teams[side] = dict(
                    team_id=r[side + '_team_id'], projected=sum(p['status'] == 'projected' for p in preds),
                    out=sum(p['status'] == 'out' for p in preds),
                    withheld=[dict(player_id=p['player_id'], name=p['name'], status=p['status'], flags=p.get('flags'),
                                   history_games=p.get('history_games')) for p in preds if p['status'] not in ('projected', 'out')],
                    minutes=sum(weight(p) * (p.get('expected_minutes') or 0.) for p in preds if p['status'] == 'projected'),
                    points=sum(weight(p) * (p.get('points_mean') or 0.) for p in preds if p['status'] == 'projected'))
            w.write(json.dumps(dict({k: r[k] for k in ('run_id', 'rowid', 'game_id', 'stage', 'created_at', 'as_of', 'start_time',
                                                       'model_version', 'model_key', 'home_team_id', 'away_team_id', 'margin')},
                                    teams=teams)) + '\n')


def saved(copy, out, items):
    """The saved game_totals status and margin forecast reason of named run-games (``ROWID:GAME_ID``)."""
    import sqlite3
    conn = sqlite3.connect((Path(copy) / 'forecast.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    result = []
    for item in items:
        rowid, gid = item.split(':')
        row = conn.execute("SELECT run_id, json_extract(forecast_json,'$.margin_forecasts'), json_extract(forecast_json,'$.game_totals')"
                           " FROM run_records WHERE rowid=?", (int(rowid),)).fetchone()
        mf = next((m for m in json.loads(row[1] or '[]') if m.get('game_id') == gid), None) or {}
        gt = next((x for x in json.loads(row[2] or '[]') if x.get('game_id') == gid), None) or {}
        result.append(dict(rowid=int(rowid), run_id=row[0], game_id=gid, margin=dict(status=mf.get('status'), reason=mf.get('reason')),
                           totals=dict(status=gt.get('status'), schema_version=gt.get('schema_version'),
                                       teams=[{k: x.get(k) for k in ('side', 'status', 'points_mean', 'projected_players', 'out_players',
                                                                     'unresolved_player_ids', 'unallocated_minutes')} for x in gt.get('teams', [])])))
    Path(out).write_text(json.dumps(result, indent=1))


def t(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def diagnose(copy, slim_path, out, *saved_paths):
    copy = Path(copy)
    known = {(x['rowid'], x['game_id']): x for path in saved_paths for x in json.loads(Path(path).read_text())}


    runs = [json.loads(line) for line in open(slim_path)]
    schedule = json.loads((copy / 'schedule' / 'latest.json').read_text())
    games = {g['game_id']: g for g in schedule['games']}

    actual, close = {}, {}
    for gid in sorted({r['game_id'] for r in runs}):
        path = copy / 'finals' / (gid + '.json')
        if not path.exists():
            continue
        rows = json.loads(path.read_text())
        g = games[gid]
        h = sum(x['pts'] for x in rows if x['team_id'] == g['home_team_id'])
        a = sum(x['pts'] for x in rows if x['team_id'] == g['away_team_id'])
        actual[gid] = dict(margin=h - a, total=h + a, home=h, away=a)
        try:
            lines = closing_lines(read_game_lines(copy, gid), g['start_time'])
        except ValueError:
            lines = None
        for pid, line in sorted((lines or {}).items()):
            spread = line['lines']['spread']['home']['current_line']
            if spread is not None:
                close[gid] = -spread
                break

    def team_state(team):
        """Inferred game_totals team status from the extract (no components there): partial if a withheld player is not
        a covered bench player, fewer than five projected, or minutes missing with fewer than eight projected."""
        uncovered = [w for w in team['withheld'] if not (set(w.get('flags') or []) - {'availability_questionable'}
                                                          and set(w.get('flags') or []) <= COVERED)]
        if team['projected'] == 0:
            return 'unavailable', 'no projected players'
        if team['projected'] < 5:
            return 'partial', f"{team['projected']} projected"
        if uncovered:
            return 'partial', 'withheld: ' + ', '.join(f"{w['name']} [{','.join(w.get('flags') or [])}]" for w in uncovered)
        if team['minutes'] < 240 - 1e-6 and team['projected'] < 8:
            return 'partial', f"{team['projected']} projected, {240 - team['minutes']:.1f} min unallocated"
        return 'complete', None

    def kind(r):
        m = r['margin']
        if m['basis'] == 'margin_forecast':
            return 'estimated'
        if m['published'] is None:
            return 'no_totals'
        states = {s: team_state(r['teams'][s]) for s in ('home', 'away')}
        complete = all(v[0] == 'complete' for v in states.values())
        if (r['rowid'], r['game_id']) in known:
            r['saved'] = known[(r['rowid'], r['game_id'])]
            complete = (r['saved']['totals'] or {}).get('status') == 'complete'
        if m['status'] is None:
            return 'no_margin_forecast:' + ('complete' if complete else 'partial')
        return 'unavailable:' + ('complete' if complete else 'partial')

    by_game = defaultdict(list)
    for r in runs:
        r['kind'] = kind(r)
        r['date'] = t(r['start_time']).astimezone(ET).date().isoformat()
        by_game[r['game_id']].append(r)
    for gid in by_game:
        by_game[gid].sort(key=lambda r: (t(r['created_at']), r['rowid']))

    finished = sorted(g for g in by_game if g in actual)
    print(f'run-games {len(runs)}, games {len(by_game)}, finished games with a final {len(finished)}, with a close {sum(g in close for g in finished)}')
    allrg = [r for r in runs if r['game_id'] in actual]
    print(f'run-games of finished games: {len(allrg)}; player-sum basis: {sum(r["margin"]["basis"] == "player_sum" for r in allrg)}')
    print('\nKind of every run-game of a finished game (team status as saved where read, else inferred from the extract):')
    for k, n in sorted(Counter(r['kind'] for r in allrg).items()):
        print(f'  {k:34s} {n:5d}')
    read = [r for r in allrg if 'saved' in r]
    if read:
        print(f'  saved game_totals read for {len(read)} run-games: status',
              dict(sorted(Counter(str((r['saved']['totals'] or {}).get('status')) for r in read).items())), '; margin forecast reason',
              dict(sorted(Counter(str((r['saved']['margin'] or {}).get('reason')) for r in read).items())),
              '; inferred complete but saved not complete',
              sum((r['saved']['totals'] or {}).get('status') != 'complete' and all(team_state(r['teams'][s])[0] == 'complete' for s in ('home', 'away'))
                  for r in read))

    fb = [r for r in allrg if r['margin']['basis'] == 'player_sum']
    print('\nFallback run-games by model version and kind:')
    for (v, k), n in sorted(Counter((r['model_version'], r['kind']) for r in fb).items()):
        print(f'  {v:40s} {k:34s} {n:5d}')
    print('\nFallback run-games by Eastern date and kind:')
    for (d, k), n in sorted(Counter((r['date'], r['kind']) for r in fb).items()):
        print(f'  {d} {k:34s} {n:5d}')
    print('\nFallback run-games by stage and kind:')
    for (s, k), n in sorted(Counter((r['stage'], r['kind']) for r in fb).items()):
        print(f'  {s:12s} {k:34s} {n:5d}')

    print('\nGames with a partial-roster fallback: why the totals were incomplete (team, inferred reason, runs):')
    reasons = Counter()
    for r in fb:
        if not r['kind'].endswith('partial'):
            continue
        for s in ('home', 'away'):
            st, why = team_state(r['teams'][s])
            if st != 'complete':
                reasons[(r['date'], r['game_id'], s, r['teams'][s]['team_id'], why)] += 1
    for (d, gid, s, tid, why), n in sorted(reasons.items()):
        print(f'  {d} {gid} {s} {tid} x{n}: {why[:160]}')

    def nearest_estimated(r):
        """Latest earlier estimated run of the same game (what a selector that prefers a valid margin would use), else the
        nearest later one."""
        rs = by_game[r['game_id']]
        earlier = [x for x in rs if x['kind'] == 'estimated' and (t(x['created_at']), x['rowid']) < (t(r['created_at']), r['rowid'])]
        if earlier:
            return earlier[-1], 'earlier'
        later = [x for x in rs if x['kind'] == 'estimated']
        return (later[0], 'later') if later else (None, None)

    print('\nFallback run-games against the actual result, paired with the same game\'s nearest estimated run'
          ' (± one SE clustered by Eastern date; error = |margin − actual|):')
    detail = []
    for group in ('unavailable:partial', 'unavailable:complete', 'no_margin_forecast:partial', 'no_margin_forecast:complete'):
        rows = []
        for r in fb:
            if r['kind'] != group:
                continue
            e, where = nearest_estimated(r)
            a = actual[r['game_id']]
            rows.append(dict(r=r, e=e, where=where, fb_err=abs(r['margin']['published'] - a['margin']),
                             est_err=abs(e['margin']['published'] - a['margin']) if e else None,
                             fb_total=(r['margin']['teams']['home']['points_mean'] or 0) + (r['margin']['teams']['away']['points_mean'] or 0),
                             est_total=(e['margin']['teams']['home']['points_mean'] + e['margin']['teams']['away']['points_mean']) if e else None))
        if not rows:
            continue
        paired = [x for x in rows if x['e']]
        games_n = len({x['r']['game_id'] for x in rows})
        print(f'  {group}: {len(rows)} run-games over {games_n} games; with an estimated run of the same game {len(paired)}'
              f" ({sum(x['where'] == 'earlier' for x in paired)} earlier, {sum(x['where'] == 'later' for x in paired)} later only)")
        if paired:
            f = _mean_se([(x['r']['date'], x['fb_err']) for x in paired])
            s = _mean_se([(x['r']['date'], x['est_err']) for x in paired])
            d = _mean_se([(x['r']['date'], x['fb_err'] - x['est_err']) for x in paired])
            ft = _mean_se([(x['r']['date'], abs(x['fb_total'] - actual[x['r']['game_id']]['total'])) for x in paired])
            st = _mean_se([(x['r']['date'], abs(x['est_total'] - actual[x['r']['game_id']]['total'])) for x in paired])
            print(f"    margin MAE: fallback {f[0]:.2f}, nearest estimated {s[0]:.2f}, fallback − estimated {d[0]:+.2f} ± {d[1]:.2f}")
            print(f"    total MAE:  fallback {ft[0]:.2f}, nearest estimated {st[0]:.2f}")
            worst = max(x['fb_err'] for x in paired)
            print(f"    largest fallback margin error {worst:.1f}; fallback margin errors > 20: {sum(x['fb_err'] > 20 for x in paired)}")
        for x in rows:
            detail.append(dict(group=group, run_id=x['r']['run_id'], rowid=x['r']['rowid'], game_id=x['r']['game_id'],
                               date=x['r']['date'], stage=x['r']['stage'], created_at=x['r']['created_at'],
                               model_version=x['r']['model_version'],
                               published=x['r']['margin']['published'], total=x['fb_total'],
                               team_points={s: x['r']['margin']['teams'][s]['points_mean'] for s in ('home', 'away')},
                               team_state={s: team_state(x['r']['teams'][s]) for s in ('home', 'away')},
                               nearest_estimated=x['e']['run_id'] if x['e'] else None, nearest_where=x['where'],
                               nearest_margin=x['e']['margin']['published'] if x['e'] else None,
                               actual=actual[x['r']['game_id']], close=close.get(x['r']['game_id'])))

    # The market report's selection (daily_review._selected): last pregame run per game/stage, final stage first.
    def select(rs, valid=None):
        for stage in ('final', 'preliminary'):
            cand = [x for x in rs if x['stage'] == stage and (valid is None or valid(x))]
            if cand:
                return cand[-1]
        return None

    print('\nThe market report\'s selected run per finished game with a close (last final-stage pregame run, else preliminary):')
    sel = {g: select(by_game[g]) for g in finished if g in close}
    print('  kind of the selected run:', dict(sorted(Counter(r['kind'] for r in sel.values()).items())))
    for g, r in sorted(sel.items(), key=lambda kv: kv[1]['start_time']):
        if r['kind'] != 'estimated':
            e, where = nearest_estimated(r)
            print(f"  {r['date']} {g} {r['stage']} {r['kind']}: published {r['margin']['published']:+.1f}"
                  f" (home {r['margin']['teams']['home']['points_mean']}, away {r['margin']['teams']['away']['points_mean']}),"
                  f" actual {actual[g]['margin']:+d}, close {close[g]:+.1f}, nearest estimated ({where}) "
                  f"{e['margin']['published'] if e else float('nan'):+.1f}  run {r['run_id']}")

    Path(out).write_text(json.dumps(dict(detail=detail, selected={g: r['run_id'] for g, r in sel.items()}), indent=1))

    # After the fix: a run publishes a margin only when estimated or from complete team totals; the market report scores
    # the selected run if it publishes one, else the latest earlier pregame run of the game that does, else nothing.
    def publishes(r):
        return r['kind'] == 'estimated' or r['kind'].endswith(':complete')

    print('\nWhat the 445 player-sum-basis run-games publish after the fix:')
    after = Counter('nothing (no team totals, unchanged)' if r['kind'] == 'no_totals' else
                    'player sum of complete teams (unchanged)' if publishes(r) else
                    'no margin: incomplete team totals (was the partial sum)' for r in fb)
    for k, n in sorted(after.items()):
        print(f'  {k:58s} {n:4d}')

    print('\nMarket report before/after on the finished games with a close (margin |error| vs actual; ± one SE clustered by Eastern date):')
    before_rows, after_rows, changed = {}, {}, []
    for g, r in sel.items():
        before_rows[g] = r
        if publishes(r):
            after_rows[g] = r
            continue
        earlier = [x for x in by_game[g] if publishes(x) and (t(x['created_at']), x['rowid']) < (t(r['created_at']), r['rowid'])]
        after_rows[g] = earlier[-1] if earlier else None
        changed.append(g)
    for g in sorted(changed, key=lambda g: sel[g]['start_time']):
        b, a = before_rows[g], after_rows[g]
        print(f"  {g} {b['date']}: before run {b['run_id'][:8]} ({b['stage']}) margin {b['margin']['published']:+.1f}"
              f" total {b['margin']['teams']['home']['points_mean'] + b['margin']['teams']['away']['points_mean']:.1f};"
              + (f" after run {a['run_id'][:8]} ({a['stage']}) margin {a['margin']['published']:+.1f}"
                 f" total {a['margin']['teams']['home']['points_mean'] + a['margin']['teams']['away']['points_mean']:.1f}" if a else
                 ' after: not scored, listed under margin_unavailable')
              + f"; actual {actual[g]['margin']:+d} / {actual[g]['total']}, close {close[g]:+.1f}")
    for label, keys in (('affected games', [g for g in changed]), ('all games with a close', sorted(sel))):
        both = [g for g in keys if after_rows[g] is not None]
        b = _mean_se([(sel[g]['date'], abs(before_rows[g]['margin']['published'] - actual[g]['margin'])) for g in keys])
        print(f'  {label}: before n={len(keys)} model margin MAE {b[0]:.2f}')
        if both:
            b2 = _mean_se([(sel[g]['date'], abs(before_rows[g]['margin']['published'] - actual[g]['margin'])) for g in both])
            a2 = _mean_se([(sel[g]['date'], abs(after_rows[g]['margin']['published'] - actual[g]['margin'])) for g in both])
            c2 = _mean_se([(sel[g]['date'], abs(close[g] - actual[g]['margin'])) for g in both])
            print(f'    scored after the fix n={len(both)}: before {b2[0]:.2f}, after {a2[0]:.2f}, close {c2[0]:.2f}')
            if len(both) > 1:
                d = _mean_se([(sel[g]['date'], abs(after_rows[g]['margin']['published'] - actual[g]['margin'])
                               - abs(before_rows[g]['margin']['published'] - actual[g]['margin'])) for g in both])
                print(f'    after − before {d[0]:+.3f} ± {d[1]:.3f}')


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'slim':
        slim(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 4 and sys.argv[1] == 'saved':
        saved(sys.argv[2], sys.argv[3], sys.argv[4:])
    elif len(sys.argv) >= 5 and sys.argv[1] == 'diagnose':
        diagnose(*sys.argv[2:])
    else:
        sys.exit(__doc__)
