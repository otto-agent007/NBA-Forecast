from copy import deepcopy
import unittest

from nba_forecast.refinement_upgrade import build_artifact
from nba_forecast.model_versions import REFINED_VERSION


class RefinementUpgradeTests(unittest.TestCase):
    def test_new_configuration_drops_old_calibration_and_accuracy_claims(self):
        base = dict(model_version='regular-season-baseline-0.5.0', model_key='old',
                    known_at='2026-10-24T01:00:00Z', parameters={'rotation_enabled': True},
                    team_games=[], calibration={'model_key': 'old', 'status': 'ready'},
                    validation_report={'holdout': {'candidate': {'mae': 3}}}, context_model=None)
        original = deepcopy(base)
        new = build_artifact(base, 'a' * 64, '2026-10-24T08:00:00Z')
        self.assertEqual(base, original)
        self.assertEqual(new['model_version'], REFINED_VERSION)
        self.assertNotEqual(new['model_key'], 'old')
        self.assertIsNone(new['calibration'])
        self.assertNotIn('holdout', new['validation_report'])
        self.assertEqual(new['parent_artifact_sha256'], 'a' * 64)
        self.assertEqual(len(new['runner_package_sha256']), 64)

    def test_future_source_or_repeat_upgrade_is_rejected(self):
        base = dict(model_version=REFINED_VERSION, known_at='2026-10-24T01:00:00Z')
        with self.assertRaises(ValueError):
            build_artifact(base, 'a' * 64, '2026-10-24T08:00:00Z')
        base['model_version'] = 'regular-season-baseline-0.5.0'
        with self.assertRaises(ValueError):
            build_artifact(base, 'a' * 64, '2026-10-23T08:00:00Z')
