"""Run main's and a branch's derived rebuild on the same copy of an operations folder and diff every output.

    python3 scripts/research/derived_equivalence.py --source COPY --main MAIN_CHECKOUT --branch BRANCH_CHECKOUT \
        --work WORKDIR [--now ISO] [--new-runs 8]

COPY is a copy of an operations directory (never production itself). Each scenario
gets its own work folder: hard links to COPY's files (every derived write replaces
a file through a rename, so COPY is never changed) and a symlink to COPY's ledger,
which all scenarios read. Scenarios:

  main   main's ``_derived_reports``
  cold   the branch with no derived sidecar
  warm   the branch with the sidecar ``cold`` left
  new    the branch with that sidecar after forgetting the last ``--new-runs`` runs,
         so they are extracted as runs saved since the last rebuild would be

Every scenario starts from COPY's files, runs at the same fixed clock (``--now``,
default the ledger's modification time) with the refined-model artifact accepted
for either code tree, and its outputs are compared with main's: every JSON file
the rebuild wrote or rewrote, ignoring only ``generated_at``, plus the report's
errors and warnings. Prints per-step timings and the differences; exit status 1
when any output differs.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

PRODUCTION = Path('/mnt/2TBHDD/NBA-Forecast/repo/data/operations/production').resolve()
OUTPUTS = ('performance.json', 'benchmarks.json', 'prospective-calibration-status.json',
           'prospective-margin-calibration-status.json', 'model-status.json', 'model-upgrade-active.json',
           'market/report.json', 'daily-reviews', 'postgame', 'margin-calibration-attempts', 'model-artifacts')

def _runner():
    return r'''
import json, sys, time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
folder, now, forget = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
import nba_forecast.operations as ops
import nba_forecast.store as store
import nba_forecast.runner_release as release
import nba_forecast.prospective_calibration as pc, nba_forecast.prospective_margin_calibration as pmc
import nba_forecast.postgame as pg, nba_forecast.daily_review as dr
if forget >= 0:
    from nba_forecast import derived_cache
    derived_cache.forget_after(folder / 'forecast.sqlite', forget)
timings = {}
def timed(module, name):
    fn = getattr(module, name)
    def wrapper(*a, **k):
        w, c = time.monotonic(), time.process_time()
        try:
            return fn(*a, **k)
        finally:
            t = timings.setdefault(name, [0., 0.])
            t[0] += time.monotonic() - w
            t[1] += time.process_time() - c
    setattr(module, name, wrapper)
for module, name in ((ops, 'performance_report'), (ops, 'benchmark_report'), (ops, 'market_report'), (pc, 'fit_prospective'),
                     (pmc, 'fit_prospective_margin'), (pmc, 'verify_margin_calibration_sources'), (pg, 'build_postgames'),
                     (dr, 'build_daily_reviews')):
    timed(module, name)
package = None
pointer = folder / 'model-upgrade-active.json'
if pointer.exists():
    digest = json.loads(pointer.read_text())['artifact_sha256']
    package = json.loads((folder / 'model-artifacts' / (digest + '.json')).read_text()).get('runner_package_sha256')
real = release.package_digest
clock = datetime.fromisoformat(now.replace('Z', '+00:00'))
with patch.object(ops, '_now', return_value=now), patch.object(store, '_now', return_value=clock), \
        patch.object(release, 'package_digest', side_effect=lambda p: package or real(p)):
    schedule, source = ops.saved_calendar(folder)
    active, model_digest = ops.load_active(folder, now)
    report = dict(errors=[], warnings=[])
    report['final_sync'] = json.loads((folder / 'last-tick.json').read_text()).get('final_sync') if (folder / 'last-tick.json').exists() else None
    w, c = time.monotonic(), time.process_time()
    ops._derived_reports(folder, folder / 'forecast.sqlite', schedule, active, model_digest, report)
    timings['TOTAL'] = [time.monotonic() - w, time.process_time() - c]
print(json.dumps(dict(timings=timings, errors=report['errors'], warnings=report['warnings']), default=str))
'''


def _prepare(source, ledger, target):
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for path in source.iterdir():
        name = path.name
        if name.startswith('forecast.sqlite') or '.derived-cache.sqlite' in name or name.endswith('.lock'):
            continue
        if path.is_dir():
            subprocess.run(['cp', '-al', str(path), str(target / name)], check=True)
        else:
            os.link(path, target / name)
    (target / 'forecast.sqlite').symlink_to(ledger)


def _run(repo, folder, now, forget=-1):
    env = dict(os.environ, PYTHONPATH=str(repo), PYTHONDONTWRITEBYTECODE='1')
    started = time.monotonic()
    done = subprocess.run([sys.executable, '-c', _runner(), str(folder), now, str(forget)], env=env, cwd=str(folder),
                          capture_output=True, text=True)
    if done.returncode:
        sys.stderr.write(done.stderr)
        raise SystemExit(f'{folder.name}: the rebuild failed')
    result = json.loads(done.stdout.strip().splitlines()[-1])
    result['wall'] = time.monotonic() - started
    return result


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k != 'generated_at'}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _outputs(folder, source):
    """Every output file the rebuild wrote or rewrote (a new inode), relative path -> content without generated_at."""
    found = {}
    for name in OUTPUTS:
        root = folder / name
        paths = [root] if root.is_file() else sorted(p for p in root.rglob('*') if p.is_file()) if root.is_dir() else []
        for path in paths:
            relative = path.relative_to(folder)
            original = source / relative
            if original.exists() and os.stat(original).st_ino == os.stat(path).st_ino:
                continue  # Untouched hard link.
            try:
                found[str(relative)] = _strip(json.loads(path.read_text()))
            except ValueError:
                found[str(relative)] = path.read_bytes()
    return found


def _diff(a, b, path='', out=None, limit=20):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if type(a) is not type(b):
        out.append(f'{path}: {type(a).__name__} != {type(b).__name__}')
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f'{path}.{k}: only in {"branch" if k in b else "main"}')
            else:
                _diff(a[k], b[k], f'{path}.{k}', out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f'{path}: {len(a)} items != {len(b)}')
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                _diff(x, y, f'{path}[{i}]', out, limit)
    elif a != b and not (a != a and b != b):
        out.append(f'{path}: {str(a)[:80]} != {str(b)[:80]}')
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--main', required=True, type=Path, help='checkout of the branch base (main)')
    parser.add_argument('--branch', required=True, type=Path)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--ledger', type=Path, help='ledger to read instead of SOURCE/forecast.sqlite')
    parser.add_argument('--now')
    parser.add_argument('--new-runs', type=int, default=8)
    parser.add_argument('--scenarios', default='main,cold,warm,new')
    args = parser.parse_args()
    # Each rebuild runs with its scenario folder as working directory: every path must be absolute.
    for name in ('source', 'main', 'branch', 'work', 'ledger'):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).resolve())
    source = args.source.resolve()
    if source == PRODUCTION or PRODUCTION in source.parents:
        raise SystemExit('Run this on a copy of production, never on production itself.')
    ledger = (args.ledger or source / 'forecast.sqlite').resolve()
    now = args.now or datetime.fromtimestamp(ledger.stat().st_mtime, timezone.utc).isoformat()
    import sqlite3
    with sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True) as conn:
        table = 'run_records' if conn.execute("SELECT 1 FROM sqlite_master WHERE name='run_records'").fetchone() else 'runs'
        last = conn.execute(f'SELECT MAX(rowid) FROM {table}').fetchone()[0] or 0
    print(f'source {source}\nledger {ledger} (newest rowid {last})\nnow {now}\n')
    wanted = args.scenarios.split(',')
    results, outputs = {}, {}
    for name in wanted:
        folder = args.work / name
        _prepare(source, ledger, folder)
        if name in ('warm', 'new'):
            shutil.copyfile(args.work / 'cold-cache.sqlite', folder / 'forecast.derived-cache.sqlite')
        repo = args.main if name == 'main' else args.branch
        results[name] = _run(repo, folder, now, forget=max(0, last - args.new_runs) if name == 'new' else -1)
        if name == 'cold':
            # Keep the cold sidecar, checkpointed, for the warm scenarios.
            with sqlite3.connect(folder / 'forecast.derived-cache.sqlite') as conn:
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            shutil.copyfile(folder / 'forecast.derived-cache.sqlite', args.work / 'cold-cache.sqlite')
        outputs[name] = _outputs(folder, source)
        print(f"{name:5} total {results[name]['timings']['TOTAL'][0]:8.1f} s wall, {results[name]['timings']['TOTAL'][1]:8.1f} s CPU")
    steps = sorted({k for r in results.values() for k in r['timings'] if k != 'TOTAL'})
    print('\n%-36s' % 'step (wall s)' + ''.join('%10s' % n for n in wanted))
    for step in steps + ['TOTAL']:
        print('%-36s' % step + ''.join('%10.1f' % results[n]['timings'].get(step, [0])[0] for n in wanted))
    differ = False
    if 'main' in outputs:
        for name in wanted:
            if name == 'main':
                continue
            a, b = outputs['main'], outputs[name]
            problems = []
            for key in sorted(set(a) | set(b)):
                if key not in a or key not in b:
                    problems.append(f'{key}: written only by {"main" if key in a else name}')
                elif a[key] != b[key]:
                    problems.extend(f'{key}{d}' for d in _diff(a[key], b[key]))
            for part in ('errors', 'warnings'):
                if _strip(results['main'][part]) != _strip(results[name][part]):
                    problems.append(f'report {part}: {results["main"][part]} != {results[name][part]}')
            differ |= bool(problems)
            print(f'\n{name} vs main: {len(a)} output files compared; ' + ('IDENTICAL' if not problems else f'{len(problems)} DIFFERENCES'))
            for line in problems[:40]:
                print('  ' + line)
        print('\nmain report errors:', json.dumps(results['main']['errors'])[:600])
    raise SystemExit(1 if differ else 0)


if __name__ == '__main__':
    main()
