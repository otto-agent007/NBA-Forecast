import unittest
from nba_forecast.injuries import report_links, checkpoints, validate_final_check

BASE='https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-21_'
GAME={'game_id':'0022600001','start_time':'2026-10-22T00:00:00Z'}

class InjuryTests(unittest.TestCase):
    def test_eastern_report_time_and_future_exclusion(self):
        html=' '.join(BASE+t+'.pdf' for t in ('07_15PM','07_30PM','07_45PM'))
        links=report_links(html,'2026-10-21T23:40:00Z')
        self.assertEqual(len(links),2)
        self.assertEqual(links[-1]['published_at'],'2026-10-21T23:30:00+00:00')

    def test_t30_anchor_preserved_later_report_separate(self):
        html=' '.join(BASE+t+'.pdf' for t in ('07_15PM','07_30PM','07_45PM'))
        checks=checkpoints([GAME],report_links(html,'2026-10-21T23:50:00Z'),'2026-10-21T23:50:00Z')
        self.assertEqual(checks[0]['anchor_report']['published_at'],'2026-10-21T23:30:00+00:00')
        self.assertEqual(checks[0]['phase'],'final_review')
        self.assertTrue(checks[0]['later_report_available'])

    def test_before_checkpoint_is_preliminary_and_missing_is_flagged(self):
        self.assertEqual(checkpoints([GAME],[],'2026-10-21T23:20:00Z')[0]['phase'],'preliminary')
        c=checkpoints([GAME],[],'2026-10-21T23:40:00Z')[0]
        self.assertEqual(c['report_status'],'missing')

    def test_final_requires_captured_fresh_anchor_and_all_games(self):
        snapshot={'games':[GAME],'as_of':'2026-10-21T23:40:00Z'}
        check={'checked_at':'2026-10-21T23:35:00Z','games':checkpoints([GAME],report_links(BASE+'07_30PM.pdf','2026-10-21T23:35:00Z'),'2026-10-21T23:35:00Z')}
        with self.assertRaises(ValueError): validate_final_check(snapshot,check)
        check['games'][0]['anchor_report'].update(sha256='a'*64,retrieved_at=check['checked_at'])
        validate_final_check(snapshot,check)
        check['games'][0]['checkpoint_at']='2026-10-20T23:30:00Z'
        with self.assertRaises(ValueError):validate_final_check(snapshot,check)

    def test_model_final_api_cannot_bypass_checkpoint(self):
        from nba_forecast.model import forecast
        snapshot={'forecast_stage':'final','as_of':'2026-10-21T23:40:00Z',
                  'season':'2026-27','games':[],'roster':{'players':[]}}
        with self.assertRaises(ValueError):forecast(snapshot,[])
