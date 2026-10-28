"""Team totals reconciled to the margin forecast's location."""
from copy import deepcopy
import unittest

from nba_forecast import margins
from nba_forecast.boxscore_model import VERSION as BOXSCORE_VERSION, expected_makes, validate_boxscore
from nba_forecast.scoreline import METHOD, player_projections, reconcile
from nba_forecast.totals import game_totals
from tests.test_margins import forecast_inputs


def sides(game, predictions):
    return {t['side']: t['points_mean'] for t in game_totals(game, predictions)['teams']}


def with_shooting(p, attempts=(14., 6., 5.)):
    """A full-box row whose makes reconcile to its points over fixed attempts."""
    percentages = dict(two_point=.55, three_point=.36, free_throw=.8)
    made = expected_makes(list(attempts), [percentages[k] for k in ('two_point', 'three_point', 'free_throw')], p['points_mean'])
    p.update(boxscore_model_version=BOXSCORE_VERSION, expected_fga=attempts[0] + attempts[1],
             expected_fg3a=attempts[1], expected_fta=attempts[2],
             expected_fgm=made[0] + made[1], expected_fg3m=made[1], expected_ftm=made[2],
             expected_oreb=None, expected_dreb=None, expected_reb=None, expected_pf=2., expected_plus_minus=0.,
             components=dict(shooting={k: dict(percentage=v) for k, v in percentages.items()}))
    for pct, (m, a) in (('fg_pct', ('fgm', 'fga')), ('fg3_pct', ('fg3m', 'fg3a')), ('ft_pct', ('ftm', 'fta'))):
        p['expected_' + pct] = p['expected_' + m] / p['expected_' + a] if p['expected_' + a] else None
    return p


class ScorelineTests(unittest.TestCase):
    def test_totals_differ_by_the_location_and_the_game_total_is_unchanged(self):
        snapshot, result = forecast_inputs(110., 105.)
        game = snapshot['games'][0]
        location = margins.build_margin_forecasts(snapshot, result)[0]['mean_margin']
        self.assertEqual(location, 2.5)  # half the player sum (5), half a zero rating
        audit = reconcile(result['predictions'], game, location)
        totals = sides(game, result['predictions'])
        self.assertAlmostEqual(totals['home'] - totals['away'], 2.5)
        self.assertAlmostEqual(totals['home'] + totals['away'], 215.)
        self.assertEqual((audit['method'], audit['team_shift']), (METHOD, -1.25))
        self.assertAlmostEqual(audit['factors']['home'], 108.75 / 110.)
        player = result['predictions'][0]
        self.assertEqual(player['components']['scoreline']['points_mean_before'], 22.)
        self.assertAlmostEqual(player['points_mean'], 22. * 108.75 / 110.)
        self.assertEqual(sides(game, player_projections(result['predictions'])), {'away': 105., 'home': 110.})

    def test_a_reconciled_forecast_reproduces_its_own_margin(self):
        snapshot, result = forecast_inputs(110., 105.)
        game = snapshot['games'][0]
        result['game_totals'] = [game_totals(game, result['predictions'])]
        before = margins.build_margin_forecasts(snapshot, result)[0]
        reconcile(result['predictions'], game, before['mean_margin'])
        result['game_totals'] = [game_totals(game, result['predictions'])]
        self.assertEqual(margins.build_margin_forecasts(snapshot, result)[0], before)

    def test_makes_are_reallocated_over_the_same_attempts(self):
        snapshot, result = forecast_inputs(110., 105.)
        for p in result['predictions']:
            with_shooting(p)
        reconcile(result['predictions'], snapshot['games'][0], 2.5)
        for p in result['predictions']:
            validate_boxscore(p)
            self.assertEqual((p['expected_fga'], p['expected_fg3a'], p['expected_fta']), (20., 6., 5.))
        self.assertLess(result['predictions'][0]['expected_fgm'] * 2 + result['predictions'][0]['expected_fg3m']
                        + result['predictions'][0]['expected_ftm'], 22.)

    def test_scaled_intervals_keep_their_order(self):
        snapshot, result = forecast_inputs(110., 105.)
        for p in result['predictions']:
            p.update(points_sd=6., points_lower80=p['points_mean'] - 7., points_upper80=p['points_mean'] + 8.)
        reconcile(result['predictions'], snapshot['games'][0], 2.5)
        for p in result['predictions']:
            self.assertLess(p['points_lower80'], p['points_mean'])
            self.assertLess(p['points_mean'], p['points_upper80'])
            factor = p['components']['scoreline']['factor']
            self.assertAlmostEqual(p['points_sd'], 6. * factor)

    def test_unsupported_games_are_left_unchanged(self):
        snapshot, result = forecast_inputs(110., 105.)
        game = snapshot['games'][0]
        original = deepcopy(result)
        self.assertIsNone(reconcile(result['predictions'], game, None))
        self.assertIsNone(reconcile(result['predictions'], game, float('nan')))
        self.assertIsNone(reconcile(result['predictions'], game, 300.))  # the away side cannot give up 97.5 of 105
        partial = deepcopy(result)
        partial['predictions'].append(dict(game_id='next', team_id='H', player_id='Hx', status='needs_review',
                                           flags=['team_change'], points_mean=None, expected_minutes=None))
        self.assertIsNone(reconcile(partial['predictions'], game, 2.5))
        self.assertEqual(result, original)
        impossible = deepcopy(result)
        for p in impossible['predictions']:
            with_shooting(p)
        impossible['predictions'][0]['points_mean'] = 4.
        with_shooting(impossible['predictions'][0], attempts=(2., 0., 0.))  # already makes every shot
        frozen = deepcopy(impossible)
        self.assertIsNone(reconcile(impossible['predictions'], game, 20.))  # home must score more
        self.assertEqual(impossible, frozen)

    def test_forecast_reconciles_each_estimated_margin(self):
        from unittest import mock
        from nba_forecast.model import forecast
        from tests.test_forecast_refinements import RefinedVersionTests
        from tests.test_model import row
        s = RefinedVersionTests.enhanced(None, 'regular-season-baseline-0.6.0')
        def estimated(snapshot, result):
            return [dict(status='estimated', game_id=g['game_id'], mean_margin=1.5) for g in snapshot['games']]
        with mock.patch('nba_forecast.margins.build_margin_forecasts', estimated), \
                mock.patch('nba_forecast.scoreline.reconcile') as spy:
            result = forecast(s, [row()])
        spy.assert_called_once_with(result['predictions'], s['games'][0], 1.5, lineups=None)
        self.assertEqual(result['game_totals'], [game_totals(s['games'][0], result['predictions'])])


if __name__ == '__main__':
    unittest.main()
