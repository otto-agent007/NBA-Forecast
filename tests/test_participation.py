"""Participation probabilities: who plays, how that weights the budget and totals, and how it is scored."""
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from nba_forecast import store
from nba_forecast.model import (PARTICIPATION_PRIORS, PARTICIPATION_PSEUDO_GAMES, STATUS_PARTICIPATION,
                                _participation, forecast)
from tests.test_model import player, row, snapshot
from tests.test_store import inputs, actual, NOW, LATER


def team_games(count, team='A'):
    return [(datetime(2025, 3, i + 1, 20, tzinfo=timezone.utc), f'g{i}') for i in range(count)]


def rows(played_games, dnp_games=(), pid='p', team='A', minutes=30, season='2025-26'):
    """Current-season rows by default (the test snapshot season), dated before its cutoff."""
    def one(i, **kw):
        r = row(i=i, pid=pid, team=team, season=season, minutes=minutes, **kw)
        if season == '2025-26':
            r.update(game_time=f'2025-12-{i % 28 + 1:02d}T20:00:00+00:00', known_at=f'2025-12-{i % 28 + 1:02d}T23:00:00+00:00')
        return r
    out = [one(i) for i in played_games]
    for i in dnp_games:
        out.append(dict(one(i, fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0), minutes=0, status='dnp'))
    return out


class ParticipationEstimateTests(unittest.TestCase):
    def test_recent_rate_is_shrunk_toward_the_role_prior(self):
        games = team_games(12)
        full = _participation(player(), rows(range(12)), games, 30)
        self.assertEqual((full['played'], full['games']), (10, 10))
        self.assertAlmostEqual(full['probability'], (10 + PARTICIPATION_PSEUDO_GAMES * .82) / (10 + PARTICIPATION_PSEUDO_GAMES))
        half = _participation(player(), rows(range(0, 12, 2), range(1, 12, 2), minutes=15), games, 15)
        self.assertAlmostEqual(half['probability'], (5 + PARTICIPATION_PSEUDO_GAMES * .70) / (10 + PARTICIPATION_PSEUDO_GAMES))
        self.assertEqual(half['prior'], .70)

    def test_games_before_joining_the_team_do_not_count_and_absences_do(self):
        games = team_games(12)
        newcomer = _participation(player(), rows([10, 11]), games, 30)
        self.assertEqual((newcomer['played'], newcomer['games']), (2, 2))
        absent = _participation(player(), rows([2, 3]), games, 8)
        self.assertEqual((absent['played'], absent['games']), (2, 10))
        self.assertAlmostEqual(absent['probability'], (2 + PARTICIPATION_PSEUDO_GAMES * .18) / 13)
        nobody = _participation(player(), [], games, 30)
        self.assertEqual((nobody['games'], nobody['probability']), (0, .82))
        early = _participation(player(), rows([0]), team_games(2), 30)
        self.assertEqual((early['games'], early['probability']), (0, .82), 'fewer than three team games: prior alone')

    def test_last_season_does_not_count_against_a_player(self):
        snap = snapshot([player(), player('q')])
        data = rows(range(10), pid='p', season='2024-25') + rows([0, 1, 2], pid='p') + rows(range(10), pid='q', season='2024-25') + rows([0, 1, 2], pid='q')
        data = [r for r in data if not (r['player_id'] == 'p' and r['season'] == '2024-25' and r['game_id'] in ('g5', 'g6', 'g7', 'g8', 'g9'))]
        by_id = {x['player_id']: x for x in forecast(snap, data)['predictions']}
        self.assertEqual(by_id['p']['components']['participation']['games'], 3)
        self.assertAlmostEqual(by_id['p']['participation_probability'], by_id['q']['participation_probability'])

    def test_reported_status_scales_the_role_rate(self):
        games = team_games(10)
        for label, factor in STATUS_PARTICIPATION.items():
            q = dict(player(status='questionable'), reported_status=label.title())
            self.assertAlmostEqual(_participation(q, rows(range(10)), games, 30)['probability'],
                                   factor * (10 + 3 * .82) / 13, msg=label)
        self.assertEqual(_participation(dict(player(status='questionable'), reported_status='Odd'), rows(range(10)), games, 30)['status_factor'], .5)


class ParticipationForecastTests(unittest.TestCase):
    def history(self):
        data = []
        for pid, played in (('p', range(10)), ('bench', range(0, 10, 3))):
            data += rows(played, [i for i in range(10) if i not in played], pid=pid, minutes=30 if pid == 'p' else 12)
        return data

    def test_questionable_players_are_projected_conditionally_with_a_reduced_probability(self):
        snap = snapshot([player(), player('bench'), dict(player('q', status='questionable'), reported_status='Questionable')])
        data = self.history() + rows(range(10), pid='q')
        by_id = {p['player_id']: p for p in forecast(snap, data)['predictions']}
        self.assertEqual(by_id['q']['status'], 'projected')
        self.assertIn('availability_questionable', by_id['q']['flags'])
        self.assertAlmostEqual(by_id['q']['participation_probability'], .55 * by_id['p']['participation_probability'])
        self.assertGreater(by_id['p']['participation_probability'], .9)
        self.assertLess(by_id['bench']['participation_probability'], .5)
        self.assertIn('participation_uncertain', by_id['bench']['flags'])
        self.assertEqual(by_id['q']['points_mean'], by_id['p']['points_mean'], 'projections stay conditional on playing')

    def test_budget_and_totals_use_expected_minutes(self):
        snap = snapshot([player(f'p{i}') for i in range(12)])
        data = []
        for i in range(12):
            data += rows(range(10) if i < 8 else range(0, 10, 5), [] if i < 8 else [k for k in range(10) if k % 5], pid=f'p{i}', minutes=36 if i < 8 else 20)
        result = forecast(snap, data)
        projected = [p for p in result['predictions'] if p['status'] == 'projected']
        expected = sum(p['participation_probability'] * p['expected_minutes'] for p in projected)
        self.assertLessEqual(expected, 240 + 1e-6)
        self.assertGreater(sum(p['expected_minutes'] for p in projected), expected)
        total = next(t for t in result['game_totals'][0]['teams'] if t['team_id'] == 'A')
        self.assertEqual(total['projected_minutes'], expected)
        self.assertAlmostEqual(total['points_mean'], sum(p['participation_probability'] * p['points_mean'] for p in projected))
        self.assertEqual(result['game_totals'][0]['schema_version'], 3)
        self.assertAlmostEqual(total['expected_players'], sum(p['participation_probability'] for p in projected))


class ActiveListTests(unittest.TestCase):
    def test_team_probabilities_cannot_sum_past_thirteen(self):
        from nba_forecast.model import ACTIVE_LIST_MAX
        snap = snapshot([player(f'p{i}') for i in range(16)])
        data = []
        for i in range(16):
            data += rows(range(10), pid=f'p{i}', minutes=30)
        projected = [p for p in forecast(snap, data)['predictions'] if p['status'] == 'projected']
        total = sum(p['participation_probability'] for p in projected)
        self.assertAlmostEqual(total, ACTIVE_LIST_MAX)
        self.assertEqual(projected[0]['components']['participation']['team_probability_goal'], ACTIVE_LIST_MAX)
        self.assertGreater(projected[0]['components']['participation']['team_probability_sum_before'], ACTIVE_LIST_MAX)
        self.assertLessEqual(sum(p['participation_probability'] * p['expected_minutes'] for p in projected), 240 + 1e-6)


    def test_a_full_team_uses_at_least_ten_players(self):
        from nba_forecast.model import ACTIVE_LIST_MIN_EXPECTED
        snap = snapshot([player(f'p{i}') for i in range(12)])
        data = []
        for i in range(12):
            data += rows(range(2), pid=f'p{i}', minutes=20)  # two team games: priors alone (0.70 each)
        projected = [p for p in forecast(snap, data)['predictions'] if p['status'] == 'projected']
        self.assertAlmostEqual(sum(p['participation_probability'] for p in projected), ACTIVE_LIST_MIN_EXPECTED)
        self.assertTrue(all(p['participation_probability'] <= 1 for p in projected))
        self.assertEqual(projected[0]['components']['participation']['team_probability_goal'], ACTIVE_LIST_MIN_EXPECTED)

    def test_the_floor_leaves_reported_statuses_alone(self):
        from nba_forecast.model import ACTIVE_LIST_MIN_EXPECTED
        players = [player(f'p{i}') for i in range(11)] + [dict(player('q', status='questionable'), reported_status='Questionable')]
        data = []
        for pid in [p['player_id'] for p in players]:
            data += rows(range(2), pid=pid, minutes=20)
        by_id = {p['player_id']: p for p in forecast(snapshot(players), data)['predictions'] if p['status'] == 'projected'}
        self.assertAlmostEqual(by_id['q']['participation_probability'], .55 * by_id['q']['components']['participation']['prior'],
                               msg='questionable: status times prior, not raised')
        self.assertAlmostEqual(sum(p['participation_probability'] for p in by_id.values()), ACTIVE_LIST_MIN_EXPECTED)
        self.assertTrue(all(p['participation_probability'] < 1 for p in by_id.values()))


class LedgerParticipationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot, self.history, self.forecast = inputs()
        self.db = __import__('tempfile').NamedTemporaryFile(suffix='.sqlite', delete=False).name
        for p in self.forecast['predictions']:
            if p['status'] == 'projected':
                p['participation_probability'] = .9

    def save(self):
        with patch('nba_forecast.store._now', return_value=NOW):
            return store.save_run(self.db, self.snapshot, self.history, self.forecast)

    def test_questionable_needs_a_probability_below_one_and_bounds_are_checked(self):
        q = self.snapshot['roster']['players'][0]; q['status'] = 'questionable'
        p1 = next(p for p in self.forecast['predictions'] if p['player_id'] == 'P1')
        p1['participation_probability'] = 1.0
        with self.assertRaisesRegex(ValueError, 'below one'):
            self.save()
        p1['participation_probability'] = .5
        self.save()
        p1['participation_probability'] = 1.5
        with self.assertRaisesRegex(ValueError, r'\[0, 1\]'):
            self.save()

    def test_scoring_reports_a_participation_brier(self):
        run_id = self.save()
        with patch('nba_forecast.store._now', return_value=LATER):
            report = store.evaluate_run(self.db, run_id, [actual(), actual('P2', status='dnp')])
        scored = report['metrics']['participation']
        self.assertEqual(scored['count'], 2)
        self.assertAlmostEqual(scored['brier'], ((.9 - 1) ** 2 + (.9 - 0) ** 2) / 2)
        self.assertEqual(scored['played_share'], .5)


if __name__ == '__main__':
    unittest.main()
