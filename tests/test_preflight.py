from copy import deepcopy
import unittest
from tests.test_store import inputs


def evidence():
    snapshot,_,_=inputs()
    game=snapshot['games'][0]
    game.update(home_tricode='HOM',away_tricode='AWY')
    for team,index in [('T1',1),('T2',2)]:
        base=next(p for p in snapshot['roster']['players'] if p['team_id']==team)
        for i in range(4):
            snapshot['roster']['players'].append({**base,'player_id':f'{team}-{i}','name':f'{team} Person {i}'})
    snapshot['injury_extractions']=[dict(parser_version='layout-exact-4',pages_complete=True,complete_teams=[
        dict(date='2026-10-22',matchup='AWY@HOM',team_id=t) for t in ('T1','T2')],rows=[],evidence=dict(
            source_url='https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-22_06_00AM.pdf',
            published_at='2026-10-22T10:00:00Z',retrieved_at='2026-10-22T10:30:00Z',sha256='a'*64))]
    profiles={'players':[dict(player_id=p['player_id'],expected_minutes_baseline=30,
        rotation_role=dict(label='starter',observations=1,starts=1,recent_minutes=30)) for p in snapshot['roster']['players']]}
    reference={'games':[dict(home='HOM',away='AWY',start_time=game['start_time'],state='pre')],
        'rosters':{t:[p['name'] for p in snapshot['roster']['players'] if p['team_id']==t] for t in ('T1','T2')},
        'sources':[dict(source_url='https://example.test/reference',retrieved_at='2026-10-22T10:50:00Z',sha256='a'*64)]}
    return snapshot,profiles,reference


class PreflightTests(unittest.TestCase):
    def test_applied_availability_must_match_official_status_or_matching_newer_review(self):
        from nba_forecast.preflight import check_inputs
        s,p,r=evidence()
        player=s['roster']['players'][0]
        s['injury_extractions'][0]['rows']=[dict(game_id=s['games'][0]['game_id'],player_id=player['player_id'],status='out')]
        result=check_inputs(s,[],p,r)
        self.assertEqual(result['status'],'blocked')
        self.assertTrue(any(i['code']=='availability_conflict' for i in result['issues']))
        player['status_basis']='sourced_review'
        s['review_input']=dict(game_id=s['games'][0]['game_id'],availability=[{k:player[k] for k in ('player_id','status','status_source_url','status_observed_at')}])
        self.assertEqual(check_inputs(s,[],p,r)['status'],'passed')
    def test_missing_stale_or_forged_injury_provenance_blocks(self):
        from nba_forecast.preflight import check_inputs
        for kind in ('missing','stale','forged'):
            s,p,r=evidence()
            e=s['injury_extractions'][0]['evidence']
            if kind=='missing':
                s['injury_extractions'][0].pop('evidence')
            if kind=='stale':
                e['published_at']='2026-10-20T10:00:00Z'
            if kind=='forged':
                e['source_url']='https://example.test/'+e['source_url'].split('/')[-1]
            with self.subTest(kind=kind):
                result=check_inputs(s,[],p,r)
                self.assertEqual(result['status'],'blocked')
                self.assertTrue(any(i['code']=='injury_provenance_invalid' for i in result['issues']))

    def test_archive_reparse_detects_forged_coverage_and_bad_pdf_hash(self):
        from nba_forecast.preflight import verify_extractions
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        s,_,_=evidence()
        extraction=s['injury_extractions'][0]
        rebuilt=deepcopy(extraction)
        rebuilt['complete_teams'].pop()
        with patch('nba_forecast.preflight.validate_extraction'),patch('nba_forecast.preflight.pdf_text',return_value='PDF text'),patch('nba_forecast.preflight.parse_report',return_value=rebuilt):
            with self.assertRaisesRegex(ValueError,'differ from'):
                verify_extractions(s,'unused')
        with tempfile.TemporaryDirectory() as folder,patch('nba_forecast.preflight.validate_extraction'):
            (Path(folder)/(extraction['evidence']['sha256']+'.pdf')).write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):
                verify_extractions(s,folder)
    def test_matching_independent_sources_and_complete_coverage_pass(self):
        from nba_forecast.preflight import check_inputs
        s,p,r=evidence()
        result=check_inputs(s,[],p,r)
        self.assertEqual(result['status'],'passed',result)
        self.assertEqual(len(result['lineups']['T1']['player_ids']),5)

    def test_missing_team_coverage_is_a_note_not_a_block(self):
        from nba_forecast.preflight import check_inputs
        s,p,r=evidence()
        s['injury_extractions'][0]['complete_teams'].pop()
        result=check_inputs(s,[],p,r)
        self.assertEqual(result['status'],'passed',result['issues'])
        self.assertTrue(any(n['code']=='injury_status_unavailable' and n['team_id']=='T2' for n in result['notes']))

    def test_changed_tipoff_blocks_while_provider_differences_are_notes(self):
        from nba_forecast.preflight import check_inputs
        original=evidence()
        for kind,status,code in (('time','blocked','tipoff_changed'),('roster','passed','roster_difference'),('future','passed','reference_stale')):
            s,p,r=deepcopy(original)
            if kind=='time':
                r['games'][0]['start_time']='2026-10-22T19:00:00Z'
            if kind=='roster':
                r['rosters']['T1'].pop()
            if kind=='future':
                r['sources'][0]['retrieved_at']='2026-10-22T12:00:00Z'
            with self.subTest(kind=kind):
                result=check_inputs(s,[],p,r)
                self.assertEqual(result['status'],status)
                self.assertIn(code,[i['code'] for i in result['issues']+result['notes']])

    def test_postponed_blocks_while_ambiguous_board_or_sparse_starters_are_notes(self):
        from nba_forecast.preflight import check_inputs
        for kind,status,code in (('postponed','blocked','matchup_not_scheduled'),('duplicate','passed','matchup_unconfirmed'),
                                 ('sparse','passed','starting_five_unresolved')):
            s,p,r=evidence()
            if kind=='postponed':
                r['games'][0]['status_name']='STATUS_POSTPONED'
            if kind=='duplicate':
                r['games'].append(deepcopy(r['games'][0]))
            if kind=='sparse':
                p['players']=[]
            with self.subTest(kind=kind):
                result=check_inputs(s,[],p,r)
                self.assertEqual(result['status'],status)
                self.assertIn(code,[i['code'] for i in result['issues']+result['notes']])

    def test_reference_outage_is_noted_and_archived(self):
        import json
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from nba_forecast.preflight import run_preflight
        s,p,_=evidence()
        from nba_forecast.validation import parse_time
        with tempfile.TemporaryDirectory() as folder,patch('nba_forecast.preflight.verify_extractions',return_value=s['injury_extractions']),\
             patch('nba_forecast.preflight.collect_references',side_effect=OSError('offline')),patch('nba_forecast.preflight.datetime') as clock:
            clock.now.return_value=parse_time(s['as_of'])
            result=run_preflight(s,[],p,folder)
            self.assertEqual(result['status'],'passed',result['issues'])
            root=Path(folder)/'preflight'/s['games'][0]['game_id']
            saved=json.loads((root/'latest.json').read_text())
            self.assertIn('reference_missing',[n['code'] for n in saved['notes']])
            self.assertIn('offline',' '.join(n['detail'] for n in saved['notes']))
            self.assertEqual(len(list((root/'records').glob('*.json'))),1)
