"""Player-impact ratings credit the player whose minutes move the margin."""
import math
import random
import unittest

from nba_forecast import player_impact
from nba_forecast.injury_parser import person_key


def league(games=80, seed=3):
    """Two teams; X wins by half a point a minute while its star X0 plays and breaks even when he rests."""
    rng = random.Random(seed)
    history, records, plus_minus = [], [], {}
    for n in range(games):
        home, away = ('X', 'Y') if n % 2 == 0 else ('Y', 'X')
        gid = f'G{n:03d}'
        star = 0. if n % 4 == 0 else 36.
        x_minutes = dict(X0=star, X1=40., X2=40., X3=40., X4=40., X5=44.) if star else {f'X{i}': 48. for i in range(1, 6)}
        minutes = dict(X=x_minutes, Y={f'Y{i}': 40. for i in range(6)})
        margin = round(.5 * star + rng.gauss(0, 3)) or 1  # X's margin; no ties
        on_with_star = {p: star * 4 * m / (240 - star) for p, m in minutes['X'].items() if p != 'X0'}
        for team, players in minutes.items():
            for p, m in players.items():
                if not m:
                    continue
                if team == 'X':
                    value = .5 * star if p == 'X0' else .5 * on_with_star.get(p, 0.)
                    value += (margin - .5 * star) * m / 48  # his share of the noise
                else:
                    value = -margin * m / 48
                plus_minus[(gid, p)] = round(value)
                history.append(dict(game_id=gid, player_id=p, name='Player ' + p, team_id=team, minutes=m,
                                    pts=round(m / 2), ast=round(m / 12), reb=round(m / 6), stl=round(m / 40),
                                    blk=round(m / 50), tov=round(m / 25), fga=round(m / 2.4), fta=round(m / 9),
                                    fg3a=round(m / 7)))
        x_points = 100 + max(margin, 0)
        y_points = 100 + max(-margin, 0)
        records.append(dict(game_id=gid, home_team_id=home, away_team_id=away,
                            team_stats={'X': dict(pts=x_points), 'Y': dict(pts=y_points)}))
    return history, records, plus_minus


class PlayerImpactTests(unittest.TestCase):
    def test_solver_matches_a_known_system(self):
        matrix = [[4., 1., 0.], [1., 3., 1.], [0., 1., 2.]]
        solution = [1., -2., 3.]
        vector = [math.fsum(a * x for a, x in zip(row, solution)) for row in matrix]
        for got, want in zip(player_impact._solve(matrix, vector), solution):
            self.assertAlmostEqual(got, want)
        with self.assertRaises(ValueError):
            player_impact._solve([[1., 2.], [2., 1.]], [1., 1.])

    def test_the_star_whose_minutes_move_the_margin_rates_highest(self):
        history, records, plus_minus = league()
        rated = player_impact.ratings(history, records, plus_minus)
        star = rated[person_key('Player X0')]
        self.assertGreater(star, 2.)
        self.assertEqual(max(rated, key=rated.get), person_key('Player X0'))
        self.assertTrue(all(abs(v) < star / 2 for k, v in rated.items() if k != person_key('Player X0')))
        # A team's strength is its players' ratings over the minutes they are projected to play.
        self.assertAlmostEqual(player_impact.team_strength(rated, {person_key('Player X0'): 48., 'unrated': 48.}), star)

    def test_sparse_plus_minus_rates_nobody(self):
        history, records, plus_minus = league()
        sparse = dict(list(sorted(plus_minus.items()))[:len(plus_minus) // 2])
        self.assertEqual(player_impact.ratings(history, records, sparse), {})
        self.assertEqual(player_impact.ratings(history, records, {}), {})

    def test_summary_plus_minus_reads_recorded_values_only(self):
        summary = dict(header=dict(id=401700001), boxscore=dict(players=[dict(statistics=[dict(
            keys=['minutes', 'points', 'plusMinus'],
            athletes=[dict(athlete=dict(id=1), stats=['30', '20', '+7']),
                      dict(athlete=dict(id=2), stats=['12', '4', '-3']),
                      dict(athlete=dict(id=3), stats=[]),
                      dict(athlete=dict(id=4), stats=['--', '0', '--'])])])]))
        self.assertEqual(player_impact.summary_plus_minus(summary),
                         [('espn-401700001', 'espn-1', 7), ('espn-401700001', 'espn-2', -3)])


if __name__ == '__main__':
    unittest.main()
