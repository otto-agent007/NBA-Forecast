"""New forecast policy must preserve source actuals and older model behavior."""
from copy import deepcopy
import unittest

from nba_forecast.model import _opportunities, forecast
from nba_forecast.rotation import rotation_role
from tests.test_model import current, player, row, snapshot


class InjuryRoleIntegrationTests(unittest.TestCase):
    def test_injury_exit_does_not_lower_observed_minute_capacity(self):
        from nba_forecast.rotation_allocation import _recipient
        rows=[current(i,minutes=m,fga=max(5,m//2),fgm=4) for i,m in enumerate([30]*7+[34,38,10])]
        item=dict(minutes=30,rates=dict(fga=.5,fg3a=.1,fta=.1,tov=.05))
        before=_recipient(player(),item,rows,'current_team')
        after=_recipient(player(),item,rows,'current_team',excluded_minute_game_ids={rows[-1]['game_id']})
        self.assertEqual(before['observed_minute_cap'],34)
        self.assertEqual(after['observed_minute_cap'],38)
        self.assertEqual(after['rate_caps'],before['rate_caps'])
        self.assertEqual(after['attempt_caps'],before['attempt_caps'])
        self.assertEqual(after['history_game_ids'],before['history_game_ids'])

    def test_shortened_game_affects_production_but_not_normal_minutes(self):
        history = [current(0, minutes=34, fga=20), current(1, minutes=17, fga=8)]
        original = deepcopy(history)
        ordinary = _opportunities([], history, 'A')
        minutes, rates, variance, audit = _opportunities(
            [], history, 'A', excluded_role_game_ids={history[1]['game_id']})
        self.assertEqual(minutes, 34)
        self.assertEqual(rates, ordinary[1])
        self.assertEqual(history, original)
        self.assertEqual(audit['injury_shortened_role_game_ids'], [history[1]['game_id']])
        self.assertTrue(audit['normal_role_supported'])
        self.assertGreater(variance, 0)

    def test_shortened_only_new_team_appearance_does_not_restore_old_team_minutes(self):
        old = [row(i, team='OLD', minutes=38) for i in range(5)]
        recent = current(minutes=9)
        minutes, rates, _, audit = _opportunities(
            old, [recent], 'A', excluded_role_game_ids={recent['game_id']})
        self.assertEqual(minutes, 0)
        self.assertFalse(audit['normal_role_supported'])
        self.assertGreater(rates['fga'], 0)

    def test_rotation_retains_starter_fact_but_filters_injury_minutes(self):
        rows = [current(0, minutes=34, starter=True), current(1, minutes=9, starter=True)]
        role = rotation_role(player(), rows, excluded_game_ids={rows[1]['game_id']})
        self.assertEqual(role['recent_minutes'], 34)
        self.assertTrue(role['last_game_started'])
        self.assertEqual(role['starts'], 2)
        self.assertEqual(role['injury_shortened_game_ids'], [rows[1]['game_id']])


class RefinedVersionTests(unittest.TestCase):
    def enhanced(self, version):
        s = snapshot()
        s['model_enhancements'] = dict(
            model_version=version, model_key='refinement-test', known_at=s['as_of'],
            parameters={'rotation_enabled': False}, context_model=None, calibration=None,
            environment={'teams': {}, 'audit': {'evidence_kind': 'live'}})
        return s

    def test_new_version_is_distinct_and_legacy_remains_replayable(self):
        old = forecast(self.enhanced('regular-season-baseline-0.5.0'), [row()])
        new = forecast(self.enhanced('regular-season-baseline-0.6.0'), [row()])
        self.assertEqual(old['model_version'], 'regular-season-baseline-0.5.0')
        self.assertEqual(new['model_version'], 'regular-season-baseline-0.6.0')
        self.assertEqual(new['predictions'][0]['points_mean'], old['predictions'][0]['points_mean'])
        self.assertNotIn('margin_forecasts', old)
        self.assertEqual(new['margin_forecasts'][0]['status'], 'unavailable')
