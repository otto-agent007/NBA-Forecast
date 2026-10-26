"""Home teams score a little more: a symmetric, bounded factor learned from flagged box scores."""
import json
from math import sqrt
import unittest

from nba_forecast import data
from nba_forecast.model import (HOME_COURT_BOUNDS, HOME_COURT_PRIOR, HOME_COURT_PSEUDO_GAMES,
                                _complete_team_games, _home_court, forecast)
from tests.test_model import row
from tests.test_store import inputs


def complete_games(count, home_points, away_points, flagged=True):
    """``count`` complete two-team games; five players a side, team H at home."""
    rows = []
    for n in range(count):
        gid = f'g{n:03d}'
        for team, opponent, home, points in (('H', 'A', True, home_points), ('A', 'H', False, away_points)):
            for i in range(5):
                r = row(i=n * 10 + i, pid=f'{team}{i}', team=team, opponent_id=opponent, season='2025-26',
                        game_time=f'2026-01-{n % 28 + 1:02d}T20:00:00Z', known_at=f'2026-01-{n % 28 + 1:02d}T23:00:00Z')
                r.update(game_id=gid, minutes=48, pts=points // 5, fga=10, fgm=points // 10, fg3a=2, fg3m=0, fta=2, ftm=0)
                if flagged:
                    r['home'] = home
                rows.append(r)
    return rows


class HomeCourtEstimateTests(unittest.TestCase):
    def test_prior_without_flags_and_shrunk_observation_with_them(self):
        self.assertAlmostEqual(_home_court(_complete_team_games(complete_games(30, 120, 100, flagged=False)))['effect'], HOME_COURT_PRIOR)
        estimate = _home_court(_complete_team_games(complete_games(50, 120, 100)))
        expected = (0.2 * 50 + HOME_COURT_PRIOR * HOME_COURT_PSEUDO_GAMES) / (50 + HOME_COURT_PSEUDO_GAMES)
        self.assertEqual(estimate['flagged_games'], 50)
        self.assertAlmostEqual(estimate['observed_effect'], 0.2)
        self.assertAlmostEqual(estimate['effect'], min(HOME_COURT_BOUNDS[1], expected))
        self.assertAlmostEqual(estimate['home_factor'] * estimate['away_factor'], 1.0)
        self.assertAlmostEqual(_home_court(_complete_team_games(complete_games(400, 100, 120)))['effect'], HOME_COURT_BOUNDS[0])

    def test_box_score_rows_carry_the_side(self):
        from tests.test_data import FIXTURES, KNOWN, URL
        payload = json.loads((FIXTURES / 'boxscore_0022600001.json').read_text())
        rows = data.normalize_boxscore(payload, URL, KNOWN)
        home = str(payload['game']['homeTeam']['teamId'])
        self.assertEqual({r['home'] for r in rows if r['team_id'] == home}, {True})
        self.assertEqual({r['home'] for r in rows if r['team_id'] != home}, {False})


class HomeCourtForecastTests(unittest.TestCase):
    def test_identical_players_project_more_at_home(self):
        snapshot, _, _ = inputs()
        game = snapshot['games'][0]  # T1 home, T2 away
        players = {p['player_id']: p for p in snapshot['roster']['players']}
        home, away = players['P1'], players['P2']
        history = [row(i=i, pid=pid, team=team, opponent_id=opp, season='2025-26',
                       game_time=f'2026-04-{i + 1:02d}T20:00:00Z', known_at=f'2026-04-{i + 1:02d}T23:00:00Z')
                   for pid, team, opp in (('P1', 'T1', 'T2'), ('P2', 'T2', 'T1')) for i in range(8)]
        result = forecast(snapshot, history)
        p1 = next(p for p in result['predictions'] if p['player_id'] == 'P1')
        p2 = next(p for p in result['predictions'] if p['player_id'] == 'P2')
        self.assertEqual((p1['status'], p2['status']), ('projected', 'projected'))
        self.assertEqual((p1['components']['home_court']['side'], p2['components']['home_court']['side']), ('home', 'away'))
        self.assertAlmostEqual(p1['components']['home_court']['factor'], sqrt(1 + HOME_COURT_PRIOR))
        self.assertAlmostEqual(p1['points_mean'] / p2['points_mean'], 1 + HOME_COURT_PRIOR, places=6)
        self.assertAlmostEqual(p1['components']['scoring']['home_court_multiplier'] * p2['components']['scoring']['home_court_multiplier'], 1.0)
        self.assertEqual(p1['components']['scoring']['opponent_multiplier'], p2['components']['scoring']['opponent_multiplier'])
        self.assertAlmostEqual(p1['components']['scoring']['points_before_opponent'], p2['components']['scoring']['points_before_opponent'])


if __name__ == '__main__':
    unittest.main()
