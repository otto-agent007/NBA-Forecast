"""A team uses about the same number of possessions every night; player rates are held to it."""
import unittest

from nba_forecast.model import (LEAGUE_USAGE_PER_240, REPLACEMENT_USAGE_PER_MINUTE, USAGE_BOUNDS,
                                _complete_team_games, _team_usage_prior, forecast)
from nba_forecast.totals import RESIDUAL_MIN_PROJECTED
from tests.test_home_court import complete_games
from tests.test_model import player, row, snapshot
from tests.test_participation import rows


def team_usage(predictions, team='A'):
    projected = [p for p in predictions if p['team_id'] == team and p['status'] == 'projected']
    return projected, sum(p['participation_probability'] * p['usage_proxy'] for p in projected)


def expected_after_budget(projected, before, prior):
    """The budget's own arithmetic: target from the prior, then the bounds."""
    residual = max(0., 240. - sum(p['participation_probability'] * p['expected_minutes'] for p in projected))
    target = prior - residual * REPLACEMENT_USAGE_PER_MINUTE
    return min(before * USAGE_BOUNDS[1], max(before * USAGE_BOUNDS[0], target))


class UsagePriorTests(unittest.TestCase):
    def test_prior_is_the_team_level_shrunk_to_the_league(self):
        complete = _complete_team_games(complete_games(50, 120, 100))
        prior = _team_usage_prior('H', complete)
        per_game = 5 * (10 + .44 * 2 + 2)  # five players, 48 minutes each
        self.assertEqual(prior['team_games'], 50)
        self.assertAlmostEqual(prior['observed_usage_per_240'], per_game)
        self.assertAlmostEqual(prior['usage_per_240'], per_game)
        self.assertEqual(_team_usage_prior('X', complete)['usage_per_240'], prior['league_usage_per_240'])
        self.assertEqual(_team_usage_prior('X', [])['usage_per_240'], LEAGUE_USAGE_PER_240)


class UsageBudgetTests(unittest.TestCase):
    def history(self, count, fga=12, fta=4, minutes=24):
        data = []
        for i in range(count):
            data += rows(range(10), pid=f'p{i}', minutes=minutes)
        for r in data:
            r.update(fga=fga, fgm=fga // 2, fta=fta, ftm=fta - 1, pts=2 * (fga // 2) + 2 + fta - 1)
        return data

    def test_heavy_rosters_are_scaled_down_to_the_team_level(self):
        snap = snapshot([player(f'p{i}') for i in range(10)])
        result = forecast(snap, self.history(10))
        projected, after = team_usage(result['predictions'])
        audit = projected[0]['components']['team_usage']
        self.assertGreater(audit['before'], audit['target'], 'ten players at twelve shots each overshoot any team')
        self.assertLess(audit['factor'], 1)
        self.assertAlmostEqual(after, expected_after_budget(projected, audit['before'], audit['usage_prior']['usage_per_240']), places=6)
        for p in projected:
            self.assertIn('team_usage_scaled', p['flags'])
            rate = p['components']['opportunities']['per_minute_rates']['fga']
            self.assertAlmostEqual(p['expected_fga'], p['expected_minutes'] * rate * audit['factor'], places=9)
        self.assertEqual(audit['stage'], 'baseline')

    def test_a_manual_adjustment_is_locked_and_counts_as_fixed_usage(self):
        snap = snapshot([player(f'p{i}') for i in range(10)])
        snap['role_adjustments'] = [dict(player_id='p0', game_id='next', observed_at='2026-01-10T11:00:00Z',
                                         source_url='https://example.test/manual', reason='Reviewed', reviewer='owner',
                                         minutes_delta=0, opportunity_multiplier=1.0)]
        result = forecast(snap, self.history(10))
        by_id = {p['player_id']: p for p in result['predictions']}
        audit = by_id['p1']['components']['team_usage']
        self.assertEqual(audit['locked_player_ids'], ['p0'])
        self.assertNotIn('team_usage_scaled', by_id['p0']['flags'])
        self.assertIn('team_usage_scaled', by_id['p1']['flags'])
        rate = by_id['p0']['components']['opportunities']['per_minute_rates']['fga']
        self.assertAlmostEqual(by_id['p0']['expected_fga'], by_id['p0']['expected_minutes'] * rate, places=9)

    def test_light_rosters_rise_only_with_a_plausible_rotation(self):
        light = self.history(10, fga=5, fta=1)
        projected, _ = team_usage(forecast(snapshot([player(f'p{i}') for i in range(10)]), light)['predictions'])
        audit = projected[0]['components']['team_usage']
        self.assertGreater(audit['factor'], 1)
        self.assertLessEqual(audit['factor'], USAGE_BOUNDS[1])
        self.assertTrue(all('team_usage_raised' in p['flags'] for p in projected))
        few = forecast(snapshot([player(f'p{i}') for i in range(5)]), self.history(5, fga=5, fta=1, minutes=40))['predictions']
        projected, _ = team_usage(few)
        self.assertLess(len(projected), RESIDUAL_MIN_PROJECTED)
        self.assertEqual(projected[0]['components']['team_usage']['factor'], 1.)
        self.assertFalse(any('team_usage_raised' in p['flags'] for p in projected))
        # A short bench, every other roster player explicitly out, may rise.
        outs = [dict(player(f'o{i}', status='out'), status_basis='explicit_report_status') for i in range(8)]
        short = forecast(snapshot([player(f'p{i}') for i in range(5)] + outs), self.history(5, fga=5, fta=1, minutes=40))['predictions']
        projected, _ = team_usage(short)
        audit = projected[0]['components']['team_usage']
        self.assertTrue(audit['short_bench'])
        self.assertGreater(audit['factor'], 1)
        self.assertTrue(all('team_usage_raised' in p['flags'] for p in projected))

    def test_the_enhanced_path_applies_the_budget_after_pace(self):
        snap = snapshot([player(f'p{i}') for i in range(10)])
        snap['model_enhancements'] = dict(model_version='regular-season-baseline-0.5.0', model_key='test', known_at=snap['as_of'],
            parameters={'rotation_enabled': False, 'pace_strength': 1., 'defense_strength': 1.},
            environment={'teams': {'A': {'pace_factor': 1.05, 'defense_factor': 1., 'audit': {}}}, 'audit': {'evidence_kind': 'live'}},
            context_model=None, calibration=None)
        result = forecast(snap, self.history(10))
        projected, after = team_usage(result['predictions'])
        audit = projected[0]['components']['team_usage']
        self.assertEqual((audit['stage'], audit['pace_factor']), ('enhanced', 1.05))
        self.assertAlmostEqual(after, min(audit['before'] * USAGE_BOUNDS[1], max(audit['before'] * USAGE_BOUNDS[0], audit['target'])), places=6)
        self.assertAlmostEqual(audit['target'] / 1.05, expected_after_budget(projected, 0, audit['usage_prior']['usage_per_240']) if False else
                               (audit['usage_prior']['usage_per_240'] - audit['residual_minutes'] * REPLACEMENT_USAGE_PER_MINUTE), places=6)


if __name__ == '__main__':
    unittest.main()
