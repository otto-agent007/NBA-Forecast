import unittest
from nba_forecast.historical import season_prior

class HistoricalPriorTests(unittest.TestCase):
    def test_three_season_totals_are_weighted_and_future_capture_is_not_used(self):
        rows=[dict(season=s,GP=70,MIN=2450,MIN_per_game=35,PTS=70*p,FGM=600,FGA=1300,FG3M=150,FG3A=400,FTM=350,FTA=420,TOV=160) for s,p in [('2023-24',28.7),('2024-25',26),('2025-26',26)]]
        s={'season':'2026-27','as_of':'2026-10-23T22:26:00Z','historical_priors':{'players':[{'player_id':'1','rows':rows,'source':{'retrieved_at':'2026-10-23T22:25:00Z'}}]}}
        p=season_prior(s,'1')
        self.assertEqual(len(p['seasons']),3)
        self.assertGreater(p['totals']['PTS']/p['totals']['GP'],26)
        s['historical_priors']['players'][0]['source']['retrieved_at']='2026-10-23T23:00:00Z'
        self.assertIsNone(season_prior(s,'1'))

    def test_archive_totals_and_roster_identity_are_verified_before_use(self):
        import json,hashlib,tempfile
        from pathlib import Path
        from nba_forecast.historical import verify_priors
        counts={'season':'2025-26','GP':10,'MIN_per_game':30.,'MIN':300.,'PTS':200,'TOV':15,'FGM':70,'FGA':150,'FG3M':20,'FG3A':60,'FTM':40,'FTA':50,'historical_team_slug':'team','historical_team_espn_id':'1'}
        payload={'categories':[{'name':'averages','names':['gamesPlayed','avgMinutes'],'statistics':[{'teamId':'1','teamSlug':'team','season':{'displayName':'2025-26'},'stats':['10','30.0']}]},
            {'name':'totals','names':['points','turnovers','fieldGoalsMade-fieldGoalsAttempted','threePointFieldGoalsMade-threePointFieldGoalsAttempted','freeThrowsMade-freeThrowsAttempted'],'statistics':[{'teamId':'1','teamSlug':'team','season':{'displayName':'2025-26'},'stats':['200','15','70-150','20-60','40-50']}]}]}
        roster={'season':{'displayName':'2026-27'},'athletes':[{'id':'99','fullName':'Test Player'}]}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'historical-raw').mkdir()
            (root/'references/raw').mkdir(parents=True)
            def archive(payload,path,url):
                raw=json.dumps(payload).encode()
                sha=hashlib.sha256(raw).hexdigest()
                (root/path/(sha+'.json')).write_bytes(raw)
                return dict(source_url=url,sha256=sha,retrieved_at='2026-10-23T22:00:00Z')
            item=dict(player_id='1',team_id='T',name='Test Player',espn_id='99',rows=[counts],
                source=archive(payload,Path('historical-raw'),'https://site.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/99/stats'),
                roster_source=archive(roster,Path('references/raw'),'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/1/roster'))
            s=dict(season='2026-27',as_of='2026-10-23T22:30:00Z',roster={'players':[{'player_id':'1','team_id':'T','name':'Test Player'}]},historical_priors={'players':[item]})
            verify_priors(s,root)
            s['roster']['players'][0]['name']='Tést Player'
            verify_priors(s,root)  # Same exact NBA ID and normalized archived name.
            s['roster']['players'][0]['name']='Different Player'
            with self.assertRaises(ValueError):
                verify_priors(s,root)
            s['roster']['players'][0]['name']='Test Player'
            item['team_id']='PREVIOUS_TEAM'
            verify_priors(s,root)  # A stable player identity carries career history across trades.
            counts['PTS']=201
            with self.assertRaises(ValueError):
                verify_priors(s,root)
            counts['PTS']=200
            item['espn_id']='98'
            with self.assertRaises(ValueError):
                verify_priors(s,root)
