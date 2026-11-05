import copy
import unittest
from nba_forecast.full_boxscore_sources import validate_complete_official_final
from tests.test_model import row


class CompleteOfficialFinalTests(unittest.TestCase):
    def complete(self):
        return [row(pid=team+str(i),team=team,minutes=48,fgm=5 if team=='B' and i==0 else 6,
                    game_period=4,plus_minus=2 if team=='A' else -2) for team in ('A','B') for i in range(5)]

    def test_minutes_and_plus_minus_conservation(self):
        rows=self.complete()
        audit=validate_complete_official_final(rows)
        self.assertEqual(audit['teams']['A']['minutes'],240)
        self.assertTrue(audit['teams']['B']['plus_minus_reconciled'])
        for kind in ('missing_player','minutes','plus_minus','period','excess_player_minutes'):
            with self.subTest(kind=kind):
                bad=copy.deepcopy(rows)
                if kind=='missing_player':
                    bad.pop()
                if kind=='minutes':
                    bad[0]['minutes']-=1
                if kind=='plus_minus':
                    bad[0]['plus_minus']+=1
                if kind=='period':
                    bad[0]['game_period']=5
                if kind=='excess_player_minutes':
                    bad[0]['minutes']+=1
                    bad[1]['minutes']-=1
                with self.assertRaises(ValueError):
                    validate_complete_official_final(bad)
        rows[0]['plus_minus']=None
        self.assertFalse(validate_complete_official_final(rows)['teams']['A']['plus_minus_reconciled'])

    def test_overtime_requires_extra_minutes(self):
        rows=self.complete()
        for r in rows:
            r.update(game_period=5,minutes=53)
        self.assertEqual(validate_complete_official_final(rows)['teams']['A']['minutes'],265)
        for r in rows:
            r['minutes']=48
        with self.assertRaises(ValueError):
            validate_complete_official_final(rows)

    def test_rounding_to_the_second_allows_half_a_second_per_player(self):
        # 0022600142: LiveData and the page both sum DAL's 13 and OKC's 11 players to 3 s
        # short of 240 minutes; per-player seconds are rounded, and that is all it is.
        def team(players,short_seconds):
            rows=[row(pid='A'+str(i),team='A',minutes=240/players,fgm=6,game_period=4,plus_minus=None)
                  for i in range(players)]
            rows[0]['minutes']-=short_seconds/60
            return rows+[row(pid='B'+str(i),team='B',minutes=48,fgm=6,game_period=4,plus_minus=None) for i in range(5)]
        for players,short,ok in ((13,3,True),(13,6,True),(13,7,False),(5,2,True),(5,3,False),(9,-4,True),(9,-5,False)):
            with self.subTest(players=players,short=short):
                if ok:
                    validate_complete_official_final(team(players,short))
                else:
                    with self.assertRaisesRegex(ValueError,'team minutes'):
                        validate_complete_official_final(team(players,short))
