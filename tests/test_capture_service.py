"""Market lines are captured by their own service; the tick captures only when that service is not running."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from nba_forecast import capture as C
from nba_forecast import operations
from nba_forecast.files import atomic_json
from nba_forecast.market_lines import refresh_index, saved_capture_evidence
from nba_forecast.schedule import normalize_season, upcoming_games
from tests.test_kalshi_lines import fetcher, payloads
from tests.test_market_lines import META, summary
from tests.test_schedule import game, payload

ROOT = Path(__file__).resolve().parents[1]
NOW = '2026-10-21T16:00:00Z'  # 9 AM Pacific


def calendar(folder):
    """A saved calendar: tips in 2, 20 (before the next 9 AM) and 30 hours, one already tipped, one TBD."""
    games = [game('0022600001', '2026-10-21T18:00:00Z'), game('0022600002', '2026-10-22T12:00:00Z'),
             game('0022600003', '2026-10-22T22:00:00Z'), game('0022600004', '2026-10-21T15:00:00Z')]
    tbd = game('0022600005', '2026-10-21T20:00:00Z')
    tbd['gameStatusText'] = 'TBD'
    schedule = normalize_season(payload(games + [tbd]), NOW)
    schedule['version'] = 'v1'
    atomic_json(Path(folder) / 'schedule' / 'latest.json', schedule)
    return schedule


def heartbeat(folder, seconds_ago, status='complete'):
    finished = (datetime.fromisoformat(NOW.replace('Z', '+00:00')) - timedelta(seconds=seconds_ago)).isoformat()
    atomic_json(Path(folder) / 'last-capture.json', dict(status=status, finished_at=finished))


class UpcomingTests(unittest.TestCase):
    def test_the_service_captures_the_games_the_tick_passes_as_upcoming(self):
        with tempfile.TemporaryDirectory() as d:
            schedule = calendar(d)
            self.assertEqual([g['game_id'] for g in upcoming_games(schedule, NOW)], ['0022600001', '0022600002'],
                             'scheduled, timed and within 24 hours, including a game waiting for its 9 AM checkpoint')
            seen = {}
            def espn(folder, games, season, as_of, deadline=None):
                seen['tick'] = [g['game_id'] for g in games]
                return dict(captured=[], errors=[], evidence={})
            with patch('nba_forecast.operations._now', return_value=NOW), patch('nba_forecast.operations.fetch_schedule', return_value=schedule), \
                 patch('nba_forecast.operations.fetch_roster', side_effect=ValueError('offline')), \
                 patch('nba_forecast.operations.capture_daily', return_value={'state': 'waiting'}), \
                 patch('nba_forecast.operations.archive_daily_references', return_value=dict(sources=[], failures=[])), \
                 patch('nba_forecast.operations.fetch_injury_check', side_effect=ValueError('offline')), \
                 patch('nba_forecast.operations.capture_game_lines', side_effect=espn), \
                 patch('nba_forecast.operations.capture_kalshi_lines', return_value=dict(captured=[], errors=[])):
                tick = operations.run_tick(d, [])
            self.assertEqual(tick['market_capture']['by'], 'tick')
            def service(folder, games, season, as_of, deadline=None, index=True):
                seen['service'] = [g['game_id'] for g in games]
                return dict(captured=[], errors=[])
            with patch('nba_forecast.market_lines.capture_game_lines', side_effect=service), \
                 patch('nba_forecast.kalshi_lines.capture_kalshi_lines', return_value=dict(captured=[], errors=[])), \
                 patch('nba_forecast.kalshi_props.capture_kalshi_props', return_value=dict(captured=[], errors=[])):
                report = C.run_capture(d, now=NOW)
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(seen['service'], seen['tick'])
            self.assertEqual(report['games'], seen['tick'])


class ServiceRunTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_waits_for_a_calendar(self):
        report = C.run_capture(self.folder, now=NOW)
        self.assertEqual(report['status'], 'waiting')
        self.assertEqual(json.loads((self.folder / 'last-capture.json').read_text())['status'], 'waiting')

    def test_a_run_captures_every_provider_logs_once_and_an_idle_run_writes_only_its_heartbeat(self):
        schedule = normalize_season(payload([game('0022600010', '2026-10-21T23:00:00Z')]), NOW)
        schedule['games'][0].update(home_tricode='BOS', away_tricode='NYK')
        atomic_json(self.folder / 'schedule' / 'latest.json', schedule)
        espn = dict(summary(), header=dict(summary()['header'], competitions=[dict(summary()['header']['competitions'][0], date='2026-10-21T23:00:00Z')]))
        kalshi = fetcher(payloads())
        def fetch(url):
            if any(series in url for series in ('KXNBAPTS', 'KXNBAREB', 'KXNBAAST', 'KXNBA3PT')):
                return b'{"markets": [], "cursor": ""}'  # props open on game day
            return kalshi(url)
        with patch('nba_forecast.reference.scoreboard_event', return_value=(dict(provider_event_id='401909848'), META)), \
             patch('nba_forecast.reference.fetch_reference', return_value=(espn, META)):
            first = C.run_capture(self.folder, fetch=fetch, now=NOW)
            self.assertEqual(first['status'], 'complete', first)
            self.assertEqual([c['game_id'] for c in first['market_lines']['captured']], ['0022600010'])
            self.assertEqual(first['kalshi_lines']['captured'], ['0022600010'])
            self.assertEqual(first['kalshi_props']['unlisted'], ['0022600010'])
            self.assertTrue(first['index_rebuilt'])
            self.assertEqual(list(saved_capture_evidence(self.folder, schedule['games'])), ['0022600010'])
            states = {p: p.stat().st_mtime_ns for p in (self.folder / 'market').rglob('*.json')}
            second = C.run_capture(self.folder, fetch=fetch, now=(datetime.fromisoformat(NOW.replace('Z', '+00:00')) + timedelta(seconds=30)).isoformat())
        self.assertEqual(second['market_lines']['skipped'], ['0022600010'])
        self.assertFalse(second['index_rebuilt'])
        self.assertEqual({p: p.stat().st_mtime_ns for p in (self.folder / 'market').rglob('*.json')}, states, 'an idle run rewrites no state')
        lines = [json.loads(line) for line in (self.folder / 'capture.jsonl').read_text().splitlines()]
        self.assertEqual(len(lines), 1, 'the first run is logged; the idle second only updates last-capture.json')
        self.assertEqual(lines[0]['market_lines']['captured'], 1)
        self.assertEqual(json.loads((self.folder / 'last-capture.json').read_text())['started_at'], second['started_at'])

    def test_a_run_that_finds_the_lock_held_is_busy_and_captures_nothing(self):
        calendar(self.folder)
        with C.capture_lock(self.folder) as held, patch('nba_forecast.market_lines.capture_game_lines', side_effect=AssertionError('captured')):
            self.assertTrue(held)
            self.assertEqual(C.run_capture(self.folder, now=NOW)['status'], 'busy')

    def test_one_failing_provider_costs_its_own_report(self):
        calendar(self.folder)
        with patch('nba_forecast.market_lines.capture_game_lines', side_effect=RuntimeError('boom')), \
             patch('nba_forecast.kalshi_lines.capture_kalshi_lines', return_value=dict(captured=['0022600001'], errors=[])), \
             patch('nba_forecast.kalshi_props.capture_kalshi_props', return_value=dict(captured=[], errors=[])):
            report = C.run_capture(self.folder, now=NOW)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['errors'], [dict(task='market_lines', error='RuntimeError: boom')])
        self.assertEqual(report['kalshi_lines']['captured'], ['0022600001'])


class LaneTests(unittest.TestCase):
    def test_a_slow_host_does_not_hold_up_the_other_and_a_lane_runs_in_order(self):
        started = {}
        clock = time.monotonic()
        def call(name, pause):
            def run(deadline):
                started[name] = time.monotonic() - clock
                time.sleep(pause)
                return dict(deadline=deadline)
            return run
        results = C.run_providers([[('espn', call('espn', .5))], [('kalshi', call('kalshi', .1)), ('props', call('props', 0))]], 10)
        self.assertLess(started['kalshi'], .2, 'Kalshi started while ESPN waited')
        self.assertTrue(.1 <= started['props'] < .4, 'the props follow the game lines in the Kalshi lane')
        self.assertEqual(len({r[0]['deadline'] for r in results.values()}), 1, 'one deadline for the run')

    def test_the_tick_gives_each_provider_its_own_share_in_turn(self):
        budgets = []
        def slow(deadline):
            budgets.append(deadline - time.monotonic())
            time.sleep(.3)
            return 'espn'
        def quick(deadline):
            budgets.append(deadline - time.monotonic())
            return 'kalshi'
        with tempfile.TemporaryDirectory() as d:
            summary_, results = C.tick_fallback(d, dict(market_lines=slow, kalshi_lines=quick), 1.0, NOW)
        self.assertEqual(summary_['by'], 'tick')
        self.assertEqual({k: v[0] for k, v in results.items()}, dict(market_lines='espn', kalshi_lines='kalshi'))
        self.assertTrue(all(.45 <= b <= .5 for b in budgets), budgets)


class TickFallbackTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.schedule = calendar(self.folder)
        for target, value in (('fetch_schedule', dict(return_value=self.schedule)), ('fetch_roster', dict(side_effect=ValueError('offline'))),
                              ('capture_daily', dict(return_value={'state': 'waiting'})),
                              ('archive_daily_references', dict(return_value=dict(sources=[], failures=[]))),
                              ('fetch_injury_check', dict(side_effect=ValueError('offline'))), ('_now', dict(return_value=NOW))):
            self.enterContext(patch('nba_forecast.operations.' + target, **value))
        self.espn = self.enterContext(patch('nba_forecast.operations.capture_game_lines', return_value=dict(captured=[], errors=[], evidence={})))
        self.kalshi = self.enterContext(patch('nba_forecast.operations.capture_kalshi_lines', return_value=dict(captured=[], errors=[])))

    def test_without_a_service_the_tick_captures_as_before(self):
        report = operations.run_tick(self.folder, [])
        self.assertEqual((report['market_capture']['by'], self.espn.call_count, self.kalshi.call_count), ('tick', 1, 1))
        self.assertIn('no capture service run', report['market_capture']['reason'])
        self.assertIn('market_lines', report)
        self.assertIn('kalshi_lines', report)

    def test_a_live_service_takes_over_and_a_stale_or_failed_one_does_not(self):
        heartbeat(self.folder, 20)
        report = operations.run_tick(self.folder, [])
        self.assertEqual((report['market_capture']['by'], self.espn.call_count, self.kalshi.call_count), ('nba-capture', 0, 0))
        self.assertNotIn('market_lines', report)
        self.assertNotIn('kalshi_lines', report)
        self.assertIn('market_lines', report['timings'])
        heartbeat(self.folder, 20, status='busy')
        self.assertEqual(operations.run_tick(self.folder, [])['market_capture']['by'], 'nba-capture', 'a busy run means someone is capturing')
        heartbeat(self.folder, C.HEARTBEAT_SECONDS + 1)
        stale = operations.run_tick(self.folder, [])
        self.assertEqual((stale['market_capture']['by'], self.espn.call_count), ('tick', 1))
        heartbeat(self.folder, 20, status='error')
        self.assertEqual(operations.run_tick(self.folder, [])['market_capture']['by'], 'tick')

    def test_the_tick_never_captures_beside_a_capture_in_progress(self):
        done = threading.Event()
        holding = threading.Event()
        def hold():
            with C.capture_lock(self.folder) as held:
                self.assertTrue(held)
                holding.set()
                done.wait(10)
        worker = threading.Thread(target=hold)
        worker.start()
        try:
            holding.wait(5)
            report = operations.run_tick(self.folder, [])
        finally:
            done.set()
            worker.join()
        self.assertEqual((report['market_capture']['by'], self.espn.call_count), ('nba-capture', 0))
        self.assertIn('capture.lock held', report['market_capture']['reason'])


class FileSafetyTests(unittest.TestCase):
    def test_the_ticks_read_of_the_saved_digests_never_sees_a_partial_file(self):
        with tempfile.TemporaryDirectory() as d:
            games = [dict(game_id=f'00226000{i:02d}') for i in range(40)]
            state = Path(d) / 'market' / 'capture-state.json'
            atomic_json(state, dict(evidence={g['game_id']: '0' * 64 for g in games}, captured={}))
            stop = threading.Event()
            def writer():
                n = 0
                while not stop.is_set():
                    n += 1
                    atomic_json(state, dict(evidence={g['game_id']: f'{n:064d}' for g in games}, captured={str(i): i for i in range(500)}))
            thread = threading.Thread(target=writer)
            thread.start()
            try:
                reads = [len(saved_capture_evidence(d, games)) for _ in range(2000)]
            finally:
                stop.set()
                thread.join()
            self.assertEqual(set(reads), {40})

    def test_the_index_is_rebuilt_only_when_a_line_file_changed(self):
        from nba_forecast import market_lines as M
        from tests.test_market_lines import GAME
        with tempfile.TemporaryDirectory() as d:
            M.record_game_lines(d, M.extract_game_lines(summary(), GAME, '2026-27', META), '2026-10-23T21:00:30Z')
            self.assertTrue(refresh_index(d))
            self.assertFalse(refresh_index(d))
            M.record_game_lines(d, M.extract_game_lines(summary(spread_home='-3.5'), GAME, '2026-27', META), '2026-10-23T21:15:30Z')
            self.assertTrue(refresh_index(d))
            index = json.loads((Path(d) / 'market' / 'index.json').read_text())
            self.assertEqual(index['games'][GAME['game_id']]['captures'], 2)

    def test_the_forecast_path_does_not_import_the_props_module(self):
        result = subprocess.run([sys.executable, '-c', 'import sys, nba_forecast.operations, nba_forecast.model, nba_forecast.candidate; '
                                 'print("nba_forecast.kalshi_props" in sys.modules)'], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), 'False', result.stderr)

    def test_the_capture_process_stays_light(self):
        result = subprocess.run([sys.executable, '-c', 'import sys, nba_forecast.capture, nba_forecast.kalshi_props, nba_forecast.market_lines; '
                                 'print(sorted(m for m in ("nba_forecast.model", "nba_forecast.store", "nba_forecast.operations", "sqlite3") if m in sys.modules))'],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), '[]', result.stderr)


class CliTests(unittest.TestCase):
    def test_capture_command_prints_one_line_and_saves_its_report(self):
        with tempfile.TemporaryDirectory() as d:
            result = subprocess.run([sys.executable, '-m', 'nba_forecast', 'capture', '--directory', d], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(result.stdout.splitlines()), 1)
            self.assertEqual(json.loads(result.stdout)['status'], 'waiting')
            self.assertEqual(json.loads((Path(d) / 'last-capture.json').read_text())['status'], 'waiting')


if __name__ == '__main__':
    unittest.main()
