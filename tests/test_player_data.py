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
        with self.assertRaises(ValueError):
            build_player_pages([],rows+rows[:1],'2026-27','2026-10-22T00:00:00Z')

    def test_every_played_player_and_zero_attempt_coverage(self):
        base=dict(player_id='99',name='Former Roster Player',team_id='A',game_id='g',season='2026-27',season_type='Regular Season',
            game_time='2026-10-20T00:00:00Z',known_at='2026-10-21T00:00:00Z',status='played',minutes=1.5,minutes_precision='seconds',
            fga=0,fgm=0,fg3a=0,fg3m=0,pf=1,plus_minus=-3,oreb=0,dreb=1,reb=1)
        game=dict(game_id='g',season='2026-27',game_time=base['game_time'],known_at=base['known_at'],shots=[],
            coverage=dict(reconciled=True,players=[dict(player_id='99',reconciled=True,attempts=0)]))
        result=build_player_pages([], [base], '2026-27', '2026-10-22T00:00:00Z',[game])['players']['99']
        self.assertEqual(result['roster_membership'],'historical_appearance')
        self.assertEqual(result['current_season']['averages']['plus_minus'],-3)
        self.assertEqual(result['current_season']['averages']['pf'],1)
        self.assertEqual(result['shot_chart']['coverage']['zero_attempt_games'],1)
        self.assertTrue(result['shot_chart']['coverage']['complete'])
        self.assertIsNone(result['shot_chart']['missing_reason'])
        self.assertEqual(result['recent_games'][0]['minutes_precision'],'seconds')
        game['coverage']['players']=[]
        incomplete=build_player_pages([], [base], '2026-27', '2026-10-22T00:00:00Z',[game])['players']['99']
        self.assertFalse(incomplete['shot_chart']['coverage']['complete'])

    def test_stale_chart_does_not_cover_corrected_final(self):
        base=dict(player_id='99',team_id='A',game_id='g',season='2026-27',season_type='Regular Season',game_time='2026-10-20T00:00:00Z',
            known_at='2026-10-21T00:00:00Z',status='played',minutes=1,fgm=0,fga=1,fg3m=0,fg3a=0)
        game=dict(game_id='g',season='2026-27',known_at=base['known_at'],shots=[],coverage=dict(reconciled=True,players=[dict(player_id='99',reconciled=True)]))
        p=build_player_pages([], [base], '2026-27', '2026-10-22T00:00:00Z',[game])['players']['99']
        self.assertEqual(p['shot_chart']['coverage']['games'],0)
        self.assertEqual(p['shot_chart']['coverage']['missing_games'],1)
