import unittest
from nba_forecast.player_data import build_player_pages

class PlayerDataTests(unittest.TestCase):
    def test_cutoff_dnp_and_weighted_percent(self):
        base={'player_id':'1','season':'2026-27','season_type':'Regular Season','game_time':'2026-10-20T00:00:00Z','known_at':'2026-10-21T00:00:00Z','status':'played'}
        rows=[dict(base,game_id='a',pts=10,fgm=1,fga=2),dict(base,game_id='b',pts=20,fgm=9,fga=10),dict(base,game_id='c',status='dnp',pts=0),dict(base,game_id='d',pts=100,known_at='2026-10-23T00:00:00Z')]
        p=build_player_pages([{'player_id':'1'}],rows,'2026-27','2026-10-22T00:00:00Z')['players']['1']
        self.assertEqual(p['current_season']['gp'],2)
        self.assertEqual(p['current_season']['averages']['pts'],15)
        self.assertIsNone(p['current_season']['averages']['reb'])
        self.assertAlmostEqual(p['current_season']['percentages']['fg_pct'],1000/12)
        self.assertEqual(p['current_season']['dnp'],1)
        self.assertIsNone(p['recent_games'][0]['pts'])
        self.assertEqual(p['prior_season']['gp'],0)
        with self.assertRaises(ValueError): build_player_pages([],rows+rows[:1],'2026-27','2026-10-22T00:00:00Z')
