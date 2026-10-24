"""Heuristic widths use the measured default, and calibration pools across model versions."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from nba_forecast.calibration import calibrated_interval, fit_calibration
from nba_forecast.intervals import HEURISTIC_Z80, INTERVAL_FAMILY, LEGACY_METHOD, interval_family_of
from nba_forecast.model import forecast
from nba_forecast.prospective_calibration import collect_samples, fit_prospective
from nba_forecast.store import _SCHEMA
from tests.test_calibration import CUTOFF, MODEL, residual
from tests.test_model import row
from tests.test_prospective_calibration import AS_OF, actual, prediction
from tests.test_store import inputs


class HeuristicWidthTests(unittest.TestCase):
    def test_bounds_use_the_measured_default_and_name_the_family(self):
        snapshot, _, _ = inputs()
        pid = snapshot['roster']['players'][0]['player_id']
        history = [row(i=i, pid=pid, team='T1', opponent_id='T2', season='2025-26',
                       game_time=f'2026-04-0{i+1}T20:00:00Z', known_at=f'2026-04-0{i+1}T23:00:00Z') for i in range(6)]
        p = next(x for x in forecast(snapshot, history)['predictions'] if x['player_id'] == pid)
        self.assertEqual(p['status'], 'projected')
        self.assertAlmostEqual(p['points_upper80'] - p['points_mean'], HEURISTIC_Z80 * p['points_sd'])
        self.assertAlmostEqual(p['points_mean'] - p['points_lower80'], min(p['points_mean'], HEURISTIC_Z80 * p['points_sd']))
        uncertainty = p['components']['uncertainty']
        self.assertEqual((uncertainty['interval_family'], uncertainty['heuristic_z80']), (INTERVAL_FAMILY, HEURISTIC_Z80))
        self.assertEqual(interval_family_of(p), INTERVAL_FAMILY)

    def test_legacy_predictions_belong_to_the_family_by_method(self):
        self.assertEqual(interval_family_of(dict(components=dict(uncertainty=dict(method=LEGACY_METHOD)))), INTERVAL_FAMILY)
        self.assertIsNone(interval_family_of(dict(components=dict(uncertainty=dict(method='other')))))
        self.assertIsNone(interval_family_of(dict(components={})))
        self.assertIsNone(interval_family_of({}))


class PooledCalibrationTests(unittest.TestCase):
    def rows(self):
        return ([residual(i, i + 1, interval_family=INTERVAL_FAMILY) for i in range(5)] +
                [residual(10 + i, i + 1, model_key='older-config', interval_family=INTERVAL_FAMILY) for i in range(5)] +
                [residual(20, 9, model_key='older-config', interval_family='some-other-family'),
                 residual(21, 9, interval_family=None)])

    def test_family_pools_other_model_keys_and_excludes_other_families(self):
        pooled = fit_calibration(self.rows(), model_key=MODEL, as_of=CUTOFF, minimum_samples=3, interval_family=INTERVAL_FAMILY)
        self.assertEqual((pooled['status'], pooled['sample_count']), ('ready', 10))
        self.assertEqual(pooled['exclusions'], {'interval_family_mismatch': 2})
        self.assertEqual(pooled['pooled_model_keys'], ['older-config', MODEL])
        self.assertEqual((pooled['model_key'], pooled['interval_family']), (MODEL, INTERVAL_FAMILY))
        exact = fit_calibration(self.rows(), model_key=MODEL, as_of=CUTOFF, minimum_samples=3)
        self.assertEqual(exact['sample_count'], 6)
        self.assertEqual(exact['exclusions'], {'model_mismatch': 6})

    def test_pooled_artifact_applies_only_to_its_family(self):
        pooled = fit_calibration(self.rows(), model_key=MODEL, as_of=CUTOFF, minimum_samples=3, interval_family=INTERVAL_FAMILY)
        self.assertEqual(calibrated_interval(20, 2, pooled, model_key=MODEL, as_of=CUTOFF, interval_family=INTERVAL_FAMILY)['status'], 'calibrated')
        self.assertEqual(calibrated_interval(20, 2, pooled, model_key=MODEL, as_of=CUTOFF)['status'], 'calibrated')
        refused = calibrated_interval(20, 2, pooled, model_key=MODEL, as_of=CUTOFF, interval_family='some-other-family')
        self.assertEqual((refused['status'], refused['reason']), ('fallback', 'interval_family_mismatch'))
        self.assertEqual(calibrated_interval(20, 2, pooled, model_key='other', as_of=CUTOFF)['reason'], 'model_mismatch')


class PooledProspectiveTests(unittest.TestCase):
    """Live ledger samples pool across the runs' model keys when their sd is built the same way."""

    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db = self.directory / 'forecast.sqlite'
        with sqlite3.connect(self.db) as conn:
            conn.executescript(_SCHEMA)
        (self.directory / 'finals').mkdir()

    def run_row(self, run_id, created, rows, model_key=MODEL):
        forecast = dict(model_key=model_key, model_version='v-' + model_key[:5], season='2025-26',
                        as_of='2026-01-05T18:00:00Z', predictions=rows)
        encoded = json.dumps(forecast)
        with sqlite3.connect(self.db) as conn:
            conn.execute('INSERT INTO runs (run_id,created_at,as_of,mode,scope,snapshot_json,history_json,forecast_json,'
                         'snapshot_sha256,history_sha256,forecast_sha256,inputs_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (run_id, created, forecast['as_of'], 'live', 'league', '{}', '[]', encoded, 'x', 'x',
                          hashlib.sha256(encoded.encode()).hexdigest(), 'x'))

    @staticmethod
    def family_prediction(pid='p', method=LEGACY_METHOD, **changes):
        return prediction(pid, components=dict(uncertainty=dict(method=method)), **changes)

    def test_runs_of_other_model_keys_pool_when_their_sd_is_built_the_same_way(self):
        self.run_row('old', '2026-01-05T18:30:00Z', [self.family_prediction(points_mean=5)], model_key='older-config')
        self.run_row('new', '2026-01-05T19:00:00Z', [self.family_prediction(points_mean=22)])
        self.run_row('p2', '2026-01-05T19:00:00Z', [self.family_prediction('p2', points_mean=10)], model_key='older-config')
        self.run_row('p3', '2026-01-05T19:00:00Z', [self.family_prediction('p3', method='other')], model_key='older-config')
        self.run_row('p4', '2026-01-05T19:00:00Z', [prediction('p4')], model_key='older-config')
        self.run_row('more', '2026-01-05T19:00:00Z', [self.family_prediction('p5'), self.family_prediction('p6')], model_key='older-config')
        (self.directory / 'finals' / 'g1.json').write_text(json.dumps([actual(p) for p in ('p', 'p2', 'p3', 'p4', 'p5', 'p6')]))
        exact = collect_samples(self.directory, MODEL, AS_OF)
        self.assertEqual([s['run_id'] for s in exact['samples']], ['new'])
        pooled = collect_samples(self.directory, MODEL, AS_OF, INTERVAL_FAMILY)
        self.assertEqual(sorted((s['run_id'], s['model_key']) for s in pooled['samples']),
                         [('more', 'older-config'), ('more', 'older-config'), ('new', MODEL), ('p2', 'older-config')])
        self.assertTrue(all(s['interval_family'] == INTERVAL_FAMILY for s in pooled['samples']))
        self.assertEqual(pooled['exclusions']['interval_family_mismatch'], 2)
        artifact = fit_prospective(self.directory, MODEL, AS_OF, minimum_samples=4, minimum_games=1, interval_family=INTERVAL_FAMILY)
        self.assertEqual((artifact['status'], artifact['sample_count'], artifact['model_key']), ('ready', 4, MODEL))
        self.assertEqual(artifact['pooled_model_keys'], sorted([MODEL, 'older-config']))
        # The latest pregame run still wins per player, whatever its key.
        self.assertEqual(next(s for s in pooled['samples'] if s['player_id'] == 'p')['points_mean'], 22)


if __name__ == '__main__':
    unittest.main()
