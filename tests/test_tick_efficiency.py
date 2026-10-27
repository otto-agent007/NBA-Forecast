"""Idle ticks and dashboard polls reuse work whose inputs have not changed."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from nba_forecast import dashboard,operations,store
from nba_forecast.benchmarks import benchmark_report
from nba_forecast.schedule import normalize_season
from tests.test_operations import NOW
from tests.test_schedule import payload,game
from tests.test_store import inputs,actual,NOW as SAVED,LATER


def at(clock,seconds=0):
    return (datetime.fromisoformat(clock.replace('Z','+00:00'))+timedelta(seconds=seconds)).isoformat()


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db=self.folder/'forecast.sqlite'
        snapshot,past,forecast=inputs()
        with patch('nba_forecast.store._now',return_value=SAVED):
            self.run_id=store.save_run(self.db,snapshot,past,forecast)
        self.enterContext(patch('nba_forecast.store._now',return_value=LATER))

    def evaluate(self):
        report=dict(evaluated_runs=0,errors=[])
        operations._evaluate_runs(self.folder,self.db,report)
        return report

    def finals(self,rows):
        (self.folder/'finals').mkdir(exist_ok=True)
        (self.folder/'finals/G1.json').write_text(json.dumps(rows))

    def revisions(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute('SELECT COUNT(*) FROM evaluations').fetchone()[0]

    def test_runs_are_rescored_only_when_their_finals_change(self):
        self.assertEqual(self.evaluate()['evaluated_runs'],0)
        self.finals([actual(),actual('P2')])
        self.assertEqual(self.evaluate()['evaluated_runs'],1)
        unchanged=self.evaluate()
        self.assertEqual((unchanged['evaluated_runs'],unchanged['unchanged_evaluation_runs']),(0,1))
        self.finals([dict(actual(),known_at='2026-10-22T22:00:00Z',pts=26,fgm=11),actual('P2')])
        self.assertEqual(self.evaluate()['evaluated_runs'],1)
        self.assertEqual(self.revisions(),2)

    def test_a_revision_written_elsewhere_rescores_the_run(self):
        self.finals([actual()])
        self.evaluate()
        store.evaluate_run(self.db,self.run_id,[actual('P2')])
        self.assertEqual(self.evaluate()['evaluated_runs'],1)

    def test_failed_scoring_is_retried(self):
        self.finals([dict(actual(),opponent_id='T9')])
        self.assertEqual(len(self.evaluate()['errors']),1)
        self.assertEqual(len(self.evaluate()['errors']),1)


class DerivedReportTests(unittest.TestCase):
    def setUp(self):
        self.folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.schedule=dict(season='2026-27',games=[dict(game_id='G1',start_time='2026-10-22T18:00:00Z')])
        self.builds=0
        self.clock='2026-10-22T20:00:00+00:00'

    def build(self,folder,db,schedule,active_model,model_digest,report):
        self.builds+=1
        for name in ('performance.json','benchmarks.json','daily-reviews/index.json','market/report.json'):
            (folder/name).parent.mkdir(parents=True,exist_ok=True)
            (folder/name).write_text('{}')
        report['daily_review']=dict(build=self.builds)
        report['errors'].append(dict(task='daily_review',date='x'))

    def refresh(self,**extra):
        report=dict(errors=[],warnings=[],**extra)
        with patch('nba_forecast.operations._derived_reports',side_effect=self.build),\
             patch('nba_forecast.operations._now',return_value=self.clock):
            operations._refresh_derived(self.folder,self.folder/'forecast.sqlite',self.schedule,None,None,report)
        return report

    def test_unchanged_inputs_reuse_reports_errors_and_summaries(self):
        first=self.refresh()
        second=self.refresh()
        self.assertEqual(self.builds,1)
        self.assertEqual(second['derived_reports']['status'],'unchanged')
        self.assertEqual(second['daily_review'],first['daily_review'])
        self.assertEqual(second['errors'],first['errors'])

    def test_new_evidence_missing_output_age_or_recovery_rebuilds(self):
        self.refresh()
        (self.folder/'finals').mkdir()
        (self.folder/'finals/G1.json').write_text('[]')
        self.refresh()
        (self.folder/'performance.json').unlink()
        self.refresh()
        self.clock=at(self.clock,operations.DERIVED_REFRESH_SECONDS+1)
        self.refresh()
        self.refresh(recovered_artifacts=1)
        self.schedule['games'][0]['start_time']='2026-10-22T19:00:00Z'
        self.refresh()
        self.assertEqual(self.builds,6)
        self.refresh()
        self.assertEqual(self.builds,6)

    def test_only_game_archives_and_changed_finals_bytes_rebuild(self):
        raw=self.folder/'raw'
        raw.mkdir()
        (self.folder/'finals').mkdir()
        (self.folder/'finals/G1.json').write_text('[]')
        self.refresh()
        (raw/'a.provenance.json').write_text(json.dumps(dict(source_url='https://www.nba.com/team/1/roster')))
        (self.folder/'finals/G1.json').write_text('[]')
        self.refresh()
        self.assertEqual(self.builds,1)
        (raw/'b.provenance.json').write_text(json.dumps(dict(
            source_url='https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_0022600001.json')))
        self.refresh()
        self.assertEqual(self.builds,2)

    def test_cross_check_metadata_alone_does_not_rebuild(self):
        self.refresh()
        self.schedule['games'][0]['schedule_corroboration']=dict(observed_at=self.clock)
        self.refresh()
        self.assertEqual(self.builds,1)


class DailyCalendarTests(unittest.TestCase):
    def setUp(self):
        self.folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.folder/'schedule/secondary').mkdir(parents=True)
        (self.folder/'schedule/latest.json').write_text(json.dumps(dict(version='saved',season='2026-27',games=[])))
        self.verified=dict(version='espn',season='2026-27',games=[],crosscheck=dict(nba_base_version='saved',blocked_game_ids=[]))

    def calendar(self,clock,nba=None,espn=None):
        """clock is Pacific wall time; returns (schedule version, NBA calls, ESPN calls, report)."""
        report=dict(warnings=[])
        when=datetime.fromisoformat(clock).replace(tzinfo=operations.PACIFIC).astimezone(timezone.utc).isoformat()
        def secondary(*args):
            if isinstance(espn,Exception):
                raise espn
            (self.folder/'schedule/secondary/verified-latest.json').write_text(json.dumps(self.verified))
            return self.verified
        with patch('nba_forecast.operations._now',return_value=when),patch('nba_forecast.operations.archive_daily_references',return_value=dict(sources=[],failures=[])),\
             patch('nba_forecast.operations.fetch_schedule',side_effect=[nba or dict(version='fresh',season='2026-27',games=[])]) as fetch,\
             patch('nba_forecast.operations.fetch_verified_secondary_schedule',side_effect=secondary) as check:
            value=operations._calendar(self.folder,'2026-27',report)
        return value['version'],fetch.call_count,check.call_count,report

    def test_checked_once_a_day_after_eight(self):
        self.assertEqual(self.calendar('2026-10-24T09:00:00')[:3],('fresh',1,0))
        self.assertEqual(self.calendar('2026-10-24T23:00:00')[:3],('saved',0,0))
        self.assertEqual(self.calendar('2026-10-25T07:59:00')[3]['schedule_source'],'saved_nba')
        self.assertEqual(self.calendar('2026-10-25T08:00:00')[:3],('fresh',1,0))

    def test_espn_verifies_the_saved_calendar_when_nba_fails_and_holds_for_the_day(self):
        self.assertEqual(self.calendar('2026-10-24T09:00:00',nba=ValueError('HTTP 403'))[:3],('espn',1,1))
        version,nba,espn,report=self.calendar('2026-10-24T18:00:00')
        self.assertEqual((version,nba,espn,report['schedule_source']),('espn',0,0,'espn_verified_cached_nba'))

    def test_failed_check_uses_the_saved_calendar_and_backs_off(self):
        down=dict(nba=ValueError('HTTP 403'),espn=ValueError('ESPN down'))
        version,nba,espn,report=self.calendar('2026-10-24T09:00:00',**down)
        self.assertEqual((version,nba,espn,report['schedule_source']),('saved',1,1,'unverified_cached_nba'))
        self.assertIn('Using the saved calendar',report['warnings'][0]['action'])
        self.assertEqual(self.calendar('2026-10-24T09:04:00',**down)[1],0)
        self.assertEqual(self.calendar('2026-10-24T09:05:00',**down)[1],1)
        self.assertEqual(self.calendar('2026-10-24T09:14:00',**down)[1],0)
        self.assertEqual(self.calendar('2026-10-24T09:15:00')[:2],('fresh',1))
        self.assertEqual(self.calendar('2026-10-24T09:16:00')[:2],('saved',0))

    def test_backoff_is_capped(self):
        state=dict(consecutive_failures=20)
        (self.folder/'schedule/fetch-state.json').write_text(json.dumps(state))
        self.calendar('2026-10-24T09:00:00',nba=ValueError('403'),espn=ValueError('down'))
        saved=json.loads((self.folder/'schedule/fetch-state.json').read_text())
        self.assertEqual(saved['next_attempt_at'],at('2026-10-24T16:00:00+00:00',operations.CALENDAR_RETRY_SECONDS[1]))

    def test_a_requested_check_runs_on_the_next_tick(self):
        self.calendar('2026-10-24T09:00:00')
        operations.request_calendar_check(self.folder,'tipoff_changed:G1')
        self.assertEqual(self.calendar('2026-10-24T10:00:00')[:2],('fresh',1))
        self.assertEqual(self.calendar('2026-10-24T10:01:00')[:2],('saved',0))


class FailedJobTests(unittest.TestCase):
    def test_a_failed_job_waits_for_its_next_slot_and_a_moved_tipoff_requests_a_check(self):
        from tests.test_store import inputs
        schedule=normalize_season(payload([game()]),NOW)
        schedule['version']='v1'
        roster=inputs()[0]['roster']
        roster['team_ids']=[str(t) for t in range(1610612737,1610612767)]
        for p,t in zip(roster['players'],roster['team_ids']):
            p['team_id']=t
            p['status']='unknown'
        roster['observed_at']=NOW
        blocked=dict(status='blocked',issues=[dict(code='tipoff_changed',detail='moved',team_id=None)])
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.operations.fetch_schedule',return_value=schedule),\
             patch('nba_forecast.operations.fetch_roster',return_value=roster),\
             patch('nba_forecast.operations.fetch_injury_check',side_effect=ValueError('offline')),\
             patch('nba_forecast.operations.capture_daily',return_value={'state':'waiting'}),patch('nba_forecast.operations.archive_daily_references',return_value=dict(sources=[],failures=[])),\
             patch('nba_forecast.operations.run_preflight',return_value=blocked) as preflight:
            def tick(seconds):
                with patch('nba_forecast.operations._now',return_value=at(NOW,seconds)):
                    return operations.run_tick(d,[])
            first=tick(0)
            self.assertEqual(preflight.call_count,1)
            self.assertIn('refresh_requested_at',json.loads((Path(d)/'schedule/fetch-state.json').read_text()))
            second=tick(60)
            self.assertEqual(preflight.call_count,1)
            self.assertEqual([j['game_id'] for j in second['deferred_jobs']],['0022600001'])
            tick(900)
            self.assertEqual(preflight.call_count,2)


class DashboardCacheTests(unittest.TestCase):
    def setUp(self):
        dashboard._VIEWS.clear()
        dashboard._PREDICTIONS.clear()
        self.folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        snapshot,_,_=inputs()
        (self.folder/'schedule').mkdir()
        (self.folder/'schedule/latest.json').write_text(json.dumps(dict(season=snapshot['season'],games=snapshot['games'])))
        self.save()

    def save(self):
        snapshot,past,forecast=inputs()
        with patch('nba_forecast.store._now',return_value=SAVED):
            return store.save_run(self.folder/'forecast.sqlite',snapshot,past,forecast)

    def test_forecast_views_are_decoded_once_per_ledger_state(self):
        with patch('nba_forecast.dashboard._decode_run',wraps=dashboard._decode_run) as build:
            first=dashboard.dashboard_data(self.folder)
            first['games'][0]['forecast']['predictions'].clear()
            second=dashboard.dashboard_data(self.folder)
            self.assertEqual(build.call_count,1)
            self.assertEqual(len(second['games'][0]['forecast']['predictions']),2)
            latest=self.save()
            db=self.folder/'forecast.sqlite'
            stat=db.stat()
            os.utime(db,ns=(stat.st_atime_ns,stat.st_mtime_ns+1))
            self.assertEqual(dashboard.dashboard_data(self.folder)['games'][0]['forecast']['run_id'],latest)
            self.assertEqual(build.call_count,2)

    def test_player_predictions_follow_the_ledger(self):
        db=self.folder/'forecast.sqlite'
        first=dashboard._selected_predictions(db,'2026-27','P1')
        first['G1']['prediction'].clear()
        self.assertEqual(dashboard._selected_predictions(db,'2026-27','P1')['G1']['prediction']['player_id'],'P1')
        latest=self.save()
        stat=db.stat()
        os.utime(db,ns=(stat.st_atime_ns,stat.st_mtime_ns+1))
        self.assertEqual(dashboard._selected_predictions(db,'2026-27','P1')['G1']['run_id'],latest)
        self.assertEqual(dashboard._selected_predictions(db,'2026-27','P30'),{})


class BenchmarkTests(unittest.TestCase):
    def test_precomputed_performance_gives_the_same_report(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'forecast.sqlite'
            past=[dict(actual(),game_id='previous',game_time='2026-10-20T18:00:00Z',known_at='2026-10-20T21:00:00Z')]
            for _ in range(2):
                snapshot,_,forecast=inputs()
                with patch('nba_forecast.store._now',return_value=SAVED):
                    rid=store.save_run(db,snapshot,past,forecast)
            with patch('nba_forecast.store._now',return_value=LATER):
                store.evaluate_run(db,rid,[actual(),actual('P2')])
            self.assertEqual(benchmark_report(db,store.performance_report(db)),benchmark_report(db))


if __name__=='__main__':
    unittest.main()
