from copy import deepcopy
import unittest

from nba_forecast.totals import game_totals
from nba_forecast.model import forecast
from nba_forecast.store import _validate_forecast
from tests.test_store import inputs
from tests.test_model import snapshot, row


GAME = dict(game_id='g', away_team_id='A', home_team_id='H')


def predictions():
    return [dict(player_id=f'{team}{i}', name=f'{team}{i}', game_id='g',
                 team_id=team, status='projected', points_mean=points,
                 expected_minutes=48.)
            for team, points in [('A', 20.01), ('H', 18.02)] for i in range(5)]


class TotalsTests(unittest.TestCase):
    def test_both_teams_sum_unrounded_means_and_ignore_other_games(self):
        rows=predictions()+[dict(predictions()[0], game_id='other', points_mean=999)]
        total=game_totals(GAME, rows)
        self.assertEqual(total['status'], 'complete')
        self.assertAlmostEqual(total['teams'][0]['points_mean'], 100.05)
        self.assertAlmostEqual(total['teams'][1]['points_mean'], 90.1)
        self.assertAlmostEqual(total['points_mean'], 190.15)

    def test_withheld_player_marks_partial_even_when_minutes_fully_allocated(self):
        rows=predictions()+[dict(player_id='unknown',name='Unknown',game_id='g',
              team_id='A',status='needs_review',points_mean=None,expected_minutes=None)]
        total=game_totals(GAME,rows)
        self.assertEqual(total['status'],'partial')
        self.assertEqual(total['teams'][0]['withheld_player_ids'],['unknown'])
        self.assertAlmostEqual(total['teams'][0]['points_mean'],100.05)

    def test_no_playing_history_does_not_make_a_full_rotation_partial(self):
        cold=dict(player_id='new',name='New',game_id='g',team_id='A',status='needs_review',
            points_mean=None,expected_minutes=None,flags=['cold_start'],
            components={'history':{'played_games':0},'availability':{'status':'available'}})
        rows=predictions()+[cold]
        total=game_totals(GAME,rows)
        self.assertEqual(total['status'],'complete')
        self.assertEqual(total['teams'][0]['cold_start_player_ids'],['new'])
        self.assertEqual(total['teams'][0]['withheld_player_ids'],['new'])
        self.assertAlmostEqual(total['points_mean'],190.15)
        self.assertIsNone(cold['points_mean'])
        self.assertEqual(game_totals(GAME,rows,schema_version=1)['status'],'partial')
        self.assertEqual(game_totals(GAME,rows,lineups={'A':{'player_ids':['new']}})['status'],'partial')
        cold['projected_starter']=True
        self.assertEqual(game_totals(GAME,rows)['status'],'partial')
        cold.pop('projected_starter')
        rows[0]['expected_minutes']=30
        self.assertEqual(game_totals(GAME,rows)['status'],'partial')

    def test_unresolved_availability_or_prior_playing_evidence_stays_partial(self):
        cold=dict(player_id='new',name='New',game_id='g',team_id='A',status='needs_review',
            points_mean=None,expected_minutes=None,flags=['cold_start'],
            components={'history':{'played_games':0},'availability':{'status':'available'}})
        for edit in ('questionable','unknown','played','extra_flag'):
            candidate=deepcopy(cold)
            if edit in ('questionable','unknown'):candidate['components']['availability']['status']=edit
            elif edit=='played':candidate['components']['history']['played_games']=1
            else:candidate['flags'].append('new_team_role_unobserved')
            if edit!='played':
                self.assertEqual(game_totals(GAME,predictions()+[candidate])['status'],'partial',edit)
            # Schema 3 also required zero played games; schema 4 covers evidence-only withholding regardless.
            self.assertEqual(game_totals(GAME,predictions()+[candidate],schema_version=3)['status'],'partial',edit)

    def test_out_player_is_known_zero_and_missing_minutes_are_disclosed(self):
        rows=predictions()+[dict(player_id='out',name='Out',game_id='g',team_id='A',
             status='out',points_mean=0,expected_minutes=0)]
        rows[0]['expected_minutes']=30
        total=game_totals(GAME,rows)
        self.assertEqual(total['teams'][0]['status'],'partial')
        self.assertEqual(total['teams'][0]['unallocated_minutes'],18)
        self.assertEqual(total['teams'][0]['withheld_player_ids'],[])
        self.assertEqual(total['teams'][1]['status'],'complete')

    def test_unallocated_minutes_score_at_the_replacement_rate_for_a_full_rotation(self):
        from nba_forecast.totals import REPLACEMENT_POINTS_PER_MINUTE
        rows=[dict(player_id=f'A{i}',name=f'A{i}',game_id='g',team_id='A',status='projected',points_mean=10.,expected_minutes=25.) for i in range(8)]
        rows+=[p for p in predictions() if p['team_id']=='H']
        rows.append(dict(player_id='out',name='Out',game_id='g',team_id='A',status='out',points_mean=0,expected_minutes=0))
        team=game_totals(GAME,rows)['teams'][0]
        self.assertEqual(team['status'],'complete')
        self.assertEqual((team['unallocated_minutes'],team['residual_minutes']),(40,40))
        self.assertAlmostEqual(team['residual_points'],40*REPLACEMENT_POINTS_PER_MINUTE)
        self.assertAlmostEqual(team['points_mean'],80+40*REPLACEMENT_POINTS_PER_MINUTE)
        self.assertAlmostEqual(team['points_mean_projected'],80)
        # Fewer than eight projected players: the deficit is disclosed, not filled.
        thin=game_totals(GAME,rows[2:])['teams'][0]
        self.assertEqual((thin['status'],thin['residual_minutes'],thin['points_mean']),('partial',0,60))
        self.assertEqual(game_totals(GAME,rows,schema_version=3)['teams'][0]['status'],'partial')

    def test_withheld_players_without_evidence_are_covered_by_the_residual(self):
        rows=[dict(player_id=f'A{i}',name=f'A{i}',game_id='g',team_id='A',status='projected',points_mean=10.,expected_minutes=25.) for i in range(8)]
        rows+=[p for p in predictions() if p['team_id']=='H']
        covered=dict(player_id='rookie',name='Rookie',game_id='g',team_id='A',status='needs_review',points_mean=None,expected_minutes=None,
                     flags=['no_scoring_opportunity_evidence','team_minutes_unresolved'],
                     components={'history':{'played_games':2},'availability':{'status':'available'}})
        team=game_totals(GAME,rows+[covered])['teams'][0]
        self.assertEqual((team['status'],team['cold_start_player_ids'],team['unresolved_player_ids']),('complete',['rookie'],[]))
        for edit in ('questionable','starter','role'):
            candidate=deepcopy(covered)
            if edit=='questionable':candidate['components']['availability']['status']='questionable'
            elif edit=='starter':candidate['projected_starter']=True
            else:candidate['flags'].append('new_team_role_unobserved')
            self.assertEqual(game_totals(GAME,rows+[candidate])['teams'][0]['status'],'partial',edit)

    def test_missing_team_is_unavailable_and_combined_total_stays_null(self):
        total=game_totals(GAME,[p for p in predictions() if p['team_id']=='A'])
        self.assertIsNone(total['points_mean'])
        self.assertIsNone(total['teams'][1]['points_mean'])
        self.assertEqual(total['teams'][1]['status'],'unavailable')

    def test_duplicate_and_nonfinite_projection_rows_are_rejected(self):
        with self.assertRaises(ValueError):game_totals(GAME,predictions()+[predictions()[0]])
        for bad in (float('nan'),float('inf'),-1,True):
            rows=predictions();rows[0]['points_mean']=bad
            with self.assertRaises(ValueError):game_totals(GAME,rows)

    def test_new_forecast_contains_totals(self):
        result=forecast(snapshot(),[row()])
        self.assertEqual(result['game_totals'][0]['game_id'],'next')
        self.assertIsNone(result['game_totals'][0]['points_mean'])

    def test_ledger_rejects_totals_that_disagree_with_player_rows(self):
        s,_,f=inputs()
        f['game_totals']=[game_totals(s['games'][0],f['predictions'])]
        _validate_forecast(s,f)
        forged=deepcopy(f);forged['game_totals'][0]['points_mean']=999
        with self.assertRaisesRegex(ValueError,'totals'):_validate_forecast(s,forged)

    def test_original_saved_totals_schema_remains_valid(self):
        s,_,f=inputs()
        f['game_totals']=[game_totals(s['games'][0],f['predictions'],schema_version=1)]
        _validate_forecast(s,f)
        forged=deepcopy(f);forged['game_totals'][0]['teams'][0]['projected_players']=True
        with self.assertRaisesRegex(ValueError,'totals'):_validate_forecast(s,forged)
