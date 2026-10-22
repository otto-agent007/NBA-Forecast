import unittest
from nba_forecast.postgame import classify_game, player_feedback

class PostgameTests(unittest.TestCase):
    def test_early_runaway_requires_sustained_lead(self):
        points=[dict(elapsed=0,margin=0),dict(elapsed=300,margin=23),dict(elapsed=1440,margin=18),dict(elapsed=2880,margin=25)]
        self.assertEqual(classify_game(points)['pattern'],'Early runaway')
        points.insert(2,dict(elapsed=1000,margin=3))
        self.assertNotEqual(classify_game(points)['pattern'],'Early runaway')
    def test_comeback_not_blowout_from_absolute_margin(self):
        points=[dict(elapsed=0,margin=0),dict(elapsed=500,margin=25),dict(elapsed=1800,margin=-2),dict(elapsed=2880,margin=-6)]
        self.assertEqual(classify_game(points)['pattern'],'Comeback win')
    def test_late_blowout_and_no_automatic_early_garbage_time(self):
        points=[dict(elapsed=0,margin=0),dict(elapsed=1440,margin=2),dict(elapsed=2300,margin=22),dict(elapsed=2880,margin=24)]
        c=classify_game(points)
        self.assertEqual(c['pattern'],'Late blowout');self.assertEqual(c['low_leverage_start'],2300)
    def test_exact_minutes_volume_conversion_decomposition(self):
        p=dict(status='projected',expected_minutes=36,expected_fga=24,points_mean=30)
        a=dict(minutes=24,fga=12,pts=18,status='played')
        f=player_feedback(p,a)
        self.assertAlmostEqual(f['points_error'],-12)
        self.assertAlmostEqual(sum(f[k] for k in ('minutes_effect','shot_volume_effect','conversion_and_ft_effect')),-12)
        self.assertAlmostEqual(f['minutes_effect'],-10)
        self.assertAlmostEqual(f['shot_volume_effect'],-5)
        self.assertAlmostEqual(f['conversion_and_ft_effect'],3)
    def test_abstentions_and_zero_attempts_not_divided(self):
        self.assertIsNone(player_feedback({'status':'review'},dict(minutes=0,fga=0,pts=0))['points_error'])
        f=player_feedback(dict(status='projected',expected_minutes=30,expected_fga=0,points_mean=2),dict(minutes=0,fga=0,pts=0))
        self.assertIsNone(f['shot_volume_effect'])

class VerifiedPostgameTests(unittest.TestCase):
    def setUp(self):
        import json
        from pathlib import Path
        self.fixture=json.loads((Path(__file__).parent/'fixtures/postgame-lakers.json').read_text())
    def analyze(self,pbp=None):
        from nba_forecast.postgame import analyze_game
        f=self.fixture
        return analyze_game(pbp or f['pbp'],f['box'],'2026-10-22T18:00:00Z')
    def test_verified_stars_exit_and_all_players_retained(self):
        report=self.analyze();self.assertEqual(report['pattern'],'Late blowout')
        self.assertEqual(report['home']['score'],127)
        players={p['name']:p for p in report['players']}
        self.assertEqual(players['Luka Dončić']['last_recorded_exit']['elapsed'],2160)
        self.assertEqual(players['Stephen Curry']['last_recorded_exit']['elapsed'],1891)
        for name in ('Luka Dončić','Stephen Curry'):
            self.assertIn('possible blowout rest',players[name]['takeaways'][0])
            self.assertIsNone(players[name]['feedback']['points_error'])
        self.assertEqual(len(report['players']),sum(len(self.fixture['box']['game'][s]['players']) for s in ('homeTeam','awayTeam')))
    def test_incomplete_period_score_duplicate_and_identity_rejected(self):
        import copy
        for kind in ('period','score','duplicate','identity'):
            p=copy.deepcopy(self.fixture['pbp']);a=p['game']['actions']
            if kind=='period':a[:]=[e for e in a if not(e['actionType']=='period' and e.get('subType')=='end' and e['period']==2)]
            if kind=='score':a[-1]['scoreHome']='999'
            if kind=='duplicate':a.append(copy.deepcopy(a[-1]))
            if kind=='identity':next(e for e in a if e['actionType']=='substitution')['personId']=999999
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.analyze(p)
