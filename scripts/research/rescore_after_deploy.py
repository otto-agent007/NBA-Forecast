"""What the first ticks after a deploy spend re-scoring live runs: ``operations._evaluate_runs`` of one
package tree, called on a private operations folder, one JSON line per call.

Usage:
  python3 scripts/research/rescore_after_deploy.py PACKAGE_ROOT FOLDER --label NAME [--calls N | --until-drained] [--changed-absences]

Run it once with main's tree (``git archive <commit> nba_forecast | tar -x -C DIR``) and once with
this branch's, each from the same starting ledger and ``evaluation-state.json`` (one written by
another release, as after a deploy), and compare the seconds of the first call and the final
digests. The ledger only accepts appends, so the starting state is a second copy, or the revisions
above the starting newest rowid deleted from a private copy between the runs.

PACKAGE_ROOT holds the ``nba_forecast`` package to import (main's exported tree, or the branch
worktree). FOLDER holds ``forecast.sqlite``, ``finals/`` and ``evaluation-state.json``; it must
be this work stream's own copy, because ``evaluate_run`` writes evaluation revisions.

--changed-absences  the same change on either tree: the rows ``_absent_from_final`` makes carry
                    another ``participation_reason``, so every run with a forecast player its
                    final leaves out has different final rows under this code and a re-score
                    writes a revision (what a release that changes evaluation would do).
Every number printed is measured here: seconds per call, the report's counters, revisions written
(row count before and after), and, before the first call and after the last, the evaluations table
digest: row count, newest rowid and a SHA-256 over (run_id, revision, actuals_sha256) read in
primary-key order.
"""
import argparse, hashlib, json, sqlite3, sys, time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('package_root'); parser.add_argument('folder')
parser.add_argument('--label', required=True)
parser.add_argument('--calls', type=int, default=1)
parser.add_argument('--until-drained', action='store_true')
parser.add_argument('--changed-absences', action='store_true')
args = parser.parse_args()
sys.dont_write_bytecode = True
sys.path.insert(0, args.package_root)
from nba_forecast import operations
assert Path(operations.__file__).resolve().is_relative_to(Path(args.package_root).resolve()), operations.__file__
folder = Path(args.folder)
db = folder / 'forecast.sqlite'
assert 'data/operations/production' not in str(folder.resolve()), 'a private copy only: evaluate_run writes revisions'


def rows():
    conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        return conn.execute('SELECT COUNT(*) FROM evaluations').fetchone()[0]
    finally:
        conn.close()


def table_digest():
    """Row count, newest rowid and a digest of keys and actuals hashes, read through the primary-key index."""
    conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        digest, count = hashlib.sha256(), 0
        for row in conn.execute('SELECT run_id, revision, actuals_sha256 FROM evaluations ORDER BY run_id, revision'):
            digest.update(json.dumps(row).encode() + b'\n')
            count += 1
        newest = conn.execute('SELECT COALESCE(MAX(rowid), 0) FROM evaluations').fetchone()[0]
        return dict(rows=count, newest_rowid=newest, sha256=digest.hexdigest())
    finally:
        conn.close()


if args.changed_absences:
    original = operations._absent_from_final
    def changed(rows, predictions):
        return [dict(r, participation_reason='Not in the final box score (changed release)') for r in original(rows, predictions)]
    operations._absent_from_final = changed

state_path = folder / 'evaluation-state.json'
saved = json.loads(state_path.read_text())
print(json.dumps(dict(label=args.label, start=table_digest(), state_code=saved.get('code'), code=operations._code_digest(),
                      remembered_runs=len(saved.get('runs', {})), stale_in_state=len(saved.get('stale', [])),
                      package=str(Path(operations.__file__).resolve().parent))), flush=True)
call, total, count = 0, 0.0, rows()
while True:
    call += 1
    report = dict(evaluated_runs=0, errors=[])
    started = time.monotonic()
    operations._evaluate_runs(folder, db, report)
    seconds = time.monotonic() - started
    total += seconds
    before, count = count, rows()
    state = json.loads(state_path.read_text())
    backlog = report.get('evaluation_backlog')
    print(json.dumps(dict(label=args.label, call=call, seconds=round(seconds, 2), cumulative_seconds=round(total, 2),
                          evaluated_runs=report['evaluated_runs'], unchanged_evaluation_runs=report.get('unchanged_evaluation_runs'),
                          evaluation_backlog=backlog, errors=len(report['errors']), first_errors=report['errors'][:2],
                          remembered_runs=len(state['runs']), stale_in_state=len(state.get('stale', [])),
                          revisions_written=count - before)), flush=True)
    if args.until_drained:
        if backlog is None or backlog['stale'] == 0 or call >= 500:
            break
    elif call >= args.calls:
        break
print(json.dumps(dict(label=args.label, end=table_digest(), calls=call, cumulative_seconds=round(total, 2))), flush=True)
