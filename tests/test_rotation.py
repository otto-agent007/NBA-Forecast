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


def returning_starter_case(reason=None, reported=()):
    """Team A: s1..s5 started g0-g4; in g5 s1 was away and the bench player f started for him."""
    from nba_forecast.rotation import rotation_role, team_games
    starters = ['s1', 's2', 's3', 's4', 's5']
    rows = [row(i, pid=p, team='A', season='2025-26', starter=True, minutes=34) for i in range(5) for p in starters]
    rows += [row(i, pid='f', team='A', season='2025-26', starter=False, minutes=18) for i in range(5)]
    rows += [row(5, pid=p, team='A', season='2025-26', starter=True, minutes=34) for p in starters[1:]]
    rows += [row(5, pid='f', team='A', season='2025-26', starter=True, minutes=30)]
    if reason is not None:
        rows.append(row(5, pid='s1', team='A', season='2025-26', status='dnp', minutes=0, fgm=0, fga=0, fg3m=0, fg3a=0,
                        ftm=0, fta=0, participation_reason=reason))
    games = team_games(rows)['A']
    players = [dict(player_id=p, name=p, team_id='A', status='available') for p in starters+['f']]
    profiles = [dict(player_id=p['player_id'], expected_minutes_baseline=30 if p['player_id'] == 'f' else 33,
                     rotation_role=rotation_role(p, [r for r in rows if r['player_id'] == p['player_id']], team_games=games,
                                                 reported_out=reported if p['player_id'] == 's1' else ()))
                for p in players]
    return players, profiles


class ReturningStarterTests(unittest.TestCase):
    def five(self, reason=None, reported=(), s1_status='available'):
        from nba_forecast.rotation import projected_lineups
        players, profiles = returning_starter_case(reason, reported)
        players[0]['status'] = s1_status
        return set(projected_lineups(players, profiles)['A']['player_ids'])

    def test_a_starter_away_since_his_last_start_takes_his_place_back_from_the_fill_in(self):
        # ATL 2026-11-04: Jalen Johnson, reported out of 0022600160, ranked behind
        # Luguentz Dort, who started that one game.
        everyone = {'s1', 's2', 's3', 's4', 's5'}
        for reason in ('Inactive', 'Inactive - Injury/Illness', 'DND - Rest', 'NWT - League Suspension'):
            with self.subTest(reason=reason):
                self.assertEqual(self.five(reason), everyone)
        with self.subTest('reported out on the official injury report, no box-score line'):
            self.assertEqual(self.five(None, reported=('g5',)), everyone)

    def test_a_coaching_decision_or_unexplained_absence_keeps_the_fill_in(self):
        fill_in = {'f', 's2', 's3', 's4', 's5'}
        for reason in ("DNP - Coach's Decision", "Inactive - Coach's Decision", "COACH'S DECISION", ''):
            with self.subTest(reason=reason):
                self.assertEqual(self.five(reason), fill_in)
        with self.subTest('no box-score line and no report'):
            self.assertEqual(self.five(None), fill_in)
        with self.subTest('still out today'):
            self.assertEqual(self.five('Inactive - Injury/Illness', s1_status='out'), fill_in)

    def test_role_records_the_games_missed_since_the_last_appearance(self):
        _, profiles = returning_starter_case('Inactive - Injury/Illness')
        roles = {p['player_id']: p['rotation_role'] for p in profiles}
        self.assertEqual((roles['s1']['missed_game_ids'], roles['s1']['absent_since_last_game']), (['g5'], True))
        self.assertEqual((roles['f']['missed_game_ids'], roles['f']['absent_since_last_game']), ([], False))

    def test_player_pages_use_box_score_absences(self):
        from nba_forecast.rotation import projected_lineups
        starters = ['s1', 's2', 's3', 's4', 's5']
        rows = [row(i, pid=p, team='A', season='2025-26', starter=True, minutes=34) for i in range(5) for p in starters]
        rows += [row(i, pid='f', team='A', season='2025-26', starter=False, minutes=18) for i in range(5)]
        rows += [row(5, pid=p, team='A', season='2025-26', starter=True, minutes=34) for p in starters[1:]+['f']]
        rows.append(row(5, pid='s1', team='A', season='2025-26', status='dnp', minutes=0, participation_reason='Inactive'))
        roster = [dict(player_id=p, name=p, team_id='A') for p in starters+['f']]
        pages = build_player_pages(roster, rows, '2025-26', '2026-01-10T12:00:00Z')['players']
        self.assertTrue(pages['s1']['rotation_role']['absent_since_last_game'])
        players = [dict(player_id=p, name=p, team_id='A') for p in pages]
        self.assertEqual(set(projected_lineups(players, list(pages.values()))['A']['player_ids']), set(starters))

    def test_profiles_read_reported_absences_from_the_snapshot(self):
        from nba_forecast.profiles import build_profiles
        from tests.test_store import inputs
        s, h, _ = inputs()
        s['reported_unavailable'] = {'P1': ['missed-game']}
        profiles = {p['player_id']: p for p in build_profiles(s, h)['players']}
        self.assertIn('absent_since_last_game', profiles['P1']['rotation_role'])
        self.assertIn('missed_game_ids', profiles['P2']['rotation_role'])
