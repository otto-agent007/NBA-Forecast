"""An ESPN summary becomes replay history rows and a team-game record, or is refused."""
import copy
import unittest

from nba_forecast import espn_season
from nba_forecast.reference import BASE

TEAMS = {'1610612747': ('Los Angeles Lakers', 'LAL'), '1610612757': ('Portland Trail Blazers', 'POR')}
KEYS = ['minutes', 'points', 'fieldGoalsMade-fieldGoalsAttempted', 'threePointFieldGoalsMade-threePointFieldGoalsAttempted',
        'freeThrowsMade-freeThrowsAttempted', 'rebounds', 'assists', 'turnovers', 'steals', 'blocks',
        'offensiveRebounds', 'defensiveRebounds', 'fouls', 'plusMinus']


def player(pid, name, minutes, made, attempts, threes, fts, tov, starter=True):
    pts = 2 * made + threes + fts
    return dict(athlete=dict(id=pid, displayName=name), starter=starter, didNotPlay=False,
                stats=[str(minutes), str(pts), f'{made}-{attempts}', f'{threes}-{threes + 1}', f'{fts}-{fts}',
                       '5', '3', str(tov), '1', '0', '1', '4', '2', '+3'])


def team(espn_id, abbreviation, side, score, periods, players, fga, fta, tov):
    return (dict(homeAway=side, score=str(score), team=dict(id=espn_id, abbreviation=abbreviation),
                 linescores=[{}] * periods),
            dict(team=dict(id=espn_id), statistics=[dict(keys=KEYS, athletes=players)]),
            dict(team=dict(id=espn_id), statistics=[
                dict(name='fieldGoalsMade-fieldGoalsAttempted', displayValue=f'0-{fga}'),
                dict(name='freeThrowsMade-freeThrowsAttempted', displayValue=f'0-{fta}'),
                dict(name='offensiveRebounds', displayValue='9'), dict(name='turnovers', displayValue=str(tov)),
                dict(name='teamTurnovers', displayValue='1'), dict(name='totalTurnovers', displayValue=str(tov + 1))]))


def summary(periods=4, note=None):
    home = team('13', 'LAL', 'home', 20, periods, [player('1966', 'LeBron James', 40, 8, 15, 2, 2, 3),
                                                   dict(athlete=dict(id='9', displayName='Bench Player'),
                                                        didNotPlay=True, reason="COACH'S DECISION", stats=[])],
                15, 2, 3)
    away = team('22', 'POR', 'away', 18, periods, [player('77', 'Away Guard', 38, 7, 16, 1, 3, 2)], 16, 3, 2)
    header = dict(id='401705034', season=dict(year=2025, type=2),
                  competitions=[dict(date='2025-01-03T03:30Z', status=dict(type=dict(completed=True)),
                                     competitors=[home[0], away[0]])])
    if note:
        header['gameNote'] = note
    return dict(header=header, boxscore=dict(players=[home[1], away[1]], teams=[home[2], away[2]]),
                gameInfo=dict(venue=dict(address=dict(city='Los Angeles', state='CA'))))


META = dict(source_url=BASE + 'summary?event=401705034', retrieved_at='2026-10-25T07:00:00+00:00', sha256='b' * 64)


class NormalizeTests(unittest.TestCase):
    def test_rows_and_record_use_nba_team_ids_and_espn_people(self):
        rows, record = espn_season.normalize(summary(), META, TEAMS, '2024-25')
        self.assertEqual([r['player_id'] for r in rows], ['espn-1966', 'espn-9', 'espn-77'])
        lebron = rows[0]
        self.assertEqual((lebron['team_id'], lebron['opponent_id'], lebron['minutes'], lebron['pts'], lebron['status']),
                         ('1610612747', '1610612757', 40., 20, 'played'))
        self.assertEqual((rows[1]['status'], rows[1]['minutes'], rows[1]['starter']), ('dnp', 0., False))
        self.assertEqual(record['game_id'], 'espn-401705034')
        self.assertEqual(record['game_time'], '2025-01-03T03:30:00Z')
        self.assertEqual(record['team_minutes'], {'1610612747': 240., '1610612757': 240.})
        self.assertEqual(record['team_stats']['1610612747']['tov'], 4)  # total turnovers
        self.assertEqual(record['venue_timezone'], 'America/Los_Angeles')
        self.assertEqual(record['provenance']['minutes_precision'], 'whole_minutes')

    def test_unrecorded_minutes_with_no_counts_did_not_play_and_bad_team_turnovers_fall_back(self):
        value = summary()
        home_box = value['boxscore']['players'][0]['statistics'][0]['athletes']
        home_box.append(dict(athlete=dict(id='5', displayName='Seconds Only'), didNotPlay=False,
                             stats=['--', '0', '0-0', '0-0', '0-0', '0', '0', '0', '0', '0', '0', '0', '0', '--']))
        for stat in value['boxscore']['teams'][1]['statistics']:
            if stat['name'] == 'teamTurnovers':
                stat['displayValue'] = '-2'
        rows, record = espn_season.normalize(value, META, TEAMS, '2024-25')
        self.assertEqual([(r['status'], r['minutes']) for r in rows if r['player_id'] == 'espn-5'], [('dnp', 0.)])
        away = record['team_stats']['1610612757']
        self.assertEqual((away['tov'], away['team_turnovers']), (2, None))
        self.assertIn('team turnovers invalid', record['provenance']['notes'][0])
        home_box[-1]['stats'][1] = '2'  # a '--' row with points is not silently dropped
        with self.assertRaises(ValueError):
            espn_season.normalize(value, META, TEAMS, '2024-25')

    def test_overtime_lengthens_team_minutes(self):
        _, record = espn_season.normalize(summary(periods=6), META, TEAMS, '2024-25')
        self.assertEqual(record['team_minutes']['1610612757'], 290.)

    def test_the_cup_final_mismatched_totals_and_other_seasons_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'Cup Championship'):
            espn_season.normalize(summary(note='NBA Cup Championship'), META, TEAMS, '2024-25')
        with self.assertRaisesRegex(ValueError, 'In-Season Tournament Championship'):  # its 2023-24 name
            espn_season.normalize(summary(note='NBA In-Season Tournament Championship'), META, TEAMS, '2024-25')
        # The tournament's semifinals count in the standings.
        espn_season.normalize(summary(note='NBA In-Season Tournament - Semifinals'), META, TEAMS, '2024-25')
        with self.assertRaisesRegex(ValueError, 'season'):
            espn_season.normalize(summary(), META, TEAMS, '2025-26')
        broken = copy.deepcopy(summary())
        broken['header']['competitions'][0]['competitors'][0]['score'] = '30'
        with self.assertRaisesRegex(ValueError, 'sum to the team total'):
            espn_season.normalize(broken, META, TEAMS, '2024-25')


if __name__ == '__main__':
    unittest.main()
