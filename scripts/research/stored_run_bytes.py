"""Where the bytes of a stored forecast run are: the ledger row, the run's files and its evaluations.

    python3 scripts/research/stored_run_bytes.py --directory COPY [--runs 6] [--step 40] [--rowids 4100,4101]
        [--files-from DIRECTORY]

COPY is a copy of an operations directory (the ledger is opened read-only). Runs
are read from ``run_records`` one at a time by rowid: the newest and every
``--step``-th before it, or the ``--rowids`` given. Nothing is aggregated in SQL.

For each run: ``forecast_json`` split into the rows of players whose team plays
in the run's games (on-game), the rows of everyone else (off-game: ``no_game``,
no numbers) and the rest of the forecast; ``snapshot_json`` split into the
roster's on-game and off-game players and its other keys; ``runs/<run_id>/``
files (from ``--files-from`` when the copy has no ``runs`` folder; sizes and one
decoded ``profiles.json`` split, read-only); and the run's evaluation
revisions. Bytes are those of the stored canonical JSON (UTF-8). A part is the
canonical JSON of its rows; "rest" is the total minus the parts, so every split
sums to its total.
"""
import argparse
import json
from pathlib import Path
import sqlite3
from statistics import fmean


def size(value):
    return len(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def split_rows(rows, teams):
    on = [r for r in rows if r['team_id'] in teams]
    off = [r for r in rows if r['team_id'] not in teams]
    return on, off


def measure_run(conn, rowid, files):
    row = conn.execute('SELECT run_id,created_at,mode,scope,snapshot_json,forecast_json FROM run_records WHERE rowid=?',
                       (rowid,)).fetchone()
    if row is None:
        return None
    run_id, created, mode, scope, snapshot_json, forecast_json = row
    snapshot, forecast = json.loads(snapshot_json), json.loads(forecast_json)
    teams = {t for g in snapshot['games'] for t in (g['home_team_id'], g['away_team_id'])}
    out = dict(rowid=rowid, run_id=run_id, created_at=created, mode=mode, scope=scope,
               stage=snapshot.get('forecast_stage', 'preliminary'), games=len(snapshot['games']))
    # forecast_json
    on, off = split_rows(forecast['predictions'], teams)
    total = len(forecast_json.encode())
    f = dict(total=total, rows=len(forecast['predictions']), on_rows=len(on), off_rows=len(off),
             off_no_game=sum(r['status'] == 'no_game' and r['game_id'] is None for r in off),
             on_bytes=sum(size(r) for r in on), off_bytes=sum(size(r) for r in off))
    f['rest_bytes'] = total - f['on_bytes'] - f['off_bytes']
    f['rest_keys'] = {k: size(v) for k, v in forecast.items() if k != 'predictions'}
    f['off_parts'] = dict(
        history_game_ids=sum(size(r.get('components', {}).get('history', {}).get('game_ids', [])) for r in off),
        other_components=sum(size(r.get('components', {})) - size(r.get('components', {}).get('history', {}).get('game_ids', []))
                             for r in off),
        null_fields=sum(size({k: v for k, v in r.items() if v is None}) for r in off),
    )
    f['on_parts'] = {}
    for r in on:
        for key, value in r.items():
            if key == 'components':
                for name, part in value.items():
                    f['on_parts']['components.' + name] = f['on_parts'].get('components.' + name, 0) + size(part)
            else:
                f['on_parts'][key] = f['on_parts'].get(key, 0) + size(value) + len(key) + 3
    f['coverage'] = {k: forecast.get('coverage', {}).get(k) for k in ('roster_players', 'pool_rows', 'by_status')}
    out['forecast'] = f
    # snapshot_json
    players = snapshot['roster']['players']
    on_p, off_p = split_rows(players, teams)
    total = len(snapshot_json.encode())
    s = dict(total=total, roster_players=len(players), roster_on=len(on_p), roster_off=len(off_p),
             roster_on_bytes=sum(size(p) for p in on_p), roster_off_bytes=sum(size(p) for p in off_p))
    s['keys'] = {k: size(v) for k, v in snapshot.items() if k != 'roster'}
    s['roster_other_bytes'] = size(snapshot['roster']) - s['roster_on_bytes'] - s['roster_off_bytes']
    s['rest_bytes'] = total - s['roster_on_bytes'] - s['roster_off_bytes'] - sum(s['keys'].values())
    out['snapshot'] = s
    # runs/<run_id>/ files
    folder = files / 'runs' / run_id if files else None
    disk = {}
    if folder is not None and folder.is_dir():
        for name in ('forecast.json', 'profiles.json', 'snapshot.json'):
            path = folder / name
            disk[name] = path.stat().st_size if path.exists() else None
        path = folder / 'profiles.json'
        if path.exists():
            profiles = json.loads(path.read_bytes())
            on_r, off_r = split_rows(profiles.get('players', []), teams)
            disk['profiles_on_bytes'] = sum(size(r) for r in on_r)
            disk['profiles_off_bytes'] = sum(size(r) for r in off_r)
            disk['profiles_players'] = len(profiles.get('players', []))
            disk['profiles_teams_bytes'] = size(profiles.get('teams', []))
    out['files'] = disk
    # evaluations (primary-key lookups; the index orders the revisions)
    revisions = []
    for revision, actuals_json, report_json in conn.execute(
            'SELECT revision,actuals_json,report_json FROM evaluations WHERE run_id=? ORDER BY revision', (run_id,)):
        report = json.loads(report_json)
        excluded = report.get('excluded', [])
        revisions.append(dict(revision=revision, actuals_bytes=len(actuals_json.encode()), report_bytes=len(report_json.encode()),
                              matched_bytes=size(report.get('matched', [])), matched=len(report.get('matched', [])),
                              excluded_no_game=sum(e.get('reason') == 'no_game' for e in excluded),
                              excluded_no_game_bytes=sum(size(e) + 1 for e in excluded if e.get('reason') == 'no_game'),
                              excluded_other=sum(e.get('reason') != 'no_game' for e in excluded),
                              pending=len(report.get('pending', [])), sample_counts=report.get('sample_counts')))
    out['evaluations'] = revisions
    return out


def kb(value):
    return '%9.1f' % (value / 1000) if value is not None else '        —'


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--directory', required=True, type=Path, help='copy of an operations directory')
    parser.add_argument('--runs', type=int, default=6)
    parser.add_argument('--step', type=int, default=40, help='rowid distance between sampled runs')
    parser.add_argument('--rowids', help='comma-separated rowids instead of the sample')
    parser.add_argument('--files-from', type=Path, help='directory whose runs/<run_id>/ files are measured (read-only)')
    parser.add_argument('--runs-per-day', type=float, default=240.)
    parser.add_argument('--json', type=Path, help='also write the measurements here')
    args = parser.parse_args()
    ledger = (args.directory / 'forecast.sqlite').resolve()
    conn = sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)
    newest = conn.execute('SELECT MAX(rowid) FROM run_records').fetchone()[0]
    rowids = ([int(v) for v in args.rowids.split(',')] if args.rowids
              else [newest - i * args.step for i in range(args.runs) if newest - i * args.step > 0])
    files = args.files_from or args.directory
    measured = [m for m in (measure_run(conn, rowid, files) for rowid in rowids) if m]
    print(f'ledger {ledger} ({ledger.stat().st_size / 1e9:.2f} GB), newest rowid {newest}; {len(measured)} runs measured\n')

    print('forecast_json (kB)')
    print('%6s %-25s %-11s %5s %4s %4s %9s %9s %9s %9s %7s' % (
        'rowid', 'created_at', 'stage', 'rows', 'on', 'off', 'total', 'on-game', 'off-game', 'rest', 'off %'))
    for m in measured:
        f = m['forecast']
        print('%6d %-25s %-11s %5d %4d %4d %s %s %s %s %6.1f%%' % (
            m['rowid'], m['created_at'][:25], m['stage'], f['rows'], f['on_rows'], f['off_rows'], kb(f['total']),
            kb(f['on_bytes']), kb(f['off_bytes']), kb(f['rest_bytes']), 100 * f['off_bytes'] / f['total']))
    mean = lambda path: fmean(path(m) for m in measured)
    f_total, f_on, f_off, f_rest = (mean(lambda m, k=k: m['forecast'][k]) for k in ('total', 'on_bytes', 'off_bytes', 'rest_bytes'))
    print('%6s %-25s %-11s %5.0f %4.0f %4.0f %s %s %s %s %6.1f%%' % (
        'mean', '', '', mean(lambda m: m['forecast']['rows']), mean(lambda m: m['forecast']['on_rows']),
        mean(lambda m: m['forecast']['off_rows']), kb(f_total), kb(f_on), kb(f_off), kb(f_rest), 100 * f_off / f_total))
    checks = [m['forecast']['off_no_game'] == m['forecast']['off_rows'] for m in measured]
    print(f'every off-game row is no_game with game_id null: {all(checks)} ({sum(checks)}/{len(checks)} runs)')
    sample = measured[0]['forecast']
    print(f"coverage block of rowid {measured[0]['rowid']}: {json.dumps(sample['coverage'], sort_keys=True)}")

    print('\noff-game rows, mean bytes per run (kB): history game_ids %s, other components %s, null fields %s' % tuple(
        kb(mean(lambda m, k=k: m['forecast']['off_parts'][k])).strip() for k in ('history_game_ids', 'other_components', 'null_fields')))
    keys = sorted({k for m in measured for k in m['forecast']['rest_keys']})
    print('rest of the forecast, mean bytes per run (kB): ' + ', '.join(
        f"{k} {fmean(m['forecast']['rest_keys'].get(k, 0) for m in measured) / 1000:.1f}" for k in sorted(
            keys, key=lambda k: -fmean(m['forecast']['rest_keys'].get(k, 0) for m in measured))))
    parts = sorted({k for m in measured for k in m['forecast']['on_parts']})
    ranked = sorted(parts, key=lambda k: -fmean(m['forecast']['on_parts'].get(k, 0) for m in measured))
    print('on-game rows, largest fields, mean bytes per run (kB): ' + ', '.join(
        f"{k} {fmean(m['forecast']['on_parts'].get(k, 0) for m in measured) / 1000:.1f}" for k in ranked[:8]))

    print('\nsnapshot_json (kB)')
    keys = sorted({k for m in measured for k in m['snapshot']['keys']},
                  key=lambda k: -fmean(m['snapshot']['keys'].get(k, 0) for m in measured))
    large = keys[:6]
    print('%6s %9s %9s %9s ' % ('rowid', 'total', 'roster on', 'roster off') + ' '.join('%18s' % k[:18] for k in large) + ' %9s' % 'other')
    for m in measured:
        s = m['snapshot']
        other = s['total'] - s['roster_on_bytes'] - s['roster_off_bytes'] - sum(s['keys'].get(k, 0) for k in large)
        print('%6d %s %s %s ' % (m['rowid'], kb(s['total']), kb(s['roster_on_bytes']), kb(s['roster_off_bytes']))
              + ' '.join('%18s' % kb(s['keys'].get(k, 0)).strip() for k in large) + ' ' + kb(other))
    s_total, s_on, s_off = (mean(lambda m, k=k: m['snapshot'][k]) for k in ('total', 'roster_on_bytes', 'roster_off_bytes'))
    print('%6s %s %s %s ' % ('mean', kb(s_total), kb(s_on), kb(s_off))
          + ' '.join('%18s' % kb(fmean(m['snapshot']['keys'].get(k, 0) for m in measured)).strip() for k in large)
          + ' ' + kb(s_total - s_on - s_off - sum(fmean(m['snapshot']['keys'].get(k, 0) for m in measured) for k in large)))
    print(f'roster off-game share of snapshot_json: {100 * s_off / s_total:.1f}%')

    with_files = [m for m in measured if m['files'].get('forecast.json') is not None]
    print(f'\nruns/<run_id>/ files (kB), {len(with_files)} of {len(measured)} runs found under {files}')
    if with_files:
        print('%6s %12s %12s %12s %12s %12s' % ('rowid', 'forecast.json', 'snapshot.json', 'profiles.json', 'profiles on', 'profiles off'))
        for m in with_files:
            d = m['files']
            print('%6d %12s %12s %12s %12s %12s' % (m['rowid'], kb(d['forecast.json']).strip(), kb(d['snapshot.json']).strip(),
                                                  kb(d['profiles.json']).strip(), kb(d.get('profiles_on_bytes')).strip(),
                                                  kb(d.get('profiles_off_bytes')).strip()))
        d_mean = {k: fmean(m['files'][k] for m in with_files if m['files'].get(k) is not None)
                  for k in ('forecast.json', 'snapshot.json', 'profiles.json', 'profiles_on_bytes', 'profiles_off_bytes')}
        print('%6s %12s %12s %12s %12s %12s' % ('mean', *(kb(d_mean[k]).strip() for k in (
            'forecast.json', 'snapshot.json', 'profiles.json', 'profiles_on_bytes', 'profiles_off_bytes'))))

    print('\nevaluations (kB per revision)')
    print('%6s %4s %9s %9s %9s %8s %9s %12s %9s' % ('rowid', 'rev', 'actuals', 'report', 'matched', 'matched#', 'no_game#', 'no_game kB', 'pending#'))
    revisions = [(m['rowid'], e) for m in measured for e in m['evaluations']]
    for rowid, e in revisions:
        print('%6d %4d %s %s %s %8d %9d %12s %9d' % (rowid, e['revision'], kb(e['actuals_bytes']), kb(e['report_bytes']),
                                                   kb(e['matched_bytes']), e['matched'], e['excluded_no_game'],
                                                   kb(e['excluded_no_game_bytes']).strip(), e['pending']))
    if not revisions:
        print('   (none of the measured runs has an evaluation yet)')

    slim_forecast = f_total - f_off
    per_run_before = f_total + s_total
    per_run_after = slim_forecast + s_total
    print('\nper run, mean bytes (ledger row = forecast_json + snapshot_json; small columns ignored)')
    print(f'  forecast_json  before {f_total / 1e6:.3f} MB   without off-game rows {slim_forecast / 1e6:.3f} MB   saved {f_off / 1e6:.3f} MB ({100 * f_off / f_total:.1f}%)')
    print(f'  ledger row     before {per_run_before / 1e6:.3f} MB   after {per_run_after / 1e6:.3f} MB   saved {100 * f_off / per_run_before:.1f}%')
    rate = args.runs_per_day
    print(f'  at {rate:.0f} runs/day: ledger rows {per_run_before * rate / 1e6:.0f} MB/day before, {per_run_after * rate / 1e6:.0f} MB/day after '
          f'(−{f_off * rate / 1e6:.0f} MB/day); histories and evaluations not included')
    if with_files:
        files_before = sum(d_mean[k] for k in ('forecast.json', 'snapshot.json', 'profiles.json'))
        print(f'  runs/<run_id>/ files: {files_before / 1e6:.3f} MB per run before, {(files_before - f_off) / 1e6:.3f} MB after '
              f'({files_before * rate / 1e6:.0f} → {(files_before - f_off) * rate / 1e6:.0f} MB/day)')
    # What an offline compaction of the rows already stored would remove: an estimate, the sample's
    # mean times the counts (COUNT(*) reads the tables' leaf pages, not the JSON).
    runs_total = conn.execute('SELECT COUNT(*) FROM run_records').fetchone()[0]
    live_total = conn.execute("SELECT COUNT(*) FROM run_records WHERE mode='live'").fetchone()[0]
    revisions_total = conn.execute('SELECT COUNT(*) FROM evaluations').fetchone()[0]
    folders = len([p for p in (files / 'runs').iterdir() if p.is_dir()]) if (files / 'runs').is_dir() else 0
    no_game_per_revision = fmean(e['excluded_no_game_bytes'] for _, e in revisions) if revisions else 0.
    print(f'\noffline compaction of what is already stored (estimate: sample mean x count)')
    print(f'  runs in the ledger {runs_total} ({live_total} live): off-game rows ~{runs_total * f_off / 1e9:.2f} GB of forecast_json')
    print(f'  evaluation revisions {revisions_total}: no_game exclusions ~{revisions_total * no_game_per_revision / 1e9:.2f} GB of report_json')
    print(f'  runs/<run_id>/ folders {folders}: off-game rows in forecast.json ~{folders * f_off / 1e9:.2f} GB')
    if args.json:
        args.json.write_text(json.dumps(measured, indent=1, sort_keys=True))


if __name__ == '__main__':
    main()
