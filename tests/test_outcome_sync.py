import json,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from nba_forecast.outcome_sync import environment_record
from nba_forecast.outcome_sync import sync_finished_games
from nba_forecast.espn_actuals import ActualsSourceConflict


class OutcomeSyncTests(unittest.TestCase):
    def test_unreconciled_final_is_pending_with_evidence_and_never_cached_as_actual(self):
        game=dict(game_id='g',status='final',start_time='2026-10-23T23:00:00Z',home_team_id='H',away_team_id='A')
        conflict=ActualsSourceConflict(game_id='g',team_id='A',provider_event_id='123456',statistic='fga',player_total=81,team_total=80,
            metadata=dict(source_url='https://example.test/summary',sha256='a'*64,retrieved_at='2026-10-24T02:00:00Z'))
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.outcome_sync.fetch_espn_final',side_effect=conflict):
            Path(d,'roster.json').write_text(json.dumps({'players':[]}))
            report=sync_finished_games(d,{'games':[game]},[],'2026-10-24T02:00:00Z')
            self.assertEqual(report['pending'],['g'])
            self.assertFalse(report['errors'])
            self.assertEqual(report['pending_details'][0]['evidence']['player_total'],81)
            self.assertFalse(Path(d,'finals/g.json').exists())

    def test_explicit_overtime_period_determines_duration_not_rounded_player_minutes(self):
        game=dict(game_id='g',start_time='2026-10-23T23:00:00Z',home_team_id='H',away_team_id='A')
        rows=[dict(game_id='g',team_id=t,known_at='2026-10-24T02:00:00Z',season='2026-27',game_period=5,
            team_boxscore=dict(pts=120,fga=90,fta=20,oreb=10,tov=12,team_turnovers=1,total_turnovers=13),
            source_provenance={'summary':{'sha256':'a'*64}},venue_timezone='America/New_York') for t in ('H','A')]
        record=environment_record(game,rows)
        self.assertEqual(record['game_minutes'],53)
        self.assertEqual(record['team_stats']['H']['tov'],13)
        self.assertEqual(record['team_stats']['H']['player_tov'],12)
        self.assertEqual(record['known_at'],'2026-10-24T02:00:00Z')
        rows[0].pop('game_period')
        with self.assertRaises(ValueError):environment_record(game,rows)
