import copy
import unittest
from unittest.mock import patch
from nba_forecast.shots import normalize_shots, fetch_game_shots, playbyplay_url

class ShotTests(unittest.TestCase):
    def setUp(self):
        self.box={'game':{'gameId':'0022600001','gameStatus':3,'gameTimeUTC':'2026-10-20T00:00:00Z','homeTeam':{'teamId':1,'players':[{'personId':7,'statistics':{'fieldGoalsAttempted':2,'fieldGoalsMade':1,'threePointersAttempted':1,'threePointersMade':0}}]},'awayTeam':{'teamId':2,'players':[]}}}
        self.pbp={'game':{'gameId':'0022600001','actions':[{'actionNumber':1,'teamId':1,'personId':7,'actionType':'2pt','shotResult':'Made','xLegacy':10,'yLegacy':20},{'actionNumber':2,'teamId':1,'personId':7,'actionType':'3pt','shotResult':'Missed'}]}}
        self.time='2026-10-21T00:00:00Z'
    def test_counts_missing_coordinates(self):
        out=normalize_shots(self.pbp,self.box,self.time)
        self.assertEqual(out['coverage'],{'attempts':2,'located_attempts':1,'reconciled':True})
        self.assertIsNone(out['shots'][1]['x'])
    def test_reject_bad_evidence(self):
        for mutate in (lambda p:p['game']['actions'].pop(),lambda p:p['game']['actions'].append(copy.deepcopy(p['game']['actions'][0])),lambda p:p['game']['actions'][0].update(teamId=3),lambda p:p['game'].update(gameId='0022600002')):
            p=copy.deepcopy(self.pbp);mutate(p)
            with self.assertRaises(ValueError):normalize_shots(p,self.box,self.time)
        with self.assertRaises(ValueError):normalize_shots(self.pbp,self.box,'2026-10-19T00:00:00Z')
        self.box['game']['gameStatus']=2
        with self.assertRaises(ValueError):normalize_shots(self.pbp,self.box,self.time)
        with self.assertRaises(ValueError):playbyplay_url('0012600001')
    def test_source_failure_reason_preserved(self):
        with patch('nba_forecast.shots._fetch',side_effect=ValueError('NBA source returned HTTP 403')):
            with self.assertRaisesRegex(ValueError,'HTTP 403'):fetch_game_shots('0022600001','unused')
