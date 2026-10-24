import copy
import unittest

from nba_forecast.model import forecast, _baseline_forecast
from nba_forecast.store import _validate_forecast, _report
from tests.test_model import row, current, snapshot, player


class FullBoxModelTests(unittest.TestCase):
    def test_shooting_identity_preserves_point_model(self):
        snap = snapshot()
        data = [row(reb=8, oreb=2, dreb=6, pf=3, plus_minus=-8)]
        old = _baseline_forecast(snap, data)['predictions'][0]
        result = forecast(snap, data)
        p = result['predictions'][0]
        self.assertEqual(p['points_mean'], old['points_mean'])
        self.assertEqual(p['points_sd'], old['points_sd'])
        self.assertAlmostEqual(p['points_mean'], 2*p['expected_fgm'] + p['expected_fg3m'] + p['expected_ftm'])
        self.assertAlmostEqual(p['expected_reb'], p['expected_oreb'] + p['expected_dreb'])
        self.assertLess(p['expected_plus_minus'], 0)
        self.assertGreater(p['expected_pf'], 0)
        self.assertLessEqual(p['expected_pf'], 6)
        for m,a,pct in [('fgm','fga','fg_pct'),('fg3m','fg3a','fg3_pct'),('ftm','fta','ft_pct')]:
            self.assertLessEqual(p['expected_'+m], p['expected_'+a])
            self.assertAlmostEqual(p['expected_'+pct],p['expected_'+m]/p['expected_'+a])
        _validate_forecast(snap,result)
        self.assertEqual(result['boxscore_model_version'],'full-boxscore-1.0.0')

    def test_missing_optional_and_zero_attempt_percent(self):
        p=forecast(snapshot(),[row(fg3m=0,fg3a=0,ftm=0,fta=0)])['predictions'][0]
        for stat in ('oreb','dreb','pf','plus_minus'):
            self.assertIsNone(p['expected_'+stat])
        self.assertIsNone(p['expected_fg3_pct'])
        self.assertIsNone(p['expected_ft_pct'])
        snap=snapshot([player(status='out')]); result=forecast(snap,[])
        out=result['predictions'][0]
        self.assertEqual(out['expected_pf'],0)
        self.assertIsNone(out['expected_fg_pct'])
        _validate_forecast(snap,result)

    def test_missing_future_and_preseason_do_not_become_evidence(self):
        data=[row(oreb=2,dreb=6,reb=8,pf=3,plus_minus=-8)]
        before=forecast(snapshot(),data)['predictions'][0]
        data += [row(1),current(pf=6,plus_minus=60,known_at='2027-01-01T00:00:00Z'),row(2,pf=6,plus_minus=60,season_type='Preseason')]
        after=forecast(snapshot(),data)['predictions'][0]
        for stat in ('oreb','dreb','pf','plus_minus'):
            self.assertEqual(before['expected_'+stat],after['expected_'+stat])

    def test_ledger_rejects_inconsistent_full_box(self):
        snap=snapshot();base=forecast(snap,[row(reb=8,oreb=2,dreb=6,pf=3,plus_minus=-8)])
        for key,value in [('expected_fgm',99),('expected_oreb',99),('expected_fg_pct',2),('expected_plus_minus',float('nan')),('expected_pf',7)]:
            result=copy.deepcopy(base);result['predictions'][0][key]=value
            with self.assertRaises(ValueError,msg=key):_validate_forecast(snap,result)
        legacy=_baseline_forecast(snap,[row()])
        _validate_forecast(snap,legacy)
        legacy['predictions'][0]['expected_fgm']=999
        with self.assertRaises(ValueError):_validate_forecast(snap,legacy)

    def test_feedback_scores_full_box_without_percentage_minute_decomposition(self):
        snap=snapshot();result=forecast(snap,[row(pf=3,plus_minus=-8)])
        actual=row(game_id='next',pf=4,plus_minus=5)
        run=dict(run_id='run',mode='replay',scope='subset',created_at=snap['as_of'],as_of=snap['as_of'])
        report=_report(run,result,[actual],1,snap['as_of'])
        for stat in ('fgm','fg3a','ftm','pf','plus_minus','fg_pct'):
            self.assertEqual(report['metrics']['per_stat'][stat]['count'],1)
        pct=report['matched'][0]['stat_feedback']['fg_pct']
        self.assertIsNone(pct['minutes_component'])
        self.assertAlmostEqual(pct['actual'],.5)

    def test_make_allocation_caps_probabilities_and_refuses_impossible_points(self):
        from nba_forecast.boxscore_model import expected_makes
        made=expected_makes([2,1,10],[.9,.9,.99],15.5)
        self.assertTrue(all(0<=m<=a for m,a in zip(made,[2,1,10])))
        self.assertAlmostEqual(sum(m*v for m,v in zip(made,[2,3,1])),15.5)
        with self.assertRaises(ValueError):expected_makes([1,0,0],[.5,.3,.8],3)


if __name__=='__main__':unittest.main()
