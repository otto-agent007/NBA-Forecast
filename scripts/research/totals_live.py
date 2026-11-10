"""Live team totals: where the published totals miss the finals, and what this checkout's code would publish.

    nice -n 19 ionice -c3 python3 scripts/research/totals_live.py --copy COPY --base MAIN_CHECKOUT --output OUT/live.json.gz
    python3 scripts/research/totals_live.py --read OUT/live.json.gz

``COPY`` is a copy of the production directory, never production itself; it is
opened read-only. The games are the market report's (``market/report.json``):
each finished game's scored pregame run, its closing total and final. For each
run the script reads the saved snapshot, forecast and history by key
(``run_records`` by ``run_id``, ``histories`` by digest, one history at a time),
re-forecasts the saved snapshot with the ``nba_forecast`` package beside this
script (``forecast(snapshot, eligible_history(snapshot, history))``, as the tick
does), and keeps each team's players from both the saved and the replayed
forecast. ``--base`` names a second checkout (main, say) whose package is
loaded under another name and replays the same snapshots in the same pass, so
each history is read once. With ``--no-reforecast`` only the saved forecast is kept.

``--read`` prints, for the saved and the replayed forecasts, the split of each
team's projected minus actual regulation-equivalent points that
``totals_decomposition.py`` prints for a backtest (played, participation,
unprojected players, the replacement credit), with the final's overtime beside
it; then game totals against the final and the closing total, and this
checkout's replay minus the base's, paired game by game.
The ± is one standard error clustered by Eastern game date.
"""
import argparse
from collections import defaultdict
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats_lite import ols  # noqa: E402

EASTERN = ZoneInfo('America/New_York')
PRODUCTION = Path('/mnt/2TBHDD/NBA-Forecast/repo/data/operations/production')
PARTS = ('played', 'participation', 'unprojected', 'listed out', 'withheld', 'not on forecast', 'replacement')


def mean_se(values, clusters):
    if len(set(clusters)) < 2:
        return (values[0] if values else float('nan')), float('nan')
    beta, se = ols([[1.]] * len(values), values, clusters)
    return beta[0], se[0]


def _players(forecast, game_id):
    """The game's prediction rows, reduced to what a team total is made of."""
    return [dict(player_id=p['player_id'], team_id=p['team_id'], status=p['status'],
                 participation_probability=p.get('participation_probability'), points_mean=p.get('points_mean'),
                 expected_minutes=p.get('expected_minutes'), expected_tov=p.get('expected_tov'),
                 points_before_scoreline=((p.get('components') or {}).get('scoreline') or {}).get('points_mean_before'))
            for p in forecast['predictions'] if p.get('game_id') == game_id]


def _summary(forecast, game_id):
    totals = [g for g in forecast.get('game_totals') or [] if g['game_id'] == game_id]
    margins = [m for m in forecast.get('margin_forecasts') or [] if m['game_id'] == game_id]
    teams = {t['team_id']: dict(points_mean=t['points_mean'], residual_points=t.get('residual_points'),
                                unallocated_minutes=t.get('unallocated_minutes'), status=t['status'])
             for t in (totals[0]['teams'] if totals else [])}
    margin = margins[0] if margins else {}
    return dict(teams=teams, total=totals[0]['points_mean'] if totals else None,
                margin=margin.get('mean_margin') if margin.get('status') == 'estimated' else None,
                player_sum_margin=margin.get('player_sum_margin'), players=_players(forecast, game_id))


def load_package(directory, alias='nba_forecast_base'):
    """``forecast``, ``eligible_history`` and the ledger's canonical JSON of another checkout's package, under ``alias``."""
    import importlib
    import importlib.util
    init = Path(directory).resolve() / 'nba_forecast' / '__init__.py'
    spec = importlib.util.spec_from_file_location(alias, init, submodule_search_locations=[str(init.parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    load = lambda name: importlib.import_module(f'{alias}.{name}')
    return load('model').forecast, load('profiles').eligible_history, load('store')._canonical


def _replay(code, snapshot_json, history, forecast_json, game_id):
    forecast, eligible_history, canonical = code
    snapshot = json.loads(snapshot_json)  # each replay gets its own objects
    try:
        replayed = forecast(snapshot, eligible_history(snapshot, history))
        return _summary(replayed, game_id), canonical(replayed) == forecast_json, None
    except Exception as exc:  # a run this code cannot forecast is a result, not a reason to stop
        return None, False, f'{type(exc).__name__}: {exc}'


def collect(copy, output, reforecast=True, base=None, every=1, report=None):
    from nba_forecast.model import forecast
    from nba_forecast.profiles import eligible_history
    from nba_forecast.store import _canonical
    codes = dict(replayed=(forecast, eligible_history, _canonical))
    if base:
        codes['replayed_base'] = load_package(base)
    copy = Path(copy).resolve()
    if copy == PRODUCTION.resolve():
        raise ValueError('refusing to read production directly; pass a copy')
    report_path = Path(report or copy / 'market' / 'report.json')
    report = json.loads(report_path.read_text())
    conn = sqlite3.connect(f'file:{copy / "forecast.sqlite"}?mode=ro', uri=True, timeout=60)
    # A report newer than the copy names runs the copy's ledger does not hold yet: those are left out.
    games = sorted((g for g in report['games'] if g.get('run_id') and g.get('actual_total') is not None
                    and (copy / 'finals' / f"{g['game_id']}.json").exists()
                    and conn.execute('SELECT 1 FROM run_records WHERE run_id=?', (g['run_id'],)).fetchone()),
                   key=lambda g: (g['start_time'], g['game_id']))
    replayed_ids = {g['game_id'] for g in games[::every]} if reforecast else set()
    digests = {}
    for g in games:
        row = conn.execute('SELECT history_sha256 FROM run_records WHERE run_id=?', (g['run_id'],)).fetchone()
        if row:
            digests[g['game_id']] = row[0]
    out = dict(copy=str(copy), report=str(report_path), report_generated_at=report.get('generated_at'), reforecast=reforecast, every=every,
               code=str(ROOT), base=str(Path(base).resolve()) if base else None, games=[])
    by_digest = defaultdict(list)
    for g in games:
        if g['game_id'] in digests:
            by_digest[digests[g['game_id']]].append(g)
    started = time.monotonic()
    for digest, batch in sorted(by_digest.items(), key=lambda kv: min(g['start_time'] for g in kv[1])):
        history = None
        if replayed_ids & {g['game_id'] for g in batch}:
            text = conn.execute('SELECT history_json FROM histories WHERE history_sha256=?', (digest,)).fetchone()[0]
            if hashlib.sha256(text.encode()).hexdigest() != digest:
                raise ValueError('saved history hash mismatch: ' + digest)
            history = json.loads(text)
            del text
        for g in sorted(batch, key=lambda g: g['start_time']):
            snapshot_json, forecast_json = conn.execute('SELECT snapshot_json, forecast_json FROM run_records WHERE run_id=?',
                                                        (g['run_id'],)).fetchone()
            stored = json.loads(forecast_json)
            final = json.loads((copy / 'finals' / f"{g['game_id']}.json").read_text())
            day = datetime.datetime.fromisoformat(g['start_time'].replace('Z', '+00:00')).astimezone(EASTERN).date().isoformat()
            item = dict(game_id=g['game_id'], run_id=g['run_id'], date=day, stage=g.get('stage'), home_team_id=g['home_team_id'],
                        away_team_id=g['away_team_id'], close_total=g.get('total_line'), close_margin=g.get('market_margin'),
                        actual_total=g['actual_total'], actual_margin=g.get('actual_margin'),
                        model_version=stored.get('model_version'), stored=_summary(stored, g['game_id']),
                        final=[dict(player_id=r['player_id'], team_id=r['team_id'], status=r['status'], pts=r['pts'],
                                    minutes=r['minutes'], tov=r.get('tov')) for r in final])
            if g['game_id'] in replayed_ids:
                for key, code in codes.items():
                    item[key], item[key + '_identical'], error = _replay(code, snapshot_json, history, forecast_json, g['game_id'])
                    if error:
                        item[key + '_error'] = error
            out['games'].append(item)
            print(json.dumps(dict(game_id=g['game_id'], seconds=round(time.monotonic() - started, 1),
                                  **{k: v for k, v in item.items() if k.endswith(('_identical', '_error'))})), file=sys.stderr, flush=True)
        history = None
        _write(output, out)  # after every history, so an interrupted run keeps what it did
    _write(output, dict(out, complete=True))
    return out


def _write(output, data):
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, sort_keys=True).encode()
    Path(output).write_bytes(gzip.compress(text, mtime=0) if str(output).endswith('.gz') else text)


def read(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)


def team_parts(summary, final, team):
    """Projected minus actual regulation points for one team, split as totals_decomposition.py splits a backtest row."""
    rows = [r for r in final if r['team_id'] == team]
    team_points, team_minutes = sum(r['pts'] for r in rows), sum(r['minutes'] for r in rows)
    k = 240. / team_minutes
    actual = {r['player_id']: r for r in rows}
    played = lambda r: r is not None and r['status'] == 'played' and r['minutes'] > 0
    predictions = [p for p in summary['players'] if p['team_id'] == team]
    on_forecast = {p['player_id'] for p in predictions}
    parts = dict.fromkeys(PARTS, 0.)
    weighted = 0.
    for p in predictions:
        a = actual.get(p['player_id'])
        if p['status'] == 'projected':
            w = 1. if p['participation_probability'] is None else p['participation_probability']
            weighted += w * p['points_mean']
            parts['played'] += (p['points_mean'] if played(a) else 0.) - (k * a['pts'] if a else 0.)
            parts['participation'] += (w - played(a)) * p['points_mean']
        elif a:
            parts['listed out' if p['status'] == 'out' else 'withheld'] -= k * a['pts']
    parts['not on forecast'] = -sum(k * r['pts'] for r in rows if r['player_id'] not in on_forecast)
    parts['unprojected'] = parts['listed out'] + parts['withheld'] + parts['not on forecast']
    published = summary['teams'][team]['points_mean']
    parts['replacement'] = published - weighted
    error = published - k * team_points
    check = parts['played'] + parts['participation'] + parts['unprojected'] + parts['replacement'] - error
    if abs(check) > 1e-6:
        raise ValueError(f'live parts do not add up: {check}')
    return dict(error=error, overtime=team_points - k * team_points, parts=parts, published=published,
                actual_regulation=k * team_points, actual=team_points)


def _teams(games, key):
    rows = []
    for g in games:
        summary = g.get(key)
        if not summary or any((summary['teams'].get(t) or {}).get('points_mean') is None for t in (g['home_team_id'], g['away_team_id'])):
            continue
        for team in (g['home_team_id'], g['away_team_id']):
            rows.append(dict(team_parts(summary, g['final'], team), date=g['date'], game_id=g['game_id'], team_id=team))
    return rows


def _cell(values, dates):
    mean, se = mean_se(values, dates)
    return f'{mean:+7.2f} ±{se:4.2f}'


def print_parts(games, key, label):
    rows = _teams(games, key)
    if not rows:
        return
    dates = [r['date'] for r in rows]
    overtime, _ = mean_se([r['overtime'] for r in rows], dates)
    print(f"\n{label}: {len(rows)} team-games of {len({r['game_id'] for r in rows})} games, projected minus actual regulation points, "
          f"± one date-clustered standard error")
    print(f"  {'error':>13s} {'overtime':>8s} " + ' '.join(f'{p:>15s}' for p in PARTS))
    print(f"  {_cell([r['error'] for r in rows], dates)} {overtime:+8.2f} " + ' '.join(f"{_cell([r['parts'][p] for r in rows], dates):>15s}" for p in PARTS))
    statuses = defaultdict(int)
    for g in games:
        if g.get(key):
            forecast_status = {p['player_id']: p['status'] for p in g[key]['players']}
            for r in g['final']:
                if r['status'] == 'played' and r['minutes'] > 0:
                    statuses[forecast_status.get(r['player_id'], 'not on forecast')] += 1
    print('  players who played, by forecast status: ' + ', '.join(f'{k} {v}' for k, v in sorted(statuses.items())))


def game_errors(games, key):
    """game_id -> (date, model total, actual total, close total), for games with a complete total."""
    out = {}
    for g in games:
        summary = g.get(key)
        if summary and summary.get('total') is not None:
            out[g['game_id']] = (g['date'], summary['total'], g['actual_total'], g['close_total'])
    return out


def print_totals(games, key, label):
    rows = game_errors(games, key)
    if not rows:
        return
    items = list(rows.values())
    dates = [i[0] for i in items]
    closed = [i for i in items if i[3] is not None]
    print(f"{label}: {len(items)} games; game total minus final {_cell([i[1] - i[2] for i in items], dates)}, "
          f"MAE {sum(abs(i[1] - i[2]) for i in items) / len(items):.2f}; minus close ({len(closed)}) "
          f"{_cell([i[1] - i[3] for i in closed], [i[0] for i in closed])}; close minus final "
          f"{_cell([i[3] - i[2] for i in closed], [i[0] for i in closed])}, close MAE {sum(abs(i[3] - i[2]) for i in closed) / len(closed):.2f}")


def compare(games, base='replayed_base', candidate='replayed'):
    """This checkout's replays minus the base checkout's, paired game by game."""
    a, b = game_errors(games, base), game_errors(games, candidate)
    keys = sorted(set(a) & set(b))
    dates = [a[k][0] for k in keys]
    closed = [k for k in keys if a[k][3] is not None]
    cdates = [a[k][0] for k in closed]
    if not keys:
        return
    print(f"\nthis checkout minus the base, paired over {len(keys)} games (game totals), ± one date-clustered standard error")
    print(f"  total change                 {_cell([b[k][1] - a[k][1] for k in keys], dates)}")
    print(f"  bias vs final                base {_cell([a[k][1] - a[k][2] for k in keys], dates)}  candidate {_cell([b[k][1] - b[k][2] for k in keys], dates)}")
    print(f"  abs error vs final change    {_cell([abs(b[k][1] - b[k][2]) - abs(a[k][1] - a[k][2]) for k in keys], dates)}"
          f"  (MAE {sum(abs(a[k][1] - a[k][2]) for k in keys) / len(keys):.2f} -> {sum(abs(b[k][1] - b[k][2]) for k in keys) / len(keys):.2f})")
    print(f"  bias vs close ({len(closed)})        base {_cell([a[k][1] - a[k][3] for k in closed], cdates)}  candidate {_cell([b[k][1] - b[k][3] for k in closed], cdates)}")
    print(f"  abs gap to close change      {_cell([abs(b[k][1] - b[k][3]) - abs(a[k][1] - a[k][3]) for k in closed], cdates)}"
          f"  (mean |model - close| {sum(abs(a[k][1] - a[k][3]) for k in closed) / len(closed):.2f} -> {sum(abs(b[k][1] - b[k][3]) for k in closed) / len(closed):.2f})")
    by_id = {g['game_id']: g for g in games}
    shared = [k for k in keys if (by_id[k].get(base) or {}).get('margin') is not None
              and (by_id[k].get(candidate) or {}).get('margin') is not None]
    if shared:
        d = [by_id[k]['date'] for k in shared]
        err = lambda g, key: abs(g[key]['margin'] - g['actual_margin'])
        print(f"  margin abs error change ({len(shared)}) {_cell([err(by_id[k], candidate) - err(by_id[k], base) for k in shared], d)}"
              f"  (MAE {sum(err(by_id[k], base) for k in shared) / len(shared):.2f} -> {sum(err(by_id[k], candidate) for k in shared) / len(shared):.2f})")
        gap = lambda g, key: abs(g[key]['margin'] - g['close_margin'])
        closed = [k for k in shared if by_id[k].get('close_margin') is not None]
        print(f"  margin |model - close| change ({len(closed)}) {_cell([gap(by_id[k], candidate) - gap(by_id[k], base) for k in closed], [by_id[k]['date'] for k in closed])}")
    # Players who played: points of the replayed forecasts against the final.
    def played_points(key):
        out = {}
        for g in games:
            if not g.get(key):
                continue
            final = {r['player_id']: r for r in g['final']}
            for p in g[key]['players']:
                a = final.get(p['player_id'])
                if p['status'] == 'projected' and a and a['status'] == 'played' and a['minutes'] > 0:
                    out[(g['game_id'], p['player_id'])] = (g['date'], p['points_mean'] - a['pts'])
        return out
    pa, pb = played_points(base), played_points(candidate)
    shared = sorted(set(pa) & set(pb))
    d = [pa[k][0] for k in shared]
    print(f"  played players' points ({len(shared)}): bias base {_cell([pa[k][1] for k in shared], d)} candidate {_cell([pb[k][1] for k in shared], d)};"
          f" abs error change {_cell([abs(pb[k][1]) - abs(pa[k][1]) for k in shared], d)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--copy', help='A copy of the production directory (never production itself)')
    parser.add_argument('--output', help='Write the collected games here (.json or .json.gz)')
    parser.add_argument('--base', help="A second checkout (holding nba_forecast/) whose code replays the same runs, e.g. main")
    parser.add_argument('--no-reforecast', action='store_true', help='Keep the saved forecasts only')
    parser.add_argument('--every', type=int, default=1, help='Re-forecast every Nth game in date order (the saved split covers all)')
    parser.add_argument('--report', help="The market report naming the games, runs and closes (default: the copy's market/report.json); "
                                         "runs missing from the copy's ledger are left out")
    parser.add_argument('--read', help='Print the tables of an output written earlier')
    args = parser.parse_args()
    if args.read:
        data = read(args.read)
    else:
        if not args.copy or not args.output:
            parser.error('--copy and --output, or --read')
        os.nice(19)
        data = collect(args.copy, args.output, reforecast=not args.no_reforecast, base=args.base, every=args.every, report=args.report)
    games = data['games']
    print(f"{len(games)} finished games of the market report ({data.get('report_generated_at')})")
    for key, code in (('replayed_base', data.get('base')), ('replayed', data.get('code'))):
        if code:
            print(f"  {key}: {code}; identical to the saved forecast {sum(bool(g.get(key + '_identical')) for g in games)}, "
                  f"errors {sum(bool(g.get(key + '_error')) for g in games)}")
    print_parts(games, 'stored', 'saved forecasts')
    print_parts(games, 'replayed_base', 'base replay')
    print_parts(games, 'replayed', 'this checkout\'s replay')
    print()
    print_totals(games, 'stored', 'saved forecasts       ')
    print_totals(games, 'replayed_base', 'base replay           ')
    print_totals(games, 'replayed', 'this checkout\'s replay')
    compare(games)


if __name__ == '__main__':
    main()
