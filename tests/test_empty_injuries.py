"""Explicit empty game reports differ from absent fields and unrelated games."""
from copy import deepcopy
import unittest
from tests.test_preflight import evidence


def fixture():
    snapshot,profiles,reference=evidence();g=snapshot['games'][0]
    g.update(home_name='Home',away_name='Away')
    extraction=snapshot['injury_extractions'][0]
    extraction['heading_matches']=True
    extraction['complete_teams']=[c for c in extraction['complete_teams'] if c['team_id']=='T1']
    extraction['team_coverage']=deepcopy(extraction['complete_teams'])
    extraction['issues']=[dict(kind='missing_team_coverage',team_id='T2',date='2026-10-22',matchup='AWY@HOM',game_id=g['game_id'])]
    for p in snapshot['roster']['players']:
        if p['team_id']=='T2':p['status']='unknown'
    meta=dict(source_url='https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=401909848',
              retrieved_at='2026-10-22T10:59:00Z',sha256='b'*64)
    summary=dict(header=dict(id='401909848',season=dict(year=2027,type=2),competitions=[dict(id='401909848',date=g['start_time'],competitors=[
        dict(homeAway='home',team=dict(id='1',abbreviation='HOM')),dict(homeAway='away',team=dict(id='2',abbreviation='AWY'))])]),
        injuries=[dict(team=dict(id='1',abbreviation='HOM'),injuries=[dict(status='Out')]),dict(team=dict(id='2',abbreviation='AWY'),injuries=[])])
    return snapshot,profiles,reference,summary,meta


class EmptyReportTests(unittest.TestCase):
    def test_preflight_applies_verified_empty_report_before_model_execution(self):
        from unittest.mock import patch
        from nba_forecast.availability import summary_no_injuries
        from nba_forecast.preflight import run_preflight
        from nba_forecast.model import forecast
        from nba_forecast.validation import parse_time
        from tests.test_model import row
        import tempfile
        s,p,r,payload,meta=fixture();r['no_injuries']=summary_no_injuries(payload,s['games'][0],s['season'],meta);r['sources'].append(meta)
        people=[p for p in s['roster']['players'] if p['team_id']=='T2']
        history=[row(i=0,pid=person['player_id'],team='T2',opponent_id='T1',season='2025-26',
            game_time='2026-04-01T20:00:00Z',known_at='2026-04-01T23:00:00Z') for person in people]
        with tempfile.TemporaryDirectory() as folder,patch('nba_forecast.preflight.collect_references',return_value=r),patch('nba_forecast.preflight.verify_extractions',return_value=s['injury_extractions']),patch('nba_forecast.preflight.datetime') as clock:
            clock.now.return_value=parse_time(s['as_of'])
            check=run_preflight(s,history,p,folder)
        self.assertEqual(check['status'],'passed',check)
        predictions=[p for p in forecast(s,history)['predictions'] if p['team_id']=='T2']
        self.assertEqual(len(predictions),5)
        self.assertTrue(all(p['status']=='projected' and p['points_mean']>0 and 'availability_not_listed' in p['flags'] for p in predictions))

    def test_empty_report_never_bypasses_roster_disagreement(self):
        from nba_forecast.availability import summary_no_injuries,apply_no_injuries
        from nba_forecast.preflight import check_inputs
        s,p,r,payload,meta=fixture();r['no_injuries']=summary_no_injuries(payload,s['games'][0],s['season'],meta)
        s=apply_no_injuries(s,r['no_injuries']);r['rosters']['T2'].pop()
        # The difference stays visible; it is a note, since the official roster is authoritative.
        self.assertTrue(any(n['code']=='roster_difference' and n['team_id']=='T2' for n in check_inputs(s,[],p,r)['notes']))
    def test_explicit_empty_team_list_fills_missing_block_and_passes_preflight(self):
        from nba_forecast.availability import summary_no_injuries,apply_no_injuries
        from nba_forecast.preflight import check_inputs
        s,p,r,payload,meta=fixture()
        reports=summary_no_injuries(payload,s['games'][0],s['season'],meta)
        self.assertEqual(len(reports),1);self.assertEqual(reports[0]['team_id'],'T2')
        updated=apply_no_injuries(s,reports);r['no_injuries']=reports;r['sources'].append(meta)
        people=[p for p in updated['roster']['players'] if p['team_id']=='T2']
        self.assertTrue(all(p['status']=='available' and p['reported_status']=='Not listed' for p in people))
        self.assertTrue(all(p['status_basis']=='not_listed_verified_empty_report' for p in people))
        result=check_inputs(updated,[],p,r)
        self.assertEqual(result['status'],'passed',result)
        self.assertTrue(all(p['status']=='unknown' for p in s['roster']['players'] if p['team_id']=='T2'))

    def test_absent_team_or_injuries_field_cannot_mean_zero(self):
        from nba_forecast.availability import summary_no_injuries
        for kind in ('team','field','null','wrong_team','nonempty'):
            s,_,_,payload,meta=fixture()
            if kind=='team':payload['injuries'].pop()
            if kind=='field':payload['injuries'][1].pop('injuries')
            if kind=='null':payload['injuries'][1]['injuries']=None
            if kind=='wrong_team':payload['injuries'][1]['team']['id']='99'
            if kind=='nonempty':payload['injuries'][1]['injuries']=[dict(status='Out')]
            with self.subTest(kind=kind):self.assertEqual(summary_no_injuries(payload,s['games'][0],s['season'],meta),[])

    def test_wrong_game_date_season_or_side_is_rejected(self):
        from nba_forecast.availability import summary_no_injuries
        for kind in ('event','date','season','side'):
            s,_,_,payload,meta=fixture();header=payload['header']
            if kind=='event':header['id']='999999999'
            if kind=='date':header['competitions'][0]['date']='2026-10-23T18:00:00Z'
            if kind=='season':header['season']['year']=2026
            if kind=='side':header['competitions'][0]['competitors'][0]['team']['abbreviation']='AWY'
            with self.subTest(kind=kind),self.assertRaises(ValueError):summary_no_injuries(payload,s['games'][0],s['season'],meta)

    def test_not_submitted_parse_conflicts_or_existing_coverage_are_never_overridden(self):
        from nba_forecast.availability import summary_no_injuries,apply_no_injuries
        for kind in ('team_not_submitted','unmatched_identity_or_game','covered'):
            s,_,_,payload,meta=fixture();e=s['injury_extractions'][0]
            if kind=='covered':e['team_coverage'].append(dict(team_id='T2',date='2026-10-22',matchup='AWY@HOM'))
            else:e['issues'].append(dict(kind=kind,team_id='T2',date='2026-10-22',matchup='AWY@HOM'))
            reports=summary_no_injuries(payload,s['games'][0],s['season'],meta)
            with self.subTest(kind=kind):
                self.assertTrue(all(p['status']=='unknown' for p in apply_no_injuries(s,reports)['roster']['players'] if p['team_id']=='T2'))

    def test_stale_future_and_after_tipoff_capture_cannot_supply_forecast_availability(self):
        from nba_forecast.availability import summary_no_injuries,apply_no_injuries
        for time in ('2026-10-22T10:00:00Z','2026-10-22T12:00:00Z','2026-10-22T18:05:00Z'):
            s,_,_,payload,meta=fixture();meta['retrieved_at']=time
            reports=summary_no_injuries(payload,s['games'][0],s['season'],meta)
            self.assertTrue(all(p['status']=='unknown' for p in apply_no_injuries(s,reports)['roster']['players'] if p['team_id']=='T2'))

    def test_explicit_or_reviewed_player_status_is_preserved(self):
        from nba_forecast.availability import summary_no_injuries,apply_no_injuries
        s,_,_,payload,meta=fixture()
        player=next(p for p in s['roster']['players'] if p['team_id']=='T2');player['status']='out'
        out=apply_no_injuries(s,summary_no_injuries(payload,s['games'][0],s['season'],meta))
        self.assertEqual(next(p for p in out['roster']['players'] if p['player_id']==player['player_id'])['status'],'out')
        player.update(status='unknown',status_basis='sourced_review')
        out=apply_no_injuries(s,summary_no_injuries(payload,s['games'][0],s['season'],meta))
        self.assertEqual(next(p for p in out['roster']['players'] if p['player_id']==player['player_id'])['status'],'unknown')

    def test_new_basis_produces_numeric_conditional_forecast_with_supported_history(self):
        from nba_forecast.model import forecast
        from nba_forecast.availability import BASIS
        from tests.test_model import snapshot,player,row
        person=player();person['status_basis']=BASIS
        prediction=forecast(snapshot([person]),[row()])['predictions'][0]
        self.assertEqual(prediction['status'],'projected')
        self.assertGreater(prediction['points_mean'],0)
        self.assertIn('availability_not_listed',prediction['flags'])
        self.assertIn('no injuries',prediction['reason'])

    def test_only_clean_missing_blocks_are_published_to_dashboard(self):
        from nba_forecast.availability import record_availability_update
        import json,tempfile
        from pathlib import Path
        s,_,_,payload,meta=fixture();g=s['games'][0]
        reports=[dict(game_id=g['game_id'],start_time=g['start_time'],team_id=tid,status='no_injuries_reported',**meta) for tid in ('T1','T2')]
        with tempfile.TemporaryDirectory() as folder:
            record_availability_update(folder,s,reports,[meta])
            update=json.loads((Path(folder)/'availability'/g['game_id']/'latest.json').read_text())
        self.assertEqual([t['team_id'] for t in update['teams']],['T2'])

    def test_new_nonempty_or_unavailable_report_supersedes_old_zero_claim(self):
        from nba_forecast.availability import record_availability_update
        import json,tempfile
        from pathlib import Path
        s,_,_,payload,meta=fixture();g=s['games'][0]
        report=dict(game_id=g['game_id'],start_time=g['start_time'],team_id='T2',status='no_injuries_reported',**meta)
        with tempfile.TemporaryDirectory() as folder:
            record_availability_update(folder,s,[report],[meta])
            root=Path(folder)/'availability'/g['game_id']
            record_availability_update(folder,s,[],[meta])
            latest=json.loads((root/'latest.json').read_text())
            self.assertEqual(latest['teams'],[])
            self.assertEqual(latest['status'],'superseded')
            self.assertEqual(len(list((root/'records').glob('*.json'))),2)

    def test_pdf_publication_heading_mismatch_blocks_empty_report_fallback(self):
        from nba_forecast.availability import summary_no_injuries,apply_no_injuries
        s,_,_,payload,meta=fixture();s['injury_extractions'][0]['heading_matches']=False
        out=apply_no_injuries(s,summary_no_injuries(payload,s['games'][0],s['season'],meta))
        self.assertTrue(all(p['status']=='unknown' for p in out['roster']['players'] if p['team_id']=='T2'))
