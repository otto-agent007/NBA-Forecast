"""A short bench: every other roster player is explicitly out, so the available players play the minutes."""
import unittest

from nba_forecast.model import forecast
from nba_forecast.totals import short_bench
from tests.test_model import player, snapshot
from tests.test_participation import rows


def history(count, minutes=20):
    data = []
    for i in range(count):
        data += rows(range(10), pid=f'p{i}', minutes=minutes)
    return data


def explicit_out(pid, basis='explicit_report_status'):
    return dict(player(pid, status='out'), status_basis=basis)


class ShortBenchTests(unittest.TestCase):
    def run_forecast(self, available, others):
        result = forecast(snapshot([player(f'p{i}') for i in range(available)] + others), history(available))
        team = [p for p in result['predictions'] if p['team_id'] == 'A']
        totals = next(t for t in result['game_totals'][0]['teams'] if t['team_id'] == 'A')
        return team, [p for p in team if p['status'] == 'projected'], totals

    def test_seven_available_players_fill_the_regulation_minutes(self):
        team, projected, totals = self.run_forecast(7, [explicit_out(f'o{i}') for i in range(10)])
        self.assertTrue(short_bench(team))
        rotation = projected[0]['components']['rotation']
        self.assertTrue(rotation['short_bench'])
        expected = sum(p['participation_probability'] * p['expected_minutes'] for p in projected)
        self.assertAlmostEqual(expected + rotation['unallocated_minutes'], 240., places=6)
        self.assertGreater(expected, 230, 'twenty-minute histories rise to fill the budget')
        self.assertTrue(all('team_minutes_raised' in p['flags'] for p in projected))
        self.assertTrue(all(p['expected_minutes'] <= 48 for p in projected))
        self.assertEqual((totals['status'], totals['short_bench'], totals['projected_players'], totals['out_players']),
                         ('complete', True, 7, 10))
        self.assertAlmostEqual(totals['projected_minutes'] + totals['residual_minutes'], 240., places=6)

    def test_the_same_seven_stay_a_deficit_when_the_roster_is_in_doubt(self):
        cases = {'no outs': [],
                 'an out from the ESPN list': [explicit_out(f'o{i}') for i in range(9)] + [explicit_out('o9', basis='espn_injury_list')],
                 'an unresolved player': [explicit_out(f'o{i}') for i in range(10)] + [player('u', status='unknown')]}
        for label, others in cases.items():
            team, projected, totals = self.run_forecast(7, others)
            self.assertFalse(short_bench(team), label)
            rotation = projected[0]['components']['rotation']
            self.assertFalse(rotation['short_bench'], label)
            self.assertEqual(rotation['scale'], 1., label)
            self.assertGreater(rotation['unallocated_minutes'], 90, label)
            self.assertFalse(any('team_minutes_raised' in p['flags'] for p in projected), label)
            self.assertEqual((totals['status'], totals['short_bench'], totals['residual_minutes']), ('partial', False, 0.), label)

    def test_eight_available_players_keep_the_quarter_uplift(self):
        team, projected, totals = self.run_forecast(8, [explicit_out(f'o{i}') for i in range(9)])
        self.assertFalse(short_bench(team))
        rotation = projected[0]['components']['rotation']
        self.assertFalse(rotation['short_bench'])
        self.assertTrue(all(p['expected_minutes'] <= 20 * 1.25 + 1e-9 for p in projected))
        self.assertGreater(rotation['unallocated_minutes'], 0)
        self.assertEqual(totals['status'], 'complete')
        self.assertGreater(totals['residual_minutes'], 0)


if __name__ == '__main__':
    unittest.main()
