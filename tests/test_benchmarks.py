import unittest
from nba_forecast.benchmarks import paired_metrics, baseline_values

class BenchmarkTests(unittest.TestCase):
    def test_paired_error_metrics(self):
        r=paired_metrics([{'model':12,'player_average':10,'minutes_rate':11,'actual':10}])
        self.assertEqual(r['paired_n'],1);self.assertEqual(r['model']['mae'],2)
        self.assertEqual(r['player_average']['mae'],0)
    def test_baseline_cannot_use_late_history(self):
        p={'player_id':'1','expected_minutes':20}
        row=dict(player_id='1',season='2026-27',season_type='Regular Season',game_time='2026-10-20T00:00:00Z',known_at='2026-10-21T01:00:00Z',game_id='0022600001',minutes=10,pts=8)
        s={'as_of':'2026-10-21T00:00:00Z','season':'2026-27'}
        self.assertIsNone(baseline_values(s,[row],p))
        row['known_at']='2026-10-20T03:00:00Z'
        self.assertEqual(baseline_values(s,[row],p),{'player_average':8,'minutes_rate':16})

    def test_real_ledger_pairs_same_selected_player_game(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from tests.test_store import inputs,actual,NOW,LATER
        from nba_forecast.store import save_run,evaluate_run
        from nba_forecast.benchmarks import benchmark_report
        snapshot,_,forecast=inputs()
        previous=dict(actual(),game_id='G0',game_time='2026-10-20T18:00:00Z',known_at='2026-10-20T21:00:00Z')
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'ledger.sqlite'
            with patch('nba_forecast.store._now',return_value=NOW):rid=save_run(db,snapshot,[previous],forecast)
            with patch('nba_forecast.store._now',return_value=LATER):evaluate_run(db,rid,[actual()])
            metrics=benchmark_report(db)['groups'][0]['metrics']
            self.assertEqual(metrics['paired_n'],1)
            self.assertEqual(metrics['model']['mae'],4)
            self.assertEqual(metrics['player_average']['mae'],0)
            self.assertEqual(metrics['minutes_rate']['mae'],1.5)
