import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from nba_forecast.player_sync import sync_player_data
from nba_forecast.dashboard import player_payload

class PlayerSyncTests(unittest.TestCase):
 @patch("nba_forecast.player_sync.datetime")
 def test_roster_observed_during_fetch_is_not_future_and_shot_failure_cached(self,clock):
  from datetime import datetime,timezone
  clock.now.return_value=datetime(2026,10,23,tzinfo=timezone.utc)
  from tests.test_store import inputs
  s,h,f=inputs()
  from tests.test_model import row
  r=row(season='2026-27',game_id='0022600001',player_id='1')
  roster=dict(s['roster'],observed_at='2026-10-22T00:00:00Z',players=[dict(player_id='1',name='A',team_id=r['team_id'],status='unknown')])
  with tempfile.TemporaryDirectory() as d:
   folder=Path(d);(folder/'schedule').mkdir();(folder/'schedule/latest.json').write_text(json.dumps({'season':'2026-27'}))
   with patch('nba_forecast.player_sync.fetch_roster',return_value=roster),patch('nba_forecast.player_sync.fetch_game_shots',side_effect=ValueError('HTTP 403')) as fetch:
    result=sync_player_data(d,[r],max_shot_games=1)
    self.assertEqual(result['shot_games_verified'],0);self.assertIn('403',result['errors'][0]['error'])
    sync_player_data(d,[r],max_shot_games=1);self.assertEqual(fetch.call_count,1)
   page=player_payload(d,'1');self.assertEqual(page['name'],'A')
   self.assertEqual(page['prediction_history'],[])
   with self.assertRaises(ValueError):player_payload(d,'../forecast.sqlite')
