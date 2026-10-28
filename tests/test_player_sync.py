import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from nba_forecast.player_sync import sync_player_data
from nba_forecast.dashboard import player_payload

class PlayerSyncTests(unittest.TestCase):
 def test_selected_schedule_is_passed_to_official_page_collection(self):
  from datetime import datetime,timezone
  from tests.test_store import inputs
  from tests.test_model import row
  s,_,_=inputs()
  r=row(season='2026-27',game_id='0022600001',player_id='1')
  game=dict(s['games'][0],game_id='0022600001',start_time=r['game_time'])
  roster=dict(s['roster'],observed_at='2026-10-22T00:00:00Z',players=[
   dict(player_id='1',name='A',team_id=r['team_id'],status='unknown')])
  captured=[]
  def unavailable(selected,**kwargs):
   captured.append(selected['start_time'])
   raise ValueError('offline fixture')
  with tempfile.TemporaryDirectory() as d:
   folder=Path(d)
   (folder/'schedule').mkdir()
   (folder/'schedule/latest.json').write_text(json.dumps(dict(season='2026-27',
    games=[dict(game,start_time='2026-10-23T20:00:00Z')])))
   with patch('nba_forecast.player_sync.datetime') as clock,\
        patch('nba_forecast.player_sync.fetch_official_game_page',side_effect=unavailable),\
        patch('nba_forecast.player_sync.fetch_game_shots',side_effect=ValueError('offline fixture')):
    clock.now.return_value=datetime(2026,10,23,tzinfo=timezone.utc)
    sync_player_data(d,[r],roster=roster,schedule=dict(season='2026-27',games=[game]),max_shot_games=1)
  self.assertEqual(captured,[r['game_time']])

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
   folder=Path(d)
   (folder/'schedule').mkdir()
   (folder/'schedule/latest.json').write_text(json.dumps({'season':'2026-27'}))
   with patch('nba_forecast.player_sync.fetch_roster',return_value=roster),patch('nba_forecast.player_sync.fetch_game_shots',side_effect=ValueError('HTTP 403')) as fetch:
    result=sync_player_data(d,[r],max_shot_games=1)
    self.assertEqual(result['shot_games_verified'],0)
    self.assertIn('403',result['errors'][0]['error'])
    sync_player_data(d,[r],max_shot_games=1)
    self.assertEqual(fetch.call_count,1)
   page=player_payload(d,'1')
   self.assertEqual(page['name'],'A')
   self.assertEqual(page['prediction_history'],[])
   with self.assertRaises(ValueError):
       player_payload(d,'../forecast.sqlite')

 def test_starter_comes_from_livedata_after_a_stat_correction_and_finals_decide_a_games_players(self):
  from nba_forecast.player_sync import enrich_final_cache
  from nba_forecast.operations import _merge_finals
  from tests.test_model import row
  final=row(season='2026-27',game_id='0022600001',pid='1',team='1610612738',opponent_id='1610612752',reb=None)
  live=dict(final,fga=final['fga']+1,reb=4,starter=True,known_at='2025-03-02T00:00:00+00:00')
  with tempfile.TemporaryDirectory() as d:
   folder=Path(d)
   (folder/'finals').mkdir()
   (folder/'finals/0022600001.json').write_text(json.dumps([final]))
   with patch('nba_forecast.player_sync.enrich_archive',return_value=[live]):
    enrich_final_cache(folder)
   saved=json.loads((folder/'finals/0022600001.json').read_text())[0]
   # The corrected attempt count does not block the starter flag, but keeps optional counts out.
   self.assertEqual((saved['starter'],saved['fga'],saved['reb']),(True,final['fga'],None))
   stale=dict(final,player_id='2',name='Stale',status='dnp',minutes=0,fgm=0,fga=0,fg3m=0,fg3a=0,ftm=0,fta=0,tov=0,pts=0)
   merged=_merge_finals([stale,dict(final,game_id='0022600002')],folder/'finals')
   self.assertEqual(sorted((r['game_id'],r['player_id']) for r in merged),[('0022600001','1'),('0022600002','1')])
