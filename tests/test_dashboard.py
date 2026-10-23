import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from nba_forecast.dashboard import dashboard_data
from nba_forecast.store import save_run
from tests.test_store import inputs,NOW

class DashboardTests(unittest.TestCase):
    def test_empty_directory_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as d:
            value=dashboard_data(d)
            self.assertEqual(value['games'],[]);self.assertEqual(value['players'],[])
            self.assertFalse((Path(d)/'forecast.sqlite').exists())
    def test_real_ledger_matches_schedule_and_withholds_changed_tipoff(self):
        s,h,f=inputs()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'schedule').mkdir()
            schedule={'season':'2026-27','observed_at':s['as_of'],'coverage':{},'games':[dict(s['games'][0],status='scheduled',home_name='Home',away_name='Away')]}
            (folder/'schedule/latest.json').write_text(json.dumps(schedule))
            with patch('nba_forecast.store._now',return_value=NOW):
                rid=save_run(folder/'forecast.sqlite',s,h,f)
            result=dashboard_data(d)
            self.assertEqual(result['games'][0]['forecast']['run_id'],rid)
            self.assertEqual(len(result['players']),30)
            self.assertEqual(len(result['games'][0]['forecast']['predictions']),2)
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
            folder=Path(d);(folder/'schedule').mkdir()
            (folder/'schedule/latest.json').write_text(json.dumps({'season':s['season'],'games':s['games']}))
            with patch('nba_forecast.store._now',return_value=NOW):
                rid=save_run(folder/'forecast.sqlite',s,h,f)
                save_run(folder/'forecast.sqlite',s,h,f,mode='replay')
            self.assertEqual(dashboard_data(d)['games'][0]['forecast']['run_id'],rid)
