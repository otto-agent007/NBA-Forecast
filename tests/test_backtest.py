"""The scoreboard replays a season from earlier game days and judges changes against noise."""
import gzip
import json
from pathlib import Path
import random
import tempfile
import unittest
import unittest.mock

from nba_forecast import backtest
from nba_forecast.model import STATUS_PARTICIPATION

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

    def test_a_limited_replay_reports_the_interval_score_without_gating_it(self):
        rows = backtest.read_json(self.output / 'rows.json.gz')
        wider = json.loads(json.dumps(rows))
        upper = backtest.PLAYER_FIELDS.index('points_upper80')
        for r in wider['players']:
            if r[upper] is not None:
                r[upper] += 10  # a worse interval score and nothing else
        gated = backtest.compare(wider, rows)
        self.assertEqual((gated['interval_score']['verdict'], gated['verdict']), ('worse', 'worse'))
        reported = backtest.compare(wider, rows, ungated=backtest.LIMIT_UNGATED)
        self.assertEqual((reported['interval_score']['verdict'], reported['verdict']), ('worse', 'within_noise'))
        self.assertFalse(reported['interval_score']['gated'])
        self.assertEqual(list(reported['ungated']), ['interval_score'])
        with tempfile.TemporaryDirectory() as d:
            board = backtest.run_backtest(self.history, self.records, Path(d) / 'l', warmup_fraction=.5,
                                          as_of=AS_OF, reference=self.output, limit=6)
        self.assertFalse(board['comparison']['interval_score']['gated'])
        self.assertIn('not gated: limited replay', backtest.format_scoreboard(board))

    def test_margin_probabilities_are_scored_and_compared(self):
        with unittest.mock.patch.object(backtest, 'MARGIN_PRIOR_MIN_GAMES', 10), tempfile.TemporaryDirectory() as d:
            board = backtest.run_backtest(self.history, self.records, Path(d) / 'p', warmup_fraction=.5, as_of=AS_OF)
            rows = backtest.read_json(Path(d) / 'p' / 'rows.json.gz')
        pr = board['metrics']['games']['probabilities']
        self.assertEqual((pr['n'], rows['game_fields']), (24, list(backtest.GAME_FIELDS)))
        self.assertTrue(0 <= pr['winner_brier'] <= 1 and pr['log_loss'] > 0)
        self.assertEqual(sum(b['n'] for b in pr['reliability']), 24)
        fit = pr['calibration_fit']
        self.assertTrue(-8 <= fit['location_bias'] <= 8 and .7 <= fit['scale_factor'] <= 1.4)
        self.assertLessEqual(fit['log_loss'], fit['log_loss_prior'])
        self.assertIsNone(pr['holdout'], 'too few games to hold out')
        self.assertIn('margin probabilities', backtest.format_scoreboard(board))
        same = backtest.compare(rows, rows)
        self.assertEqual((same['winner']['n'], same['winner']['mean']), (24, 0))
        legacy = dict(rows)
        legacy.pop('games')
        # Scored with the current prior scale on its team-total location, which the
        # current run's team-rating blend moves.
        legacy_winner = backtest.compare(rows, legacy)['winner']
        self.assertEqual(legacy_winner['n'], 24, 'a reference without margin rows is scored with the current prior scale')
        self.assertIsNotNone(legacy_winner['mean'])
        self.assertEqual(backtest.compare(legacy, rows)['winner'].get('n'), 0)

    def test_team_totals_agree_with_the_margin_location(self):
        with unittest.mock.patch.object(backtest, 'MARGIN_PRIOR_MIN_GAMES', 10), tempfile.TemporaryDirectory() as d:
            backtest.run_backtest(self.history, self.records, Path(d) / 's', warmup_fraction=.5, as_of=AS_OF)
            rows = backtest.read_json(Path(d) / 's' / 'rows.json.gz')
        teams = {(t[0], t[2]): t[3] for t in rows['teams']}
        scales = {}
        for g in (dict(zip(rows['game_fields'], g)) for g in rows['games']):
            self.assertAlmostEqual(teams[(g['game_id'], True)] - teams[(g['game_id'], False)], g['projected_margin'])
            scales.setdefault(g['game_id'][:3], set()).add(g['prior_sd'])
        # The scale is the outcome prior of earlier finals: one per day, never a player's points SD.
        self.assertEqual({len(s) for s in scales.values()}, {1})

    def test_market_metrics_score_the_close_and_a_naive_rating_on_the_same_games(self):
        rows = backtest.read_json(self.output / 'rows.json.gz')
        games = [dict(zip(backtest.GAME_FIELDS, g)) for g in rows['games']]
        perfect = {g['game_id']: dict(home_spread=-g['actual_margin'], total=None, home_moneyline=None,
                                      away_moneyline=None) for g in games}
        m = backtest.market_metrics(rows['games'], rows['teams'], self.records, perfect)
        self.assertEqual(m['n'], len(games))
        self.assertEqual(m['market']['margin_mae'], 0)
        self.assertIn('close_teacher', m)
        self.assertEqual(m['close_teacher']['overall']['n'], m['n'])
        self.assertAlmostEqual(m['encompassing']['slope'], 0)  # nothing can add to a perfect close
        self.assertGreater(m['naive_point_differential']['n'], 0)
        echo = {g['game_id']: dict(home_spread=-g['projected_margin'], total=230., home_moneyline=-150,
                                   away_moneyline=130) for g in games}
        m = backtest.market_metrics(rows['games'], rows['teams'], self.records, echo)
        self.assertAlmostEqual(m['market']['margin_mae'], m['model']['margin_mae'])
        self.assertNotIn('encompassing', m)  # the model never disagrees with this close
        self.assertAlmostEqual(backtest._no_vig_home(echo[games[0]['game_id']]), .6 / (.6 + 100 / 230))
        self.assertEqual(m['team_points']['n'], 2 * len(games))
        with tempfile.TemporaryDirectory() as d:
            board = backtest.run_backtest(self.history, self.records, Path(d) / 'm', warmup_fraction=.5, as_of=AS_OF,
                                          market=echo)
        self.assertIn('closing line', backtest.format_scoreboard(board))

    def test_injury_reports_set_each_games_statuses(self):
        # Each rest day's report lists the resting bench player out and a starter questionable.
        injuries = {}
        for r in self.records:
            day = int(r['game_id'][1:3])
            evidence = dict(source_url=f'https://example.test/report-{day}', published_at=r['game_time'],
                            retrieved_at='2026-02-01T00:00:00Z', sha256='0' * 64)
            if day % 5 == 0:
                statuses = {f'{t}8': ['out', 'Out'] for t in (r['home_team_id'], r['away_team_id'])}
                statuses[r['home_team_id'] + '0'] = ['questionable', 'Questionable']
                injuries[r['game_id']] = dict(report=evidence, statuses=statuses,
                                              complete_team_ids=[r['home_team_id'], r['away_team_id']])
            elif day % 5 == 1:
                injuries[r['game_id']] = dict(report=evidence, statuses={}, complete_team_ids=[r['away_team_id']])
        with tempfile.TemporaryDirectory() as d:
            board = backtest.run_backtest(self.history, self.records, Path(d) / 'i', warmup_fraction=.5, as_of=AS_OF,
                                          injuries=injuries)
            rows = backtest.read_json(Path(d) / 'i' / 'rows.json.gz')
        players = [dict(zip(backtest.PLAYER_FIELDS, p)) for p in rows['players']]
        rested = [p for p in players if p['player_id'].endswith('8') and int(p['game_id'][1:3]) % 5 == 0]
        self.assertTrue(rested)
        self.assertTrue(all(p['expected_minutes'] is None for p in rested))  # out: no minutes drawn
        home = {r['game_id']: r['home_team_id'] for r in self.records}
        questionable = [p for p in players if p['player_id'] == home[p['game_id']] + '0'
                        and int(p['game_id'][1:3]) % 5 == 0]
        self.assertTrue(questionable)
        self.assertTrue(all(p['participation_probability'] <= STATUS_PARTICIPATION['questionable'] for p in questionable))
        counts = board['games']['availability']['by_status']
        self.assertEqual(counts['out']['played'], 0)
        self.assertEqual(set(counts), {'out', 'questionable', 'not listed', 'no complete report'})
        self.assertEqual(board['games']['availability']['games_with_report'],
                         sum(1 for r in self.records[24:] if int(r['game_id'][1:3]) % 5 in (0, 1)))
        self.assertIn('injury report', board['limitations'])
        self.assertIn('injury reports:', backtest.format_scoreboard(board))
        # Without a report every player stays available, as before.
        self.assertEqual(backtest._roster([self.history[0]], None)[0]['status'], 'available')

    def test_a_listed_player_missing_from_the_box_score_is_forecast_and_scored_as_sitting(self):
        game = self.records[-1]
        team = game['home_team_id']
        history = [r for r in self.history if not (r['game_id'] == game['game_id'] and r['player_id'] == team + '1')]
        evidence = dict(source_url='https://example.test/report', published_at=game['game_time'],
                        retrieved_at='2026-02-01T00:00:00Z', sha256='0' * 64)
        injuries = {game['game_id']: dict(report=evidence, complete_team_ids=[team],
                                          statuses={team + '1': ['questionable', 'Doubtful', team]})}
        with tempfile.TemporaryDirectory() as d:
            board = backtest.run_backtest(history, self.records, Path(d) / 'a', warmup_fraction=.5, as_of=AS_OF,
                                          injuries=injuries, limit=1)
            rows = backtest.read_json(Path(d) / 'a' / 'rows.json.gz')
        players = {p[1]: dict(zip(backtest.PLAYER_FIELDS, p)) for p in rows['players']}
        sat = players[team + '1']
        self.assertEqual((sat['actual_status'], sat['actual_minutes']), ('dnp', 0.))
        self.assertLess(sat['participation_probability'], .3)  # doubtful
        self.assertEqual(board['games']['availability']['by_status']['doubtful'], dict(players=1, played=0, play_rate=0.))

    def test_a_reported_out_regular_lowers_his_teams_rating_in_the_replay(self):
        from nba_forecast import margins
        game = self.records[-1]
        team = game['home_team_id']
        evidence = dict(source_url='https://example.test/report', published_at=game['game_time'],
                        retrieved_at='2026-02-01T00:00:00Z', sha256='0' * 64)
        injuries = {game['game_id']: dict(report=evidence, complete_team_ids=[team],
                                          statuses={team + '0': ['out', 'Out', team]})}
        locations = []
        for per_minute in (margins.ABSENCE_RATING_PER_MINUTE, 0.):
            with tempfile.TemporaryDirectory() as d, unittest.mock.patch.object(margins, 'ABSENCE_RATING_PER_MINUTE', per_minute):
                backtest.run_backtest(self.history, self.records, Path(d) / 'o', warmup_fraction=.5, as_of=AS_OF,
                                      injuries=injuries, limit=1)
                locations.append(backtest.read_json(Path(d) / 'o' / 'rows.json.gz')['games'][0][3])
        before = [r['minutes'] for r in self.history if r['player_id'] == team + '0' and r['game_time'] < game['game_time']
                  and r['status'] == 'played']
        expected = -margins.RATING_WEIGHT * margins.ABSENCE_RATING_PER_MINUTE * sum(before) / len(before)
        self.assertAlmostEqual(locations[0] - locations[1], expected)

    def test_seasons_replay_separately_and_pool_into_one_verdict(self):
        def earlier(value):
            return value.replace('2026-', '2025-', 1)
        history = [dict(r, game_id='X' + r['game_id'], season='2024-25', game_time=earlier(r['game_time']),
                        known_at=earlier(r['known_at'])) for r in self.history]
        records = [dict(r, game_id='X' + r['game_id'], season='2024-25', game_time=earlier(r['game_time']),
                        known_at=earlier(r['known_at'])) for r in self.records]
        seasons = [dict(history=history, records=records, sources=dict(team_games='datasets/earlier/team-games.json.gz')),
                   dict(history=self.history, records=self.records, sources=dict(team_games='datasets/later/team-games.json.gz'))]
        for season in seasons:
            season['market'] = {r['game_id']: dict(home_spread=-1., total=None) for r in season['records']}
        with tempfile.TemporaryDirectory() as d:
            board = backtest.run_seasons(seasons, Path(d) / 'both', warmup_fraction=.5, as_of=AS_OF)
            again = backtest.run_seasons(seasons, Path(d) / 'again', warmup_fraction=.5, as_of=AS_OF,
                                         reference=Path(d) / 'both')
        one = self.board['metrics']['players']['all']
        self.assertEqual(board['metrics']['players']['all']['n'], 2 * one['n'])  # the same season twice
        self.assertEqual(board['games']['evaluated'], 48)
        self.assertEqual(board['metrics']['market']['close_teacher']['overall']['n'], 48)
        self.assertEqual(len(board['metrics']['market']['close_teacher']['by_season']), 2)
        self.assertEqual([s['metrics']['players']['all']['n'] for s in board['seasons']], [one['n'], one['n']])
        self.assertAlmostEqual(board['seasons'][1]['metrics']['players']['all']['points_mae'], one['points_mae'])
        self.assertEqual(again['comparison']['verdict'], 'within_noise')
        self.assertEqual([s['comparison']['points']['n'] for s in again['seasons']], [one['n'], one['n']])
        text = backtest.format_scoreboard(again)
        self.assertEqual([s['label'] for s in board['seasons']], ['2024-25', '2025-26'])
        self.assertIn('season 2024-25:', text)
        self.assertIn('(pooled over seasons)', text)

    def test_the_previous_season_seeds_opening_night(self):
        from nba_forecast import margins
        def earlier(value):
            return value.replace('2026-', '2025-', 1)
        history = [dict(r, game_id='X' + r['game_id'], season='2024-25', game_time=earlier(r['game_time']),
                        known_at=earlier(r['known_at'])) for r in self.history]
        records = [dict(r, game_id='X' + r['game_id'], season='2024-25', game_time=earlier(r['game_time']),
                        known_at=earlier(r['known_at'])) for r in self.records]
        later = [dict(r, season='2025-26') for r in self.records]
        # The location is checked against the player model's own totals, so they stay unscaled here.
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.object(backtest, 'reconcile', lambda *a, **k: None):
            cold = backtest.run_seasons([dict(history=self.history, records=later)], Path(d) / 'cold',
                                        warmup_fraction=0., as_of=AS_OF)
            seeded = backtest.run_seasons([dict(history=self.history, records=later, previous_history=history,
                                                previous_records=records)], Path(d) / 'seeded', warmup_fraction=0., as_of=AS_OF)
            rows = {k: backtest.read_json(Path(d) / k / 'rows.json.gz') for k in ('cold', 'seeded')}
        first = {r['game_id'] for r in later if r['game_time'] == later[0]['game_time']}
        self.assertFalse(first & {g[0] for g in rows['cold']['games']})  # nothing to forecast opening night from
        self.assertEqual(first, first & {g[0] for g in rows['seeded']['games']})
        self.assertEqual(seeded['games']['evaluated'], 48)
        self.assertEqual(seeded['parameters']['previous_season_seeded'], [True])
        self.assertNotIn('previous_season_seeded', cold['parameters'])
        # Opening night blends the player sum with last season's home edge and shrunk ratings.
        samples = [backtest._final_sample(g) for g in records]
        ratings = margins.team_ratings(samples, later[0]['game_time'])
        self.assertTrue(all(r['games'] == 0 and r['prior'] for r in ratings.values()))
        edge = sum(s['home_points'] - s['away_points'] for s in samples) / len(samples)
        teams = {(t[0], t[1]): t[3] for t in rows['seeded']['teams']}
        for gid, home, away, location, *_ in rows['seeded']['games']:
            if gid in first:
                expected, _ = margins.blended_location(teams[gid, home] - teams[gid, away], edge, ratings, home, away)
                self.assertAlmostEqual(location, expected)

    def test_the_production_path_reads_only_earlier_games_and_moves_the_forecast(self):
        from nba_forecast import candidate
        counts = ('minutes', 'fgm', 'fga', 'fg3m', 'fg3a', 'ftm', 'fta', 'tov', 'pts', 'reb', 'ast', 'stl', 'blk')
        # The production path validates history strictly: a did-not-play row has zero counts.
        history = [r if r['status'] == 'played' else dict(r, **dict.fromkeys(counts, 0)) for r in self.history]
        box = {}
        for r in history:
            t = box.setdefault((r['game_id'], r['team_id']), dict(fga=0, fta=0, oreb=0, tov=0))
            for key in ('fga', 'fta', 'tov'):
                t[key] += r[key]
            t['oreb'] += r['reb'] // 4
        records = [dict(r, team_stats={t: dict(s, **box[(r['game_id'], t)]) for t, s in r['team_stats'].items()})
                   for r in self.records]
        artifact = dict(model_version='regular-season-baseline-0.5.0', model_key='k' * 64, known_at='2026-01-01T00:00:00Z',
                        parameters=dict(pace_strength=.25, defense_strength=1., context_strength=.5, rotation_enabled=True,
                                        context_features_enabled=True), context_model=None, excluded_history_game_ids=[])
        seen, real = [], candidate.forecast_candidate
        def spy(snapshot, history):
            seen.append(snapshot)
            return real(snapshot, history)
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.object(candidate, 'forecast_candidate', spy):
            production = backtest.run_seasons([dict(history=history, records=records)], Path(d) / 'p', warmup_fraction=.5,
                                              as_of=AS_OF, artifact=artifact)
            baseline = backtest.run_seasons([dict(history=history, records=records)], Path(d) / 'b', warmup_fraction=.5,
                                            as_of=AS_OF)
            rows = {k: backtest.read_json(Path(d) / k / 'rows.json.gz')['players'] for k in ('p', 'b')}
        time = {r['game_id']: r['game_time'] for r in records}
        self.assertEqual(len(seen), 24)
        for snapshot in seen:
            game = snapshot['games'][0]
            used = {gid for team in snapshot['model_enhancements']['environment']['teams'].values() for gid in team['audit']['game_ids']}
            self.assertTrue(used)
            self.assertTrue(all(backtest._day(time[gid]) < backtest._day(game['start_time']) for gid in used))
            self.assertEqual(set(snapshot['schedule_context']), {game['home_team_id'], game['away_team_id']})
        self.assertEqual((production['model_path'], baseline['model_path']), ('production', 'baseline'))
        self.assertEqual(production['parameters']['production_artifact']['parameters'], artifact['parameters'])
        self.assertNotEqual([r[5] for r in rows['p']], [r[5] for r in rows['b']])  # pace and defense move points

    def test_refuses_future_known_sources_and_a_used_output(self):
        with self.assertRaisesRegex(ValueError, 'future-known'):
            backtest.run_backtest(self.history, self.records, self.folder.name + '/x', as_of='2026-01-10T00:00:00Z')
        with self.assertRaisesRegex(ValueError, 'not empty'):
            backtest.run_backtest(self.history, self.records, self.output, as_of=AS_OF)


class MaterialityTests(unittest.TestCase):
    def test_a_change_below_the_floor_is_noise_however_many_rows_agree(self):
        rng = random.Random(6)
        reference = {f'G{i:05d}:p': 5. for i in range(20000)}
        current = {k: v * 1.002 + rng.gauss(0, .001) for k, v in reference.items()}  # 0.2% worse, tiny noise
        without = backtest._paired(current, reference, materiality=0)
        with_floor = backtest._paired(current, reference)
        self.assertEqual(without['verdict'], 'worse')
        self.assertEqual(with_floor['verdict'], 'within_noise')
        self.assertAlmostEqual(with_floor['materiality_floor'], backtest.MATERIALITY * 5.)
        self.assertGreater(with_floor['materiality_floor'], 2 * with_floor['se'])
        larger = {k: v * 1.01 for k, v in reference.items()}  # 1% is material
        self.assertEqual(backtest._paired(larger, reference)['verdict'], 'worse')
        self.assertEqual(backtest._paired(reference, larger)['verdict'], 'improved')


class EncompassingTests(unittest.TestCase):
    def test_the_slope_error_is_clustered_by_date_and_never_below_ols(self):
        rng = random.Random(8)
        game_rows, records, lines = [], [], {}
        for day in range(1, 31):
            shock = rng.gauss(0, 6)  # every game that day shares a miss of the close
            lean = rng.gauss(0, 3)  # and the model's disagreement with it is shared too (the day's news)
            for n in range(5):
                gid = f'D{day:02d}{n}'
                market = rng.gauss(0, 8)
                model = market + lean + rng.gauss(0, 1.5)
                actual = market + shock + .1 * (model - market) + rng.gauss(0, 1)
                game_rows.append([gid, f'H{n}', f'A{n}', model, round(actual), 15., .5])
                records.append(dict(game_id=gid, game_time=f'2026-01-{day:02d}T23:00:00Z', season='2025-26',
                                    home_team_id=f'H{n}', away_team_id=f'A{n}',
                                    team_stats={f'H{n}': dict(pts=110), f'A{n}': dict(pts=110 - round(actual))}))
                lines[gid] = dict(home_spread=-market, total=None, home_moneyline=None, away_moneyline=None)
        e = backtest.market_metrics(game_rows, [], records, lines)['encompassing']
        self.assertEqual(e['clusters'], 30)
        self.assertGreater(e['se'], 1.5 * e['se_ols'])
        self.assertGreaterEqual(e['se'], e['se_ols'])
        # One game per date: the clustered error is the robust one, still never below OLS.
        spread = [dict(r, game_time=f'2026-0{1 + i // 150}-{1 + i % 28:02d}T{i % 24:02d}:00:00Z') for i, r in enumerate(records)]
        single = backtest.market_metrics(game_rows, [], spread, lines)['encompassing']
        self.assertGreaterEqual(single['se'], single['se_ols'])


class ClusteredStandardErrorTests(unittest.TestCase):
    def test_shared_shocks_inflate_the_standard_error_by_the_cluster_size(self):
        rng = random.Random(3)
        shocks = [rng.gauss(0, 1) for _ in range(200)]
        values = [s for s in shocks for _ in range(10)]
        labels = [(str(i // 10), str(i % 10)) for i in range(len(values))]
        se, effect = backtest._mean_se(values, labels)
        self.assertAlmostEqual(effect, 10, delta=.5)
        self.assertGreater(se, backtest._mean_se(values, None)[0] * 3)

    def test_independent_errors_and_negative_correlation_keep_the_independent_floor(self):
        rng = random.Random(4)
        values = [rng.gauss(0, 1) for _ in range(2000)]
        labels = [(str(i // 20), str(i % 50)) for i in range(len(values))]
        self.assertLess(backtest._mean_se(values, labels)[1], 1.3)
        alternating = [1., -1.] * 500
        se, effect = backtest._mean_se(alternating, [(str(i // 2),) for i in range(len(alternating))])
        self.assertEqual(effect, 1.0)
        self.assertAlmostEqual(se, backtest._mean_se(alternating, None)[0])

    def test_a_correlated_player_shift_is_noise_once_clustered(self):
        # Each player's whole season moves together: 40 independent draws, not 2,400.
        rng = random.Random(5)
        current, reference = {}, {}
        for player in range(40):
            shift = rng.gauss(0, 1)
            for game in range(60):
                key = f'G{game:03d}:{player}'
                reference[key] = 0.
                current[key] = .1 + shift + rng.gauss(0, .1)
        independent = backtest._paired(current, reference)
        clustered = backtest._paired(current, reference, lambda key: tuple(key.split(':')))
        self.assertEqual(independent['verdict'], 'worse')
        self.assertEqual(clustered['verdict'], 'within_noise')
        self.assertGreater(clustered['design_effect'], 20)


if __name__ == '__main__':
    unittest.main()
