"""Diagnostic-only regression tests; no forecasts or accuracy gates are fitted here."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import math
from pathlib import Path
import random
import unittest

from nba_forecast import close_teacher as teacher

ROOT = Path(__file__).resolve().parents[1]


def games(n=120):
    rng = random.Random(31)
    return [dict(game_id=str(i), season='2025-26', time=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i // 3),
                 home=f't{i % 6}', away=f't{(i + 1) % 6}', model=rng.uniform(-10, 10), close=rng.uniform(-8, 8),
                 actual=rng.uniform(-20, 20)) for i in range(n)]


class CloseTeacherTests(unittest.TestCase):
    def test_clustered_fit_matches_independent_research_implementation(self):
        spec = importlib.util.spec_from_file_location('research_stats', ROOT / 'scripts/research/stats_lite.py')
        research = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(research)
        rows = games()
        x = [[1., g['model'] - g['close']] for g in rows]
        y = [g['actual'] - g['close'] for g in rows]
        clusters = [g['time'].date() for g in rows]
        b, se = research.ols(x, y, clusters)
        result = teacher.clustered_fit(x, y, clusters, ['constant', 'disagreement'])
        self.assertEqual(result['status'], 'estimated')
        self.assertEqual(result['clusters'], 40)
        for i, row in enumerate(result['coefficients']):
            self.assertAlmostEqual(row['estimate'], b[i], places=12)
            self.assertAlmostEqual(row['se'], se[i], places=12)

    def test_buckets_boundaries_pushes_and_sign(self):
        rows = games(24)
        for i, g in enumerate(rows):
            g.update(close=0., model=[0., 2., -4., 6.][i % 4], actual=[1., 1., -1., 0.][i % 4])
        result = teacher.diagnostics(rows)
        self.assertEqual([b['n'] for b in result['buckets']], [6, 6, 6, 6])
        self.assertEqual(result['overall']['cover_n'], 12)
        self.assertEqual(result['overall']['cover_rate'], 1.)
        self.assertEqual(result['overall']['mean_abs_disagreement'], 3.)
        self.assertEqual(result['buckets'][3]['cover_rate'], None)
        self.assertEqual(result['buckets'][0]['slope']['status'], 'insufficient_games')
        json.dumps(result, allow_nan=False)

    def test_short_constant_and_single_date_samples_are_unavailable(self):
        self.assertEqual(teacher.diagnostics([])['overall']['n'], 0)
        x = [[1., float(i)] for i in range(25)]
        self.assertEqual(teacher.clustered_fit(x, list(range(25)), ['one'] * 25, ['a', 'b'])['status'], 'insufficient_dates')
        self.assertEqual(teacher.clustered_fit([[1., 1.]] * 25, list(range(25)), list(range(25)), ['a', 'b'])['status'], 'singular_design')

    def test_persistent_rows_use_only_earlier_games_and_reset_by_season(self):
        rows = games(90)
        rows.sort(key=lambda g: g['time'])
        first = teacher.standing_rows(rows)
        self.assertTrue(first)
        later = [dict(g) for g in rows]
        later[-1]['model'] += 1000
        second = teacher.standing_rows(later)
        self.assertEqual(first[:-1], second[:-1])
        self.assertEqual(first[-1]['standing_view'], second[-1]['standing_view'])
        self.assertNotEqual(first[-1]['game_day'], second[-1]['game_day'])
        self.assertEqual(teacher.standing_rows([dict(g, season=str(i)) for i, g in enumerate(rows)]), [])
        self.assertEqual(first, teacher.standing_rows(list(reversed(rows))))

    def test_fixed_feature_design_and_prior_date_standings(self):
        rows = games(120)
        teacher.situations(rows)
        f = teacher.features(dict(rows[-1], rest=(1.49, 2.5)))
        self.assertEqual(len(f), 12)
        self.assertEqual(f[1:5], [True, False, False, True])
        self.assertEqual(f[-2:], [False, True])
        self.assertEqual(rows[0]['rank'], rows[1]['rank'])
        result = teacher.diagnostics(rows, historical=True)
        self.assertEqual(result['teacher']['fits']['close_minus_model']['status'], 'singular_design')
        self.assertEqual(result['teacher']['feature_names'], teacher.FEATURE_NAMES)

    def test_study_grouping_preserves_source_season_and_discloses_mismatches(self):
        rows = [dict(game_id='g', projected_margin=3)]
        records = [dict(game_id='g', season='2026-27', game_time='2026-10-21T23:00:00Z',
                        home_team_id='h', away_team_id='a', team_stats={'h': {'pts': 101}, 'a': {'pts': 99}})]
        lines = {'g': dict(home_spread=-1)}
        schedule = teacher.replay_schedule(rows, records, lines, season_by_game={'g': '2025-26'})
        self.assertEqual((schedule[0]['model'], schedule[0]['close'], schedule[0]['actual']), (3, 1, 2))
        self.assertEqual(schedule[0]['season'], '2025-26')
        self.assertEqual(schedule[0]['source_season'], '2026-27')
        self.assertEqual(records[0]['season'], '2026-27')
        result = teacher.replay_diagnostics(rows, records, lines, season_by_game={'g': '2025-26'})
        self.assertEqual(result['season_group_mismatches'], [dict(game_id='g', source_season='2026-27', study_group='2025-26')])

    def test_live_providers_are_not_pooled_as_independent_games(self):
        rows = [dict(game_id='g', start_time='2026-11-01T00:00:00Z', provider_id=p, provider='book'+p,
                     home_team_id='h', away_team_id='a', model_margin=3, market_margin=close, actual_margin=5)
                for p, close in [('1', 2), ('2', 4)]]
        result = teacher.live_diagnostics(rows, '2026-27')
        self.assertEqual(set(result), {'1', '2'})
        self.assertEqual(result['1']['diagnostics']['overall']['n'], 1)
        self.assertEqual(result['2']['diagnostics']['overall']['close_mae'], 1)

    def test_nonfinite_rows_are_excluded_and_counted(self):
        rows = games(30)
        rows[0]['close'] = math.nan
        rows[1]['actual'] = None
        result = teacher.diagnostics(rows)
        self.assertEqual(result['excluded_rows'], 2)
        self.assertEqual(result['overall']['n'], 28)
        json.dumps(result, allow_nan=False)


@unittest.skipUnless((ROOT / 'datasets/backtest-2026-11-06-rating-weight/rows.json.gz').exists(),
                     'requires committed November 6 replay archive (available in CI checkout)')
class November6ReferenceTests(unittest.TestCase):
    def assert_nested_close(self, current, saved):
        # Python's summation improvements can alter the last bits across 3.11–3.13.
        if isinstance(current, float):
            self.assertAlmostEqual(current, saved, delta=1e-10)
        elif isinstance(current, dict):
            self.assertEqual(set(current), set(saved))
            for key in current:
                self.assert_nested_close(current[key], saved[key])
        elif isinstance(current, list):
            self.assertEqual(len(current), len(saved))
            for a, b in zip(current, saved):
                self.assert_nested_close(a, b)
        else:
            self.assertEqual(current, saved)

    def test_published_historical_results_and_research_parity(self):
        report = teacher.reference_report(ROOT / 'datasets/backtest-2026-11-06-rating-weight', ROOT / 'datasets')
        snapshot = teacher.read(ROOT / 'datasets/backtest-2026-11-06-rating-weight/close-teacher.json')
        self.assert_nested_close(report, snapshot)
        d = report['diagnostics']
        overall = d['overall']
        self.assertEqual(overall['n'], 2604)
        self.assertEqual(d['persistent']['team_seasons'], 90)
        self.assertEqual(len(d['season_group_mismatches']), 14)
        self.assertAlmostEqual(overall['model_mae'], 11.25, delta=.005)
        self.assertAlmostEqual(overall['close_mae'], 10.90, delta=.005)
        slope = overall['slope']['coefficients'][1]
        self.assertAlmostEqual(slope['estimate'], .121, delta=.0005)
        self.assertAlmostEqual(slope['se'], .080, delta=.0005)
        coeffs = d['teacher']['fits']['close_minus_model']['coefficients']
        for index, estimate, se in [(1, -1.17, .18), (2, .87, .17), (6, -1.56, .27), (7, .96, .28)]:
            self.assertAlmostEqual(coeffs[index]['estimate'], estimate, delta=.005)
            self.assertAlmostEqual(coeffs[index]['se'], se, delta=.005)
        self.assertEqual(d['persistent']['n'], 2137)
        for row, estimate, se in zip(d['persistent']['fit']['coefficients'][1:], [.069, .030], [.121, .102]):
            self.assertAlmostEqual(row['estimate'], estimate, delta=.0005)
            self.assertAlmostEqual(row['se'], se, delta=.0005)
        self.assertAlmostEqual(d['persistent']['team_season_mean_sd'], 1.45, delta=.005)
        self.assertAlmostEqual(d['persistent']['standing_variance_share'], .37, delta=.005)
        self.assertAlmostEqual(d['buckets'][-1]['cover_rate'], .485, delta=.0005)
        # Independent original loader and OLS, not only rounded numbers from the prose.
        spec = importlib.util.spec_from_file_location('study', ROOT / 'scripts/research/close_teacher.py')
        study = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(study)
        rows = [g for g in study.load(ROOT / 'datasets', ROOT / 'datasets/backtest-2026-11-06-rating-weight') if g['model'] is not None]
        for key, values in [('close_minus_model', [g['close'] - g['model'] for g in rows]),
                            ('actual_minus_model', [g['actual'] - g['model'] for g in rows]),
                            ('actual_minus_close', [g['actual'] - g['close'] for g in rows])]:
            b, se = study.ols([study.features(g) for g in rows], values, [g['time'].date() for g in rows])
            for i, c in enumerate(d['teacher']['fits'][key]['coefficients']):
                self.assertAlmostEqual(c['estimate'], b[i], places=9)
                self.assertAlmostEqual(c['se'], se[i], places=9)
        print('November 6 close-teacher parity verified:', overall['n'], 'games')
