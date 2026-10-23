import unittest
from nba_forecast.player_data import build_player_pages
from tests.test_model import row


class RotationTests(unittest.TestCase):
    def test_equivalent_and_mixed_offset_times_select_same_latest_five(self):
        from nba_forecast.rotation import projected_lineups
        players=[dict(player_id=str(i),name=str(i),team_id='A') for i in range(6)]
        profiles=[dict(player_id=str(i),expected_minutes_baseline=30,
            rotation_role=dict(label='starter',observations=2,last_game_started=True,
                last_game_time=('2026-10-22T03:00:00Z' if i==0 else '2026-10-21T20:00:00-07:00') if i<5 else '2026-10-22T02:00:00Z')) for i in range(6)]
        self.assertEqual(set(projected_lineups(players,profiles)['A']['player_ids']),{'0','1','2','3','4'})
    def test_projected_five_prefers_latest_starts_and_excludes_out_players(self):
        from nba_forecast.rotation import projected_lineups
        players=[dict(player_id=str(i),name=str(i),team_id='A',status='available') for i in range(8)]
        players[0]['status']='out'
        profiles=[dict(player_id=str(i),expected_minutes_baseline=35-i,rotation_role=dict(
            label='starter',observations=5,starts=5,recent_minutes=35-i,
            last_game_time='2026-10-21T00:00:00Z',last_game_started=i<5)) for i in range(8)]
        lineup=projected_lineups(players,profiles)['A']
        self.assertEqual(len(lineup['player_ids']),5)
        self.assertEqual(len(set(lineup['player_ids'])),5)
        self.assertNotIn('0',lineup['player_ids'])
        self.assertEqual(set(lineup['player_ids']),{'1','2','3','4','5'})
        self.assertFalse(lineup['confirmed'])

    def test_sparse_evidence_leaves_unconfirmed_slots_instead_of_inventing_players(self):
        from nba_forecast.rotation import projected_lineups
        result=projected_lineups([dict(player_id='1',team_id='A',status='unknown')],[])['A']
        self.assertEqual(result['player_ids'],[])
        self.assertEqual(result['unfilled_slots'],5)

    def test_role_uses_current_team_appearances_and_excludes_future_evidence(self):
        roster=[dict(player_id='1',name='Test',team_id='A')]
        history=[row(i,pid='1',team='A',starter=True) for i in range(3)]
        history += [row(4,pid='1',team='B',starter=False),row(5,pid='1',team='A',starter=False,known_at='2099-01-01T00:00:00Z')]
        page=build_player_pages(roster,history,'2025-26','2026-01-10T12:00:00Z')['players']['1']
        self.assertEqual(page.get('rotation_role',{}).get('label'),'starter')
        self.assertEqual(page['rotation_role']['observations'],3)
        self.assertFalse(page['rotation_role']['confirmed_lineup'])

    def test_trade_and_missing_starter_evidence_leave_role_unverified(self):
        page=build_player_pages([dict(player_id='1',team_id='NEW')],[row(pid='1',team='OLD',starter=True)],
                                '2025-26','2026-01-10T12:00:00Z')['players']['1']
        self.assertEqual(page.get('rotation_role',{}).get('label'),'unknown')
