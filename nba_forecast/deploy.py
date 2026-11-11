"""Deploy this package to production in one step and record what is deployed.

    python3 -m nba_forecast.deploy --directory data/operations/production

Each step is idempotent, so a second run after a success changes nothing:

1. pin the package as a runner release (``runner_release.pin_release``);
2. build the refined model artifact bound to that package digest from the active
   model's 0.5.0 parent (``refinement_upgrade.build_artifact``) and install it;
3. back up the activation pointer under ``activation-backups/`` and point it at
   the new artifact, holding the derived service's and the tick's locks so a
   running rebuild or tick finishes first and neither can write a refit of the
   old artifact over the new one. The derived lock is taken first: a rebuild
   takes minutes, and the ticks keep running while the deploy waits for it;
   the tick lock is then held only from the end of the running tick;
4. store write-ahead logging in the ledger (``PRAGMA journal_mode=WAL``), retrying
   while a reader such as a dashboard request holds the file; the derived
   service's long reads then never hold up the tick's commits;
5. write the units this release needs that the host lacks (the derived and
   capture services and their timers, from ``nba_forecast.units``), rewrite
   ``WorkingDirectory`` in every service unit, reload systemd, restart the
   dashboard and enable each new timer: the timers run the new release on their
   next tick;
6. write ``deployed.json``: the commit, package digest, release, artifact, time,
   the ledger's journal mode and the units written or rewritten.

The next tick re-forecasts every pregame game under the new artifact; the next
derived run refits the prospective player calibration, which is bound to the
model key. The margin correction is bound to the margin method instead
(``margins.CALIBRATION_FAMILY``), so a valid one carries over from the artifact
being replaced. Nothing under the production directory is edited by hand.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

from .enhancements import _read, install_artifact
from .files import atomic_bytes, atomic_json
from .margins import CALIBRATION_FAMILY
from .model_versions import REFINED_VERSION
from .prospective_margin_calibration import validate_margin_calibration
from .refinement_upgrade import build_artifact
from .runner_release import package_digest, pin_release
from .units import CREATED_BY_DEPLOY, SERVICES, created_units

UNITS = SERVICES  # each has a WorkingDirectory to point at the release
# Taken in this order by everything that holds both (``_locks``, ``dedupe-histories``).
# The slow holder comes first: waiting for a rebuild must not keep the ticks out.
LOCKS = ('derived.lock', 'tick.lock')
LOCK_HOLDERS = {'derived.lock': 'a derived rebuild', 'tick.lock': 'a tick'}
# Rebuilds run back to back on a game night, the next starting two or three seconds
# after the last; the lock is tried often enough to be taken in that gap.
LOCK_POLL_SECONDS = .1
PACKAGE = Path(__file__).resolve().parent


def _git(repo):
    """The commit the package was deployed from, and whether the tree had uncommitted changes."""
    try:
        head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(['git', '-C', str(repo), 'status', '--porcelain', '--', 'nba_forecast'],
                                    capture_output=True, text=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return head, dirty


def _active(directory, now):
    """The active artifact as saved, and its 0.5.0 parent (the base a refined artifact is built from).

    The active artifact is read directly: ``enhancements._read`` refuses a
    refined artifact bound to another package digest, which is exactly what the
    one being replaced is.
    """
    pointer = json.loads((directory / 'model-upgrade-active.json').read_text())
    raw = (directory / 'model-artifacts' / (pointer['artifact_sha256'] + '.json')).read_bytes()
    if hashlib.sha256(raw).hexdigest() != pointer['artifact_sha256']:
        raise ValueError('Active model artifact archive hash mismatch')
    active = json.loads(raw)
    if active['model_version'] == REFINED_VERSION:
        parent_digest = active['parent_artifact_sha256']
        base = _read(directory, parent_digest, now)
    else:
        parent_digest, base = pointer['artifact_sha256'], active
    return pointer, active, base, parent_digest


@contextmanager
def _locks(directory, wait):
    """Hold the derived service's lock, then the tick's: a running rebuild or tick finishes first, and none starts until the activation is written.

    A derived rebuild that finished after reading the old activation would
    otherwise refit its calibration and write the old artifact back over the
    new one (a tick did this on 2026-10-26, when it owned the rebuild). Both
    locks are held from before the activation is read until everything is
    written, so no rebuild or tick overlaps any of it; the order in which they
    were taken does not change that.

    The derived lock comes first because a rebuild takes minutes and a tick
    seconds. While the deploy waits for a rebuild it holds nothing, and the
    ticks run as usual; once it has the derived lock it waits for the running
    tick, and only then do timer ticks exit as 'already running' (and derived
    runs as 'busy') until the deploy is done. Taking the tick lock first kept
    every tick out for the rest of a rebuild: the eight ticks from 16:53 to
    17:00 Pacific on 2026-11-09 all exited, the last of them a tipoff.

    This cannot deadlock: a tick takes only the tick lock and a derived run
    only the derived lock, each without waiting (they exit when it is held),
    so the one process that waits while holding a lock is this one, and what
    it waits for never waits for anything. Two holders of both (a deploy and
    ``recover-evidence``) take them in the same order. Each lock is waited for
    up to ``wait`` seconds.
    """
    handles = [(name, (Path(directory) / name).open('a')) for name in LOCKS]
    try:
        for name, handle in handles:
            deadline = time.monotonic() + wait
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError(f'{LOCK_HOLDERS[name]} has held the lock {name} for more than {wait} seconds; retry later') from None
                    time.sleep(LOCK_POLL_SECONDS)
        yield
    finally:
        for _, handle in reversed(handles):
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


def _rewrite_unit(path, release):
    """Point a unit's WorkingDirectory at the release; the setting is a literal path, with % escaped."""
    text = path.read_text()
    lines = [line for line in text.splitlines() if line.startswith('WorkingDirectory=')]
    if len(lines) != 1:
        raise ValueError(f'{path} must have exactly one WorkingDirectory line')
    value = 'WorkingDirectory=' + str(release).replace('%', '%%')
    if lines[0] == value:
        return False
    atomic_bytes(path, re.sub(r'^WorkingDirectory=.*$', lambda m: value, text, count=1, flags=re.M).encode())
    return True


def _wal(directory, wait):
    """Store write-ahead logging in the ledger; the switch needs the file to itself, so retry while a reader holds it."""
    db = Path(directory) / 'forecast.sqlite'
    if not db.exists():
        return None
    deadline = time.monotonic() + wait
    while True:
        conn = sqlite3.connect(db, timeout=1)
        try:
            mode = conn.execute('PRAGMA journal_mode = WAL').fetchone()[0]
            if mode == 'wal':
                return mode
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()
        if time.monotonic() >= deadline:
            raise ValueError(f'the ledger could not be switched to write-ahead logging in {wait} seconds; a reader holds it')
        time.sleep(1)


def _missing_units(units):
    return [name for name in CREATED_BY_DEPLOY if not (units / name).exists()]


def _create_units(units, release, directory):
    """Write the units this release needs that the host does not have yet, pointed at the release."""
    files = created_units(sys.executable, release, directory)
    created = _missing_units(units)
    for name in created:
        units.mkdir(parents=True, exist_ok=True)
        atomic_bytes(units / name, files[name].encode())
    return created


def _plan(directory, units, package, digest, now, dry_run):
    pointer, active, base, parent_digest = _active(directory, now)
    artifact = build_artifact(base, parent_digest, now)
    inherited = active.get('margin_calibration')
    if inherited is not None and validate_margin_calibration(inherited, CALIBRATION_FAMILY, now):
        artifact['margin_calibration'] = inherited
    unchanged = (active.get('model_key') == artifact['model_key'] and active.get('runner_package_sha256') == digest)
    commit, dirty = _git(package.parent)
    plan = dict(deployed_at=now, commit=commit, dirty_worktree=dirty, package_sha256=digest,
                release=str(directory / 'runner-releases' / digest),
                model_version=artifact['model_version'], model_key=artifact['model_key'],
                margin_calibration_inherited=artifact.get('margin_calibration') is not None,
                previous=dict(artifact_sha256=pointer['artifact_sha256'], model_key=active.get('model_key'),
                              package_sha256=active.get('runner_package_sha256')),
                units=[str(units / name) for name in UNITS], units_created=_missing_units(units),
                unchanged=unchanged, dry_run=dry_run)
    return plan, pointer, artifact


def deploy(directory, units, *, systemd=True, dry_run=False, package=PACKAGE, wait=600):
    directory, units = Path(directory).resolve(), Path(units)
    now = datetime.now(timezone.utc).isoformat()
    digest = package_digest(package)
    if dry_run:
        return _plan(directory, units, package, digest, now, dry_run)[0]
    with _locks(directory, wait):
        # Read the activation only once no tick or rebuild can move it, and write everything before one can start.
        plan, pointer, artifact = _plan(directory, units, package, digest, now, dry_run)
        release = pin_release(package, directory / 'runner-releases')
        if plan['unchanged']:
            plan['artifact_sha256'] = pointer['artifact_sha256']
        else:
            plan['artifact_sha256'] = installed = install_artifact(directory, artifact)
            backup = directory / 'activation-backups' / installed
            atomic_json(backup / 'previous-model-upgrade-active.json', pointer)
            activation = dict(artifact_sha256=installed, activated_at=now)
            atomic_json(backup / 'activation.json', activation)
            atomic_json(directory / 'model-upgrade-active.json', activation)
        plan['ledger_journal_mode'] = _wal(directory, wait)
        created = _create_units(units, release, directory)
        rewritten = [name for name in UNITS if _rewrite_unit(units / name, release)]
        plan['units_created'], plan['units_rewritten'] = created, rewritten
        if systemd and (rewritten or created):
            subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
        atomic_json(directory / 'deployed.json', plan)
    if systemd and 'nba-dashboard.service' in rewritten:
        subprocess.run(['systemctl', '--user', 'restart', 'nba-dashboard.service'], check=True)
    for timer in [name for name in created if name.endswith('.timer')] if systemd else []:
        subprocess.run(['systemctl', '--user', 'enable', '--now', timer], check=True)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--directory', default='data/operations/production', help='Production operations directory')
    parser.add_argument('--units', default=str(Path.home() / '.config/systemd/user'), help='User systemd unit directory')
    parser.add_argument('--no-systemd', action='store_true', help='Rewrite the units without daemon-reload or a restart')
    parser.add_argument('--dry-run', action='store_true', help='Print what would be deployed; write nothing')
    parser.add_argument('--wait', type=int, default=900, help='Seconds to wait for a running derived rebuild to finish, and then for a running tick')
    args = parser.parse_args()
    result = deploy(args.directory, args.units, systemd=not args.no_systemd, dry_run=args.dry_run, wait=args.wait)
    if result.get('dirty_worktree'):
        result['warning'] = 'The package has uncommitted changes; production will run code that is not in any commit.'
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
