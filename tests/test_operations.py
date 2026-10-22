import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from nba_forecast.operations import run_tick
from nba_forecast.schedule import normalize_season
from tests.test_schedule import payload,game,NOW
from tests.test_store import inputs

class OperationsTests(unittest.TestCase):
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
        check={'games':[{'latest_report':evidence}]}
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
