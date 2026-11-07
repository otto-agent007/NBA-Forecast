import json
from datetime import datetime,timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from nba_forecast import dashboard
from nba_forecast.dashboard import dashboard_data, dashboard_payload, player_payload, _current_schedule
from nba_forecast.store import save_run
from tests.test_store import inputs,NOW

class DashboardTests(unittest.TestCase):
    def test_committed_teacher_reference_is_visible_without_an_operational_copy(self):
        with tempfile.TemporaryDirectory() as d:
            report = dashboard_data(d)['close_teacher']
            self.assertEqual(report['reference'], 'backtest-2026-11-06-rating-weight')
            self.assertEqual(report['diagnostics']['overall']['n'], 2604)
            self.assertEqual(len(report['diagnostics']['season_group_mismatches']), 14)


    def test_historical_teacher_is_separate_from_live_and_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / 'market').mkdir()
            reference = dict(reference='test-replay', diagnostics=dict(overall=dict(n=2604)))
            live = dict(summary=dict(games=91))
            (folder / 'market/close-teacher.json').write_text(json.dumps(reference))
            (folder / 'market/report.json').write_text(json.dumps(live))
            before = {str(p): p.read_bytes() for p in folder.rglob('*.json')}
            data = dashboard_data(folder)
            self.assertEqual(data['close_teacher'], reference)
            self.assertEqual(data['market'], live)
            self.assertEqual(before, {str(p): p.read_bytes() for p in folder.rglob('*.json')})

    def test_current_secondary_schedule_is_displayed_only_for_its_nba_base(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'schedule'
            (folder/'secondary').mkdir(parents=True)
            official=dict(version='nba-a',season='2026-27',games=[dict(game_id='0022600104',start_time='old')])
            secondary=dict(version='espn-a',season='2026-27',games=[dict(game_id='0022600104',start_time='new')],
                crosscheck=dict(nba_base_version='nba-a',observed_at=datetime.now(timezone.utc).isoformat()))
            (folder/'latest.json').write_text(json.dumps(official))
            path=folder/'secondary/verified-latest.json'
            path.write_text(json.dumps(secondary))
            self.assertEqual(_current_schedule(Path(d))['games'][0]['start_time'],'new')
            (Path(d)/'last-tick.json').write_text(json.dumps(dict(
                schedule_source='unverified_cached_nba',schedule_version='nba-a')))
            self.assertEqual(_current_schedule(Path(d))['games'][0]['start_time'],'old')
            (Path(d)/'last-tick.json').write_text(json.dumps(dict(
                schedule_source='espn_verified_cached_nba',schedule_version='espn-a')))
            self.assertEqual(_current_schedule(Path(d))['games'][0]['start_time'],'new')
            # Checked once a day: an older check stays in use while the runner uses it.
            secondary['crosscheck']['observed_at']='2026-01-01T00:00:00Z'
            path.write_text(json.dumps(secondary))
            self.assertEqual(_current_schedule(Path(d))['games'][0]['start_time'],'new')
            secondary['crosscheck']['observed_at']=datetime.now(timezone.utc).isoformat()
            secondary['crosscheck']['nba_base_version']='nba-older'
            path.write_text(json.dumps(secondary))
            self.assertEqual(_current_schedule(Path(d))['games'][0]['start_time'],'old')

    def test_played_players_absent_from_current_roster_remain_discoverable(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'players').mkdir()
            (root/'roster.json').write_text(json.dumps(dict(players=[])))
            (root/'players/index.json').write_text(json.dumps(dict(player_summaries={'99':dict(name='Past Player',team_id='A',roster_membership='historical_appearance',points_average=5)})))
            data=dashboard_data(root)
            self.assertEqual([p['player_id'] for p in data['players']],['99'])
            self.assertFalse(data['players'][0]['projected_starter'])
            self.assertEqual(data['players'][0]['roster_membership'],'historical_appearance')

    def test_audit_and_observed_stats_preserve_provenance_and_rescheduled_audit_is_hidden(self):
        s,_,_=inputs()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            (folder/'schedule').mkdir()
            (folder/'players').mkdir()
            (folder/'schedule/latest.json').write_text(json.dumps({'season':s['season'],'games':s['games']}))
            (folder/'roster.json').write_text(json.dumps(s['roster']))
            pid=s['roster']['players'][0]['player_id']
            (folder/'players/index.json').write_text(json.dumps({'player_summaries':{pid:{'points_average':18.5}}}))
            game=s['games'][0]
            root=folder/'preflight'/game['game_id']
            root.mkdir(parents=True)
            audit=dict(game_id=game['game_id'],start_time=game['start_time'],status='blocked',issues=[])
            (root/'latest.json').write_text(json.dumps(audit))
            availability=folder/'availability'/game['game_id']
            availability.mkdir(parents=True)
            (availability/'latest.json').write_text(json.dumps({**audit,'teams':[{'team_id':game['home_team_id'],'status':'no_injuries_reported'}]}))
            data=dashboard_data(d)
            self.assertEqual(data['games'][0]['preflight']['status'],'blocked')
            self.assertEqual(data['players'][0]['observed_stats']['points_average'],18.5)
            self.assertIsNone(data['games'][0]['forecast'])
            self.assertEqual(len(data['games'][0]['availability_update']['teams']),1)
            audit['start_time']='2026-10-23T20:00:00Z'
            (root/'latest.json').write_text(json.dumps(audit))
            self.assertIsNone(dashboard_data(d)['games'][0]['preflight'])
    def test_preliminary_remains_available_after_a_final_run_is_saved(self):
        from copy import deepcopy
        from datetime import timedelta
        from nba_forecast.injuries import checkpoints,report_links
        s,h,f=inputs()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            (folder/'schedule').mkdir()
            (folder/'schedule/latest.json').write_text(json.dumps({'season':s['season'],'games':s['games']}))
            with patch('nba_forecast.store._now',return_value=NOW):
                pre=save_run(folder/'forecast.sqlite',s,h,f)
            final_s=deepcopy(s)
            final_f=deepcopy(f)
            final_s['forecast_stage']=final_f['forecast_stage']='final'
            final_s['as_of']=final_f['as_of']='2026-10-22T17:35:00Z'
            url='https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-22_01_30PM.pdf'
            check={'checked_at':'2026-10-22T17:35:00Z','games':checkpoints(s['games'],report_links(url,'2026-10-22T17:35:00Z'),'2026-10-22T17:35:00Z')}
            check['games'][0]['anchor_report'].update(sha256='a'*64,retrieved_at=check['checked_at'])
            final_s['injury_check']=check
            with patch('nba_forecast.store._now',return_value=NOW+timedelta(hours=5,minutes=35)):
                final=save_run(folder/'forecast.sqlite',final_s,h,final_f)
            result=dashboard_data(d)['games'][0]
            self.assertEqual(result['forecast']['run_id'],final)
            self.assertEqual(result.get('forecasts',{}).get('preliminary',{}).get('run_id'),pre)
            self.assertEqual(result['forecasts']['final']['run_id'],final)
            refreshed_s=deepcopy(s)
            refreshed_f=deepcopy(f)
            refreshed_s['as_of']=refreshed_f['as_of']='2026-10-22T17:45:00Z'
            with patch('nba_forecast.store._now',return_value=NOW+timedelta(hours=5,minutes=45)):
                refreshed=save_run(folder/'forecast.sqlite',refreshed_s,h,refreshed_f)
            result=dashboard_data(d)['games'][0]
            self.assertEqual(result['forecast']['run_id'],final)
            self.assertEqual(result['forecasts']['preliminary']['run_id'],refreshed)

    def test_final_games_carry_the_captured_score_and_overtime_periods(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            (folder/'schedule').mkdir()
            (folder/'finals').mkdir()
            games=[dict(game_id='0022600109',status='scheduled',start_time='2026-10-25T00:30:00Z',home_team_id='H',away_team_id='A'),
                   dict(game_id='0022600110',status='scheduled',start_time='2026-10-25T01:00:00Z',home_team_id='H2',away_team_id='A2')]
            (folder/'schedule/latest.json').write_text(json.dumps(dict(season='2026-27',games=games)))
            rows=[dict(team_id='H',pts=60,game_period=5,team_boxscore=dict(pts=131)),dict(team_id='H',pts=71,game_period=5),
                  dict(team_id='A',pts=126,game_period=5)]
            (folder/'finals/0022600109.json').write_text(json.dumps(rows))
            by_id={g['game_id']:g for g in dashboard_data(d)['games']}
            # Official team total when present, else the player sum; unfinished games carry none.
            self.assertEqual(by_id['0022600109']['status'],'final')
            self.assertEqual(by_id['0022600109']['final_score'],dict(home=131,away=126,periods=5))
            self.assertIsNone(by_id['0022600110']['final_score'])
            (folder/'finals/0022600109.json').write_text(json.dumps(rows[:2]))
            self.assertIsNone(dashboard_data(d)['games'][0]['final_score'],'one side alone is not a final score')

    def test_empty_directory_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as d:
            value=dashboard_data(d)
            self.assertEqual(value['games'],[])
            self.assertEqual(value['players'],[])
            self.assertFalse((Path(d)/'forecast.sqlite').exists())
    def test_real_ledger_matches_schedule_and_withholds_changed_tipoff(self):
        from nba_forecast.totals import game_totals
        s,h,f=inputs()
        f['game_totals']=[game_totals(s['games'][0],f['predictions'],schema_version=1)]
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            (folder/'schedule').mkdir()
            schedule={'season':'2026-27','observed_at':s['as_of'],'coverage':{},'games':[dict(s['games'][0],status='scheduled',home_name='Home',away_name='Away')]}
            (folder/'schedule/latest.json').write_text(json.dumps(schedule))
            with patch('nba_forecast.store._now',return_value=NOW):
                rid=save_run(folder/'forecast.sqlite',s,h,f)
            result=dashboard_data(d)
            self.assertEqual(result['games'][0]['forecast']['run_id'],rid)
            self.assertEqual(len(result['players']),30)
            self.assertEqual(len(result['games'][0]['forecast']['predictions']),2)
            total=result['games'][0]['forecast']['totals']
            self.assertEqual(total['schema_version'],6)
            self.assertEqual(result['games'][0]['forecast']['saved_totals'],f['game_totals'][0])
            self.assertEqual(len(total['teams']),2)
            self.assertAlmostEqual(total['points_mean'],30)
            self.assertEqual(total['status'],'partial')
            schedule['games'][0]['status']='postponed'
            (folder/'schedule/latest.json').write_text(json.dumps(schedule))
            self.assertIsNone(dashboard_data(d)['games'][0]['forecast'])
            schedule['games'][0]['status']='scheduled'
            schedule['games'][0]['start_time']='2026-10-22T19:00:00Z'
            (folder/'schedule/latest.json').write_text(json.dumps(schedule))
            self.assertIsNone(dashboard_data(d)['games'][0]['forecast'])
    def test_newer_replay_cannot_replace_live_forecast(self):
        s,h,f=inputs()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            (folder/'schedule').mkdir()
            (folder/'schedule/latest.json').write_text(json.dumps({'season':s['season'],'games':s['games']}))
            with patch('nba_forecast.store._now',return_value=NOW):
                rid=save_run(folder/'forecast.sqlite',s,h,f)
                save_run(folder/'forecast.sqlite',s,h,f,mode='replay')
            self.assertEqual(dashboard_data(d)['games'][0]['forecast']['run_id'],rid)

    def test_scoring_a_run_does_not_rebuild_the_views_and_a_new_run_does(self):
        from copy import deepcopy
        from datetime import timedelta
        from nba_forecast.store import evaluate_run
        from tests.test_store import actual, LATER
        s,h,f=inputs()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            (folder/'schedule').mkdir()
            (folder/'players').mkdir()
            (folder/'schedule/latest.json').write_text(json.dumps({'season':s['season'],'games':s['games']}))
            (folder/'players/P1.json').write_text(json.dumps(dict(player_id='P1',name='Player 1',season='2026-27')))
            with patch('nba_forecast.store._now',return_value=NOW):
                rid=save_run(folder/'forecast.sqlite',s,h,f)
            with patch.object(dashboard,'_decode_run',wraps=dashboard._decode_run) as decode:
                self.assertEqual(dashboard_data(d)['games'][0]['forecast']['run_id'],rid)
                with patch('nba_forecast.store._now',return_value=LATER):
                    evaluate_run(folder/'forecast.sqlite',rid,[actual('P1'),actual('P2')])
                self.assertEqual(dashboard_data(d)['games'][0]['forecast']['run_id'],rid)
                self.assertEqual(decode.call_count,1,'scoring changes the ledger file, not its forecasts')
                later=deepcopy(s)
                later['as_of']='2026-10-22T12:30:00Z'
                with patch('nba_forecast.store._now',return_value=NOW+timedelta(minutes=30)):
                    second=save_run(folder/'forecast.sqlite',later,h,dict(f,as_of=later['as_of']))
                self.assertEqual(dashboard_data(d)['games'][0]['forecast']['run_id'],second)
                self.assertEqual(decode.call_count,2,'only the new run is decoded')
            history=dashboard._selected_predictions(folder/'forecast.sqlite','2026-27','P1')
            self.assertEqual([item['run_id'] for item in history.values()],[second])
            self.assertNotIn('components',history['G1']['prediction'])
            self.assertEqual(history['G1']['prediction']['points_mean'],20)
            with self.assertRaises(ValueError):
                player_payload(d,'../forecast.sqlite')

    def test_the_page_payload_is_compact_and_revalidates(self):
        s,h,f=inputs()
        for p in f['predictions']:
            if p['status']=='projected':
                p['components']=dict(history=dict(game_ids=['x']*50),uncertainty=dict(method='m'),
                                     game_environment=dict(pace_factor=1.01,audit=dict(big=list(range(100)))))
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            (folder/'schedule').mkdir()
            (folder/'schedule/latest.json').write_text(json.dumps({'season':s['season'],'games':s['games']}))
            (folder/'performance.json').write_text(json.dumps(dict(groups=[dict(season='2026-27',metrics={},matched=[1,2,3],selected_forecasts=[1])])))
            with patch('nba_forecast.store._now',return_value=NOW):
                save_run(folder/'forecast.sqlite',s,h,f)
            etag,body=dashboard_payload(d)
            self.assertEqual((etag,body),dashboard_payload(d),'nothing changed: the same ETag and the same generated_at')
            value=json.loads(body)
            game=value['games'][0]
            self.assertNotIn('forecast',game)
            self.assertEqual(game['forecast_stage'],'preliminary')
            components=game['forecasts']['preliminary']['predictions'][0]['components']
            self.assertEqual(components,dict(uncertainty=dict(method='m'),game_environment=dict(pace_factor=1.01)))
            self.assertEqual(value['performance']['groups'][0],dict(season='2026-27',metrics={}))
            self.assertTrue(value['generated_at'])
            # Python callers keep the full view and the opening forecast itself.
            full=dashboard_data(d)['games'][0]
            self.assertIn('history',full['forecast']['predictions'][0]['components'])
            self.assertIs(full['forecast'],full['forecasts'][full['forecast_stage']])
