"""Deploy this package to production in one step and record what is deployed.

    python3 -m nba_forecast.deploy --directory data/operations/production

Each step is idempotent, so a second run after a success changes nothing:

1. pin the package as a runner release (``runner_release.pin_release``);
2. build the refined model artifact bound to that package digest from the active
   model's 0.5.0 parent (``refinement_upgrade.build_artifact``) and install it;
3. back up the activation pointer under ``activation-backups/`` and point it at
   the new artifact;
4. rewrite ``WorkingDirectory`` in the user systemd units and reload them: the
   timer runs the new release on its next tick and the dashboard is restarted;
5. write ``deployed.json``: the commit, package digest, release, artifact and time.

The next tick re-forecasts every pregame game under the new artifact and refits
the prospective calibration, which is bound to the model key. Nothing under the
production directory is edited by hand.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

from .enhancements import _read, install_artifact
from .files import atomic_bytes, atomic_json
from .model_versions import REFINED_VERSION
from .refinement_upgrade import build_artifact
from .runner_release import package_digest, pin_release

UNITS = ('nba-forecast.service', 'nba-dashboard.service')
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


def deploy(directory, units, *, systemd=True, dry_run=False, package=PACKAGE):
    directory, units = Path(directory).resolve(), Path(units)
    now = datetime.now(timezone.utc).isoformat()
    digest = package_digest(package)
    pointer, active, base, parent_digest = _active(directory, now)
    artifact = build_artifact(base, parent_digest, now)
    unchanged = (active.get('model_key') == artifact['model_key'] and active.get('runner_package_sha256') == digest)
    release = directory / 'runner-releases' / digest
    commit, dirty = _git(package.parent)
    plan = dict(deployed_at=now, commit=commit, dirty_worktree=dirty, package_sha256=digest, release=str(release),
                model_version=artifact['model_version'], model_key=artifact['model_key'],
                previous=dict(artifact_sha256=pointer['artifact_sha256'], model_key=active.get('model_key'),
                              package_sha256=active.get('runner_package_sha256')),
                units=[str(units / name) for name in UNITS], unchanged=unchanged, dry_run=dry_run)
    if dry_run:
        return plan
    release = pin_release(package, directory / 'runner-releases')
    if unchanged:
        plan['artifact_sha256'] = pointer['artifact_sha256']
    else:
        plan['artifact_sha256'] = installed = install_artifact(directory, artifact)
        backup = directory / 'activation-backups' / installed
        atomic_json(backup / 'previous-model-upgrade-active.json', pointer)
        activation = dict(artifact_sha256=installed, activated_at=now)
        atomic_json(backup / 'activation.json', activation)
        atomic_json(directory / 'model-upgrade-active.json', activation)
    rewritten = [name for name in UNITS if _rewrite_unit(units / name, release)]
    plan['units_rewritten'] = rewritten
    if systemd and rewritten:
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
        if 'nba-dashboard.service' in rewritten:
            subprocess.run(['systemctl', '--user', 'restart', 'nba-dashboard.service'], check=True)
    atomic_json(directory / 'deployed.json', plan)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--directory', default='data/operations/production', help='Production operations directory')
    parser.add_argument('--units', default=str(Path.home() / '.config/systemd/user'), help='User systemd unit directory')
    parser.add_argument('--no-systemd', action='store_true', help='Rewrite the units without daemon-reload or a restart')
    parser.add_argument('--dry-run', action='store_true', help='Print what would be deployed; write nothing')
    args = parser.parse_args()
    result = deploy(args.directory, args.units, systemd=not args.no_systemd, dry_run=args.dry_run)
    if result.get('dirty_worktree'):
        result['warning'] = 'The package has uncommitted changes; production will run code that is not in any commit.'
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
