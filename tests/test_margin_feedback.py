import unittest
from nba_forecast.margin_feedback import score_margin, score_game_margin
from tests.test_margins import small_distribution


class MarginFeedbackTests(unittest.TestCase):
    def test_scores_frozen_distribution_and_all_diagnostic_lines_without_selecting_winners(self):
        d=small_distribution()
        result=score_margin(d,108,100)
        self.assertEqual(result['status'],'evaluated')
        self.assertEqual(result['actual_home_margin'],8)
        self.assertAlmostEqual(result['home_win_brier'],.3**2)
        self.assertGreater(result['ranked_probability_score'],0)
        self.assertTrue(any(r['line']==7.5 and r['actual_yes'] for r in result['thresholds']))
        self.assertIn('diagnostic',result['threshold_policy'].lower())

    def test_missing_or_nonfinal_actuals_and_unavailable_predictions_remain_unscored(self):
        for home,away in [(None,100),(100,100),(100.5,100),(True,100)]:
            self.assertEqual(score_margin(small_distribution(),home,away)['status'],'pending')
        self.assertEqual(score_margin(None,108,100)['status'],'unavailable')
        self.assertEqual(score_margin({'status':'unavailable'},108,100)['status'],'unavailable')

    def test_complete_game_gate_scores_all_players_and_rejects_missing_players(self):
        from tests.test_store import actual
        rows=[]
        for side in ('home','away'):
            for i in range(5):
                r=actual('P1' if side=='home' else 'P2')
                r.update(player_id=side+str(i),minutes=48,game_period=4)
                if side=='home':
                    r.update(ftm=3,pts=25)
                rows.append(r)
        game=dict(game_id='G1',home_team_id='T1',away_team_id='T2')
        d=dict(small_distribution(),**game)
        self.assertEqual(score_game_margin(d,game,rows)['actual_home_margin'],5)
        self.assertEqual(score_game_margin(d,game,rows[:-1])['status'],'pending')
