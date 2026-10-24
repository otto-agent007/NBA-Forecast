import tempfile
import unittest
from nba_forecast.research_evaluation import split_games,prior_game_days,run_research,calendar_games,historical_context


class ResearchEvaluationTests(unittest.TestCase):
    def test_rejected_statistics_do_not_erase_a_game_from_the_rest_calendar(self):
        history=[dict(game_id='excluded',game_time='2026-01-19T00:00:00Z',known_at='2026-10-24T00:00:00Z',
            season='2025-26',team_id=t,opponent_id=o) for t,o in [('A','B'),('B','A')]]
        records=calendar_games(history,[])
        target=dict(game_id='next',game_time='2026-01-20T00:00:00Z',season='2025-26',home_team_id='A',away_team_id='C',venue_timezone='America/New_York')
        context=historical_context(records,target,'A')
        self.assertEqual(context['previous_game_id'],'excluded')
        self.assertTrue(context['back_to_back'])
        self.assertIsNone(context['timezone_shift_hours'])
    def test_same_day_games_do_not_supply_other_games_final_results(self):
        target={'game_time':'2026-01-20T02:00:00Z'}
        records=[{'game_id':'before','game_time':'2026-01-19T02:00:00Z'},
                 {'game_id':'same_day','game_time':'2026-01-20T00:00:00Z'},
                 {'game_id':'same_time','game_time':'2026-01-20T02:00:00Z'}]
        self.assertEqual([r['game_id'] for r in prior_game_days(records,target)],['before'])

    def test_date_groups_are_disjoint_and_ordered(self):
        rows=[{'game_id':str(i),'game_time':f'2026-01-{i+1:02d}T20:00:00Z'} for i in range(20)]
        splits=split_games(rows)
        self.assertEqual(splits['train'],[str(i) for i in range(10)])
        self.assertEqual(splits['holdout'],['16','17','18','19'])
        self.assertEqual(len({i for part in splits.values() for i in part}),20)

    def test_research_fit_cannot_use_sources_observed_after_its_creation(self):
        row={'game_id':'future','game_time':'2025-12-01T00:00:00Z','known_at':'2026-10-24T03:00:00Z'}
        with tempfile.TemporaryDirectory() as d,self.assertRaisesRegex(ValueError,'future-known'):
            run_research([], [row], d,'2026-10-24T02:00:00Z')
