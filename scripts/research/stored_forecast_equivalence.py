"""Show on saved runs that leaving the off-slate rows out of a stored forecast changes nothing a reader uses.

    python3 scripts/research/stored_forecast_equivalence.py --source COPY --main MAIN_CHECKOUT --branch BRANCH_CHECKOUT \
        --work WORKDIR [--runs 240 | --sample-days 4 --per-day 3 | --rowids 1,2,3] [--replay all|N] [--old-share 0.4] [--now ISO]

COPY is a copy of an operations directory (never production itself; its ledger is
opened read-only and rows are read one at a time by rowid). The chosen runs are
rebuilt into two scratch ledgers under WORKDIR with their run IDs and creation
times:

  full   every run exactly as COPY's ledger holds it (every roster player's row)
  slim   the older ``--old-share`` of the runs as they are, the rest saved by BRANCH
         through ``store.stored_forecast`` and ``store.save_run``

A slim run is saved with BRANCH's ``save_run`` at the run's own creation time and
run ID. A run that ``save_run`` rejects (one saved by an older release whose totals
or margin today's validator no longer reproduces) is inserted directly, with the
hash of its slim bytes, once the script has shown that both trees' validators
reject its full stored forecast with the same message: the rejection is the old
release's, not the slimming's. A third ledger, ``rollback``, is a copy of the slim
one read by MAIN, as after a revert of the deploy.

Checks, each printed with its counts:

  replay      for the ``--replay`` runs, each tree recomputes ``model.forecast``
              from the saved snapshot and history; MAIN's and BRANCH's results
              must be the same bytes, and are compared with the stored forecast
  stored      every slim run's stored forecast against the full one: every
              top-level key and every on-slate row equal, only ``no_game`` rows
              of other teams gone, as many as ``coverage.by_status.no_game``
  evaluate    each tree scores its ledger against COPY's finals
              (``operations._evaluate_runs``); reports are compared field by field,
              and MAIN scoring the slim ledger (``rollback``) must write the same bytes
  derived     each tree rebuilds the derived reports (``operations._derived_reports``)
              at one fixed clock; every output file is compared (``rollback`` too)
  dashboard   ``dashboard.dashboard_data`` and the page payload of each tree; the
              games' payloads must be equal

A difference is expected only where a field counts the off-slate rows or quotes the
hash of the stored forecast bytes (the bytes are what changed); each difference is
classified, a quoted hash is checked against the two ledgers, and anything else is
listed as UNEXPLAINED and fails the run.

``--dev-recompute`` is for trying the script on a copy whose runs today's code
cannot validate: the forecast MAIN recomputes stands in for the stored one.

``--folders N --files-from DIR`` checks only the validator, on the N newest
``runs/<run_id>/`` folders of DIR (read-only; their forecast.json and snapshot.json
are the ledger row's bytes): each tree's ``_validate_forecast`` on the stored
forecast, and BRANCH's on its ``stored_forecast`` shape. No history is needed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

PRODUCTION = Path('/mnt/2TBHDD/NBA-Forecast/repo/data/operations/production').resolve()
COLUMNS = ('run_id', 'created_at', 'as_of', 'mode', 'scope', 'snapshot_json', 'history_sha256', 'forecast_json',
           'snapshot_sha256', 'forecast_sha256', 'inputs_sha256')
OUTPUTS = ('performance.json', 'benchmarks.json', 'prospective-calibration-status.json',
           'prospective-margin-calibration-status.json', 'model-status.json', 'model-upgrade-active.json',
           'market/report.json', 'daily-reviews', 'postgame', 'margin-calibration-attempts', 'model-artifacts')
# Fields that count the off-slate rows a run stores.
COUNTERS = {'off_slate_rows_ignored', 'off_slate'}
# Per-run evaluation fields that count or list every stored row.
EVALUATION_FIELDS = {'.sample_counts.predictions', '.sample_counts.excluded', '.excluded'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def read_only(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


# ---------------------------------------------------------------- child steps (run under one code tree)

def _block_network():
    import socket
    import urllib.request

    def refuse(*args, **kwargs):
        raise OSError('network disabled for offline verification')
    socket.create_connection = refuse
    urllib.request.urlopen = refuse


def _expect_tree(tree):
    import nba_forecast
    found = Path(nba_forecast.__file__).resolve().parent.parent
    if found != Path(tree).resolve():
        raise SystemExit(f'nba_forecast imported from {found}, expected {tree}')


def child_build(args):
    """Fill one scratch ledger from the plan; recompute the planned replays."""
    from unittest.mock import patch
    import uuid
    from nba_forecast import store
    from nba_forecast.model import forecast
    slim_shape = getattr(store, 'stored_forecast', None)
    plan = json.loads(Path(args.plan).read_text())
    source = read_only(plan['ledger'])
    target = Path(args.ledger)
    store._connect(target).close()
    records = []
    cached = {}

    def history_of(digest):
        if digest not in cached:
            cached.clear()
            text = source.execute('SELECT history_json FROM histories WHERE history_sha256=?', (digest,)).fetchone()[0]
            cached[digest] = (text, json.loads(text))
        return cached[digest]

    def rejection(snapshot, value):
        try:
            store._validate_forecast(snapshot, value)
            return None
        except ValueError as exc:
            return str(exc)[:200]

    def insert(row, forecast_text):
        conn = sqlite3.connect(target)
        with conn:
            digest = row['history_sha256']
            if not conn.execute('SELECT 1 FROM histories WHERE history_sha256=?', (digest,)).fetchone():
                conn.execute('INSERT INTO histories VALUES (?,?)', (digest, history_of(digest)[0]))
            values = dict(row, forecast_json=forecast_text, forecast_sha256=sha(forecast_text))
            conn.execute(f'INSERT INTO run_records ({",".join(COLUMNS)}) VALUES ({",".join("?" * len(COLUMNS))})',
                         tuple(values[c] for c in COLUMNS))
        conn.close()

    for item in plan['runs']:
        row = dict(zip(COLUMNS, source.execute(
            f'SELECT {",".join(COLUMNS)} FROM run_records WHERE rowid=?', (item['rowid'],)).fetchone()))
        record = dict(rowid=item['rowid'], run_id=row['run_id'], shape='full', saved_via='copied')
        snapshot = json.loads(row['snapshot_json'])
        stored = json.loads(row['forecast_json'])
        # Today's validator on the forecast exactly as the run stored it, under this tree.
        record['full_rejected'] = rejection(snapshot, stored)
        base = stored
        if item['replay']:
            started = time.monotonic()
            try:
                value = forecast(json.loads(row['snapshot_json']), history_of(row['history_sha256'])[1])
                recomputed = canonical(value)
                record.update(recomputed_sha256=sha(recomputed), reproduces=recomputed == row['forecast_json'],
                              recomputed_bytes=len(recomputed.encode()))
                if not record['reproduces']:
                    record['differing_keys'] = sorted(k for k in set(stored) | set(value) if stored.get(k) != value.get(k))
                if slim_shape:
                    # The rule applied to the recomputation keeps exactly what it keeps of the stored forecast
                    # (equal when the run reproduces; when it does not, the same keys differ as above).
                    record['slim_recomputed_equals_slim_stored'] = canonical(slim_shape(snapshot, value)) == canonical(slim_shape(snapshot, stored))
                if args.dev_recompute:
                    base = value
            except Exception as exc:  # An older snapshot today's code cannot forecast at all.
                record.update(reproduces=False, replay_error=f'{type(exc).__name__}: {exc}'[:200])
            record['forecast_seconds'] = round(time.monotonic() - started, 2)
        wanted = None
        if args.shape == 'slim' and item['slim']:
            wanted = slim_shape(snapshot, base)
        elif base is not stored:
            wanted = base
        if wanted is not None:
            created = datetime.fromisoformat(row['created_at'])
            started = time.monotonic()
            record['rejected'] = rejection(snapshot, wanted)
            if record['rejected'] is None:
                with patch.object(store, '_now', return_value=created), \
                        patch.object(store.uuid, 'uuid4', return_value=uuid.UUID(row['run_id'])):
                    saved = store.save_run(target, snapshot, history_of(row['history_sha256'])[1], wanted,
                                           mode=row['mode'], scope=row['scope'])
                assert saved == row['run_id']
                record['saved_via'] = 'save_run'
            elif record['rejected'] == record['full_rejected']:
                insert(row, canonical(wanted))
                record['saved_via'] = 'direct_after_same_rejection'
            record['save_seconds'] = round(time.monotonic() - started, 2)
            if record['saved_via'] != 'copied':
                record['shape'] = 'slim' if args.shape == 'slim' and item['slim'] else 'full'
        if record['saved_via'] == 'copied':
            insert(row, row['forecast_json'])
        conn = read_only(target)
        stored_row = dict(zip(COLUMNS, conn.execute(
            f'SELECT {",".join(COLUMNS)} FROM run_records WHERE run_id=?', (row['run_id'],)).fetchone()))
        conn.close()
        record.update(forecast_sha256=stored_row['forecast_sha256'], forecast_bytes=len(stored_row['forecast_json'].encode()),
                      forecast_hash_ok=sha(stored_row['forecast_json']) == stored_row['forecast_sha256'],
                      same={c: stored_row[c] == row[c] for c in COLUMNS})
        records.append(record)
        print(f"  {args.shape} {item['rowid']} {record['saved_via']} reproduces={record.get('reproduces')}", file=sys.stderr, flush=True)
    Path(args.out).write_text(json.dumps(records))


def _clock(now):
    from unittest.mock import patch
    import nba_forecast.operations as ops
    import nba_forecast.store as store
    import nba_forecast.runner_release as release
    folder = Path.cwd()
    package = None
    pointer = folder / 'model-upgrade-active.json'
    if pointer.exists():
        digest = json.loads(pointer.read_text())['artifact_sha256']
        package = json.loads((folder / 'model-artifacts' / (digest + '.json')).read_text()).get('runner_package_sha256')
    real = release.package_digest
    moment = datetime.fromisoformat(now.replace('Z', '+00:00'))
    return (patch.object(ops, '_now', return_value=now), patch.object(store, '_now', return_value=moment),
            patch.object(release, 'package_digest', side_effect=lambda p: package or real(p)))


def child_evaluate(args):
    import nba_forecast.operations as ops
    folder = Path.cwd()
    report = dict(evaluated_runs=0, errors=[])
    a, b, c = _clock(args.now)
    with a, b, c:
        ops._evaluate_runs(folder, folder / 'forecast.sqlite', report)
    Path(args.out).write_text(json.dumps(report))


def child_derived(args):
    import nba_forecast.operations as ops
    folder = Path.cwd()
    report = dict(errors=[], warnings=[])
    a, b, c = _clock(args.now)
    started = time.monotonic()
    with a, b, c:
        schedule, _ = ops.saved_calendar(folder)
        active, model_digest = ops.load_active(folder, args.now)
        last_tick = folder / 'last-tick.json'
        report['final_sync'] = json.loads(last_tick.read_text()).get('final_sync') if last_tick.exists() else None
        ops._derived_reports(folder, folder / 'forecast.sqlite', schedule, active, model_digest, report)
        seconds = round(time.monotonic() - started, 1)
        # The samples behind the interval calibration's residual_evidence_sha256, as the fit read them.
        samples = []
        if active:
            from nba_forecast.prospective_calibration import collect_samples
            from nba_forecast.intervals import INTERVAL_FAMILY
            samples = collect_samples(folder, active['model_key'], args.now, interval_family=INTERVAL_FAMILY)['samples']
    Path(args.out).write_text(json.dumps(dict(seconds=seconds, errors=report['errors'], warnings=report['warnings'],
                                              samples=samples), default=str))


def child_validate(args):
    from nba_forecast import store
    slim_shape = getattr(store, 'stored_forecast', None)
    out = []
    for name in json.loads(args.players):
        folder = Path(name)
        snapshot_text, forecast_text = ((folder / f).read_text() for f in ('snapshot.json', 'forecast.json'))
        snapshot, stored = json.loads(snapshot_text), json.loads(forecast_text)
        record = dict(run_id=folder.name, as_of=snapshot['as_of'], stage=snapshot.get('forecast_stage', 'preliminary'),
                      rows=len(stored['predictions']), model_key=stored.get('model_key'))
        for label, value in (('full', stored), ('slim', slim_shape(snapshot, stored) if slim_shape else None)):
            if value is None:
                continue
            try:
                store._validate_forecast(snapshot, value)
                record[label] = 'accepted'
            except ValueError as exc:
                record[label] = str(exc)[:160]
            if label == 'slim':
                teams = {t for g in snapshot['games'] for t in (g['home_team_id'], g['away_team_id'])}
                record['slim_rows'] = len(value['predictions'])
                record['slim_is_stored_without_off_slate'] = canonical(value) == canonical(
                    dict(stored, predictions=[r for r in stored['predictions'] if r['team_id'] in teams]))
                record['no_game_count_matches'] = stored['coverage']['by_status'].get('no_game') == len(stored['predictions']) - len(value['predictions'])
        out.append(record)
    Path(args.out).write_text(json.dumps(out))


def child_dashboard(args):
    from nba_forecast import dashboard
    folder = Path.cwd()
    data = dashboard.dashboard_data(folder)
    etag, body = dashboard.dashboard_payload(folder)
    players = {pid: dashboard.player_payload(folder, pid) for pid in json.loads(args.players)}
    Path(args.out).write_text(json.dumps(dict(data=data, payload=json.loads(body), payload_bytes=len(body), players=players),
                                         default=str))


# ---------------------------------------------------------------- parent

def run_child(tree, step, cwd, **options):
    command = [sys.executable, str(Path(__file__).resolve()), '--child', step, '--tree', str(tree)]
    for key, value in options.items():
        if value is True:
            command.append('--' + key.replace('_', '-'))
        elif value not in (None, False):
            command += ['--' + key.replace('_', '-'), str(value)]
    env = dict(os.environ, PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE='1')
    started = time.monotonic()
    done = subprocess.run(command, env=env, cwd=str(cwd))
    if done.returncode:
        raise SystemExit(f'{step} under {tree} failed')
    return time.monotonic() - started


def prepare_folder(source, target, run_ids):
    """Hard links to COPY's files (reports are replaced through renames, so COPY never changes); the chosen runs' folders only."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    skipped = ('ticks.jsonl', 'derived.jsonl', 'runs', 'activation-backups', 'runner-releases')
    for path in source.iterdir():
        name = path.name
        if name.startswith('forecast.sqlite') or name.endswith('.lock') or name.startswith('.') or name in skipped \
                or '.derived-cache.sqlite' in name:
            continue
        if path.is_dir():
            subprocess.run(['cp', '-al', str(path), str(target / name)], check=True)
        else:
            os.link(path, target / name)
    # Files a failed download appends to must not be COPY's own.
    for path in target.rglob('fetch-failures.jsonl'):
        raw = path.read_bytes()
        path.unlink()
        path.write_bytes(raw)
    for run_id in run_ids:
        folder = source / 'runs' / run_id
        if folder.is_dir():
            (target / 'runs' / run_id).mkdir(parents=True)
            for path in folder.iterdir():
                os.link(path, target / 'runs' / run_id / path.name)


def strip(value):
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k != 'generated_at'}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def diff(a, b, path='', out=None):
    """Every leaf (or shape) that differs: (path, a, b)."""
    out = [] if out is None else out
    if type(a) is not type(b):
        out.append((path, a, b))
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append((f'{path}.{k}', a.get(k, '<absent>'), b.get(k, '<absent>')))
            else:
                diff(a[k], b[k], f'{path}.{k}', out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append((path, f'{len(a)} items', f'{len(b)} items'))
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diff(x, y, f'{path}[{i}]', out)
    elif a != b and not (a != a and b != b):
        out.append((path, a, b))
    return out


def outputs(folder, source):
    """Every output file the rebuild wrote or rewrote (a new inode): relative path -> content without generated_at."""
    found = {}
    for name in OUTPUTS:
        root = folder / name
        paths = [root] if root.is_file() else sorted(p for p in root.rglob('*') if p.is_file()) if root.is_dir() else []
        for path in paths:
            relative = path.relative_to(folder)
            original = source / relative
            if original.exists() and os.stat(original).st_ino == os.stat(path).st_ino:
                continue
            try:
                found[str(relative)] = strip(json.loads(path.read_text()))
            except ValueError:
                found[str(relative)] = path.read_bytes().hex()
    return found


def complete(found, folder, other):
    """Add to ``found`` (one side's rewritten files) the files only ``other`` rewrote that this side left as COPY had them."""
    out = dict(found)
    for key in other:
        if key not in out and (folder / key).is_file():
            path = folder / key
            try:
                out[key] = strip(json.loads(path.read_text()))
            except ValueError:
                out[key] = path.read_bytes().hex()
    return out


def substitute(value, mapping):
    """``value`` with every string that is a key of ``mapping`` replaced by its value."""
    if isinstance(value, dict):
        return {k: substitute(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, mapping) for v in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def verify_signatures(work, keys, stored, samples):
    """Show that every derived digest differs only because it covers the stored hash.

    ``stored`` maps each slim run's forecast_sha256 to the full run's. For the daily reviews (date reports and their
    revisions, then the recurrence and the index, which quote the date reports' signatures), each side's
    ``input_signature`` must be the SHA-256 of its own report without it (and without ``generated_at``), and the slim
    report with the stored hashes and the date signatures mapped must equal the full one. For the interval
    calibration, the samples its residual_evidence_sha256 is computed from must be equal once the stored hash is mapped.
    Returns (files checked, files that fail).
    """
    mapping = dict(stored)
    checked, failed = 0, []
    name = lambda key: key if isinstance(key, str) else key[0]
    reviews = sorted((k for k in keys if name(k).startswith('daily-reviews/')), key=name)
    # The recurrence quotes the date reports' signatures and the index all of them: in that order.
    last = ('recurrence.json', 'index.json')
    order = [k for k in reviews if Path(name(k)).name not in last] + \
        [k for n in last for k in reviews if Path(name(k)).name == n]
    signatures = {}
    for key in order:
        full_key, slim_key = key if isinstance(key, tuple) else (key, key)
        a, b = (json.loads((work / side / k).read_text()) for side, k in (('full', full_key), ('slim', slim_key)))
        checked += 1
        ok = True
        for value in (a, b):
            if 'input_signature' in value:
                body = {k: v for k, v in value.items() if k not in ('input_signature', 'generated_at')}
                ok &= hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest() \
                    == value['input_signature']
        mapped = substitute(b, {**mapping, **signatures})
        ok &= strip({k: v for k, v in mapped.items() if k != 'input_signature'}) == strip({k: v for k, v in a.items() if k != 'input_signature'})
        if 'input_signature' in a and 'input_signature' in b:
            signatures[b['input_signature']] = a['input_signature']
        if not ok:
            failed.append(key)
    if samples is not None:
        checked += 1
        full_samples, slim_samples = samples
        if substitute(slim_samples, mapping) != full_samples:
            failed.append('interval calibration samples')
    return checked, failed


def classify(differences, hash_pairs):
    """Sort differences into the expected classes; whatever fits none is unexplained.

    ``counter``       a field that counts off-slate rows
    ``stored hash``   a quoted forecast_sha256: the full ledger's hash of a run against the slim ledger's hash of the same run
    ``derived hash``  a digest computed over records that quote such a hash (a *_sha256 or *signature field whose
                      sibling fields are otherwise equal)
    """
    classes = dict(counter=[], stored_hash=[], derived_hash=[], unexplained=[])
    for path, a, b in differences:
        leaf = path.rsplit('.', 1)[-1].split('[')[0]
        if leaf in COUNTERS:
            classes['counter'].append((path, a, b))
        elif leaf == 'forecast_sha256' and (a, b) in hash_pairs:
            classes['stored_hash'].append((path, a, b))
        elif (leaf.endswith('sha256') or leaf.endswith('signature')) and leaf != 'forecast_sha256' \
                and all(isinstance(v, str) and len(v) == 64 for v in (a, b)):
            classes['derived_hash'].append((path, a, b))
        else:
            classes['unexplained'].append((path, a, b))
    return classes


def summarize(title, classes, limit=6):
    total = sum(len(v) for v in classes.values())
    print(f'{title}: {total} differing fields — ' + ', '.join(f'{k.replace("_", " ")} {len(v)}' for k, v in classes.items()))
    names = {}
    for kind in ('counter', 'stored_hash', 'derived_hash'):
        for path, a, b in classes[kind]:
            leaf = path.rsplit('.', 1)[-1].split('[')[0]
            names.setdefault((kind, leaf), []).append((path, a, b))
    for (kind, leaf), items in sorted(names.items()):
        path, a, b = items[0]
        print(f'    {kind.replace("_", " "):13} {leaf:28} x{len(items):<5} e.g. {path[:90]}: {str(a)[:20]} -> {str(b)[:20]}')
    for path, a, b in classes['unexplained'][:limit]:
        print(f'    UNEXPLAINED {path[:120]}: {str(a)[:60]} -> {str(b)[:60]}')
    return len(classes['unexplained'])


def choose_runs(args, conn, source):
    newest = conn.execute('SELECT MAX(rowid) FROM run_records').fetchone()[0]
    if args.rowids:
        return [int(v) for v in args.rowids.split(',')], newest
    if not args.sample_days:
        return list(range(max(1, newest - args.runs + 1), newest + 1)), newest
    # A sample over days and both stages, from the tick's job keys (game:tipoff:stage:...), without opening a forecast.
    jobs = json.loads((source / 'state.json').read_text())['jobs']
    stage_of = {}
    for key, run_id in jobs.items():
        parts = key.split(':')
        stage = next((p for p in parts if p in ('final', 'preliminary')), None)
        stage_of[run_id] = (stage, parts[0])
    picked, per = [], {}
    rowid = newest
    while rowid > 0 and len({d for d, _ in per}) <= args.sample_days:
        row = conn.execute("SELECT run_id,created_at,mode FROM run_records WHERE rowid=?", (rowid,)).fetchone()
        rowid -= 1
        if row is None or row[2] != 'live' or row[0] not in stage_of:
            continue
        day = (datetime.fromisoformat(row[1]) .astimezone(timezone.utc)).strftime('%Y-%m-%d')
        key = (day, stage_of[row[0]][0])
        if len({d for d, _ in per} | {day}) > args.sample_days:
            break
        games = per.setdefault(key, set())
        if len(games) < args.per_day and stage_of[row[0]][1] not in games:
            games.add(stage_of[row[0]][1])
            picked.append(rowid + 1)
    return sorted(picked), newest


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--main', type=Path, help='checkout of the branch base (main)')
    parser.add_argument('--branch', type=Path)
    parser.add_argument('--work', type=Path)
    parser.add_argument('--runs', type=int, default=240, help='the newest N runs')
    parser.add_argument('--sample-days', type=int, help='instead: a sample over this many UTC days of saved runs')
    parser.add_argument('--per-day', type=int, default=3, help='with --sample-days: games per day and stage')
    parser.add_argument('--rowids')
    parser.add_argument('--replay', default='24', help='how many runs to recompute (newest first), or "all"')
    parser.add_argument('--old-share', type=float, default=.4, help='share of the runs (the oldest) kept in the stored shape in the slim ledger')
    parser.add_argument('--now')
    parser.add_argument('--steps', default='build,stored,evaluate,derived,dashboard')
    parser.add_argument('--dev-recompute', action='store_true')
    parser.add_argument('--folders', type=int, help='validator check on the N newest run folders of --files-from')
    parser.add_argument('--files-from', type=Path)
    parser.add_argument('--json', type=Path, help='write the full results here')
    # child
    parser.add_argument('--child')
    parser.add_argument('--tree')
    parser.add_argument('--shape')
    parser.add_argument('--plan')
    parser.add_argument('--ledger')
    parser.add_argument('--out')
    parser.add_argument('--players')
    args = parser.parse_args()
    if args.child:
        _block_network()
        _expect_tree(args.tree)
        return dict(build=child_build, evaluate=child_evaluate, derived=child_derived, dashboard=child_dashboard,
                    validate=child_validate)[args.child](args)
    if args.folders:
        files = args.files_from.resolve()
        folders = sorted((p for p in (files / 'runs').iterdir() if (p / 'forecast.json').is_file()),
                         key=lambda p: (p / 'forecast.json').stat().st_mtime, reverse=True)[:args.folders]
        args.work.mkdir(parents=True, exist_ok=True)
        verdicts = {}
        for shape, tree in (('main', args.main), ('branch', args.branch)):
            run_child(tree.resolve(), 'validate', args.work, players=json.dumps([str(p) for p in folders]),
                      out=args.work / f'validate-{shape}.json')
            verdicts[shape] = json.loads((args.work / f'validate-{shape}.json').read_text())
        print(f'{len(folders)} newest run folders of {files} (saved {verdicts["main"][-1]["as_of"][:19]} .. {verdicts["main"][0]["as_of"][:19]} UTC)')
        print('%-36s %-11s %5s %5s %-10s %-10s %-10s %s' % ('run', 'stage', 'rows', 'slim', 'main full', 'br. full', 'br. slim', 'slim = stored without off-slate rows, no_game count'))
        failures = 0
        for m, b in zip(verdicts['main'], verdicts['branch']):
            print('%-36s %-11s %5d %5d %-10s %-10s %-10s %s, %s' % (m['run_id'], m['stage'], m['rows'], b['slim_rows'], m['full'][:10], b['full'][:10],
                                                           b['slim'][:10], b['slim_is_stored_without_off_slate'], b['no_game_count_matches']))
            failures += not (m['full'] == b['full'] == b['slim'] == 'accepted' and b['slim_is_stored_without_off_slate'] and b['no_game_count_matches'])
        print(f'accepted in both shapes by both validators, slim shape exact: {len(folders) - failures}/{len(folders)}')
        raise SystemExit(1 if failures else 0)

    source, work = args.source.resolve(), args.work.resolve()
    trees = dict(full=args.main.resolve(), slim=args.branch.resolve())
    if source == PRODUCTION or PRODUCTION in source.parents:
        raise SystemExit('Run this on a copy of production, never on production itself.')
    ledger = source / 'forecast.sqlite'
    stamp = source / '.copied_at'
    now = args.now or (stamp.read_text().strip() if stamp.exists()
                       else datetime.fromtimestamp(ledger.stat().st_mtime, timezone.utc).isoformat())
    steps = args.steps.split(',')
    work.mkdir(parents=True, exist_ok=True)
    conn = read_only(ledger)
    rowids, newest = choose_runs(args, conn, source)
    meta = {}
    for rowid in rowids:
        row = conn.execute("SELECT run_id,created_at,mode,scope,history_sha256,json_extract(snapshot_json,'$.forecast_stage'),"
                           "json_extract(snapshot_json,'$.games[0].game_id'),json_extract(forecast_json,'$.model_key') "
                           "FROM run_records WHERE rowid=?", (rowid,)).fetchone()
        if row:
            meta[rowid] = dict(rowid=rowid, run_id=row[0], created_at=row[1], mode=row[2], scope=row[3], history=row[4],
                               stage=row[5] or 'preliminary', game_id=row[6], model_key=row[7])
    rowids = [r for r in rowids if r in meta]
    cut = rowids[int(len(rowids) * args.old_share)] if args.old_share > 0 and rowids else 0
    replay = len(rowids) if args.replay == 'all' else int(args.replay)
    replayed = set(sorted((r for r in rowids if r >= cut), reverse=True)[:replay]) if not args.sample_days and not args.rowids \
        else set(sorted(rowids, reverse=True)[:replay])
    plan = dict(ledger=str(ledger), runs=[dict(rowid=r, slim=r >= cut, replay=r in replayed) for r in rowids])
    (work / 'plan.json').write_text(json.dumps(plan))
    days = sorted({m['created_at'][:10] for m in meta.values()})
    print(f'source {source}\nledger newest rowid {newest}; clock {now}')
    print(f'runs {len(rowids)} (rowids {rowids[0]}..{rowids[-1]}), saved {days[0]}..{days[-1]} UTC over {len(days)} days; '
          f"stages: {sum(m['stage'] == 'final' for m in meta.values())} final, {sum(m['stage'] != 'final' for m in meta.values())} preliminary; "
          f"{len({m['history'] for m in meta.values()})} input histories; {len({m['model_key'] for m in meta.values()})} model keys")
    print(f'slim ledger: runs from rowid {cut} on go through stored_forecast ({sum(r >= cut for r in rowids)}), older stay as stored '
          f'({sum(r < cut for r in rowids)}); replayed by both trees: {len(replayed)}\n')
    failures = 0
    results = dict(now=now, plan=plan, meta=meta)

    if 'build' in steps:
        for shape, tree in trees.items():
            folder = work / shape
            prepare_folder(source, folder, [m['run_id'] for m in meta.values()])
            seconds = run_child(tree, 'build', folder, shape=shape, plan=work / 'plan.json', ledger=folder / 'forecast.sqlite',
                                out=work / f'build-{shape}.json', dev_recompute=args.dev_recompute)
            print(f'build {shape}: {seconds:.0f} s')
        # The slim ledger as MAIN would read it after a revert of the deploy.
        prepare_folder(source, work / 'rollback', [m['run_id'] for m in meta.values()])
        with sqlite3.connect(work / 'slim' / 'forecast.sqlite') as original, sqlite3.connect(work / 'rollback' / 'forecast.sqlite') as copy:
            original.backup(copy)
    built = {shape: {r['rowid']: r for r in json.loads((work / f'build-{shape}.json').read_text())} for shape in trees}
    results['build'] = built

    # ---- replay
    print('\nREPLAY (model.forecast recomputed from the saved snapshot and history)')
    rows = [(r, built['full'][r], built['slim'][r]) for r in rowids if r in replayed]
    same_trees = sum(a.get('recomputed_sha256') == b.get('recomputed_sha256') and 'recomputed_sha256' in a for _, a, b in rows)
    reproduced = [r for r, a, b in rows if a.get('reproduces') and b.get('reproduces')]
    print(f'  runs replayed {len(rows)}; MAIN and BRANCH recompute the same bytes: {same_trees}/{len(rows)}; '
          f'recomputed forecast equals the stored one: {len(reproduced)}/{len(rows)}')
    by_group = {}
    for r, a, b in rows:
        m = meta[r]
        group = by_group.setdefault((m['created_at'][:10], m['stage'], (m['model_key'] or '')[:8]), [0, 0, 0, set()])
        group[0] += 1
        group[1] += bool(a.get('reproduces') and b.get('reproduces'))
        group[2] += a.get('recomputed_sha256') == b.get('recomputed_sha256') and 'recomputed_sha256' in a
        group[3].update(a.get('differing_keys', []) + ([a['replay_error'][:60]] if a.get('replay_error') else []))
    print('  %-12s %-12s %-10s %5s %12s %12s  %s' % ('saved (UTC)', 'stage', 'model key', 'runs', 'reproduced', 'trees agree', 'keys that differ from the stored forecast'))
    for (day, stage, key), (n, ok, agree, keys) in sorted(by_group.items()):
        print('  %-12s %-12s %-10s %5d %12d %12d  %s' % (day, stage, key, n, ok, agree, ', '.join(sorted(keys)) or '—'))
    slim_same = sum(b.get('slim_recomputed_equals_slim_stored') is True for _, a, b in rows)
    print(f'  stored_forecast of the recomputation equals stored_forecast of the stored forecast: {slim_same}/{len(rows)} '
          f'(expected exactly for the runs that reproduce: {len(reproduced)})')
    if same_trees != len(rows):
        failures += 1
        print('  FAILED: the two trees recompute different forecasts')
    if any(b.get('slim_recomputed_equals_slim_stored') is not True for r, a, b in rows if r in reproduced):
        failures += 1
        print('  FAILED: a reproducing run keeps different rows after the rule')
    results['replay'] = dict(runs=len(rows), trees_agree=same_trees, reproduced=len(reproduced), slim_same=slim_same)

    # ---- stored forecasts
    full_db, slim_db = read_only(work / 'full' / 'forecast.sqlite'), read_only(work / 'slim' / 'forecast.sqlite')
    hash_pairs = set()
    if 'stored' in steps:
        print('\nSTORED FORECASTS (slim ledger against full ledger, run by run)')
        counts = dict(slim=0, identical_but_rows=0, kept_as_stored=0, source_same=0, save_run=0, direct=0)
        verdicts = {}
        bytes_full = bytes_slim = dropped_rows = on_rows = 0
        keys_compared = set()
        problems = []
        rejected = {}
        for rowid in rowids:
            a, b = built['full'][rowid], built['slim'][rowid]
            run_id = meta[rowid]['run_id']
            counts['source_same'] += all(a['same'].values())
            # Today's validator on each run's full stored forecast: MAIN and BRANCH must agree.
            verdicts.setdefault((a['full_rejected'], b['full_rejected']), []).append(rowid)
            if b['shape'] == 'slim':
                counts['save_run' if b['saved_via'] == 'save_run' else 'direct'] += 1
                if b['saved_via'] != 'save_run':
                    rejected.setdefault(b['rejected'], []).append(rowid)
            if b['shape'] != 'slim':
                counts['kept_as_stored'] += 1
                if a['forecast_sha256'] != b['forecast_sha256']:
                    problems.append(f'{rowid}: a run kept as stored differs between the ledgers')
                continue
            counts['slim'] += 1
            hash_pairs.add((a['forecast_sha256'], b['forecast_sha256']))
            old_text, snapshot_text = full_db.execute('SELECT forecast_json,snapshot_json FROM run_records WHERE run_id=?', (run_id,)).fetchone()
            new_text = slim_db.execute('SELECT forecast_json FROM run_records WHERE run_id=?', (run_id,)).fetchone()[0]
            old, new, snapshot = json.loads(old_text), json.loads(new_text), json.loads(snapshot_text)
            teams = {t for g in snapshot['games'] for t in (g['home_team_id'], g['away_team_id'])}
            gone = [p for p in old['predictions'] if p['team_id'] not in teams]
            kept = [p for p in old['predictions'] if p['team_id'] in teams]
            ok = True
            for key in sorted(set(old) | set(new)):
                if key == 'predictions':
                    continue
                keys_compared.add(key)
                if key not in old or key not in new or canonical(old[key]) != canonical(new[key]):
                    ok = False
                    problems.append(f'{rowid}: top-level key {key} differs')
            if canonical(kept) != canonical(new['predictions']):
                ok = False
                problems.append(f'{rowid}: on-slate rows differ')
            if any(p['status'] != 'no_game' or p['game_id'] is not None for p in gone):
                ok = False
                problems.append(f'{rowid}: a row left out is not a no_game row')
            if len(gone) != old['coverage']['by_status'].get('no_game') or old['coverage']['pool_rows'] != len(old['predictions']):
                ok = False
                problems.append(f'{rowid}: rows left out are not the coverage count')
            if canonical(dict(old, predictions=kept)) != new_text or not b['forecast_hash_ok']:
                ok = False
                problems.append(f'{rowid}: stored bytes are not the full forecast without its off-slate rows')
            if not all(b['same'][c] for c in ('run_id', 'created_at', 'as_of', 'mode', 'scope', 'snapshot_json', 'history_sha256',
                                              'snapshot_sha256', 'inputs_sha256')):
                ok = False
                problems.append(f'{rowid}: an input column differs from the source run')
            counts['identical_but_rows'] += ok
            bytes_full += len(old_text.encode())
            bytes_slim += len(new_text.encode())
            dropped_rows += len(gone)
            on_rows += len(kept)
        print(f"  full ledger rows byte-identical to COPY's in every column: {counts['source_same']}/{len(rowids)}")
        print(f"  slim-shape runs {counts['slim']}; equal to the full forecast in every top-level key and on-slate row, only no_game rows of "
              f"other teams gone, as many as coverage.by_status.no_game: {counts['identical_but_rows']}/{counts['slim']}")
        print(f"  top-level keys compared: {', '.join(sorted(keys_compared))}")
        print(f"  runs kept as stored in the slim ledger: {counts['kept_as_stored']}")
        print(f"  slim runs saved with BRANCH's save_run: {counts['save_run']}; inserted directly after the same rejection of the "
              f"full stored forecast: {counts['direct']}")
        for message, items in rejected.items():
            print(f'    slim and full both rejected x{len(items)}: {message}')
        print("  today's validator on the full stored forecasts (MAIN verdict | BRANCH verdict: runs):")
        for (x, y), items in sorted(verdicts.items(), key=lambda kv: -len(kv[1])):
            print(f"    {x or 'accepted'} | {y or 'accepted'}: {len(items)}")
            if x != y:
                problems.append(f'MAIN and BRANCH judge {len(items)} full stored forecasts differently')
        if counts['slim']:
            n = counts['slim']
            print(f'  per slim run, mean: rows {(dropped_rows + on_rows) / n:.1f} -> {on_rows / n:.1f}; forecast_json {bytes_full / n / 1e6:.3f} MB -> '
                  f'{bytes_slim / n / 1e6:.3f} MB ({100 * (bytes_full - bytes_slim) / bytes_full:.1f}% smaller)')
        for line in problems[:20]:
            print('  PROBLEM ' + line)
        failures += bool(problems) or counts['slim'] == 0
        results['stored'] = dict(counts=counts, bytes_full=bytes_full, bytes_slim=bytes_slim, problems=problems)
    else:
        for rowid in rowids:
            a, b = built['full'][rowid], built['slim'][rowid]
            if b['shape'] == 'slim':
                hash_pairs.add((a['forecast_sha256'], b['forecast_sha256']))
    slim_ids = {meta[r]['run_id'] for r in rowids if built['slim'][r]['shape'] == 'slim'}

    # ---- evaluations
    if 'evaluate' in steps:
        print('\nEVALUATIONS (operations._evaluate_runs on each ledger against COPY\'s finals)')
        reports = {}
        for shape, tree in dict(trees, rollback=trees['full']).items():
            seconds = run_child(tree, 'evaluate', work / shape, now=now, out=work / f'evaluate-{shape}.json')
            reports[shape] = json.loads((work / f'evaluate-{shape}.json').read_text())
            print(f"  {shape}: evaluated {reports[shape]['evaluated_runs']} runs, {len(reports[shape]['errors'])} errors, {seconds:.0f} s")
        query = 'SELECT run_id,revision,actuals_json,report_json FROM evaluations ORDER BY run_id,revision'
        full_rows = {(r[0], r[1]): r[2:] for r in full_db.execute(query)}
        slim_rows = {(r[0], r[1]): r[2:] for r in slim_db.execute(query)}
        stats = dict(slim_equal_but_counts=0, slim=0, old_identical=0, old=0, actuals_equal=0, source_identical=0, source_compared=0)
        fields, unexpected = {}, []
        excluded_gone = report_bytes_full = report_bytes_slim = 0
        for key in sorted(set(full_rows) | set(slim_rows)):
            if key not in full_rows or key not in slim_rows:
                unexpected.append(f'{key}: evaluated in one ledger only')
                continue
            (actuals_a, report_a), (actuals_b, report_b) = full_rows[key], slim_rows[key]
            stats['actuals_equal'] += actuals_a == actuals_b
            a, b = json.loads(report_a), json.loads(report_b)
            if key[0] in slim_ids:
                stats['slim'] += 1
                differing = set()
                for path, x, y in diff({k: v for k, v in a.items() if k != 'excluded'}, {k: v for k, v in b.items() if k != 'excluded'}):
                    differing.add(path)
                gone = [e for e in a['excluded'] if e['reason'] == 'no_game']
                if [e for e in a['excluded'] if e['reason'] != 'no_game'] != b['excluded']:
                    differing.add('.excluded (other than no_game rows)')
                elif gone:
                    differing.add('.excluded')
                for path in differing:
                    fields[path] = fields.get(path, 0) + 1
                ok = differing <= EVALUATION_FIELDS and \
                    a['sample_counts']['predictions'] - b['sample_counts']['predictions'] == len(gone) == \
                    a['sample_counts']['excluded'] - b['sample_counts']['excluded']
                stats['slim_equal_but_counts'] += ok
                if not ok:
                    unexpected.append(f'{key}: {sorted(differing - EVALUATION_FIELDS)}')
                excluded_gone += len(gone)
                report_bytes_full += len(report_a.encode())
                report_bytes_slim += len(report_b.encode())
            else:
                stats['old'] += 1
                stats['old_identical'] += report_a == report_b
            # What production itself recorded for this run, when the finals have not been revised since.
            original = conn.execute('SELECT actuals_json,report_json FROM evaluations WHERE run_id=? ORDER BY revision DESC LIMIT 1',
                                    (key[0],)).fetchone()
            if original and original[0] == actuals_a:
                stats['source_compared'] += 1
                keep = lambda r: {k: v for k, v in r.items() if k not in ('revision', 'evaluated_at')}
                stats['source_identical'] += keep(json.loads(original[1])) == keep(a)
        print(f"  evaluation revisions: full {len(full_rows)}, slim {len(slim_rows)}; actuals_json equal: {stats['actuals_equal']}/{len(full_rows)}")
        print(f"  runs stored slim: {stats['slim']}; report equal to the full run's except the fields below: {stats['slim_equal_but_counts']}/{stats['slim']}")
        for path, n in sorted(fields.items()):
            print(f'    differs in {n}/{stats["slim"]} reports: {path}')
        if stats['slim']:
            print(f"    no_game exclusions no longer listed: {excluded_gone / stats['slim']:.1f} per report; report_json "
                  f"{report_bytes_full / stats['slim'] / 1000:.1f} kB -> {report_bytes_slim / stats['slim'] / 1000:.1f} kB")
        print(f"  runs kept as stored: {stats['old']}; report_json byte-identical between the trees: {stats['old_identical']}/{stats['old']}")
        print(f"  full-ledger report equal to COPY's own latest evaluation of the run (same actuals; revision and evaluated_at aside): "
              f"{stats['source_identical']}/{stats['source_compared']}")
        rollback_db = read_only(work / 'rollback' / 'forecast.sqlite')
        rollback_rows = {(r[0], r[1]): r[2:] for r in rollback_db.execute(query)}
        same_rollback = sum(rollback_rows.get(k) == v for k, v in slim_rows.items())
        print(f"  MAIN scoring the slim ledger (rollback): revisions {len(rollback_rows)}; actuals_json and report_json byte-identical to "
              f"BRANCH's: {same_rollback}/{len(slim_rows)}")
        if same_rollback != len(slim_rows) or len(rollback_rows) != len(slim_rows):
            unexpected.append('MAIN and BRANCH score the slim ledger differently')
        for line in unexpected[:20]:
            print('  UNEXPLAINED ' + line)
        failures += bool(unexpected)
        results['evaluate'] = dict(stats=stats, fields=fields, unexpected=unexpected, rollback_identical=same_rollback)

    # ---- derived reports
    if 'derived' in steps:
        print('\nDERIVED REPORTS (operations._derived_reports on each ledger, same clock)')
        ran = {}
        for shape, tree in dict(trees, rollback=trees['full']).items():
            run_child(tree, 'derived', work / shape, now=now, out=work / f'derived-{shape}.json')
            ran[shape] = json.loads((work / f'derived-{shape}.json').read_text())
            print(f"  {shape}: {ran[shape]['seconds']} s, {len(ran[shape]['errors'])} errors, {len(ran[shape]['warnings'])} warnings")
        a, b = outputs(work / 'full', source), outputs(work / 'slim', source)
        rewritten = (len(a), len(b))
        # A report one side rewrote and the other left unchanged is compared with the other side's unchanged copy.
        a, b = complete(a, work / 'full', b), complete(b, work / 'slim', a)
        differences, only = [], []
        identical_files = 0
        per_file = {}
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                only.append((key, 'full' if key in a else 'slim'))
                continue
            found = diff(a[key], b[key])
            identical_files += not found
            if found:
                per_file[key] = len(found)
            differences += [(key + path, x, y) for path, x, y in found]
        for part in ('errors', 'warnings'):
            differences += [(f'report.{part}' + path, x, y) for path, x, y in diff(strip(ran['full'][part]), strip(ran['slim'][part]))]
        print(f'  output files rewritten: full {rewritten[0]}, slim {rewritten[1]}; compared (rewritten by either, present in both): '
              f'{len(set(a) & set(b))}; identical: {identical_files}; with differences: {len(per_file)}')
        for key, n in sorted(per_file.items()):
            print(f'    {key}: {n} fields')
        for key, side in only:
            print(f'    written only by {side}: {key}')
        failures += summarize('  derived outputs', classify(differences, hash_pairs))
        # A file named by its content's digest (daily-reviews/revisions/DATE/SIGNATURE.json) is written under another name
        # when its content differs; one such file a side in the same folder is paired and compared like the others.
        folders = {}
        for key, side in only:
            folders.setdefault(str(Path(key).parent), {}).setdefault(side, []).append(key)
        # A revision one side wrote and the other did not (its report unchanged from COPY's) pairs with the revision
        # the other side's report names: daily-reviews/revisions/DATE/<that report's input_signature>.json.
        for folder, sides in folders.items():
            parts = Path(folder).parts
            if len(parts) == 3 and parts[:2] == ('daily-reviews', 'revisions') and len(sides) == 1:
                (side, keys), = sides.items()
                other, values = ('slim', b) if side == 'full' else ('full', a)
                report = values.get(f'daily-reviews/{parts[2]}.json') or {}
                key = f"{folder}/{report.get('input_signature')}.json"
                if len(keys) == 1 and (work / other / key).is_file():
                    values[key] = strip(json.loads((work / other / key).read_text()))
                    sides[other] = [key]
        pairs = []
        for folder, sides in sorted(folders.items()):
            if len(sides.get('full', [])) == len(sides.get('slim', [])) == 1:
                pairs.append((sides['full'][0], sides['slim'][0]))
            else:
                failures += 1
                print(f'  UNEXPLAINED: files written by one side only in {folder}: {sides}')
        paired = []
        for x, y in pairs:
            paired += [(f'{x}~{Path(y).name}' + path, u, v) for path, u, v in diff(a[x], b[y])]
            paired += [(f'{x}~{Path(y).name}.(file name)', Path(x).stem, Path(y).stem)] if Path(x).stem != Path(y).stem else []
        if pairs:
            # The file names are the reports' input_signature: a digest of a report that quotes the stored hash.
            failures += summarize(f'  {len(pairs)} content-addressed files paired by folder', classify(
                [(path.replace('.(file name)', '.input_signature'), u, v) for path, u, v in paired], hash_pairs))
        # Each content-addressed revision is checked under its own name on each side.
        revisions = [(x, y) for x, y in pairs]
        stored_map = {y: x for x, y in hash_pairs}
        changed = [k for k in per_file if k.startswith('daily-reviews/')] + [p for p in revisions]
        checked, bad = verify_signatures(work, changed, stored_map,
                                         (ran['full'].get('samples', []), ran['slim'].get('samples', [])))
        print(f"  derived digests checked by recomputation and mapping (daily-review signatures, calibration samples "
              f"{len(ran['full'].get('samples', []))}): {checked - len(bad)}/{checked} explained by the stored hash")
        for key in bad:
            print(f'    UNEXPLAINED digest: {key}')
        failures += bool(bad)
        r = outputs(work / 'rollback', source)
        r, b = complete(r, work / 'rollback', b), complete(b, work / 'slim', r)
        rollback_diff = [(key + path, x, y) for key in sorted(set(r) | set(b))
                         for path, x, y in (diff(r[key], b[key]) if key in r and key in b else [('', 'only one side', '')])]
        for part in ('errors', 'warnings'):
            rollback_diff += [(f'report.{part}' + path, x, y) for path, x, y in diff(strip(ran['rollback'][part]), strip(ran['slim'][part]))]
        print(f'  MAIN on the slim ledger (rollback) against BRANCH on it: {len(set(r) & set(b))} files, {len(rollback_diff)} differing fields')
        for path, x, y in rollback_diff[:6]:
            print(f'    UNEXPLAINED {path[:120]}: {str(x)[:60]} -> {str(y)[:60]}')
        failures += bool(rollback_diff)
        print(f"  report errors (full): {json.dumps(ran['full']['errors'])[:400]}")
        results['derived'] = dict(files=len(set(a) & set(b)), identical=identical_files, per_file=per_file, only=only,
                                  errors=ran['full']['errors'], rollback_differences=len(rollback_diff))

    # ---- dashboard
    if 'dashboard' in steps:
        print('\nDASHBOARD (dashboard_data and the page payload of each folder)')
        sample = []
        for rowid in sorted((r for r in rowids if built['slim'][r]['shape'] == 'slim'), reverse=True)[:3]:
            text = slim_db.execute('SELECT forecast_json FROM run_records WHERE run_id=?', (meta[rowid]['run_id'],)).fetchone()[0]
            sample += [p['player_id'] for p in json.loads(text)['predictions'][:2]]
        pages = {}
        for shape, tree in trees.items():
            run_child(tree, 'dashboard', work / shape, out=work / f'dashboard-{shape}.json', players=json.dumps(sorted(set(sample))))
            pages[shape] = json.loads((work / f'dashboard-{shape}.json').read_text())
        for name in ('data', 'payload'):
            a, b = strip(pages['full'][name]), strip(pages['slim'][name])
            games_a = {g['game_id']: g for g in a['games']}
            games_b = {g['game_id']: g for g in b['games']}
            with_forecast = [g for g in games_a if games_a[g].get('forecasts')]
            run_ids = lambda g: {v['run_id'] for v in (g.get('forecasts') or {}).values()}
            with_slim = [g for g in with_forecast if run_ids(games_a[g]) & slim_ids]
            equal = sum(games_a[g] == games_b.get(g) for g in games_a)
            equal_slim = sum(games_a[g] == games_b.get(g) for g in with_slim)
            print(f'  {name}: games {len(games_a)}, with a forecast from these ledgers {len(with_forecast)}, of them showing a slim run '
                  f'{len(with_slim)}; game payloads equal: {equal}/{len(games_a)} (showing a slim run: {equal_slim}/{len(with_slim)})')
            failures += equal != len(games_a) or len(games_a) != len(games_b) or not with_slim
            rest = diff({k: v for k, v in a.items() if k != 'games'}, {k: v for k, v in b.items() if k != 'games'})
            failures += summarize(f'  {name} outside the games', classify(rest, hash_pairs))
        players_equal = sum(strip(pages['full']['players'][p]) == strip(pages['slim']['players'][p]) for p in pages['full']['players'])
        print(f"  player pages equal: {players_equal}/{len(pages['full']['players'])}; page payload bytes {pages['full']['payload_bytes']} (full) "
              f"{pages['slim']['payload_bytes']} (slim)")
        failures += players_equal != len(pages['full']['players'])
        results['dashboard'] = dict(players_equal=players_equal)

    print('\nRESULT ' + ('OK: every difference is an off-slate count or the hash of the stored bytes' if not failures
                         else f'FAILED: {failures} checks'))
    if args.json:
        args.json.write_text(json.dumps(results, default=str))
    raise SystemExit(1 if failures else 0)


if __name__ == '__main__':
    main()
