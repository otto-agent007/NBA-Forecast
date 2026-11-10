"""A stored forecast keeps the game's players; the rest of the roster stays as counts, and both stored shapes read the same."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from nba_forecast import store
from nba_forecast.benchmarks import benchmark_report
from nba_forecast import dashboard
from nba_forecast.dashboard import dashboard_data
from nba_forecast.daily_review import review_day
from nba_forecast.prospective_calibration import collect_samples
from tests.test_store import inputs, actual, NOW, LATER


def counted():
    """The ledger suite's inputs with the coverage counts the model writes: 30 roster players, 2 of them playing."""
    snapshot, history, forecast = inputs()
    forecast['coverage'] = dict(roster_players=30, pool_rows=30, by_status=dict(no_game=28, projected=2))
    return snapshot, history, forecast


class StoredForecastTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'ledger.sqlite'
        self.snapshot, self.history, self.forecast = counted()
        self.enterContext(patch('nba_forecast.store._now', return_value=NOW))

    def save(self, forecast):
        return store.save_run(self.db, self.snapshot, self.history, forecast)

    def test_only_the_off_slate_rows_are_left_out(self):
        before = deepcopy(self.forecast)
        slim = store.stored_forecast(self.snapshot, self.forecast)
        self.assertEqual(self.forecast, before, 'the model\'s forecast is not changed')
        self.assertEqual(slim['predictions'], before['predictions'][:2])
        self.assertEqual({k: v for k, v in slim.items() if k != 'predictions'},
                         {k: v for k, v in before.items() if k != 'predictions'})
        self.assertEqual(slim['coverage']['by_status']['no_game'], len(before['predictions']) - len(slim['predictions']))
        self.assertEqual(store.stored_forecast(self.snapshot, slim), slim)

    def test_both_shapes_are_saved_as_given(self):
        slim = store.stored_forecast(self.snapshot, self.forecast)
        for forecast, rows in ((self.forecast, 30), (slim, 2)):
            run_id = self.save(forecast)
            conn = store._connect(self.db)
            try:
                saved = conn.execute('SELECT forecast_json FROM runs WHERE run_id=?', (run_id,)).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(saved, store._canonical(forecast))
            self.assertEqual(len(json.loads(saved)['predictions']), rows)

    def test_a_forecast_between_the_two_shapes_is_rejected(self):
        slim = store.stored_forecast(self.snapshot, self.forecast)
        off_slate = self.forecast['predictions'][2]
        changes = dict(
            a_player_of_the_game_is_missing=lambda f: f['predictions'].pop(),
            one_off_slate_row_is_left_in=lambda f: f['predictions'].append(deepcopy(off_slate)),
            an_off_slate_row_replaces_a_player_of_the_game=lambda f: f['predictions'].__setitem__(1, deepcopy(off_slate)),
            a_player_is_listed_twice=lambda f: f['predictions'].__setitem__(1, deepcopy(f['predictions'][0])),
            an_unknown_player=lambda f: f['predictions'][1].update(player_id='P99'),
            no_rows=lambda f: f['predictions'].clear(),
        )
        for name, change in changes.items():
            with self.subTest(name):
                forecast = deepcopy(slim)
                change(forecast)
                with self.assertRaises(ValueError):
                    self.save(forecast)
        # One off-slate row missing from the full shape is no shape either.
        forecast = deepcopy(self.forecast)
        forecast['predictions'].pop()
        with self.assertRaises(ValueError):
            self.save(forecast)

    def test_the_rows_left_out_must_be_counted(self):
        slim = store.stored_forecast(self.snapshot, self.forecast)
        changes = dict(
            no_counts=lambda c: c.clear(),
            fewer_no_game=lambda c: c['by_status'].update(no_game=27),
            no_no_game=lambda c: c['by_status'].pop('no_game'),
            another_roster=lambda c: c.update(roster_players=29),
            stored_rows_as_pool=lambda c: c.update(pool_rows=2),
            statuses_not_counted=lambda c: c.update(by_status=None),
            a_flag_for_a_count=lambda c: c['by_status'].update(no_game=True),
        )
        for name, change in changes.items():
            with self.subTest(name):
                forecast = deepcopy(slim)
                change(forecast['coverage'])
                with self.assertRaises(ValueError):
                    self.save(forecast)
        # The full shape never needed the counts.
        self.forecast['coverage'] = {}
        self.save(self.forecast)

    def test_an_off_slate_row_that_is_not_no_game_is_kept_and_rejected(self):
        for change in (dict(status='needs_review'), dict(game_id='G1', start_time='2026-10-22T18:00:00Z')):
            with self.subTest(change):
                forecast = deepcopy(self.forecast)
                forecast['predictions'][2].update(change)
                kept = store.stored_forecast(self.snapshot, forecast)
                self.assertEqual([p['player_id'] for p in kept['predictions']], ['P1', 'P2', 'P3'])
                with self.assertRaises(ValueError):
                    self.save(kept)

    def test_the_evaluation_scores_the_same_players(self):
        reports = []
        for forecast in (self.forecast, store.stored_forecast(self.snapshot, self.forecast)):
            run_id = self.save(forecast)
            with patch('nba_forecast.store._now', return_value=LATER):
                reports.append(store.evaluate_run(self.db, run_id, [actual(), actual('P2', 'dnp')]))
        full, slim = reports
        self.assertEqual((full['sample_counts']['predictions'], full['sample_counts']['excluded']), (30, 28))
        self.assertEqual((slim['sample_counts']['predictions'], slim['sample_counts']['excluded']), (2, 0))
        self.assertEqual({e['reason'] for e in full['excluded']}, {'no_game'})
        self.assertEqual(slim['excluded'], [])
        same = lambda r: {k: v for k, v in r.items() if k not in ('run_id', 'excluded', 'sample_counts')}
        self.assertEqual(same(slim), same(full))
        counts = lambda r: {k: v for k, v in r['sample_counts'].items() if k not in ('predictions', 'excluded')}
        self.assertEqual(counts(slim), counts(full))


class BothShapesReadTheSame(unittest.TestCase):
    """The same three nights saved in each shape under the same run IDs: every reader returns the same."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.folders = {}
        previous = dict(actual(), game_id='previous', game_time='2026-10-20T18:00:00Z', known_at='2026-10-20T21:00:00Z')
        for shape in ('full', 'slim'):
            folder = cls.folders[shape] = Path(cls.tmp.name) / shape
            (folder / 'schedule').mkdir(parents=True)
            (folder / 'finals').mkdir()
            games = []
            for i in range(3):
                snapshot, _, forecast = counted()
                gid = f'G{i}'
                snapshot['games'][0]['game_id'] = gid
                forecast['model_key'] = 'key'
                for p in forecast['predictions']:
                    if p['game_id'] is not None:
                        p['game_id'] = gid
                games.append(dict(snapshot['games'][0], status='final', home_tricode='AAA', away_tricode='BBB'))
                finals = [dict(actual(), game_id=gid), dict(actual('P2', 'dnp'), game_id=gid)]
                (folder / 'finals' / (gid + '.json')).write_text(json.dumps(finals))
                if shape == 'slim':
                    forecast = store.stored_forecast(snapshot, forecast)
                # Two runs a game: the later one is selected, the earlier one only counted.
                for j in range(2):
                    with patch('nba_forecast.store._now', return_value=NOW + timedelta(minutes=10 * j)), \
                            patch('nba_forecast.store.uuid.uuid4', return_value=uuid.UUID(int=10 * i + j)):
                        run_id = store.save_run(folder / 'forecast.sqlite', snapshot, [previous], forecast)
                    with patch('nba_forecast.store._now', return_value=LATER):
                        store.evaluate_run(folder / 'forecast.sqlite', run_id, finals)
            (folder / 'schedule/latest.json').write_text(json.dumps(dict(season='2026-27', games=games)))

    def both(self, read):
        return read(self.folders['full']), read(self.folders['slim'])

    def test_performance_differs_only_in_the_off_slate_count(self):
        full, slim = self.both(lambda folder: store.performance_report(folder / 'forecast.sqlite'))
        self.assertEqual((full['off_slate_rows_ignored'], slim['off_slate_rows_ignored']), (6 * 28, 0))
        same = lambda r: {k: v for k, v in r.items() if k not in ('generated_at', 'off_slate_rows_ignored')}
        self.assertEqual(same(slim), same(full))
        self.assertEqual(full['sample_counts']['evaluated'], 6)

    def test_benchmarks_are_the_same(self):
        full, slim = self.both(lambda folder: benchmark_report(folder / 'forecast.sqlite'))
        self.assertEqual(slim, full)
        self.assertEqual(full['groups'][0]['metrics']['paired_n'], 3)

    def test_calibration_samples_differ_only_in_the_off_slate_count_and_the_stored_hash(self):
        full, slim = self.both(lambda folder: collect_samples(folder, 'key', '2026-10-23T12:00:00Z'))
        self.assertEqual(full['exclusions'].pop('off_slate'), 6 * 28)
        self.assertNotIn('off_slate', slim['exclusions'])
        self.assertEqual(len(full['samples']), 3)
        for a, b in zip(full['samples'], slim['samples'], strict=True):
            # The hash names the stored bytes, which are the difference between the shapes.
            self.assertNotEqual(a.pop('forecast_sha256'), b.pop('forecast_sha256'))
        self.assertEqual(slim, full)

    def test_the_dashboard_and_player_pages_are_the_same(self):
        full, slim = self.both(lambda folder: dict(dashboard_data(folder), generated_at=None))
        self.assertEqual(slim, full)
        self.assertEqual(len(full['games'][0]['forecast']['predictions']), 2)
        self.assertEqual(len(full['players']), 30)
        full, slim = self.both(lambda folder: dashboard._selected_predictions(folder / 'forecast.sqlite', '2026-27', 'P1'))
        self.assertEqual(slim, full)
        self.assertEqual(sorted(full), ['G0', 'G1', 'G2'])

    def test_the_daily_review_differs_only_in_the_stored_hash(self):
        def strip(value):
            if isinstance(value, dict):
                return {k: strip(v) for k, v in value.items() if k not in ('forecast_sha256', 'input_signature', 'generated_at')}
            return [strip(v) for v in value] if isinstance(value, list) else value
        full, slim = self.both(lambda folder: review_day(folder, '2026-10-22', '2026-10-23T12:00:00Z'))
        self.assertEqual(strip(slim), strip(full))
        self.assertEqual(full['coverage']['verified_finals'], 3)
        self.assertEqual(len(full['games'][0]['stages']['preliminary']['players']), 2)


if __name__ == '__main__':
    unittest.main()
