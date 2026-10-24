from copy import deepcopy
import unittest
from nba_forecast.model import forecast
from tests.test_model import snapshot,row


class CandidateModelTests(unittest.TestCase):
    def enhanced(self,s):
        s=deepcopy(s)
        s['model_enhancements']=dict(model_version='regular-season-baseline-0.5.0',model_key='test',
            known_at=s['as_of'],parameters={'rotation_enabled':False,'pace_strength':1.,'defense_strength':1.},
            environment={'teams':{'A':{'pace_factor':1.2,'defense_factor':1.1,'audit':{}}},'audit':{'evidence_kind':'live'}},
            context_model=None,calibration=None)
        return s

    def test_pace_changes_attempts_and_defense_changes_scoring_once(self):
        s=snapshot();baseline=forecast(s,[row()])['predictions'][0]
        result=forecast(self.enhanced(s),[row()]);p=result['predictions'][0]
        self.assertEqual(result['model_version'],'regular-season-baseline-0.5.0')
        self.assertAlmostEqual(p['expected_fga'],baseline['expected_fga']*1.2)
        self.assertAlmostEqual(p['points_mean'],baseline['points_mean']*1.2*1.1)
        self.assertEqual(p['expected_minutes'],baseline['expected_minutes'])
        self.assertEqual(p['components']['uncertainty']['calibration']['status'],'fallback')

    def test_future_artifact_or_reconstruction_cannot_masquerade_as_live(self):
        s=self.enhanced(snapshot());s['model_enhancements']['known_at']='2027-01-01T00:00:00Z'
        with self.assertRaises(ValueError):forecast(s,[row()])
        s=self.enhanced(snapshot());s['model_enhancements']['environment']['audit']['evidence_kind']='historical_reconstruction'
        with self.assertRaises(ValueError):forecast(s,[row()])

    def test_uncertain_player_stays_unprojected(self):
        s=self.enhanced(snapshot());s['roster']['players'][0]['status']='questionable'
        p=forecast(s,[row()])['predictions'][0]
        self.assertIsNone(p['points_mean'])

class CandidateAttemptBudgetTests(unittest.TestCase):
    def test_automatic_extra_minutes_obey_team_attempt_budget_and_audit(self):
        from tests.test_model import player,current
        s=snapshot([player(str(i)) for i in range(5)]+[player('out',status='out')]);history=[]
        for pid in [str(i) for i in range(5)]+['out']:
            for i,minutes in enumerate([20,20,40] if pid!='out' else [30,30,30]):
                history.append(current(i,pid=pid,minutes=minutes,fga=minutes//2,fgm=minutes//4,
                    fg3a=0,fg3m=0,fta=minutes//5,ftm=0,tov=minutes//10))
        baseline=forecast(s,history);before={p['player_id']:p for p in baseline['predictions']}
        fga=sum(p['expected_fga'] or 0 for p in before.values());fta=sum(p['expected_fta'] or 0 for p in before.values())
        s['model_enhancements']=dict(model_version='regular-season-baseline-0.5.0',model_key='test',known_at=s['as_of'],
            parameters=dict(rotation_enabled=True,pace_strength=0,defense_strength=0,context_strength=0),
            environment={'teams':{'A':dict(pace_factor=1,defense_factor=1,expected_fga=fga+10,expected_fta=fta+2,audit={})},'audit':{'evidence_kind':'live'}},
            context_model=None,calibration=None)
        result=forecast(s,history);predicted=[p for p in result['predictions'] if p['status']=='projected']
        self.assertLessEqual(sum(p['expected_fta'] for p in predicted),fta+2+1e-9)
        self.assertLessEqual(sum(p['expected_fga'] for p in predicted),fga+10+1e-9)
        self.assertTrue(all(p['expected_minutes']>=before[p['player_id']]['expected_minutes'] for p in predicted))
        audit=result['allocation_audit']['A']
        self.assertGreater(audit['initial_allocator_result']['team_minutes_after'],audit['team_minutes_after'])
        self.assertAlmostEqual(audit['team_minutes_after'],sum(p['expected_minutes'] for p in predicted))
        self.assertAlmostEqual(audit['adjusted_attempts']['fta'],sum(p['expected_fta'] for p in predicted))
        self.assertGreater(audit['unallocated_vacancy_minutes'],0)
        self.assertLessEqual(audit['team_minutes_after'],240)
        for p in predicted:
            self.assertAlmostEqual(audit['recipients'][p['player_id']]['adjusted_minutes'],p['expected_minutes'])

    def test_zero_team_volume_room_keeps_original_minutes_and_manual_locks(self):
        from tests.test_model import player,current
        s=snapshot([player('p'),player('out',status='out')]);history=[]
        for pid in ('p','out'):
            for i,minutes in enumerate([20,20,40] if pid=='p' else [30,30,30]):
                history.append(current(i,pid=pid,minutes=minutes,fga=minutes//2,fgm=minutes//4,
                    fg3a=0,fg3m=0,fta=minutes//5,ftm=0,tov=minutes//10))
        before=forecast(s,history)['predictions'][1]
        s['model_enhancements']=dict(model_version='regular-season-baseline-0.5.0',model_key='test',known_at=s['as_of'],
            parameters=dict(rotation_enabled=True,pace_strength=0,defense_strength=0,context_strength=0),
            environment={'teams':{'A':dict(pace_factor=1,defense_factor=1,expected_fga=before['expected_fga'],expected_fta=before['expected_fta'],audit={})},'audit':{'evidence_kind':'live'}},
            context_model=None,calibration=None)
        p=forecast(s,history)['predictions'][1]
        self.assertAlmostEqual(p['expected_minutes'],before['expected_minutes'])
        self.assertAlmostEqual(p['expected_fga'],before['expected_fga'])
        self.assertAlmostEqual(p['expected_fta'],before['expected_fta'])

    def test_manual_minute_and_rate_locks_survive_automatic_budget_constraints(self):
        from tests.test_model import player,current
        s=snapshot([player('p'),player('out',status='out')]);history=[]
        for pid in ('p','out'):
            for i,minutes in enumerate([20,20,40] if pid=='p' else [30,30,30]):
                history.append(current(i,pid=pid,minutes=minutes,fga=minutes//2,fgm=minutes//4,
                    fg3a=0,fg3m=0,fta=minutes//5,ftm=0,tov=minutes//10))
        s['role_adjustments']=[dict(player_id='p',game_id='next',observed_at='2026-01-10T11:00:00Z',
            source_url='https://example.test/manual',reason='Reviewed restriction',reviewer='owner',
            minutes_delta=0,minutes_cap=25,opportunity_multiplier=1.1)]
        before=forecast(s,history)['predictions'][1]
        s['model_enhancements']=dict(model_version='regular-season-baseline-0.5.0',model_key='test',known_at=s['as_of'],
            parameters=dict(rotation_enabled=True,pace_strength=0,defense_strength=0,context_strength=0),
            environment={'teams':{'A':dict(pace_factor=1,defense_factor=1,expected_fga=100,expected_fta=50,audit={})},'audit':{'evidence_kind':'live'}},
            context_model=None,calibration=None)
        result=forecast(s,history);p=result['predictions'][1]
        self.assertEqual(p['expected_minutes'],25)
        self.assertEqual(p['expected_minutes'],before['expected_minutes'])
        self.assertEqual(p['expected_fga'],before['expected_fga'])
        self.assertEqual(p['expected_fta'],before['expected_fta'])
        self.assertEqual(result['allocation_audit']['A']['recipients']['p']['adjusted_minutes'],25)
