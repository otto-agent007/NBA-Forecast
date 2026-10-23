"""Early-season role priors must not turn prior absences into healthy minutes."""
from copy import deepcopy
import unittest
from nba_forecast.model import forecast
from nba_forecast.profiles import build_profiles
from tests.test_model import row,current,snapshot,player


def dnp(i,**kw):
    return row(i,minutes=0,fgm=0,fga=0,fg3m=0,fg3a=0,ftm=0,fta=0,tov=0,status='dnp',**kw)


class RoleMinutesTests(unittest.TestCase):
    def test_prior_season_dnps_do_not_dilute_minutes_for_available_player(self):
        base=[row(i,minutes=31) for i in range(12)]+[current(minutes=32)]
        with_absences=base+[dnp(20+i) for i in range(12)]
        before=forecast(snapshot(),base)['predictions'][0]
        after=forecast(snapshot(),with_absences)['predictions'][0]
        self.assertAlmostEqual(before['expected_minutes'],after['expected_minutes'])
        self.assertGreater(after['expected_minutes'],30)
        self.assertEqual(after['components']['opportunities']['prior_dnp_excluded'],12)
        self.assertEqual(after['components']['history']['dnp_games'],12)

    def test_new_team_minutes_use_observed_new_role_and_preserve_shooting_prior(self):
        old=[row(i,team='OLD',minutes=12) for i in range(12)]
        observed=current(minutes=34,starter=True)
        result=forecast(snapshot(),old+[observed])['predictions'][0]
        self.assertAlmostEqual(result['expected_minutes'],34)
        self.assertEqual(result['components']['opportunities']['role_prior_effective_games'],0)
        self.assertEqual(result['components']['opportunities']['role_current_team_game_ids'],[observed['game_id']])
        self.assertGreater(result['components']['shooting']['two_point']['prior_attempts'],0)
        self.assertIn('team_change',result['flags'])

    def test_new_team_dnp_without_observed_role_is_withheld_and_not_old_team_minutes(self):
        old=[row(i,team='OLD',minutes=25) for i in range(10)]
        not_played=current(minutes=0,fgm=0,fga=0,fg3m=0,fg3a=0,ftm=0,fta=0,tov=0,status='dnp')
        p=forecast(snapshot(),old+[not_played])['predictions'][0]
        self.assertEqual(p['status'],'needs_review')
        self.assertIsNone(p['expected_minutes'])
        self.assertIn('new_team_role_unobserved',p['flags'])

    def test_current_team_role_profile_matches_forecast_and_future_games_are_excluded(self):
        s=snapshot();data=[row(i,team='OLD',minutes=12) for i in range(8)]+[current(minutes=33)]
        profile=build_profiles(s,data)['players'][0]
        p=forecast(s,data)['predictions'][0]
        self.assertAlmostEqual(profile['expected_minutes_baseline'],p['expected_minutes'])
        self.assertAlmostEqual(p['expected_minutes'],33)
        future=deepcopy(data[-1]);future.update(game_id='future',minutes=48,known_at='2099-01-01T00:00:00Z')
        self.assertAlmostEqual(forecast(s,data+[future])['predictions'][0]['expected_minutes'],p['expected_minutes'])

    def test_repeated_current_season_dnps_still_raise_role_review_and_reduce_minutes(self):
        previous=[row(i,minutes=30) for i in range(12)]
        inactive=current(minutes=0,fgm=0,fga=0,fg3m=0,fg3a=0,ftm=0,fta=0,tov=0,status='dnp')
        p=forecast(snapshot(),previous+[inactive])['predictions'][0]
        self.assertLess(p['expected_minutes'],30)
        self.assertIn('recent_dnp_role_review',p['flags'])
