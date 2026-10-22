import copy
import unittest
from nba_forecast import clutch
from nba_forecast.data import _allowed_url

class ClutchTests(unittest.TestCase):
    def fixture(self):
        return {'parameters':clutch.parameters('2024-25'), 'resultSets':[{'name':'LeagueDashPlayerClutch','headers':['PLAYER_ID','PLAYER_NAME','GP','MIN','FGM','FGA','FG3M','FG3A','FTM','FTA','PTS','AST','TOV'], 'rowSet':[[1,'Player',2,7.0,3,6,1,2,2,3,9,2,1]]}]}
    def test_complete_request_and_allowlist(self):
        url=clutch.clutch_url('2024-25');_allowed_url(url)
        self.assertIn('MeasureType=Base',url)
        self.assertIn('LastNGames=0',url)
        with self.assertRaises(ValueError):_allowed_url(url.replace('PointDiff=5','PointDiff=10'))
        with self.assertRaises(ValueError):clutch.clutch_url('2024-26')
    def test_validated_totals_and_missing_are_distinct(self):
        rows=clutch.normalize(self.fixture(),'2024-25')
        self.assertEqual(rows[0]['PTS'],9)
    def test_wrong_scope_schema_and_identity_rejected(self):
        for kind in ('season','scope','duplicate','width','points'):
            p=self.fixture()
            if kind=='season':p['parameters']['Season']='2023-24'
            if kind=='scope':p['parameters']['SeasonType']='Playoffs'
            if kind=='duplicate':p['resultSets'][0]['rowSet']*=2
            if kind=='width':p['resultSets'][0]['rowSet'][0].pop()
            if kind=='points':p['resultSets'][0]['rowSet'][0][10]=99
            with self.subTest(kind=kind),self.assertRaises(ValueError):clutch.normalize(p,'2024-25')
