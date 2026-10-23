import unittest
from nba_forecast.player_data import build_player_pages
from tests.test_model import row


class RotationTests(unittest.TestCase):
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
