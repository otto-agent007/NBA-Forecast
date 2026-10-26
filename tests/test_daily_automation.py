"""The same restartable tick saves a preliminary run, then a sourced T−30 final."""
import json
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from nba_forecast.operations import run_tick
from nba_forecast.injuries import checkpoints,report_links
from nba_forecast.schedule import normalize_season,atomic_json
from tests.test_schedule import payload,game
from tests.test_store import inputs
from tests.test_model import row


class DailyAutomationTests(unittest.TestCase):
    def test_nine_am_preliminary_then_t30_final_with_archived_report(self):
        clock=['2026-10-22T16:00:00Z']
        schedule=normalize_season(payload([game(start='2026-10-22T18:00:00Z')]),clock[0]);schedule['version']='test-v1'
        roster=inputs()[0]['roster'];roster['team_ids']=[str(t) for t in range(1610612737,1610612767)]
        for person,team in zip(roster['players'],roster['team_ids']):
            person['team_id']=team;person['status']='unknown'
        roster['observed_at']=clock[0]
        pid=next(p['player_id'] for p in roster['players'] if p['team_id']=='1610612738')
        history=[row(pid=pid,team='1610612738',opponent_id='1610612752',season='2025-26')]
        def injury_check(snapshot,raw,*,capture_cache=None):
            time='12_00PM' if snapshot['forecast_stage']=='preliminary' else '01_30PM'
            url=f'https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-22_{time}.pdf'
            checks=checkpoints(snapshot['games'],report_links(url,clock[0]),clock[0])
            for item in checks:
                for key in ('anchor_report','latest_report'):
                    if item[key]:item[key].update(sha256='a'*64,retrieved_at=clock[0])
            return dict(checked_at=clock[0],games=checks)
        def parsed(text,schedule,roster,evidence):
            return dict(rows=[dict(player_id=pid,team_id='1610612738',game_id='0022600001',status='available',reported_status='Available')],
                        team_coverage=[],evidence=evidence,issues=[])
        with tempfile.TemporaryDirectory() as d:
            self.enterContext(patch('nba_forecast.operations.run_preflight',return_value={'status':'passed','issues':[]}))
            folder=Path(d);atomic_json(folder/'schedule/latest.json',schedule)
            with patch('nba_forecast.operations._now',side_effect=lambda:clock[0]),patch('nba_forecast.store._now',side_effect=lambda:datetime.fromisoformat(clock[0].replace('Z','+00:00'))),patch('nba_forecast.operations.fetch_schedule',return_value=schedule),patch('nba_forecast.operations.fetch_roster',return_value=roster),patch('nba_forecast.operations.capture_daily',return_value={'state':'captured'}),patch('nba_forecast.operations.fetch_injury_check',side_effect=injury_check),patch('nba_forecast.operations.pdf_text',return_value='text'),patch('nba_forecast.operations.parse_report',side_effect=parsed),patch('nba_forecast.player_sync.sync_player_data',return_value={'errors':[]}),patch('nba_forecast.postgame.build_postgames',return_value={'errors':[]}),patch('nba_forecast.operations.archive_daily_references',return_value=dict(sources=[],failures=[])):
                preliminary=run_tick(folder,history)
                self.assertEqual(preliminary['created_runs'],1,preliminary['errors'])
                self.assertFalse((folder/'injuries/games/0022600001/final.json').exists())
                clock[0]='2026-10-22T17:30:00Z'
                final=run_tick(folder,history)
                self.assertEqual(final['created_runs'],1,final['errors'])
                self.assertFalse(final['errors'])
                self.assertEqual(run_tick(folder,history)['created_runs'],0)
            with sqlite3.connect(folder/'forecast.sqlite') as conn:
                forecasts=[json.loads(r[0]) for r in conn.execute('SELECT forecast_json FROM runs ORDER BY rowid')]
            self.assertEqual([f['forecast_stage'] for f in forecasts],['preliminary','final'])
            self.assertTrue(all(next(p for p in f['predictions'] if p['player_id']==pid)['points_mean']>0 for f in forecasts))
            check=json.loads((folder/'injuries/games/0022600001/final.json').read_text())
            self.assertEqual(check['games'][0]['checkpoint_at'],'2026-10-22T17:30:00+00:00')
