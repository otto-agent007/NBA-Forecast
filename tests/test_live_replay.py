"""The live replay tool's pure parts: run selection, as-of filters, pairing and the clustered errors."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from nba_forecast import backtest as B

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('live_replay', ROOT / 'scripts/research/live_replay.py')
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

GAME = dict(game_id='0022600100', start_time='2026-11-01T23:00:00+00:00', home_team_id='H', away_team_id='A')
KEY = ('0022600100:2026-11-01T23:00:00+00:00:{stage}:model:regular-season-baseline-0.6.0:parser:layout-exact-4:'
       'validator:independent-inputs-8:evidence:' + 'e' * 64 + ':boxscore:full-boxscore-1.0.0:configuration:' + 'c' * 64 +
       ':artifact:' + 'a' * 64 + ':history-prior:' + 'f' * 64)


def run(run_id, stage, created, order, mode='live', start=GAME['start_time']):
    return dict(run_id=run_id, stage=stage, start=start, created_at=created, insertion_order=order, mode=mode, scope='league')


class SelectionTests(unittest.TestCase):
    def test_job_keys_name_the_game_stage_model_and_artifact(self):
        parsed = L.parse_job_key(KEY.format(stage='final'))
        self.assertEqual((parsed['game_id'], parsed['stage'], parsed['model_version'], parsed['model_key'], parsed['artifact_sha256']),
                         ('0022600100', 'final', 'regular-season-baseline-0.6.0', 'c' * 64, 'a' * 64))
        self.assertEqual(parsed['start'], '2026-11-01T23:00:00+00:00')
        old = L.parse_job_key('0022600008:2026-10-23T23:00:00+00:00:final:model:regular-season-baseline-0.4.0:parser:layout-exact-3:slot:5975983')
        self.assertEqual((old['stage'], old['model_key'], old['artifact_sha256']), ('final', None, None))
        self.assertIsNone(L.parse_job_key('not a key'))
        by_game, unreadable = L.index_jobs({KEY.format(stage='final'): 'r1', KEY.format(stage='preliminary'): 'r2', 'junk': 'r3'})
        self.assertEqual((sorted(c['run_id'] for c in by_game['0022600100']), unreadable), (['r1', 'r2'], 1))

    def test_the_final_choice_is_the_market_reports_last_final_before_tip_else_the_last_preliminary(self):
        runs = [run('p1', 'preliminary', '2026-11-01T17:00:00+00:00', 1), run('p2', 'preliminary', '2026-11-01T22:00:00+00:00', 5),
                run('f1', 'final', '2026-11-01T22:31:00+00:00', 6), run('f2', 'final', '2026-11-01T22:40:00+00:00', 7),
                run('late', 'final', '2026-11-01T23:00:00+00:00', 8), run('replay', 'final', '2026-11-01T22:50:00+00:00', 9, mode='replay'),
                run('moved', 'final', '2026-11-01T22:55:00+00:00', 10, start='2026-11-02T00:00:00+00:00')]
        self.assertEqual(L.select_run(GAME, runs)['run_id'], 'f2', 'at tipoff is not pregame; a replay and another tipoff do not count')
        self.assertEqual(L.select_run(GAME, [r for r in runs if r['stage'] == 'preliminary'])['run_id'], 'p2')
        self.assertEqual(L.select_run(GAME, runs, 'first')['run_id'], 'p1')
        tie = [run('a', 'final', '2026-11-01T22:40:00+00:00', 3), run('b', 'final', '2026-11-01T22:40:00+00:00', 4)]
        self.assertEqual(L.select_run(GAME, tie)['run_id'], 'b', 'equal clocks fall to the ledger row order')
        self.assertIsNone(L.select_run(GAME, [runs[4]]))

    def test_finished_games_need_a_saved_final(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / f"{GAME['game_id']}.json").write_text('[]')
            schedule = dict(games=[GAME, dict(GAME, game_id='0022600101'), dict(GAME, game_id='0022600102', status='postponed')])
            (Path(d) / '0022600102.json').write_text('[]')
            self.assertEqual([g['game_id'] for g in L.finished_games(schedule, d)], [GAME['game_id']])


class AsOfTests(unittest.TestCase):
    def test_captures_count_from_their_retrieval_time(self):
        captures = [dict(retrieved_at='2026-11-01T20:00:00Z'), dict(retrieved_at='2026-11-02T09:00:00Z', recovered_close={})]
        self.assertEqual(L.as_of_captures(captures, '2026-11-02T08:59:59Z'), captures[:1])
        self.assertEqual(L.as_of_captures(captures, '2026-11-02T09:00:00Z'), captures)

    def test_finals_choose_the_newest_known_record_per_game_as_prepare_does(self):
        records = [('d1', dict(game_id='G1', known_at='2026-11-01T02:00:00Z')), ('d2', dict(game_id='G1', known_at='2026-11-03T02:00:00Z')),
                   ('d3', dict(game_id='G2', known_at='2026-11-02T02:00:00Z'))]
        self.assertEqual(L.as_of_finals(records, '2026-11-02T12:00:00Z'), dict(G1='d1', G2='d3'))
        self.assertEqual(L.as_of_finals(records, '2026-11-04T00:00:00Z'), dict(G1='d2', G2='d3'))
        self.assertEqual(L.as_of_finals(records, '2026-11-01T00:00:00Z'), {})

    def test_the_view_shows_prepare_only_the_named_finals_and_the_copys_lines(self):
        from nba_forecast.enhancements import _contemporary_games
        with tempfile.TemporaryDirectory() as d:
            copy = Path(d) / 'copy'
            folder = copy / 'environment-finals' / 'records'
            folder.mkdir(parents=True)
            (copy / 'market' / 'game-lines').mkdir(parents=True)
            digests = []
            for gid, known in (('G1', '2026-11-01T02:00:00+00:00'), ('G1', '2026-11-03T02:00:00+00:00'), ('G2', '2026-11-01T03:00:00+00:00')):
                raw = json.dumps(dict(game_id=gid, known_at=known, game_time='2026-11-01T00:00:00+00:00')).encode()
                digest = hashlib.sha256(raw).hexdigest()
                (folder / (digest + '.json')).write_bytes(raw)
                digests.append(digest)
            view = L.FinalsView(copy, Path(d) / 'view')
            records, sources = _contemporary_games(view.show(digests[:1] + digests[2:]), '2026-11-05T00:00:00+00:00')
            self.assertEqual(sorted(s['sha256'] for s in sources), sorted([digests[0], digests[2]]), 'the later G1 record is not shown')
            self.assertEqual(_contemporary_games(copy, '2026-11-05T00:00:00+00:00')[1][0]['sha256'], digests[1], 'unrestricted, prepare takes it')
            self.assertTrue((Path(d) / 'view' / 'market' / 'game-lines').is_dir())
            view.show([digests[1]])
            self.assertEqual([p.stem for p in view.records.iterdir()], [digests[1]])
            self.assertEqual(len(list(folder.iterdir())), 3, 'nothing in the copy changes')
            self.assertEqual(len(L.load_finals(copy)), 3)


class ThrottleTests(unittest.TestCase):
    def test_a_tick_that_started_over_two_minutes_ago_counts_as_stalled(self):
        from datetime import datetime, timezone
        now = datetime(2026, 11, 10, 8, 40, tzinfo=timezone.utc)
        self.assertFalse(L.tick_stalled(dict(started_at='2026-11-10T08:38:30+00:00'), now))
        self.assertTrue(L.tick_stalled(dict(started_at='2026-11-10T08:37:59+00:00'), now))
        self.assertFalse(L.tick_stalled({}, now), 'an unreadable record never blocks')

    def test_the_guard_waits_for_a_newer_finish(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'last-tick.json'
            path.write_text(json.dumps(dict(started_at='2026-11-10T08:00:00+00:00', finished_at='2026-11-10T08:01:00+00:00')))
            sleeps = []
            def tick_completes(seconds):
                sleeps.append(seconds)
                if len(sleeps) == 2:
                    path.write_text(json.dumps(dict(started_at='2026-11-10T08:02:00+00:00', finished_at='2026-11-10T08:12:00+00:00')))
            with patch.object(L.time, 'sleep', side_effect=tick_completes):
                waited = L.wait_for_tick(path, poll=15., log=lambda *a, **k: None)
            self.assertEqual((waited, sleeps), (30., [15., 15.]))
            fresh = dict(started_at=L.datetime.now(L.timezone.utc).isoformat(), finished_at='x')
            path.write_text(json.dumps(fresh))
            self.assertEqual(L.wait_for_tick(path, log=lambda *a, **k: None), 0.)

    def test_reading_is_kept_to_a_share_of_the_time(self):
        from unittest.mock import patch
        throttle = L.Throttle(share=1 / 3, after_history=1.)
        with patch.object(L.time, 'sleep') as sleep:
            throttle.rest(.2)
            throttle.rest(.1, history_read=True)
            throttle.rest(2., history_read=True)
            throttle.rest(0.)
        self.assertEqual([round(c.args[0], 9) for c in sleep.call_args_list], [.4, 1., 4.])
        self.assertAlmostEqual(throttle.slept, 5.4)


class StatisticsTests(unittest.TestCase):
    def test_the_clustered_error_is_the_backtests_one_way_date_cluster(self):
        values, dates = [1., 3., -2., 4., 0., 5.], ['d1', 'd1', 'd2', 'd2', 'd3', 'd3']
        mean, se = L.mean_se(values, dates)
        self.assertAlmostEqual(mean, 11 / 6)
        self.assertAlmostEqual(se, B._mean_se(values, [(d,) for d in dates])[0])
        # Hand check: cluster sums of residuals 4/6, -2/6... G/(G-1) * sum(s^2) / n^2, here below independence, so the independent error.
        sums = [sum(v - mean for v, d in zip(values, dates) if d == g) for g in ('d1', 'd2', 'd3')]
        clustered = 3 / 2 * sum(s * s for s in sums) / 36
        independent = sum((v - mean) ** 2 for v in values) / 6 / 6
        self.assertAlmostEqual(se ** 2, max(clustered, independent))
        self.assertEqual(L.mean_se([2.], ['d1']), (2., None))
        self.assertEqual(L.mean_se([], []), (None, None))

    def test_paired_differences_use_only_items_with_both_values(self):
        items = [dict(date='d1', a=3., b=1.), dict(date='d2', a=1., b=2.), dict(date='d2', a=None, b=5.), dict(date='d3', b=1.)]
        out = L.paired(items, 'a', 'b')
        self.assertEqual((out['n'], out['dates'], out['first'], out['second'], out['mean']), (2, 2, 2., 1.5, .5))
        self.assertAlmostEqual(out['se'], B._mean_se([2., -1.], [('d1',), ('d2',)])[0])

    def test_differences_name_the_fields_that_changed(self):
        stored = dict(a=1, b=[dict(x=1.0, y='s'), dict(x=2.0, y='s')], c=dict(d=True))
        replayed = dict(a=1, b=[dict(x=1.5, y='s'), dict(x=2.0, y='t')], c=dict(d=True, e=0))
        found = {d['path']: d for d in L.differences(stored, replayed)['listed']}
        self.assertEqual(sorted(found), ['.b[*].x', '.b[*].y', '.c.e'])
        self.assertEqual((found['.b[*].x']['count'], found['.b[*].x']['max_abs_difference']), (1, .5))
        self.assertEqual(L.differences(stored, json.loads(json.dumps(stored)))['paths'], 0)
        self.assertEqual(L.differences(dict(v=1), dict(v=1.0))['paths'], 1, 'an int that became a float is a byte difference')


def game(gid, date, era, run_id, published, replayed, close, actual, family='f', players=()):
    return dict(game_id=gid, date=date, start_time=date + 'T23:00:00+00:00', run_id=run_id, home_team_id='H', away_team_id='A',
                original=dict(era=era, margin_family=family, rating_weight=.65),
                actual=dict(margin=actual, total=220, home_points=110 + actual / 2, away_points=110 - actual / 2),
                closes=[dict(provider='Draft Kings', provider_id='100', market_margin=close, total_line=221., market_home_win=.5)],
                published=dict(margin=published, total=222., home_win=.6, home_points=111., away_points=111.),
                replayed=None if replayed is None else dict(margin=replayed, total=223., home_win=.55, home_points=112., away_points=111.),
                players=list(players))


def output(games, package='p' * 64, mode='rebuilt'):
    return dict(mode=mode, which='final', copy='/copy', finals='run', games=games, code=dict(commit='c', package_sha256=package))


class PairingTests(unittest.TestCase):
    def test_outputs_pair_by_game_and_original_run(self):
        base = output([game('G1', '2026-11-01', 'v3', 'r1', 5, 4, 3, 1), game('G2', '2026-11-02', 'v4', 'r2', 2, 2, 1, 0),
                       game('G3', '2026-11-02', 'v4', 'r3', 2, 2, 1, 0)])
        candidate = output([game('G1', '2026-11-01', 'v3', 'r1', 5, 3, 3, 1), game('G2', '2026-11-02', 'v4', 'other', 2, 2, 1, 0),
                            game('G4', '2026-11-03', 'v4', 'r4', 2, 2, 1, 0)], package='q' * 64)
        pairs, unpaired = L.pair_outputs(base, candidate)
        self.assertEqual([a['game_id'] for a, _ in pairs], ['G1'])
        self.assertEqual(sorted((u['game_id'], u['reason']) for u in unpaired),
                         [('G2', 'different original run'), ('G3', 'only in base'), ('G4', 'only in candidate')])

    def test_compare_reports_candidate_minus_base_with_the_clustered_error(self):
        player = lambda pid, base_points: dict(player_id=pid, team_id='H', played=True, actual_status='played', actual_points=10, actual_minutes=30,
                                               published=dict(forecast_status='projected', points_error=1., minutes_error=1., participation_probability=.9),
                                               replayed=dict(forecast_status='projected', points_error=base_points, minutes_error=1., participation_probability=.9))
        base = output([game('G1', '2026-11-01', 'v3', 'r1', 5, 4, 3, 1, players=[player('P1', 2.)]),
                       game('G2', '2026-11-02', 'v4', 'r2', 2, 6, 1, 0, players=[player('P2', -1.)])])
        candidate = output([game('G1', '2026-11-01', 'v3', 'r1', 5, 3, 3, 1, players=[player('P1', 1.)]),
                            game('G2', '2026-11-02', 'v4', 'r2', 2, 1, 1, 0, players=[player('P2', -3.)])], package='q' * 64)
        result = L.compare(base, candidate)
        margin = result['margin'][0]
        # Base errors |4-1|=3 and |6-0|=6; candidate |3-1|=2 and |1-0|=1: differences -1 and -5 on two dates.
        self.assertEqual((margin['group'], margin['n'], margin['base'], margin['candidate']), ('all', 2, 4.5, 1.5))
        self.assertEqual(margin['candidate_minus_base']['mean'], -3.)
        self.assertAlmostEqual(margin['candidate_minus_base']['se'], B._mean_se([-1., -5.], [('2026-11-01',), ('2026-11-02',)])[0])
        self.assertEqual(result['margins_moved'], 2)
        self.assertEqual(result['games_paired'], 2)
        groups = [r['group'] for r in result['margin']]
        self.assertIn('original run v3', groups)
        self.assertIn('without |published - close| >= 10', groups)
        points = result['player_points'][0]
        self.assertEqual((points['n'], points['base'], points['candidate'], points['candidate_minus_base']['mean']), (2, 1.5, 2., .5))

    def test_summary_splits_outliers_and_eras_from_the_published_margin(self):
        games = [game('G1', '2026-11-01', 'v1', 'r1', 15, 4, 3, 1), game('G2', '2026-11-02', 'v4', 'r2', 2, 2, 1, 0),
                 dict(game('G3', '2026-11-02', 'v4', 'r3', 2, None, 1, 0), error='ValueError: x')]
        out = output(games)
        out['games'][0]['identical'] = False
        summary = L.summarize(out)
        margin = {r['group']: r for r in summary['margin']}
        self.assertEqual((margin['all']['n'], margin['without |published - close| >= 10']['n'], margin['only |published - close| >= 10']['n']), (2, 1, 1))
        self.assertEqual(margin['original run v1']['published'], 14.)
        self.assertEqual(margin['all']['replayed_minus_published']['mean'], (3 - 14 + 2 - 2) / 2)
        self.assertEqual([o['game_id'] for o in summary['outliers']], ['G1'])
        self.assertEqual(summary['replay_errors'], [dict(game_id='G3', error='ValueError: x')])
        team = {r['group']: r for r in summary['team_points']}
        self.assertEqual(team['only |published - close| >= 10']['n'], 2, 'team rows carry their game\'s outlier flag')


if __name__ == '__main__':
    unittest.main()
