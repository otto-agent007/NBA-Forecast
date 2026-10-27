import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from nba_forecast.historical import expand_prior_fields,season_prior
from nba_forecast.model import forecast
from tests.test_model import row,snapshot,current


class FullBoxPriorTests(unittest.TestCase):
    def test_unpaired_aggregate_seasons_do_not_invent_rebound_split(self):
        snap=snapshot()
        base=dict(GP=50,MIN=1500,MIN_per_game=30,PTS=850,FGM=300,FGA=600,FG3M=100,FG3A=250,FTM=150,FTA=200,TOV=100,REB=200)
        snap['historical_priors']=dict(players=[dict(player_id='p',source=dict(retrieved_at='2026-01-01T00:00:00Z'),
            rows=[dict(base,season='2024-25',OREB=50),dict(base,season='2023-24',DREB=150)])])
        p=forecast(snap,[row()])['predictions'][0]
        self.assertIsNotNone(p['expected_reb'])
        self.assertIsNone(p['expected_oreb'])
        self.assertIsNone(p['expected_dreb'])
        snap['historical_priors']['players'][0]['rows'][0]['DREB']=150
        p=forecast(snap,[row()])['predictions'][0]
        self.assertAlmostEqual(p['expected_reb'],p['expected_oreb']+p['expected_dreb'])
        split=p['components']['boxscore']['rebound_split']
        self.assertEqual(split['offensive']['prior_seasons'],split['defensive']['prior_seasons'])

    def test_archived_totals_expand_without_network_or_fabricated_plus_minus(self):
        with TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'historical-raw').mkdir()
            payload=dict(categories=[dict(name='totals',names=['offensiveRebounds','defensiveRebounds','totalRebounds','fouls'],
                statistics=[dict(teamId='1',teamSlug='test',season={'displayName':'2024-25'},stats=['50','150','200','100'])])])
            raw=json.dumps(payload).encode()
            sha=hashlib.sha256(raw).hexdigest()
            (folder/'historical-raw'/(sha+'.json')).write_bytes(raw)
            r=dict(season='2024-25',GP=50,MIN=1500,MIN_per_game=30,PTS=850,FGM=300,FGA=600,FG3M=100,FG3A=250,FTM=150,FTA=200,TOV=100,
                historical_team_espn_id='1',historical_team_slug='test')
            book=dict(players=[dict(player_id='p',rows=[r],source=dict(sha256=sha,retrieved_at='2026-01-01T00:00:00Z'))])
            expanded=expand_prior_fields(book,folder)
            self.assertNotIn('OREB',book['players'][0]['rows'][0])
            self.assertEqual(expanded['players'][0]['rows'][0]['OREB'],50)
            snap=snapshot()
            snap['historical_priors']=expanded
            prior=season_prior(snap,'p')
            self.assertEqual(prior['boxscore_totals']['pf']['count'],40)
            self.assertNotIn('plus_minus',prior['boxscore_totals'])
            result=forecast(snap,[row(pf=0),current(pf=6,reb=12,oreb=6,dreb=6)])['predictions'][0]
            self.assertEqual(result['components']['boxscore']['other_stats']['pf']['prior_source'],'verified_season_totals')
            self.assertLess(result['expected_pf'],3)
            self.assertAlmostEqual(result['expected_reb'],result['expected_oreb']+result['expected_dreb'])
            future=copy.deepcopy(snap)
            future['historical_priors']['players'][0]['source']['retrieved_at']='2027-01-01T00:00:00Z'
            self.assertIsNone(season_prior(future,'p'))
            (folder/'historical-raw'/(sha+'.json')).write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                expand_prior_fields(book,folder)


if __name__=='__main__':
    unittest.main()
