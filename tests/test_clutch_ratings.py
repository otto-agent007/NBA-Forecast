import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import shutil
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'datasets/clutch-2026-10-22'


class ClutchRatingsTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('nba_forecast.clutch_ratings'),
                             'The reproducible clutch rating module must exist')
        from nba_forecast import clutch_ratings
        return clutch_ratings

    def test_archive_replays_and_keeps_missing_seasons_missing(self):
        m = self.module()
        report = m.build_report(ARCHIVE)
        self.assertEqual(sum(len(v['rows']) for v in report['periods'][:3]), 1459)
        brunson = next(p for p in report['players'] if p['player_id'] == 1628973)
        self.assertEqual([r['raw']['pts_gp'] for r in brunson['seasons']], [106/30, 156/28, 116/32])
        hali = next(p for p in report['players'] if p['player_id'] == 1630169)
        self.assertIsNone(hali['seasons'][2])
        self.assertEqual(hali['available_seasons'], 2)
        self.assertFalse(report['forecast_eligible'])
        json.dumps(report, allow_nan=False)

    def test_beta_shrinkage_and_uncertainty_depend_on_shot_sample(self):
        m = self.module()
        small = m.shooting(3, 3, .35, 30, 1)
        large = m.shooting(300, 300, .35, 30, 1)
        self.assertLess(small['adjusted'], .5)
        self.assertGreater(large['adjusted'], small['adjusted'])
        self.assertGreater(small['interval90'][1]-small['interval90'][0], large['interval90'][1]-large['interval90'][0])
        self.assertEqual(small['prior_share'], 30/33)
        self.assertIsNone(m.shooting(0, 0, .35, 30, 1)['raw'])

    def test_uniform_beta_quantiles_and_seeded_draws(self):
        m = self.module()
        a = m.shooting(0, 0, .5, 2, 27)
        b = m.shooting(0, 0, .5, 2, 27)
        self.assertEqual(a, b)
        self.assertEqual(a['adjusted'], .5)
        self.assertAlmostEqual(a['interval90'][0], .05, delta=.03)
        self.assertAlmostEqual(a['interval90'][1], .95, delta=.03)

    def test_identical_samples_tie_and_sensitivity_moves_toward_prior(self):
        m = self.module()
        r = dict(PLAYER_ID=1, PLAYER_NAME='A', GP=20, MIN=50, FGA=30, FGM=30, FG3A=30, FG3M=30, FTA=0, FTM=0, PTS=90, AST=5, TOV=2)
        other = dict(r, PLAYER_ID=2, PLAYER_NAME='B')
        result = m.rate_period('test', [r, other])['rows']
        self.assertEqual(result[0]['ratings'], result[1]['ratings'])
        self.assertEqual(result[0]['ratings']['efg'], 50)
        self.assertGreater(result[0]['sensitivity']['0.5']['three'], result[0]['sensitivity']['2']['three'])
        self.assertGreater(result[0]['raw']['efg'], 1)

    def test_decomposition_is_exact_and_order_symmetric(self):
        m = self.module()
        a = dict(GP=20, MIN=50, FGA=40, FGM=20, FG3A=10, FG3M=3, FTA=10, FTM=8, PTS=51)
        b = dict(GP=25, MIN=100, FGA=60, FGM=35, FG3A=20, FG3M=10, FTA=20, FTM=16, PTS=96)
        d = m.decompose(a, b)
        self.assertAlmostEqual(sum(d['effects'].values()), 96/25-51/20)
        reverse = m.decompose(b, a)
        for key in d['effects']:
            self.assertAlmostEqual(d['effects'][key], -reverse['effects'][key])
        self.assertTrue(all(abs(v)<1e-12 for v in m.decompose(a, a)['effects'].values()))
        self.assertIsNone(m.decompose(dict(a, FG3A=0, FG3M=0), b))

    def test_tiny_perfect_sample_not_ranked_and_zero_turnovers_not_infinite(self):
        m = self.module()
        row = dict(PLAYER_ID=1, PLAYER_NAME='Tiny', GP=1, MIN=1, FGA=3, FGM=3, FG3A=3, FG3M=3, FTA=0, FTM=0, PTS=9, AST=1, TOV=0)
        other = dict(row, PLAYER_ID=2, PLAYER_NAME='Qualified', GP=30, MIN=100, FGA=100, FGM=50, FG3A=30, FG3M=10, FTA=30, FTM=24, PTS=134, AST=20, TOV=10)
        period = m.rate_period('test', [row, other])
        tiny = period['rows'][0]
        self.assertFalse(tiny['qualified'])
        self.assertIsNone(tiny['ratings']['efg'])
        self.assertIsNone(tiny['raw']['ast_tov'])
        self.assertGreater(tiny['adjusted']['tov_min'], 0)
        self.assertEqual(period['rows'][1]['shooting']['three']['prior_mean'], 3.5/4)
        json.dumps(period, allow_nan=False)

    def test_pooled_rates_use_totals_not_average_of_percentages(self):
        m = self.module()
        r = dict(PLAYER_ID=1, PLAYER_NAME='A', GP=20, MIN=50, FGA=10, FGM=2, FG3A=5, FG3M=1, FTA=5, FTM=4, PTS=9, AST=5, TOV=2)
        s = dict(r, GP=40, MIN=150, FGA=90, FGM=60, FG3A=30, FG3M=20, FTA=20, FTM=18, PTS=158)
        pooled = m.pool_rows([[r], [s]])[0]
        self.assertEqual(pooled['FGA'], 100)
        self.assertEqual(pooled['FGM'], 62)
        self.assertEqual(pooled['MIN'], 200)
        result = m.rate_period('pooled', [pooled])['rows'][0]
        self.assertAlmostEqual(result['raw']['fg'], .62)

    def test_hash_mismatch_and_invalid_rows_fail_closed(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            shutil.copytree(ARCHIVE/'raw', target/'raw')
            for p in ARCHIVE.glob('*.gz'):
                (target/p.name).write_bytes(p.read_bytes())
            (target/'manifest.json').write_bytes((ARCHIVE/'manifest.json').read_bytes())
            season = target/'2023-24.json.gz'
            season.write_bytes(season.read_bytes()+b'x')
            with self.assertRaisesRegex(ValueError, 'hash'):
                m.load_archive(target)
            payload = json.loads(gzip.decompress((ARCHIVE/season.name).read_bytes()))
            payload['rows'][0]['FG3A'] = payload['rows'][0]['FGA']
            payload['rows'][0]['FG3M'] = 0
            # A forged normalized file must also fail source/row checks, even if rehashed.
            raw = json.dumps(payload).encode(); compressed = gzip.compress(raw)
            season.write_bytes(compressed)
            manifest = json.loads((target/'manifest.json').read_text())
            manifest[0].update(compressed_sha256=hashlib.sha256(compressed).hexdigest(), uncompressed_sha256=hashlib.sha256(raw).hexdigest())
            (target/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                m.load_archive(target)

    def test_repeatability_and_archive_is_read_only(self):
        m = self.module()
        before = {p.name: p.read_bytes() for p in ARCHIVE.glob('*') if p.is_file()}
        a = m.build_report(ARCHIVE)
        b = m.build_report(ARCHIVE)
        self.assertEqual(a, b)
        self.assertEqual(before, {p.name:p.read_bytes() for p in ARCHIVE.glob('*') if p.is_file()})

class ClutchEndpointTests(unittest.TestCase):
    def test_missing_report_is_explicit_and_reads_do_not_write(self):
        from nba_forecast import dashboard
        self.assertTrue(hasattr(dashboard, 'clutch_payload'), 'Read-only clutch endpoint required')
        with tempfile.TemporaryDirectory() as d:
            self.assertIn('missing_reason', dashboard.clutch_payload(d))
            self.assertEqual(list(Path(d).iterdir()), [])
            p = Path(d)/'clutch.json'
            p.write_text(json.dumps({'schema_version':1,'forecast_eligible':True}))
            with self.assertRaises(ValueError):
                dashboard.clutch_payload(d)


if __name__ == '__main__':
    unittest.main()
