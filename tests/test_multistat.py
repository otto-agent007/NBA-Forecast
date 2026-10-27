import copy
import unittest
from nba_forecast.model import forecast
from nba_forecast.validation import validate_history
from nba_forecast.store import _report, _metrics
from tests.test_model import row, current, snapshot, player

class MultistatTests(unittest.TestCase):
    def one(self, rows, snap=None):
        return forecast(snap or snapshot(), rows)['predictions'][0]

    def test_unknown_is_not_zero_and_out_is_zero(self):
        p = self.one([row()])
        self.assertIsNone(p['expected_reb'])
        self.assertGreater(p['expected_fg3m'], 0)
        self.assertEqual(self.one([], snapshot([player(status='out')]))['expected_reb'], 0)

    def test_optional_validation_and_dnp(self):
        for value in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                validate_history([row(reb=value)])
        validate_history([row(reb=None)])
        dnp = row(minutes=0,fgm=0,fga=0,fg3m=0,fg3a=0,ftm=0,fta=0,tov=0,status='dnp',reb=1)
        with self.assertRaises(ValueError):
            validate_history([dnp])

    def test_missing_observations_do_not_dilute_rate_and_bad_evidence_excluded(self):
        base = self.one([row(reb=10, ast=6, stl=2, blk=1)])
        more = self.one([row(reb=10,ast=6,stl=2,blk=1),row(1),row(2,reb=99,season_type='Preseason')])
        self.assertEqual(base['expected_reb'], more['expected_reb'])
        self.assertLess(base['expected_reb'],10)
        self.assertIn('heuristic', base['components']['multistat']['reb']['uncertainty_method'])

    def test_minutes_cap_scales_every_supported_stat(self):
        data = [row(i,reb=8,ast=4,stl=1,blk=1) for i in range(10)]
        snap=snapshot()
        snap['role_adjustments']=[dict(player_id='p',game_id='next',observed_at=snap['as_of'],source_url='https://example.test/role',reviewer='test',reason='Restriction',minutes_delta=0,opportunity_multiplier=1,minutes_cap=15)]
        before, after = self.one(data),self.one(data,snap)
        for stat in ('reb','ast','fg3m','stl','blk','tov'):
            self.assertAlmostEqual(after['expected_'+stat],before['expected_'+stat]/2)

    def test_feedback_stat_denominators_and_old_reports(self):
        snap=snapshot()
        prediction=forecast(snap,[row(reb=8)])
        actual=row(game_id='next',reb=10)
        run=dict(run_id='run',mode='replay',scope='subset',created_at=snap['as_of'],as_of=snap['as_of'])
        report=_report(run,prediction,[actual],1,snap['as_of'])
        self.assertEqual(report['metrics']['per_stat']['reb']['count'],1)
        self.assertEqual(report['metrics']['per_stat']['ast']['count'],0)
        detail=report['matched'][0]['stat_feedback']['reb']
        self.assertAlmostEqual(detail['minutes_component']+detail['rate_component'],detail['error'])
        old=copy.deepcopy(report['matched'])
        old[0].pop('stat_errors')
        old[0].pop('stat_feedback')
        self.assertEqual(_metrics(old)['per_stat']['reb']['count'],0)

    def test_ingestion_preserves_optional_unknown_and_observed_counts(self):
        import json
        from nba_forecast.data import normalize_boxscore
        from tests.test_data import FIXTURES, URL, KNOWN
        payload = json.loads((FIXTURES / 'boxscore_0022600001.json').read_text())
        raw = payload['game']['homeTeam']['players'][0]
        expected = raw['statistics']['reboundsTotal']
        raw['statistics'].pop('assists', None)
        rows = normalize_boxscore(payload, URL, KNOWN)
        observed = next(r for r in rows if r['player_id'] == str(raw['personId']))
        self.assertEqual(observed['reb'], expected)
        self.assertIsNone(observed['ast'])

    def test_three_point_makes_do_not_overreact_to_one_bad_night(self):
        prior = [row(i) for i in range(28)]
        before = self.one(prior)
        after = self.one(prior + [current(fgm=0,fg3m=0,ftm=0)])
        self.assertGreater(after['expected_fg3m'], before['expected_fg3m'] * .9)

    def test_optional_fields_reject_nonfinite_and_out_null_in_ledger(self):
        from nba_forecast.store import _validate_forecast
        snap = snapshot()
        result = forecast(snap, [row()])
        _validate_forecast(snap, result)
        result['predictions'][0]['expected_reb'] = float('nan')
        with self.assertRaises(ValueError):
            _validate_forecast(snap, result)
        snap = snapshot([player(status='out')])
        result = forecast(snap, [])
        result['predictions'][0]['expected_ast'] = None
        with self.assertRaises(ValueError):
            _validate_forecast(snap, result)

    def test_minutes_restriction_validation(self):
        from nba_forecast.validation import validate_inputs
        snap = snapshot([player(), player('b',team='B'),player('c',team='C')])
        adjustment = dict(player_id='p',game_id='next',observed_at=snap['as_of'],source_url='https://example.test/role',reviewer='test',reason='Restriction',minutes_delta=0,opportunity_multiplier=1,minutes_cap=15)
        snap['role_adjustments'] = [adjustment]
        validate_inputs(snap, [], scope='subset')
        for invalid in (-1,49,True,None):
            adjustment['minutes_cap'] = invalid
            with self.assertRaises(ValueError):
                validate_inputs(snap, [], scope='subset')

    def test_not_listed_report_projection_is_explicitly_conditional(self):
        explicit = self.one([row()])
        snap = snapshot()
        snap['roster']['players'][0]['status_basis'] = 'not_listed_complete_report'
        conditional = self.one([row()], snap)
        self.assertEqual(conditional['status'], 'projected')
        self.assertEqual(conditional['points_mean'], explicit['points_mean'])
        self.assertIn('availability_not_listed', conditional['flags'])
        self.assertIn('participation is not confirmed', conditional['reason'])
        self.assertNotIn('availability_not_listed', explicit['flags'])
        self.assertEqual(conditional['components']['availability']['status_basis'], 'not_listed_complete_report')
