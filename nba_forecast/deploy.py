"""Deploy this package to production in one step and record what is deployed.

    python3 -m nba_forecast.deploy --directory data/operations/production

Each step is idempotent, so a second run after a success changes nothing:

1. pin the package as a runner release (``runner_release.pin_release``);
2. build the refined model artifact bound to that package digest from the active
   model's 0.5.0 parent (``refinement_upgrade.build_artifact``) and install it;
3. back up the activation pointer under ``activation-backups/`` and point it at
   the new artifact, holding the tick's and the derived service's locks so a
   running tick or rebuild finishes first and neither can write a refit of the
   old artifact over the new one;
4. store write-ahead logging in the ledger (``PRAGMA journal_mode=WAL``), retrying
   while a reader such as a dashboard request holds the file; the derived
   service's long reads then never hold up the tick's commits;
5. write the units this release needs that the host lacks (the derived service
   and its timer, from ``nba_forecast.units``), rewrite ``WorkingDirectory`` in
   every service unit, reload systemd, restart the dashboard and enable a new
   timer: the timers run the new release on their next tick;
6. write ``deployed.json``: the commit, package digest, release, artifact, time,
   the ledger's journal mode and the units written or rewritten.

The next tick re-forecasts every pregame game under the new artifact; the next
derived run refits the prospective calibration, which is bound to the model key.
Nothing under the production directory is edited by hand.
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
from .model_versions import REFINED_VERSION
from .refinement_upgrade import build_artifact
from .runner_release import package_digest, pin_release
from .units import CREATED_BY_DEPLOY, SERVICES, derived_units

UNITS = SERVICES  # each has a WorkingDirectory to point at the release
LOCKS = ('tick.lock', 'derived.lock')
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
    """Hold the tick's and the derived service's own locks: a running tick or rebuild finishes first, and none starts until the activation is written.

    A derived rebuild that finished after reading the old activation would
    otherwise refit its calibration and write the old artifact back over the
    new one (a tick did this on 2026-10-26, when it owned the rebuild). A timer
    run that fires meanwhile exits as 'already running' or 'busy' and the next
    minute's runs. The wait is shared by both locks.
    """
    handles = [(name, (Path(directory) / name).open('a')) for name in LOCKS]
    deadline = time.monotonic() + wait
    try:
        for name, handle in handles:
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError(f'a tick or derived run has held the lock {name} for more than {wait} seconds; retry later') from None
                    time.sleep(1)
        yield
    finally:
        for _, handle in handles:
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
    files = derived_units(sys.executable, release, directory)
    created = _missing_units(units)
    for name in created:
        units.mkdir(parents=True, exist_ok=True)
        atomic_bytes(units / name, files[name].encode())
    return created


def _plan(directory, units, package, digest, now, dry_run):
    pointer, active, base, parent_digest = _active(directory, now)
    artifact = build_artifact(base, parent_digest, now)
    unchanged = (active.get('model_key') == artifact['model_key'] and active.get('runner_package_sha256') == digest)
    commit, dirty = _git(package.parent)
    plan = dict(deployed_at=now, commit=commit, dirty_worktree=dirty, package_sha256=digest,
                release=str(directory / 'runner-releases' / digest),
                model_version=artifact['model_version'], model_key=artifact['model_key'],
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
    if systemd and 'nba-derived.timer' in created:
        subprocess.run(['systemctl', '--user', 'enable', '--now', 'nba-derived.timer'], check=True)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--directory', default='data/operations/production', help='Production operations directory')
    parser.add_argument('--units', default=str(Path.home() / '.config/systemd/user'), help='User systemd unit directory')
    parser.add_argument('--no-systemd', action='store_true', help='Rewrite the units without daemon-reload or a restart')
    parser.add_argument('--dry-run', action='store_true', help='Print what would be deployed; write nothing')
    parser.add_argument('--wait', type=int, default=900, help='Seconds to wait for a running tick or derived rebuild to finish')
    args = parser.parse_args()
    result = deploy(args.directory, args.units, systemd=not args.no_systemd, dry_run=args.dry_run, wait=args.wait)
    if result.get('dirty_worktree'):
        result['warning'] = 'The package has uncommitted changes; production will run code that is not in any commit.'
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
