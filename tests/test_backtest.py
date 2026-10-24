"""The scoreboard replays a season from earlier game days and judges changes against noise."""
import gzip
import json
from pathlib import Path
import random
import tempfile
import unittest

from nba_forecast import backtest

TEAMS = ('A', 'B', 'C', 'D')
AS_OF = '2026-03-01T00:00:00Z'


def season(days=24):
    """Four teams, nine players each, two games a day; one bench player rests now and then."""
    rng = random.Random(7)
    roles = {t: [(f'{t}{i}', 34 if i < 5 else 20, .35 + .06 * i) for i in range(9)] for t in TEAMS}
    history, records = [], []
    for day in range(days):
        time = f'2026-01-{day + 1:02d}T23:00:00Z'
        known = f'2026-01-{day + 2:02d}T03:00:00Z'
        pairs = [('A', 'B'), ('C', 'D')] if day % 2 == 0 else [('B', 'A'), ('D', 'C')]
        for n, (home, away) in enumerate(pairs):
            gid = f'G{day:02d}{n}'
            team_points = {}
            for tid, opp in ((home, away), (away, home)):
                rest = 8 if day % 5 == 0 else None
                minutes = {}
                for index, (pid, base, _) in enumerate(roles[tid]):
                    minutes[pid] = 0 if index == rest else max(4, base + rng.gauss(0, 4))
                scale = 240 / sum(minutes.values())
                total = 0
                for index, (pid, base, rate) in enumerate(roles[tid]):
                    m = round(minutes[pid] * scale, 1)
                    played = m > 0
                    fg3m = int(m * rate * .12 + rng.random()) if played else 0
                    twos = int(m * rate * .3 + rng.random()) if played else 0
                    ftm = int(m * rate * .15 + rng.random()) if played else 0
                    pts = 2 * twos + 3 * fg3m + ftm
                    total += pts
                    history.append(dict(game_id=gid, season='2025-26', season_type='Regular Season', game_time=time, known_at=known,
                                        source_url=f'https://example.test/{gid}', player_id=pid, name='Player ' + pid, team_id=tid,
                                        opponent_id=opp, minutes=m, fgm=twos + fg3m, fga=twos + fg3m + int(.8 * (twos + fg3m)) + 1,
                                        fg3m=fg3m, fg3a=fg3m + int(.7 * fg3m) + 1, ftm=ftm, fta=ftm + int(.25 * ftm), tov=1 if played else 0,
                                        pts=pts, status='played' if played else 'dnp', starter=index < 5 and played,
                                        reb=int(m / 8), ast=int(m / 12), stl=0, blk=0))
                team_points[tid] = total
            records.append(dict(game_id=gid, game_time=time, known_at=known, season='2025-26', home_team_id=home, away_team_id=away,
                                team_minutes={home: 240., away: 240.}, team_stats={t: dict(pts=p) for t, p in team_points.items()},
                                venue_timezone=None))
    return history, records


class BacktestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history, cls.records = season()
        cls.folder = tempfile.TemporaryDirectory()
        cls.output = Path(cls.folder.name) / 'first'
        cls.board = backtest.run_backtest(cls.history, cls.records, cls.output, warmup_fraction=.5, as_of=AS_OF,
                                          sources=dict(history='synthetic'))

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_evaluation_set_is_every_game_after_the_warmup_dates(self):
        ordered, evaluated = backtest.evaluation_games(self.records, .5)
        self.assertEqual((len(ordered), len(evaluated)), (48, 24))
        self.assertEqual(evaluated[0]['game_id'], 'G120')
        self.assertEqual(self.board['games'], dict(total=48, evaluated=24, skipped={}))

    def test_scoreboard_covers_players_teams_and_games(self):
        m = self.board['metrics']
        self.assertGreater(m['players']['all']['n'], 150)
        self.assertEqual(set(m['players']['by_expected_minutes']), {'0-10', '10-20', '20-30', '30+'})
        for key in ('points_mae', 'minutes_mae', 'coverage80', 'mean_width80', 'points_bias'):
            self.assertIsInstance(m['players']['all'][key], float)
        self.assertGreater(m['players']['projected_dnp']['count'], 0)
        self.assertEqual(m['teams']['n'], 48)
        self.assertEqual(m['games']['n'], 24)
        self.assertLess(m['games']['margin_mae'], 40)
        self.assertEqual(self.board['parameters']['heuristic_z80'], backtest.HEURISTIC_Z80)
        self.assertEqual(self.board['sources'], dict(history='synthetic'))
        saved = json.loads(gzip.decompress((self.output / 'rows.json.gz').read_bytes()))
        self.assertEqual(saved['player_fields'], list(backtest.PLAYER_FIELDS))
        self.assertEqual(json.loads((self.output / 'scoreboard.json').read_text())['metrics'], m)

    def test_comparison_pairs_the_same_rows_and_reports_noise(self):
        rows = backtest.read_json(self.output / 'rows.json.gz')
        same = backtest.compare(rows, rows)
        self.assertEqual(same['verdict'], 'within_noise')
        self.assertEqual([same[k]['mean'] for k in ('points', 'minutes', 'team_points', 'margin')], [0, 0, 0, 0])
        worse = json.loads(json.dumps(rows))
        mean_index = backtest.PLAYER_FIELDS.index('points_mean')
        for r in worse['players']:
            if r[mean_index] is not None:
                r[mean_index] += 6
        self.assertEqual(backtest.compare(rows, worse)['points']['verdict'], 'improved')
        self.assertEqual(backtest.compare(worse, rows)['verdict'], 'worse')
        self.assertEqual(backtest.compare(rows, worse)['minutes']['verdict'], 'within_noise')
        with tempfile.TemporaryDirectory() as d:
            board = backtest.run_backtest(self.history, self.records, Path(d) / 'again', warmup_fraction=.5,
                                          as_of=AS_OF, reference=self.output, limit=6)
        self.assertEqual(board['games']['evaluated'], 6)
        self.assertEqual(board['comparison']['verdict'], 'within_noise')
        self.assertEqual(board['comparison']['points']['n'], board['metrics']['players']['all']['n'])
        self.assertIn('vs reference', backtest.format_scoreboard(board))

    def test_refuses_future_known_sources_and_a_used_output(self):
        with self.assertRaisesRegex(ValueError, 'future-known'):
            backtest.run_backtest(self.history, self.records, self.folder.name + '/x', as_of='2026-01-10T00:00:00Z')
        with self.assertRaisesRegex(ValueError, 'not empty'):
            backtest.run_backtest(self.history, self.records, self.output, as_of=AS_OF)


if __name__ == '__main__':
    unittest.main()
