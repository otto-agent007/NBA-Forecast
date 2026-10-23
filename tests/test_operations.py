import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from nba_forecast.operations import run_tick
from nba_forecast.schedule import normalize_season
from tests.test_schedule import payload,game,NOW
from tests.test_store import inputs
from tests.test_model import row

class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.daily=self.enterContext(patch('nba_forecast.operations.capture_daily',return_value={'state':'waiting'}))
    def test_duplicate_tick_and_crash_recovery_do_not_duplicate_run(self):
        schedule=normalize_season(payload([game()]),NOW);schedule['version']='v1'
        roster=inputs()[0]['roster']
        # Production roster validation is exercised by the ledger suite; this
        # test drives orchestration with explicit identities for all 30 teams.
        roster['team_ids']=[str(t) for t in range(1610612737,1610612767)]
        for p,t in zip(roster['players'],roster['team_ids']):p['team_id']=t;p['status']='unknown'
        roster['observed_at']=NOW
        with tempfile.TemporaryDirectory() as d, patch('nba_forecast.operations.fetch_schedule',return_value=schedule),patch('nba_forecast.operations.fetch_roster',return_value=roster),patch('nba_forecast.operations._now',return_value=NOW),patch('nba_forecast.store._now',return_value=__import__('datetime').datetime.fromisoformat(NOW.replace('Z','+00:00'))),patch('nba_forecast.operations.fetch_injury_check',side_effect=ValueError('offline')):
            first=run_tick(d,[]);self.assertEqual(first['created_runs'],1)
            (Path(d)/'state.json').unlink()
            import shutil
            shutil.rmtree(Path(d)/'runs')
            second=run_tick(d,[]);self.assertEqual(second['created_runs'],0)
            self.assertEqual(second['recovered_runs'],1)
            self.assertEqual(len(list((Path(d)/'runs').glob('*/profiles.json'))),1)
    def test_lock_refuses_overlapping_tick(self):
        with tempfile.TemporaryDirectory() as d:
            import fcntl
            with (Path(d)/'tick.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(ValueError):run_tick(d,[])
    def test_missing_schedule_is_visible_error(self):
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.operations.fetch_schedule',side_effect=ValueError('unavailable')):
            r=run_tick(d,[])
            self.assertEqual(r['status'],'error');self.assertTrue(r['errors'])
            self.assertEqual(run_tick(d,[])['status'],'error')
            self.assertEqual(self.daily.call_count,2,'daily collection must not depend on schedule access')

    def test_review_evidence_is_game_specific_and_cannot_replace_newer_status(self):
        from nba_forecast.operations import apply_review
        snapshot,_,_=inputs()
        p=snapshot['roster']['players'][0]
        update=dict(player_id=p['player_id'],status='out',status_observed_at='2026-10-22T09:00:00Z',status_source_url='https://example.test/status')
        with self.assertRaises(ValueError):apply_review(snapshot,{'game_id':'G1','availability':[update]})
        update['status_observed_at']='2026-10-22T10:30:00Z'
        result=apply_review(snapshot,{'game_id':'G1','availability':[update]})
        self.assertEqual(result['roster']['players'][0]['status'],'out')
        with self.assertRaises(ValueError):apply_review(snapshot,{'game_id':'G2','availability':[update]})

    def test_process_death_releases_lock(self):
        import subprocess,sys
        with tempfile.TemporaryDirectory() as d:
            subprocess.run([sys.executable,'-c',"import fcntl,os,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); os._exit(0)",str(Path(d)/'tick.lock')],check=True)
            with patch('nba_forecast.operations.fetch_schedule',side_effect=ValueError('offline')):
                self.assertEqual(run_tick(d,[])['status'],'error')

    def test_missing_team_issue_is_logged_without_aborting_forecast(self):
        schedule=normalize_season(payload([game()]),NOW);schedule['version']='v1'
        roster=inputs()[0]['roster'];roster['team_ids']=[str(t) for t in range(1610612737,1610612767)]
        for p,t in zip(roster['players'],roster['team_ids']):p['team_id']=t;p['status']='unknown'
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
        snapshot,_,_=inputs();p=snapshot['roster']['players'][0]
        p['status_published_at']='2026-10-22T09:00:00Z'
        update=dict(player_id=p['player_id'],status='out',status_observed_at='2026-10-22T09:30:00Z',status_source_url='https://example.test/status')
        self.assertEqual(apply_review(snapshot,{'game_id':'G1','availability':[update]})['roster']['players'][0]['status'],'out')

    def test_superseded_policy_still_rejects_invalid_review_evidence(self):
        from nba_forecast.operations import apply_review
        snapshot,_,_=inputs();pid=snapshot['roster']['players'][0]['player_id']
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
        snapshot,_,_=inputs();pid=snapshot['roster']['players'][0]['player_id']
        update=dict(player_id=pid,status='out',status_observed_at='2026-10-20T09:00:00Z',
                    status_source_url='https://example.test/status')
        result=apply_review(snapshot,dict(game_id='G1',availability=[update]),skip_superseded=True)
        self.assertEqual(result['roster']['players'][0]['status'],'available')
        self.assertEqual(result['review_warnings'][0]['player_id'],pid)


class EvidenceTickTests(unittest.TestCase):
    def setUp(self):
        self.folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.schedule=normalize_season(payload([game()]),NOW)
        self.schedule['version']='v1'
        self.roster=inputs()[0]['roster']
        self.roster['team_ids']=[str(t) for t in range(1610612737,1610612767)]
        for player,team in zip(self.roster['players'],self.roster['team_ids']):
            player['team_id']=team;player['status']='unknown'
        self.roster['observed_at']=NOW
        self.pid=next(p['player_id'] for p in self.roster['players'] if p['team_id']=='1610612738')
        for name,value in (('fetch_schedule',self.schedule),('fetch_roster',self.roster),('_now',NOW)):
            self.enterContext(patch('nba_forecast.operations.'+name,return_value=value))
        self.enterContext(patch('nba_forecast.store._now',return_value=datetime.fromisoformat(NOW.replace('Z','+00:00'))))
        self.enterContext(patch('nba_forecast.player_sync.sync_player_data',return_value={'errors':[]}))
        self.enterContext(patch('nba_forecast.postgame.build_postgames',return_value={'errors':[]}))

    def saved(self):
        with sqlite3.connect(self.folder/'forecast.sqlite') as conn:
            snapshot,history=conn.execute('SELECT snapshot_json,history_json FROM runs ORDER BY rowid DESC LIMIT 1').fetchone()
        return json.loads(snapshot),json.loads(history)

    def assert_history_choice(self,imported_time,cached_time,expected_minutes):
        imported=row(pid=self.pid,team='1610612738',opponent_id='1610612752',
                     season='2025-26',game_id='0022500001',game_time='2026-03-01T20:00:00Z',
                     known_at=imported_time,minutes=30)
        cached=dict(imported,known_at=cached_time,minutes=10)
        cache=self.folder/'finals';cache.mkdir()
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
        review_path=self.folder/'reviews/0022600001.json';review_path.parent.mkdir()
        review_path.write_text(json.dumps(review))
        before=deepcopy(review)
        with patch('nba_forecast.operations.fetch_injury_check',return_value=check),patch('nba_forecast.operations.pdf_text',return_value='text'),patch('nba_forecast.operations.parse_report',return_value=parsed):
            result=run_tick(self.folder,[])
        self.assertEqual(result['created_runs'],1,result['errors'])
        self.assertFalse(result['errors'])
        snapshot,_=self.saved();players={p['player_id']:p for p in snapshot['roster']['players']}
        self.assertEqual(players[self.pid]['status'],'out')
        self.assertEqual(players[self.pid]['status_source_url'],evidence['source_url'])
        self.assertEqual(players[other]['status'],'available')
        self.assertEqual(snapshot['role_adjustments'],[adjustment])
        self.assertEqual(snapshot['review_input'],before)
        self.assertEqual(json.loads(review_path.read_text()),before)
        self.assertTrue(any(w.get('kind')=='superseded_review_availability' and w.get('player_id')==self.pid for w in result['warnings']))
