import json
from copy import deepcopy
from datetime import datetime,timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from nba_forecast.operations import run_tick,run_derived,request_derived
from nba_forecast.schedule import normalize_season
from tests.test_schedule import payload,game
from tests.test_store import inputs
from tests.test_model import row

# Orchestration fixtures run after the owner's 9 AM Pacific initial checkpoint.
NOW='2026-10-21T16:00:00Z'

class OperationsTests(unittest.TestCase):
    def test_secondary_calendar_recovers_cdn_denial_without_attention_warning(self):
        source=normalize_season(payload([game(start='2026-10-24T22:00:00Z')]),'2026-10-22T03:00:00Z')
        source['version']='nba-source'
        verified=deepcopy(source)
        verified['crosscheck']=dict(status='verified',verified_game_ids=['0022600001'],blocked_game_ids=[])
        clock='2026-10-24T15:59:00Z'
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'schedule'
            folder.mkdir()
            (folder/'latest.json').write_text(json.dumps(source))
            with patch('nba_forecast.operations._now',return_value=clock),\
                 patch('nba_forecast.operations.fetch_schedule',side_effect=ValueError('NBA CDN HTTP 403')),\
                 patch('nba_forecast.operations.fetch_verified_secondary_schedule',return_value=verified) as secondary:
                result=run_tick(d,[])
        secondary.assert_called_once()
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['schedule_source'],'espn_verified_cached_nba')
        self.assertEqual(result['warnings'],[])
        self.assertEqual(result['created_runs'],0)

    def test_unverified_due_calendar_game_is_noted_not_withheld(self):
        source=normalize_season(payload([game(start='2026-10-24T22:00:00Z')]),'2026-10-22T03:00:00Z')
        source['version']='nba-source'
        verified=deepcopy(source)
        verified['crosscheck']=dict(status='partial',verified_game_ids=[],blocked_game_ids=['0022600001'])
        clock='2026-10-24T16:00:00Z'
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'schedule'
            folder.mkdir()
            (folder/'latest.json').write_text(json.dumps(source))
            with patch('nba_forecast.operations._now',return_value=clock),\
                 patch('nba_forecast.operations.fetch_schedule',side_effect=ValueError('NBA CDN HTTP 403')),\
                 patch('nba_forecast.operations.fetch_verified_secondary_schedule',return_value=verified),\
                 patch('nba_forecast.operations.fetch_roster',side_effect=ValueError('roster offline')) as roster,\
                 patch('nba_forecast.operations.fetch_injury_check',side_effect=ValueError('offline')):
                result=run_tick(d,[])
        # The forecast proceeds to its own preflight, which confirms the game against ESPN.
        roster.assert_called_once()
        self.assertEqual(result['schedule_unverified_games'],['0022600001'])
        self.assertNotIn('schedule_crosscheck',[e.get('task') for e in result['errors']])

    def test_both_schedule_sources_unavailable_use_the_saved_calendar(self):
        source=normalize_season(payload([game(start='2026-10-24T22:00:00Z')]),'2026-10-22T03:00:00Z')
        source['version']='nba-source'
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'schedule'
            folder.mkdir()
            (folder/'latest.json').write_text(json.dumps(source))
            with patch('nba_forecast.operations._now',return_value='2026-10-24T16:00:00Z'),\
                 patch('nba_forecast.operations.fetch_schedule',side_effect=ValueError('NBA CDN HTTP 403')),\
                 patch('nba_forecast.operations.fetch_verified_secondary_schedule',side_effect=ValueError('ESPN unavailable')),\
                 patch('nba_forecast.operations.fetch_roster',side_effect=ValueError('roster offline')) as roster,\
                 patch('nba_forecast.operations.fetch_injury_check',side_effect=ValueError('offline')):
                result=run_tick(d,[])
                state=json.loads((folder/'fetch-state.json').read_text())
        roster.assert_called_once()
        self.assertEqual(result['schedule_source'],'unverified_cached_nba')
        self.assertIn('Using the saved calendar',[w for w in result['warnings'] if w.get('task')=='schedule'][0]['action'])
        self.assertEqual((state['consecutive_failures'],state['next_attempt_at']),(1,'2026-10-24T16:05:00+00:00'))

    def test_unmatched_secondary_event_is_a_visible_warning(self):
        source=normalize_season(payload([game(start='2026-10-26T22:00:00Z')]),'2026-10-22T03:00:00Z')
        source['version']='nba-source'
        verified=deepcopy(source)
        verified['crosscheck']=dict(status='partial',verified_game_ids=[],blocked_game_ids=[],
                                   unmatched_secondary_events=[dict(espn_id='401909853')])
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'schedule'
            folder.mkdir()
            (folder/'latest.json').write_text(json.dumps(source))
            with patch('nba_forecast.operations._now',return_value='2026-10-24T16:00:00Z'),\
                 patch('nba_forecast.operations.fetch_schedule',side_effect=ValueError('NBA CDN HTTP 403')),\
                 patch('nba_forecast.operations.fetch_verified_secondary_schedule',return_value=verified):
                result=run_tick(d,[])
        self.assertEqual(result['status'],'complete')
        self.assertEqual([w['espn_id'] for w in result['warnings'] if w.get('task')=='schedule_crosscheck'],['401909853'])

    def test_tomorrow_jobs_wait_without_fetching_or_raising_attention(self):
        schedule=normalize_season(payload([game(start='2026-10-22T22:00:00Z')]),NOW)
        schedule['version']='v1'
        clock='2026-10-21T23:00:00Z'
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.operations._now',return_value=clock),patch('nba_forecast.operations.fetch_schedule',return_value=schedule),patch('nba_forecast.operations.fetch_roster') as roster:
            result=run_tick(d,[])
        roster.assert_not_called()
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['waiting_jobs'][0]['game_id'],'0022600001')
        self.assertFalse(result['errors'])

    def test_the_tick_never_rebuilds_derived_reports_and_times_its_phases(self):
        schedule=normalize_season(payload([game(start='2026-10-22T22:00:00Z')]),NOW)
        schedule['version']='v1'
        clock='2026-10-21T23:00:00Z'
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.operations._now',return_value=clock),patch('nba_forecast.operations.fetch_schedule',return_value=schedule),patch('nba_forecast.operations.fetch_roster'),\
             patch('nba_forecast.operations._derived_reports',side_effect=AssertionError('the tick rebuilt the derived reports')):
            result=run_tick(d,[])
            self.assertFalse((Path(d)/'derived-reports.json').exists())
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['derived_reports'],dict(status='none',computed_at=None,service='nba-derived'))
        self.assertTrue({'calendar','market_lines','forecasts','evaluate_runs','derived'}<=set(result['timings']))
    def setUp(self):
        self.daily=self.enterContext(patch('nba_forecast.operations.capture_daily',return_value={'state':'waiting'}))
        self.enterContext(patch('nba_forecast.operations.archive_daily_references',return_value=dict(sources=[],failures=[])))
        self.preflight=self.enterContext(patch('nba_forecast.operations.run_preflight',return_value={'status':'passed','issues':[]}))
    def test_duplicate_tick_and_crash_recovery_do_not_duplicate_run(self):
        schedule=normalize_season(payload([game()]),NOW)
        schedule['version']='v1'
        roster=inputs()[0]['roster']
        # Production roster validation is exercised by the ledger suite; this
        # test drives orchestration with explicit identities for all 30 teams.
        roster['team_ids']=[str(t) for t in range(1610612737,1610612767)]
        for p,t in zip(roster['players'],roster['team_ids']):
            p['team_id']=t
            p['status']='unknown'
        roster['observed_at']=NOW
        with tempfile.TemporaryDirectory() as d, patch('nba_forecast.operations.fetch_schedule',return_value=schedule),patch('nba_forecast.operations.fetch_roster',return_value=roster),patch('nba_forecast.operations._now',return_value=NOW),patch('nba_forecast.store._now',return_value=__import__('datetime').datetime.fromisoformat(NOW.replace('Z','+00:00'))),patch('nba_forecast.operations.fetch_injury_check',side_effect=ValueError('offline')):
            first=run_tick(d,[])
            self.assertEqual(first['created_runs'],1)
            (Path(d)/'state.json').unlink()
            import shutil
            shutil.rmtree(Path(d)/'runs')
            second=run_tick(d,[])
            self.assertEqual(second['created_runs'],0)
            self.assertEqual(second['recovered_runs'],1)
            self.assertEqual(len(list((Path(d)/'runs').glob('*/profiles.json'))),1)
    def test_lock_refuses_overlapping_tick(self):
        with tempfile.TemporaryDirectory() as d:
            import fcntl
            with (Path(d)/'tick.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(ValueError):
                    run_tick(d,[])
    def test_missing_schedule_is_visible_error(self):
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.operations.fetch_schedule',side_effect=ValueError('unavailable')):
            r=run_tick(d,[])
            self.assertEqual(r['status'],'error')
            self.assertTrue(r['errors'])
            self.assertEqual(run_tick(d,[])['status'],'error')
            self.assertEqual(self.daily.call_count,2,'daily collection must not depend on schedule access')

    def test_review_evidence_is_game_specific_and_cannot_replace_newer_status(self):
        from nba_forecast.operations import apply_review
        snapshot,_,_=inputs()
        p=snapshot['roster']['players'][0]
        update=dict(player_id=p['player_id'],status='out',status_observed_at='2026-10-22T09:00:00Z',status_source_url='https://example.test/status')
        with self.assertRaises(ValueError):
            apply_review(snapshot,{'game_id':'G1','availability':[update]})
        update['status_observed_at']='2026-10-22T10:30:00Z'
        result=apply_review(snapshot,{'game_id':'G1','availability':[update]})
        self.assertEqual(result['roster']['players'][0]['status'],'out')
        with self.assertRaises(ValueError):
            apply_review(snapshot,{'game_id':'G2','availability':[update]})

    def test_process_death_releases_lock(self):
        import subprocess,sys
        with tempfile.TemporaryDirectory() as d:
            subprocess.run([sys.executable,'-c',"import fcntl,os,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); os._exit(0)",str(Path(d)/'tick.lock')],check=True)
            with patch('nba_forecast.operations.fetch_schedule',side_effect=ValueError('offline')):
                self.assertEqual(run_tick(d,[])['status'],'error')

    def test_missing_team_issue_is_logged_without_aborting_forecast(self):
        schedule=normalize_season(payload([game()]),NOW)
        schedule['version']='v1'
        roster=inputs()[0]['roster']
        roster['team_ids']=[str(t) for t in range(1610612737,1610612767)]
        for p,t in zip(roster['players'],roster['team_ids']):
            p['team_id']=t
            p['status']='unknown'
        roster['observed_at']=NOW
        evidence=dict(published_at=NOW,retrieved_at=NOW,source_url='https://example.test/report',sha256='a'*64)
        check={'checked_at':NOW,'games':[{'game_id':'0022600001','start_time':schedule['games'][0]['start_time'],
                'phase':'preliminary','report_status':'captured_pending_review','latest_report':evidence}]}
        parsed={'rows':[],'team_coverage':[],'evidence':evidence,'issues':[{'kind':'missing_team_coverage','game_id':'0022600001'}]}
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.operations.fetch_schedule',return_value=schedule),patch('nba_forecast.operations.fetch_roster',return_value=roster),patch('nba_forecast.operations._now',return_value=NOW),patch('nba_forecast.store._now',return_value=__import__('datetime').datetime.fromisoformat(NOW.replace('Z','+00:00'))),patch('nba_forecast.operations.fetch_injury_check',return_value=check),patch('nba_forecast.operations.pdf_text',return_value='text'),patch('nba_forecast.operations.parse_report',return_value=parsed):
            result=run_tick(d,[])
            self.assertEqual(result['created_runs'],1,result['errors'])
            self.assertTrue(result['warnings'])

    def test_review_newer_than_publication_can_override_later_download(self):
        from nba_forecast.operations import apply_review
        snapshot,_,_=inputs()
        p=snapshot['roster']['players'][0]
        p['status_published_at']='2026-10-22T09:00:00Z'
        update=dict(player_id=p['player_id'],status='out',status_observed_at='2026-10-22T09:30:00Z',status_source_url='https://example.test/status')
        self.assertEqual(apply_review(snapshot,{'game_id':'G1','availability':[update]})['roster']['players'][0]['status'],'out')

    def test_superseded_policy_still_rejects_invalid_review_evidence(self):
        from nba_forecast.operations import apply_review
        snapshot,_,_=inputs()
        pid=snapshot['roster']['players'][0]['player_id']
        original=deepcopy(snapshot)
        update=dict(player_id=pid,status='out',status_observed_at='2026-10-22T09:30:00Z',
                    status_published_at='2026-10-22T09:00:00Z',status_source_url='https://example.test/status')
        for change in (dict(status_observed_at='2026-10-22T12:00:00Z'),
                       dict(status_observed_at='2026-10-22T08:30:00Z'),
                       dict(status='healthy'),dict(status_source_url='http://example.test/status'),
                       dict(player_id='missing')):
            review=dict(game_id='G1',availability=[{**update,**change}])
            with self.subTest(change=change),self.assertRaises(ValueError):
                apply_review(snapshot,review,skip_superseded=True)
        with self.assertRaises(ValueError):
            apply_review(snapshot,dict(game_id='G1',availability=[update,update]),skip_superseded=True)
        self.assertEqual(snapshot,original)

    def test_expired_override_can_be_superseded_without_expiring_fresh_official_status(self):
        from nba_forecast.operations import apply_review
        snapshot,_,_=inputs()
        pid=snapshot['roster']['players'][0]['player_id']
        update=dict(player_id=pid,status='out',status_observed_at='2026-10-20T09:00:00Z',
                    status_source_url='https://example.test/status')
        result=apply_review(snapshot,dict(game_id='G1',availability=[update]),skip_superseded=True)
        self.assertEqual(result['roster']['players'][0]['status'],'available')
        self.assertEqual(result['review_warnings'][0]['player_id'],pid)


class EvidenceTickTests(unittest.TestCase):
    def test_unreplayed_ready_margin_never_activates(self):
        from nba_forecast.enhancements import install_artifact
        from nba_forecast.refinement_upgrade import build_artifact
        base=dict(model_version='regular-season-baseline-0.5.0',model_key='base',
            known_at='2026-10-20T00:00:00Z',parameters={},team_games=[],
            context_model=None,calibration=None,excluded_history_game_ids=[])
        refined=build_artifact(base,'a'*64,'2026-10-21T15:00:00Z')
        old=install_artifact(self.folder,refined)
        pointer=self.folder/'model-upgrade-active.json'
        pointer.write_text(json.dumps(dict(artifact_sha256=old,activated_at=refined['known_at'])))
        margin=dict(status='ready',evidence_sha256='b'*64,model_key=refined['model_key'],
            collection_errors=[])
        player=dict(status='not_ready',reason='insufficient_games',sample_count=0,
            game_count=0,collection_errors=[])
        with patch('nba_forecast.prospective_calibration.fit_prospective',return_value=player),\
             patch('nba_forecast.prospective_margin_calibration.fit_prospective_margin',return_value=margin),\
             patch('nba_forecast.prospective_margin_calibration.verify_margin_calibration_sources',return_value=False),\
             patch('nba_forecast.outcome_sync.sync_finished_games',return_value={'errors':[]}):
            run_tick(self.folder,[])
            report=run_derived(self.folder)
        self.assertEqual(report['status'],'rebuilt')
        self.assertEqual(json.loads(pointer.read_text())['artifact_sha256'],old)
        self.assertTrue(any(e.get('task')=='prospective_margin_calibration' for e in report['errors']))

    def test_ready_margin_correction_is_pinned_only_for_future_forecasts(self):
        from nba_forecast.enhancements import install_artifact
        from nba_forecast.refinement_upgrade import build_artifact
        from nba_forecast.prospective_margin_calibration import METHOD as CORRECTION_METHOD,_fit_window
        from tests.test_prospective_margin_calibration import sample as margin_sample
        base=dict(model_version='regular-season-baseline-0.5.0',model_key='base',known_at='2026-10-20T00:00:00Z',
            parameters={},team_games=[],context_model=None,calibration=None,excluded_history_game_ids=[])
        refined=build_artifact(base,'a'*64,'2026-10-21T15:00:00Z')
        old=install_artifact(self.folder,refined)
        pointer=self.folder/'model-upgrade-active.json'
        pointer.write_text(json.dumps(dict(artifact_sha256=old,activated_at=refined['known_at'])))
        rows=[]
        for i in range(112):
            sample=margin_sample(i)
            sample['model_key']=refined['model_key']
            for field in ('game_time','forecast_created_at','actual_known_at'):
                sample[field]=(datetime.fromisoformat(sample[field])-timedelta(days=61)).isoformat()
            rows.append(sample)
        cutoff='2026-10-21T15:30:00Z'
        margin=dict(schema_version=1,method=CORRECTION_METHOD,model_key=refined['model_key'],
            window_index=0,training_cutoff=cutoff,evidence_samples=rows,
            **_fit_window(rows,refined['model_key'],cutoff),collection_errors=[])
        self.assertEqual(margin['status'],'ready')
        player=dict(status='not_ready',reason='insufficient_games',sample_count=0,game_count=0,collection_errors=[])
        with patch('nba_forecast.prospective_calibration.fit_prospective',return_value=player),\
             patch('nba_forecast.prospective_margin_calibration.fit_prospective_margin',return_value=margin),\
             patch('nba_forecast.prospective_margin_calibration.verify_margin_calibration_sources',return_value=True),\
             patch('nba_forecast.outcome_sync.sync_finished_games',return_value={'errors':[]}):
            result=run_tick(self.folder,[])
            self.assertFalse(result['errors'],result['errors'])
            self.assertEqual(json.loads(pointer.read_text())['artifact_sha256'],old,'the tick itself activates nothing')
            result=run_derived(self.folder)
            self.assertFalse(result['errors'],result['errors'])
            updated=json.loads(pointer.read_text())['artifact_sha256']
            self.assertNotEqual(updated,old)
            archive=json.loads((self.folder/'model-artifacts'/(updated+'.json')).read_text())
            self.assertEqual(archive['margin_calibration']['evidence_sha256'],margin['evidence_sha256'])
            status=json.loads((self.folder/'model-status.json').read_text())
            self.assertEqual(status['applied_margin_calibration']['evidence_sha256'],margin['evidence_sha256'])
            margin['status']='not_ready'
            second=run_tick(self.folder,[])
            self.assertFalse(second['errors'],second['errors'])
            request_derived(self.folder,'test')
            second=run_derived(self.folder)
            self.assertFalse(second['errors'],second['errors'])
            self.assertEqual(json.loads(pointer.read_text())['artifact_sha256'],updated)
            status=json.loads((self.folder/'model-status.json').read_text())
            self.assertEqual(status['applied_margin_calibration']['status'],'ready')
            self.assertEqual(status['prospective_margin']['status'],'not_ready')

    def setUp(self):
        self.enterContext(patch('nba_forecast.operations.archive_daily_references',return_value=dict(sources=[],failures=[])))
        self.enterContext(patch('nba_forecast.operations.capture_daily',return_value={'state':'captured'}))
        self.enterContext(patch('nba_forecast.operations.run_preflight',return_value={'status':'passed','issues':[]}))
        self.folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.schedule=normalize_season(payload([game()]),NOW)
        self.schedule['version']='v1'
        (self.folder/'schedule').mkdir()
        (self.folder/'schedule/latest.json').write_text(json.dumps(self.schedule))  # what the derived run reads
        self.roster=inputs()[0]['roster']
        self.roster['team_ids']=[str(t) for t in range(1610612737,1610612767)]
        for player,team in zip(self.roster['players'],self.roster['team_ids']):
            player['team_id']=team
            player['status']='unknown'
        self.roster['observed_at']=NOW
        self.pid=next(p['player_id'] for p in self.roster['players'] if p['team_id']=='1610612738')
        for name,value in (('fetch_schedule',self.schedule),('fetch_roster',self.roster),('_now',NOW)):
            self.enterContext(patch('nba_forecast.operations.'+name,return_value=value))
        self.enterContext(patch('nba_forecast.store._now',return_value=datetime.fromisoformat(NOW.replace('Z','+00:00'))))
        self.enterContext(patch('nba_forecast.player_sync.sync_player_data',return_value={'errors':[]}))
        self.enterContext(patch('nba_forecast.postgame.build_postgames',return_value={'errors':[]}))
        self.enterContext(patch('nba_forecast.postgame.collect_sources',return_value={'errors':[]}))

    def test_blocked_preflight_prevents_model_execution_and_ledger_save(self):
        with patch('nba_forecast.operations.fetch_injury_check',side_effect=ValueError('offline')),patch('nba_forecast.operations.run_preflight',return_value={
            'status':'blocked','issues':[{'code':'injury_coverage_missing','detail':'Missing team report'}]}),patch('nba_forecast.operations.forecast') as model:
            result=run_tick(self.folder,[])
        model.assert_not_called()
        self.assertEqual(result['created_runs'],0)
        self.assertTrue(any(e['task']=='preflight' for e in result['errors']))

    def test_active_artifact_is_saved_with_forecast_and_status_without_refitting_on_same_evidence(self):
        from nba_forecast.enhancements import install_artifact
        artifact=dict(model_version='regular-season-baseline-0.5.0',model_key='test',known_at='2026-10-21T15:00:00Z',
            parameters={},team_games=[],context_model=None,calibration=None,excluded_history_game_ids=[],validation_report={'scope':'test'})
        digest=install_artifact(self.folder,artifact)
        pointer=self.folder/'model-upgrade-active.json'
        pointer.write_text(json.dumps(dict(artifact_sha256=digest,activated_at=artifact['known_at'])))
        assessment=dict(status='ready',model_key='test',training_cutoff=NOW,evidence_kind='prospective_saved_forecast',
            residual_evidence_sha256='evidence',sample_count=200,game_count=20,errors=[])
        with patch('nba_forecast.operations.fetch_injury_check',side_effect=ValueError('offline')),patch('nba_forecast.outcome_sync.sync_finished_games',return_value={'errors':[]}),patch('nba_forecast.prospective_calibration.fit_prospective',return_value=assessment):
            first=run_tick(self.folder,[])
            self.assertEqual(first['created_runs'],1,first['errors'])
            saved=self.saved()[0]
            self.assertEqual(saved['model_enhancements']['artifact_sha256'],digest)
            derived=run_derived(self.folder)
            self.assertEqual(derived['status'],'rebuilt',derived['errors'])
            upgraded=pointer.read_bytes()
            status=json.loads((self.folder/'model-status.json').read_text())
            self.assertNotEqual(status['artifact_sha256'],digest)
            self.assertEqual(status['validation'],artifact['validation_report'])
            second=run_tick(self.folder,[])
            self.assertEqual(second['created_runs'],1,second['errors'])
            self.assertEqual(run_derived(self.folder)['status'],'rebuilt','the ledger changed')
            self.assertEqual(pointer.read_bytes(),upgraded,'the same evidence is not refit')
            third=run_tick(self.folder,[])
            self.assertEqual(third['created_runs'],0,third['errors'])

    def saved(self):
        with sqlite3.connect(self.folder/'forecast.sqlite') as conn:
            snapshot,history=conn.execute('SELECT snapshot_json,history_json FROM runs ORDER BY rowid DESC LIMIT 1').fetchone()
        return json.loads(snapshot),json.loads(history)

    def assert_history_choice(self,imported_time,cached_time,expected_minutes):
        imported=row(pid=self.pid,team='1610612738',opponent_id='1610612752',
                     season='2025-26',game_id='0022500001',game_time='2026-03-01T20:00:00Z',
                     known_at=imported_time,minutes=30)
        cached=dict(imported,known_at=cached_time,minutes=10)
        cache=self.folder/'finals'
        cache.mkdir()
        (cache/'0022500001.json').write_text(json.dumps([cached]))
        with patch('nba_forecast.operations.fetch_injury_check',side_effect=ValueError('offline')):
            result=run_tick(self.folder,[imported])
        self.assertEqual(result['created_runs'],1,result['errors'])
        saved=self.saved()[1][0]
        self.assertEqual(saved['minutes'],expected_minutes)
        self.assertEqual(saved['known_at'],imported_time if expected_minutes==30 else cached_time)

    def test_newer_imported_correction_survives_older_final_cache(self):
        self.assert_history_choice('2026-10-21T11:00:00Z','2026-10-21T10:00:00Z',30)

    def test_newer_cached_correction_survives_older_import(self):
        self.assert_history_choice('2026-10-21T10:00:00Z','2026-10-21T11:00:00Z',10)

    def test_equal_instants_keep_deterministic_cache_precedence(self):
        self.assert_history_choice('2026-10-21T11:00:00Z','2026-10-21T07:00:00-04:00',10)

    def test_tick_skips_superseded_availability_but_retains_other_review_inputs(self):
        evidence=dict(published_at='2026-10-21T11:30:00Z',retrieved_at='2026-10-21T11:31:00Z',
                      source_url='https://example.test/report',sha256='a'*64)
        check={'checked_at':NOW,'games':[{'game_id':'0022600001','start_time':self.schedule['games'][0]['start_time'],
                'phase':'preliminary','report_status':'captured_pending_review','latest_report':evidence}]}
        parsed={'rows':[dict(player_id=self.pid,team_id='1610612738',game_id='0022600001',status='out',reported_status='Out')],
                'team_coverage':[],'evidence':evidence,'issues':[]}
        other=next(p['player_id'] for p in self.roster['players'] if p['team_id']=='1610612752')
        overrides=[dict(player_id=pid,status='available',status_observed_at='2026-10-21T10:00:00Z',
                        status_source_url='https://example.test/review') for pid in (self.pid,other)]
        adjustment=dict(player_id=other,game_id='0022600001',observed_at='2026-10-21T10:00:00Z',
                        source_url='https://example.test/role',reason='Reviewed restriction',reviewer='analyst',
                        minutes_delta=0,opportunity_multiplier=1,minutes_cap=15)
        review=dict(game_id='0022600001',availability=overrides,role_adjustments=[adjustment])
        review_path=self.folder/'reviews/0022600001.json'
        review_path.parent.mkdir()
        review_path.write_text(json.dumps(review))
        before=deepcopy(review)
        with patch('nba_forecast.operations.fetch_injury_check',return_value=check),patch('nba_forecast.operations.pdf_text',return_value='text'),patch('nba_forecast.operations.parse_report',return_value=parsed):
            result=run_tick(self.folder,[])
        self.assertEqual(result['created_runs'],1,result['errors'])
        self.assertFalse(result['errors'])
        snapshot,_=self.saved()
        players={p['player_id']:p for p in snapshot['roster']['players']}
        self.assertEqual(players[self.pid]['status'],'out')
        self.assertEqual(players[self.pid]['status_source_url'],evidence['source_url'])
        self.assertEqual(players[other]['status'],'available')
        self.assertEqual(snapshot['role_adjustments'],[adjustment])
        self.assertEqual(snapshot['review_input'],before)
        self.assertEqual(json.loads(review_path.read_text()),before)
        self.assertTrue(any(w.get('kind')=='superseded_review_availability' and w.get('player_id')==self.pid for w in result['warnings']))


class ActivationTests(unittest.TestCase):
    def test_a_refit_does_not_move_an_activation_a_deploy_changed(self):
        import json,tempfile
        from pathlib import Path
        from nba_forecast.operations import _activate
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            pointer=folder/'model-upgrade-active.json'
            pointer.write_text(json.dumps(dict(artifact_sha256='a'*64,activated_at='2026-10-27T03:00:00Z')))
            report=dict(warnings=[])
            self.assertTrue(_activate(folder,'a'*64,'b'*64,report))
            self.assertEqual(json.loads(pointer.read_text())['artifact_sha256'],'b'*64)
            # A deploy moved the pointer meanwhile: the tick's refit of its old artifact stays installed, not active.
            pointer.write_text(json.dumps(dict(artifact_sha256='d'*64,activated_at='2026-10-27T03:01:00Z')))
            self.assertFalse(_activate(folder,'b'*64,'c'*64,report))
            self.assertEqual(json.loads(pointer.read_text())['artifact_sha256'],'d'*64)
            self.assertEqual(report['warnings'][0]['task'],'activation')
