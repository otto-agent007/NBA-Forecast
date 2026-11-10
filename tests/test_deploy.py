import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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
    import sqlite3
    ledger = sqlite3.connect(root / 'forecast.sqlite')  # a rollback-mode ledger from before this release
    ledger.execute('CREATE TABLE marker (x)')
    ledger.commit()
    ledger.close()
    units = root / 'units'
    units.mkdir()
    for name in ('nba-forecast.service', 'nba-dashboard.service'):  # a host installed before the derived service existed
        (units / name).write_text('[Unit]\nDescription=x\n[Service]\nType=oneshot\nWorkingDirectory=/old/release\nUMask=0077\nExecStart=/usr/bin/python3 -m nba_forecast tick\n')
    return active, units


def free(root, name):
    """Whether a tick (``tick.lock``) or a derived run (``derived.lock``) starting now would get its lock."""
    import fcntl
    with (root / name).open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(handle, fcntl.LOCK_UN)
        return True


class DeployTests(unittest.TestCase):
    def test_deploy_pins_builds_activates_rewrites_and_records_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_active, units = production(root)
            plan = D.deploy(root, units, systemd=False, dry_run=True)
            self.assertFalse(plan['unchanged'])
            self.assertEqual(plan['units_created'], list(D.CREATED_BY_DEPLOY))
            self.assertEqual(json.loads((root / 'model-upgrade-active.json').read_text())['artifact_sha256'], old_active, 'a dry run writes nothing')
            self.assertFalse((root / 'deployed.json').exists())
            self.assertFalse((units / 'nba-derived.timer').exists())
            import sqlite3
            self.assertEqual(sqlite3.connect(root / 'forecast.sqlite').execute('PRAGMA journal_mode').fetchone()[0], 'delete')
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
            self.assertEqual(sorted(result['units_rewritten']), ['nba-dashboard.service', 'nba-forecast.service'])
            self.assertEqual(result['units_created'], list(D.CREATED_BY_DEPLOY), 'the derived units are written for the release')
            derived = (units / 'nba-derived.service').read_text()
            self.assertIn(f'-m nba_forecast derived --directory "{root}"', derived)
            self.assertIn('Nice=10', derived)
            self.assertIn('Unit=nba-derived.service', (units / 'nba-derived.timer').read_text())
            recorded = json.loads((root / 'deployed.json').read_text())
            self.assertEqual((recorded['package_sha256'], recorded['artifact_sha256']), (digest, result['artifact_sha256']))
            self.assertEqual(recorded['previous']['artifact_sha256'], old_active)
            self.assertEqual(recorded['units_created'], list(D.CREATED_BY_DEPLOY))
            self.assertEqual(recorded['ledger_journal_mode'], 'wal')
            self.assertEqual(sqlite3.connect(root / 'forecast.sqlite').execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            again = D.deploy(root, units, systemd=False)
            self.assertTrue(again['unchanged'])
            self.assertEqual(again['artifact_sha256'], result['artifact_sha256'], 'the same code deploys to the same artifact')
            self.assertEqual((again['units_rewritten'], again['units_created']), ([], []))

    def test_deploy_carries_a_valid_margin_correction_across_releases(self):
        from datetime import datetime, timedelta
        from nba_forecast.margins import CALIBRATION_FAMILY
        from nba_forecast.prospective_margin_calibration import METHOD, _fit_window
        from tests.test_prospective_margin_calibration import sample
        rows = []
        for i in range(112):
            row = sample(i)
            for field in ('game_time', 'forecast_created_at', 'actual_known_at'):
                row[field] = (datetime.fromisoformat(row[field]) - timedelta(days=61)).isoformat()
            rows.append(row)
        cutoff = '2026-10-21T15:30:00Z'
        correction = dict(schema_version=1, method=METHOD, margin_family=CALIBRATION_FAMILY, model_keys=['margin-model-key'],
                          window_index=0, training_cutoff=cutoff, evidence_samples=rows, **_fit_window(rows, CALIBRATION_FAMILY, cutoff))
        self.assertEqual(correction['status'], 'ready')
        for valid in (True, False):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                old_active, units = production(root)
                archive = root / 'model-artifacts' / (old_active + '.json')
                active = json.loads(archive.read_text())
                active['margin_calibration'] = dict(correction) if valid else dict(correction, location_bias=7.5)
                digest = install_artifact(root, active)
                (root / 'model-upgrade-active.json').write_text(json.dumps(dict(artifact_sha256=digest, activated_at='2026-10-25T08:00:00Z')))
                result = D.deploy(root, units, systemd=False)
                self.assertEqual(result['margin_calibration_inherited'], valid)
                artifact, _ = load_active(root, '2027-01-01T00:00:00Z')
                if valid:
                    self.assertEqual(artifact['margin_calibration']['evidence_sha256'], correction['evidence_sha256'])
                else:
                    self.assertIsNone(artifact.get('margin_calibration'), 'a correction that fails its replay is not carried')

    def test_deploy_waits_for_a_running_tick_or_rebuild_and_gives_up_after_the_wait(self):
        import fcntl
        self.assertEqual(D.LOCKS, ('derived.lock', 'tick.lock'), 'the slow holder is waited for first')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); old_active, units = production(root)
            for name, holder in (('derived.lock', 'a derived rebuild'), ('tick.lock', 'a tick')):
                with (root / name).open('a') as held:
                    fcntl.flock(held, fcntl.LOCK_EX)  # a tick, or a derived rebuild, is running
                    with self.assertRaisesRegex(ValueError, f'{holder} has held the lock {name} for more than 1 seconds; retry later'):
                        D.deploy(root, units, systemd=False, wait=1)
                    self.assertEqual(json.loads((root / 'model-upgrade-active.json').read_text())['artifact_sha256'], old_active)
                    self.assertFalse((root / 'deployed.json').exists(), 'a deploy that gave up wrote nothing')
                    fcntl.flock(held, fcntl.LOCK_UN)
                for other in D.LOCKS:  # giving up releases what it had taken
                    self.assertTrue(free(root, other))
            result = D.deploy(root, units, systemd=False, wait=1)
            self.assertNotEqual(result['artifact_sha256'], old_active)

    def test_ticks_keep_running_while_the_deploy_waits_for_a_rebuild(self):
        import fcntl
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); old_active, units = production(root)
            rebuild = (root / 'derived.lock').open('a')
            fcntl.flock(rebuild, fcntl.LOCK_EX)  # a derived rebuild is part-way through its minutes
            seen = []

            def waiting(seconds):
                # The deploy is waiting for the rebuild. Ticks must still start and finish.
                seen.append(dict(tick_lock_free=free(root, 'tick.lock'),
                                 pointer=json.loads((root / 'model-upgrade-active.json').read_text())['artifact_sha256']))
                if len(seen) == 3:
                    fcntl.flock(rebuild, fcntl.LOCK_UN)  # the rebuild finishes

            with patch.object(D, 'time', SimpleNamespace(monotonic=time.monotonic, sleep=waiting)):
                result = D.deploy(root, units, systemd=False, wait=60)
            rebuild.close()
            self.assertEqual(seen, [dict(tick_lock_free=True, pointer=old_active)] * 3)
            self.assertNotEqual(result['artifact_sha256'], old_active)

    def test_the_deploy_holds_the_derived_lock_while_it_waits_for_the_tick(self):
        import fcntl
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); old_active, units = production(root)
            tick = (root / 'tick.lock').open('a')
            fcntl.flock(tick, fcntl.LOCK_EX)  # a tick is running
            seen = []

            def waiting(seconds):
                seen.append(free(root, 'derived.lock'))  # no rebuild may start now: it could outlast the tick
                fcntl.flock(tick, fcntl.LOCK_UN)  # the tick finishes

            with patch.object(D, 'time', SimpleNamespace(monotonic=time.monotonic, sleep=waiting)):
                D.deploy(root, units, systemd=False, wait=60)
            tick.close()
            self.assertEqual(seen, [False])

    def test_both_locks_are_held_while_the_activation_is_read_and_written(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); old_active, units = production(root)
            held = {}

            def planning(*args, **kwargs):
                held['read'] = [free(root, name) for name in D.LOCKS]
                return plan(*args, **kwargs)

            def writing(path, value):
                if Path(path) == root / 'model-upgrade-active.json':
                    held['written'] = [free(root, name) for name in D.LOCKS]
                return write(path, value)

            plan, write = D._plan, D.atomic_json
            with patch.object(D, '_plan', side_effect=planning), patch.object(D, 'atomic_json', side_effect=writing):
                result = D.deploy(root, units, systemd=False, wait=1)
            self.assertEqual(held, dict(read=[False, False], written=[False, False]))
            self.assertEqual(json.loads((root / 'model-upgrade-active.json').read_text())['artifact_sha256'], result['artifact_sha256'])
            self.assertEqual([free(root, name) for name in D.LOCKS], [True, True], 'both are released afterwards')

    def test_deploy_waits_for_a_ledger_reader_and_gives_up_after_the_wait(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); old_active, units = production(root)
            reader = sqlite3.connect(root / 'forecast.sqlite')
            reader.execute('BEGIN')
            reader.execute('SELECT * FROM marker').fetchall()  # a dashboard request part-way through a read
            with self.assertRaisesRegex(ValueError, 'write-ahead'):
                D.deploy(root, units, systemd=False, wait=2)
            reader.rollback()
            reader.close()
            self.assertEqual(D.deploy(root, units, systemd=False, wait=2)['ledger_journal_mode'], 'wal')

    def test_a_unit_without_one_working_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, units = production(root)
            (units / 'nba-dashboard.service').write_text('[Service]\nExecStart=x\n')
            with self.assertRaisesRegex(ValueError, 'WorkingDirectory'):
                D.deploy(root, units, systemd=False)
