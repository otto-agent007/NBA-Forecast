"""The server's payloads: one game day per page payload, other days and review nights on demand, cached on file stamps."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from nba_forecast import dashboard
from nba_forecast.dashboard import (DETAIL_FIELDS, BadRequest, dashboard_data, dashboard_payload, default_day,
                                    forecasts_payload, game_day, make_server, review_payload)
from nba_forecast.store import save_run
from tests.test_store import NOW, inputs

# G1 tips 2026-10-22 11:00 PDT, G2 2026-10-23 16:00 PDT; "now" is between them.
G2_START = '2026-10-23T23:00:00Z'
BETWEEN = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
REVIEW = dict(date='2026-10-22', status='complete', games=[], coverage=dict(scheduled_games=1))


def _write(path, value):
    """Replace a file whole, as the runner's atomic writes do."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    os.replace(temporary, path)


class PayloadTests(unittest.TestCase):
    def setUp(self):
        dashboard._PAYLOADS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        s, h, f = inputs()
        self.s, self.h, self.f = s, h, f
        second = dict(s['games'][0], game_id='G2', start_time=G2_START)
        self.games = [dict(s['games'][0], status='final'), dict(second, status='scheduled')]
        _write(self.folder / 'schedule/latest.json', dict(season=s['season'], games=self.games))
        _write(self.folder / 'performance.json', dict(groups=[dict(season='2026-27', metrics={}, matched=[1, 2])]))
        _write(self.folder / 'daily-reviews/2026-10-22.json', REVIEW)
        with patch('nba_forecast.store._now', return_value=NOW):
            self.first = save_run(self.folder / 'forecast.sqlite', s, h, f)
        later = deepcopy(s)
        later['games'] = [second]
        later['as_of'] = '2026-10-22T11:30:00Z'
        forecast = deepcopy(f)
        forecast['as_of'] = later['as_of']
        for p in forecast['predictions']:
            if p['game_id']:
                p.update(game_id='G2', start_time=G2_START)
        with patch('nba_forecast.store._now', return_value=NOW + timedelta(minutes=30)):
            self.second = save_run(self.folder / 'forecast.sqlite', later, h, forecast)

    def tearDown(self):
        dashboard._PAYLOADS.clear()
        self.tmp.cleanup()

    def page(self, now=BETWEEN):
        etag, body = dashboard_payload(self.folder, now=now)
        return etag, json.loads(body)

    def test_page_carries_one_day_of_details_and_lists_review_dates(self):
        _, value = self.page()
        by_id = {g['game_id']: g for g in value['games']}
        self.assertEqual(value['default_day'], '2026-10-23')
        self.assertEqual(value['detail_days'], ['2026-10-23'])
        self.assertEqual(by_id['G2']['forecasts']['preliminary']['run_id'], self.second)
        self.assertFalse(set(DETAIL_FIELDS) & set(by_id['G1']), "another day's details stay on the server")
        self.assertEqual(by_id['G1']['status'], 'final', 'the calendar itself is complete')
        self.assertEqual(value['daily_reviews'], {})
        self.assertEqual(value['daily_review_dates'], ['2026-10-22'])
        self.assertEqual(value['performance']['groups'], [dict(season='2026-27', metrics={})])

    def test_every_day_matches_the_full_page_copy(self):
        full = dashboard_data(self.folder, compact=True)
        _, page = self.page()
        light = {g['game_id']: g for g in page['games']}
        for game in full['games']:
            self.assertEqual({k: v for k, v in game.items() if k not in DETAIL_FIELDS},
                             {k: v for k, v in light[game['game_id']].items() if k not in DETAIL_FIELDS})
            _, body = forecasts_payload(self.folder, game_day(game))
            details = json.loads(body)['games'][game['game_id']]
            self.assertEqual(details, {k: game[k] for k in DETAIL_FIELDS if k in game})
        for key in set(full) - {'games', 'daily_reviews', 'generated_at'}:
            self.assertEqual(page[key], full[key], key)
        self.assertEqual(json.loads(review_payload(self.folder, '2026-10-22')[1]), full['daily_reviews']['2026-10-22'])

    def test_unchanged_poll_does_not_rebuild(self):
        with patch.object(dashboard, 'dashboard_data', wraps=dashboard.dashboard_data) as build, \
             patch.object(dashboard, '_games', wraps=dashboard._games) as games:
            first = self.page()
            self.assertEqual(self.page(), first)
            self.assertEqual(build.call_count, 1)
            forecasts_payload(self.folder, '2026-10-22')
            forecasts_payload(self.folder, '2026-10-22')
            self.assertEqual(games.call_count, 2, 'one page build and one day build')

    def test_a_changed_file_rebuilds_and_identical_content_keeps_the_etag(self):
        etag, value = self.page()
        _write(self.folder / 'performance.json', dict(groups=[dict(season='2026-27', metrics={})]))
        with patch.object(dashboard, 'dashboard_data', wraps=dashboard.dashboard_data) as build:
            again, rewritten = self.page()
        self.assertEqual(build.call_count, 1, 'a replaced file is rebuilt')
        self.assertEqual((again, rewritten['generated_at']), (etag, value['generated_at']),
                         'the same content keeps its ETag and the time it last changed')
        _write(self.folder / 'performance.json', dict(groups=[dict(season='2026-27', metrics={'n': 1})]))
        changed, value = self.page()
        self.assertNotEqual(changed, etag)
        self.assertEqual(value['performance']['groups'][0]['metrics'], {'n': 1})
        _write(self.folder / 'daily-reviews/2026-10-21.json', dict(REVIEW, date='2026-10-21'))
        self.assertEqual(self.page()[1]['daily_review_dates'], ['2026-10-21', '2026-10-22'])

    def test_a_new_run_or_a_new_final_rebuilds(self):
        etag, _ = self.page()
        _, day = forecasts_payload(self.folder, '2026-10-23')
        later = deepcopy(self.s)
        later['games'] = [dict(self.games[1], status=None)]
        later['games'][0].pop('status')
        later['as_of'] = '2026-10-22T11:45:00Z'
        forecast = deepcopy(self.f)
        forecast['as_of'] = later['as_of']
        for p in forecast['predictions']:
            if p['game_id']:
                p.update(game_id='G2', start_time=G2_START)
        with patch('nba_forecast.store._now', return_value=NOW + timedelta(minutes=45)):
            third = save_run(self.folder / 'forecast.sqlite', later, self.h, forecast)
        changed, value = self.page()
        self.assertNotEqual(changed, etag)
        g2 = next(g for g in value['games'] if g['game_id'] == 'G2')
        self.assertEqual(g2['forecasts']['preliminary']['run_id'], third)
        self.assertNotEqual(forecasts_payload(self.folder, '2026-10-23')[1], day)
        # A captured box score turns a scheduled game final; after its tipoff the page opens on the latest day.
        _write(self.folder / 'finals/G2.json', [dict(team_id='T1', pts=100), dict(team_id='T2', pts=90)])
        _, value = self.page(now=datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
        g2 = next(g for g in value['games'] if g['game_id'] == 'G2')
        self.assertEqual((g2['status'], g2['final_score']), ('final', dict(home=100, away=90, periods=None)))

    def test_review_nights_on_demand_and_dates_are_validated(self):
        etag, body = review_payload(self.folder, '2026-10-22')
        self.assertEqual(json.loads(body), REVIEW)
        self.assertEqual(review_payload(self.folder, '2026-10-22'), (etag, body))
        self.assertIsNone(review_payload(self.folder, '2026-10-20'))
        _, empty = forecasts_payload(self.folder, '2026-11-01')
        self.assertEqual(json.loads(empty)['games'], {})
        for bad in ('2026-13-01', '2026-02-30', '2026-1-1', '../forecast', '', None, '2026-10-22T00'):
            with self.assertRaises(BadRequest, msg=bad):
                forecasts_payload(self.folder, bad)
            with self.assertRaises(BadRequest, msg=bad):
                review_payload(self.folder, bad)

    def test_plain_payload_keeps_main_shape(self):
        """A page built before the one-day view (an open tab across a deploy, a skipped build) gets everything."""
        _, body = dashboard_payload(self.folder, full=True)
        value = json.loads(body)
        expected = dashboard_data(self.folder, compact=True)
        value.pop('generated_at'), expected.pop('generated_at')
        self.assertEqual(value, json.loads(json.dumps(expected)))
        self.assertNotIn('detail_days', value)
        self.assertEqual(value['daily_reviews'], {'2026-10-22': REVIEW})
        _write(self.folder / 'daily-reviews/2026-10-22.json', dict(REVIEW, status='partial'))
        self.assertEqual(json.loads(dashboard_payload(self.folder, full=True)[1])['daily_reviews']['2026-10-22']['status'],
                         'partial', 'a rewritten review rebuilds the full payload')

    def test_empty_or_missing_performance_is_served_as_main_served_it(self):
        for value in ({}, None):
            _write(self.folder / 'performance.json', value)
            self.assertEqual(self.page()[1]['performance'], value)
            self.assertEqual(json.loads(dashboard_payload(self.folder, full=True)[1])['performance'], value)

    def test_a_file_written_during_a_build_is_checked_again(self):
        """A run's profiles are saved just after the run: one written while a payload was built is not taken as seen."""
        tracked = self.folder / 'runs/R/profiles.json'
        builds = []

        def build():
            builds.append(1)
            if len(builds) == 1:
                _write(tracked, dict(players={}))  # lands mid-build, after the build read the run
            return dict(value=len(builds)), [tracked]

        dashboard._cached(('test', 'x', None), 'key', build, stamped=False)
        dashboard._cached(('test', 'x', None), 'key', build, stamped=False)
        self.assertEqual(len(builds), 2, 'the next request rebuilds')
        old = datetime.now().timestamp() - 60
        os.utime(tracked, (old, old))
        dashboard._cached(('test', 'x', None), 'key', build, stamped=False)
        dashboard._cached(('test', 'x', None), 'key', build, stamped=False)
        self.assertEqual(len(builds), 3, 'a file older than the build is taken as seen')

    def test_default_day_follows_the_page_rule(self):
        games = [dict(game_id='A', status='final', start_time='2026-10-22T02:00:00Z'),
                 dict(game_id='B', status='scheduled', start_time='2026-10-24T03:00:00Z'),
                 dict(game_id='C', status='tbd', source_start_time='2026-12-04T05:00:00Z')]
        self.assertEqual(game_day(games[0]), '2026-10-21', 'Pacific calendar day')
        self.assertEqual(game_day(games[2]), '2026-12-04', 'New York day of an unconfirmed tipoff')
        self.assertEqual(default_day(games, datetime(2026, 10, 22, 12, tzinfo=timezone.utc)), '2026-10-23')
        self.assertEqual(default_day(games[:1] + games[2:], datetime(2026, 10, 22, 4, tzinfo=timezone.utc)), '2026-10-21',
                         'today when it has games')
        self.assertEqual(default_day(games[:1] + games[2:], datetime(2026, 11, 1, tzinfo=timezone.utc)), '2026-12-04',
                         "else the season's latest day")


class ServerTests(unittest.TestCase):
    """The HTTP layer: ETags, 304, gzip, endpoints and status codes."""

    @classmethod
    def setUpClass(cls):
        cls.payloads = PayloadTests('test_page_carries_one_day_of_details_and_lists_review_dates')
        cls.payloads.setUp()
        cls.assets = tempfile.TemporaryDirectory()
        Path(cls.assets.name, 'index.html').write_text('<!doctype html>')
        cls.server = make_server(cls.payloads.folder, cls.assets.name, 0)
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.assets.cleanup()
        cls.payloads.tearDown()

    def get(self, path, **headers):
        request = urllib.request.Request(self.base + path, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_gzip_etag_and_not_modified(self):
        status, headers, plain = self.get('/api/dashboard')
        self.assertEqual(status, 200)
        self.assertNotIn('Content-Encoding', headers)
        status, zipped_headers, zipped = self.get('/api/dashboard', **{'Accept-Encoding': 'gzip, deflate'})
        self.assertEqual((status, zipped_headers['Content-Encoding']), (200, 'gzip'))
        self.assertEqual(gzip.decompress(zipped), plain)
        self.assertEqual(zipped_headers['ETag'], 'W/' + headers['ETag'])
        self.assertIn('Accept-Encoding', zipped_headers['Vary'])
        for tag in (headers['ETag'], zipped_headers['ETag']):
            status, _, body = self.get('/api/dashboard', **{'If-None-Match': tag, 'Accept-Encoding': 'gzip'})
            self.assertEqual((status, body), (304, b''))
        self.assertEqual(self.get('/api/dashboard', **{'If-None-Match': '"other"'})[0], 200)
        self.assertIn("default-src 'self'", headers['Content-Security-Policy'])

    def test_view_parameter(self):
        light = json.loads(self.get('/api/dashboard?view=day')[2])
        full = json.loads(self.get('/api/dashboard')[2])
        self.assertIn('detail_days', light)
        self.assertNotIn('detail_days', full)
        self.assertEqual(full['daily_reviews'], {'2026-10-22': REVIEW})
        for path in ('/api/dashboard?view=all', '/api/dashboard?view=day&view=day', '/api/dashboard?view='):
            self.assertEqual(self.get(path)[0], 400, path)

    def test_day_and_review_endpoints(self):
        status, headers, body = self.get('/api/forecasts?date=2026-10-22')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['games']['G1']['forecasts']['preliminary']['run_id'], self.payloads.first)
        self.assertEqual(self.get('/api/forecasts?date=2026-10-22', **{'If-None-Match': headers['ETag']})[0], 304)
        status, _, body = self.get('/api/daily-reviews/2026-10-22')
        self.assertEqual((status, json.loads(body)), (200, REVIEW))
        self.assertEqual(self.get('/api/daily-reviews/2026-10-20')[0], 404)
        for path in ('/api/forecasts', '/api/forecasts?date=2026-13-01', '/api/forecasts?date=x&date=y',
                     '/api/forecasts?date=2026-10-22&date=2026-10-23', '/api/daily-reviews/2026-02-30',
                     '/api/daily-reviews/forecast'):
            self.assertEqual(self.get(path)[0], 400, path)
        for path in ('/api/daily-reviews/..%2Fforecast.sqlite', '/api/daily-reviews/../../forecast.sqlite', '/forecast.sqlite'):
            self.assertEqual(self.get(path)[0], 404, path)


if __name__ == '__main__':
    unittest.main()
