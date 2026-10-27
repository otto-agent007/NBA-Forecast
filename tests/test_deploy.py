import json
from pathlib import Path
import tempfile
import unittest

from nba_forecast import deploy as D
from nba_forecast.enhancements import install_artifact, load_active
from nba_forecast.model_versions import REFINED_VERSION
from nba_forecast.refinement_upgrade import build_artifact
from nba_forecast.runner_release import package_digest


def production(root):
    """A production directory whose active model is a refined artifact bound to some other package."""
    base = dict(model_version='regular-season-baseline-0.5.0', model_key='base', known_at='2026-10-24T01:00:00Z',
                parameters={'rotation_enabled': True}, team_games=[], calibration=None, validation_report={}, context_model=None)
    parent = install_artifact(root, base)
    old = build_artifact(base, parent, '2026-10-25T08:00:00Z')
    old['runner_package_sha256'] = 'f' * 64  # the release being replaced
    active = install_artifact(root, old)
    (root / 'model-upgrade-active.json').write_text(json.dumps(dict(artifact_sha256=active, activated_at='2026-10-25T08:00:00Z')))
    units = root / 'units'
    units.mkdir()
    for name in D.UNITS:
        (units / name).write_text('[Unit]\nDescription=x\n[Service]\nType=oneshot\nWorkingDirectory=/old/release\nUMask=0077\nExecStart=/usr/bin/python3 -m nba_forecast tick\n')
    return active, units


class DeployTests(unittest.TestCase):
    def test_deploy_pins_builds_activates_rewrites_and_records_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_active, units = production(root)
            plan = D.deploy(root, units, systemd=False, dry_run=True)
            self.assertFalse(plan['unchanged'])
            self.assertEqual(json.loads((root / 'model-upgrade-active.json').read_text())['artifact_sha256'], old_active, 'a dry run writes nothing')
            self.assertFalse((root / 'deployed.json').exists())
            result = D.deploy(root, units, systemd=False)
            digest = package_digest(D.PACKAGE)
            self.assertEqual(result['package_sha256'], digest)
            self.assertTrue((root / 'runner-releases' / digest / 'nba_forecast' / 'deploy.py').exists())
            pointer = json.loads((root / 'model-upgrade-active.json').read_text())
            self.assertEqual(pointer['artifact_sha256'], result['artifact_sha256'])
            self.assertNotEqual(pointer['artifact_sha256'], old_active)
            artifact, _ = load_active(root, '2027-01-01T00:00:00Z')  # the new artifact is bound to this package
            self.assertEqual((artifact['model_version'], artifact['runner_package_sha256']), (REFINED_VERSION, digest))
            backup = root / 'activation-backups' / result['artifact_sha256']
            self.assertEqual(json.loads((backup / 'previous-model-upgrade-active.json').read_text())['artifact_sha256'], old_active)
            for name in D.UNITS:
                settings = dict(line.split('=', 1) for line in (units / name).read_text().splitlines() if '=' in line)
                self.assertEqual(settings['WorkingDirectory'], str(root / 'runner-releases' / digest))
                self.assertEqual(settings['UMask'], '0077', 'other settings are untouched')
            self.assertEqual(sorted(result['units_rewritten']), sorted(D.UNITS))
            recorded = json.loads((root / 'deployed.json').read_text())
            self.assertEqual((recorded['package_sha256'], recorded['artifact_sha256']), (digest, result['artifact_sha256']))
            self.assertEqual(recorded['previous']['artifact_sha256'], old_active)
            again = D.deploy(root, units, systemd=False)
            self.assertTrue(again['unchanged'])
            self.assertEqual(again['artifact_sha256'], result['artifact_sha256'], 'the same code deploys to the same artifact')
            self.assertEqual(again['units_rewritten'], [])

    def test_a_unit_without_one_working_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, units = production(root)
            (units / 'nba-dashboard.service').write_text('[Service]\nExecStart=x\n')
            with self.assertRaisesRegex(ValueError, 'WorkingDirectory'):
                D.deploy(root, units, systemd=False)
