import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nba_forecast import injuries


class DailySourceTests(unittest.TestCase):
    def test_shared_index_refreshes_when_a_game_enters_its_final_window(self):
        from nba_forecast.injuries import fetch_injury_check
        first='https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-23_12_00PM.pdf'
        final='https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-23_12_15PM.pdf'
        snapshot={'season':'2026-27','as_of':'2026-10-23T16:00:00Z','games':[
            {'game_id':'0022600001','start_time':'2026-10-23T16:45:00Z'}]}
        cache={}
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.injuries._fetch',side_effect=[
            (first.encode(),'2026-10-23T16:00:00Z'),(b'%PDF-early','2026-10-23T16:00:01Z'),
            (final.encode(),'2026-10-23T16:15:00Z'),(b'%PDF-final','2026-10-23T16:15:01Z')]) as fetch:
            fetch_injury_check(snapshot,d,capture_cache=cache)
            result=fetch_injury_check({**snapshot,'as_of':'2026-10-23T16:15:00Z'},d,capture_cache=cache)
        self.assertEqual(fetch.call_count,4)
        self.assertEqual(result['games'][0]['anchor_report']['source_url'],final)

    def test_tick_reuses_index_and_pdf_capture_for_multiple_games(self):
        from nba_forecast.injuries import fetch_injury_check
        url='https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-23_12_00PM.pdf'
        snapshot={'season':'2026-27','as_of':'2026-10-23T16:00:00Z','games':[
            {'game_id':'0022600001','start_time':'2026-10-23T20:00:00Z'}]}
        cache={}
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.injuries._fetch',side_effect=[
            (url.encode(),'2026-10-23T16:00:00Z'),(b'%PDF-captured','2026-10-23T16:00:01Z')]) as fetch:
            first=fetch_injury_check(snapshot,d,capture_cache=cache)
            second=fetch_injury_check({**snapshot,'games':[{'game_id':'0022600002','start_time':'2026-10-23T21:00:00Z'}]},d,capture_cache=cache)
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(first['games'][0]['latest_report']['retrieved_at'],second['games'][0]['latest_report']['retrieved_at'])

    def test_latest_report_is_downloaded_even_without_a_game_slate(self):
        url='https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-23_12_00PM.pdf'
        fetch=getattr(injuries,'fetch_daily_report',None)
        self.assertTrue(callable(fetch),'daily report capture must not depend on scheduled games')
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.injuries._fetch',side_effect=[
            (url.encode(),'2026-10-23T16:00:00Z'),(b'%PDF-captured','2026-10-23T16:00:01Z')]) as request:
            result=fetch('2026-27',d)
        self.assertEqual(result['report']['source_url'],url)
        self.assertEqual(request.call_count,2)
        self.assertEqual(len(result['report']['sha256']),64)


class DailyCaptureTests(unittest.TestCase):
    def test_new_pacific_day_waits_for_its_own_nine_am_capture(self):
        from nba_forecast.injury_capture import capture_daily
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.injury_capture.fetch_daily_report',return_value={
            'checked_at':'2026-10-23T16:00:01Z','report':{'source_url':'https://example.test/report'}}) as fetch:
            capture_daily(d,'2026-27','2026-10-23T16:00:00Z')
            self.assertEqual(capture_daily(d,'2026-27','2026-10-24T06:59:59Z')['state'],'captured')
            self.assertEqual(capture_daily(d,'2026-27','2026-10-24T07:00:00Z')['state'],'waiting')
            self.assertEqual(fetch.call_count,1)
            fetch.return_value={'checked_at':'2026-10-24T16:00:01Z','report':{'source_url':'https://example.test/new-report'}}
            self.assertEqual(capture_daily(d,'2026-27','2026-10-24T16:00:00Z')['state'],'captured')
            self.assertEqual(fetch.call_count,2)
            self.assertEqual(len(list((Path(d)/'injuries/daily').glob('*.json'))),2)

    def test_waits_until_nine_pacific_and_captures_once_across_restarts(self):
        from nba_forecast.injury_capture import capture_daily
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.injury_capture.fetch_daily_report',return_value={
            'checked_at':'2026-10-23T16:00:01Z','report':{'source_url':'https://example.test/report'}}) as fetch:
            self.assertEqual(capture_daily(d,'2026-27','2026-10-23T15:59:59Z')['state'],'waiting')
            self.assertEqual(fetch.call_count,0)
            result=capture_daily(d,'2026-27','2026-10-23T16:00:00Z')
            self.assertEqual(result['state'],'captured')
            self.assertEqual(capture_daily(d,'2026-27','2026-10-23T23:00:00Z')['state'],'captured')
            self.assertEqual(fetch.call_count,1)
            self.assertTrue(list((Path(d)/'injuries/daily').glob('*.json')))

    def test_winter_and_new_pacific_day_use_local_nine_am(self):
        from nba_forecast.injury_capture import capture_daily
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.injury_capture.fetch_daily_report') as fetch:
            result=capture_daily(d,'2026-27','2026-11-02T16:59:00Z')
            self.assertEqual(result['state'],'waiting')
            self.assertEqual(result['scheduled_at'],'2026-11-02T17:00:00+00:00')
            fetch.assert_not_called()

    def test_failed_daily_capture_retries_without_an_empty_success_record(self):
        from nba_forecast.injury_capture import capture_daily
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.injury_capture.fetch_daily_report',side_effect=ValueError('offline')):
            with self.assertRaises(ValueError):capture_daily(d,'2026-27','2026-10-23T16:00:00Z')
            self.assertFalse(list((Path(d)/'injuries/daily').glob('*.json')))

    def test_checkpoint_archive_preserves_first_valid_final_and_later_checks(self):
        from nba_forecast.injury_capture import record_check
        check={'checked_at':'2026-10-23T23:30:01Z','games':[{
            'game_id':'0022600001','start_time':'2026-10-24T00:00:00Z',
            'phase':'final_review','report_status':'captured_pending_review','anchor_report':{'sha256':'a'*64}}]}
        with tempfile.TemporaryDirectory() as d:
            record_check(d,check,'final')
            later={**check,'checked_at':'2026-10-23T23:35:00Z'}
            record_check(d,later,'final')
            root=Path(d)/'injuries/games/0022600001'
            self.assertEqual(json.loads((root/'final.json').read_text())['checked_at'],check['checked_at'])
            self.assertEqual(json.loads((root/'latest.json').read_text())['checked_at'],later['checked_at'])
            self.assertEqual(len(list((root/'records').glob('*.json'))),2)
