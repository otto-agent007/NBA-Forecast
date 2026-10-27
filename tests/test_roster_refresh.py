import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nba_forecast.data import fetch_roster
from nba_forecast.roster_refresh import fetch_team_rosters


class RosterRefreshTests(unittest.TestCase):
    def capture(self,url,directory,max_age):
        tid=int(url.split('/')[-2])
        return ({'info':{'TEAM_ID':tid,'SEASON_YEAR':'2026-27','TEAM_ABBREVIATION':'TEST'},
            'roster':[{'PLAYER_ID':tid,'PLAYER':f'Player {tid}','TeamID':tid,'SEASON':'2026'}]},
            {'source_url':url,'retrieved_at':'2026-10-24T00:20:00Z' if tid==1610612737 else '2026-10-24T00:21:00Z','sha256':'a'*64})

    def test_main_endpoint_failure_uses_all_thirty_official_team_pages(self):
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.data._fetch',side_effect=ValueError('HTTP 403')),patch('nba_forecast.roster_refresh.fetch_reference',side_effect=self.capture) as fetch:
            roster=fetch_roster('2026-27',cache_dir=Path(d))
        self.assertEqual(fetch.call_count,30)
        self.assertEqual(len(roster['players']),30)
        self.assertEqual(roster['observed_at'],'2026-10-24T00:20:00Z')
        self.assertEqual(len(roster['sources']),30)
        self.assertEqual({p['status'] for p in roster['players']},{'unknown'})
        self.assertEqual(roster['source_method'],'official_team_rosters')

    def test_incomplete_wrong_team_wrong_season_or_duplicate_capture_fails_closed(self):
        for case in ('missing','wrong_team','wrong_season','duplicate'):
            def capture(url,directory,max_age):
                payload,meta=self.capture(url,directory,max_age)
                if url.endswith('/1610612738/roster'):
                    if case=='missing':
                        raise ValueError('unavailable')
                    if case=='wrong_team':
                        payload['info']['TEAM_ID']=1610612737
                    if case=='wrong_season':
                        payload['roster'][0]['SEASON']='2025'
                    if case=='duplicate':
                        payload['roster'][0]['PLAYER_ID']=1610612737
                return payload,meta
            with self.subTest(case=case),tempfile.TemporaryDirectory() as d,patch('nba_forecast.roster_refresh.fetch_reference',side_effect=capture):
                with self.assertRaises(ValueError):
                    fetch_team_rosters('2026-27',Path(d))
