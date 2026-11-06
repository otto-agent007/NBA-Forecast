"""Late captures after an outage: labeled, never replacing a live record, and read by the live paths."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nba_forecast import recover_evidence as R
from nba_forecast.kalshi_lines import closing_kalshi_line, read_kalshi_lines
from nba_forecast.market_lines import closing_lines, read_game_lines, record_game_lines

NOW = '2026-11-03T20:00:00+00:00'
TIP = '2026-11-01T00:00:00+00:00'
GAME = dict(game_id='0022600151', start_time=TIP, status='final', season_type='Regular Season',
            home_team_id='1610612749', away_team_id='1610612739', home_name='Milwaukee Bucks', away_name='Cleveland Cavaliers',
            home_tricode='MIL', away_tricode='CLE')
LATER = dict(GAME, game_id='0022600170', start_time='2026-11-04T00:00:00+00:00', status='scheduled')
SCHEDULE = dict(season='2026-27', games=[GAME, LATER])


def pdf_fetch(available):
    """A fetch of official editions: the URLs in ``available`` download as PDFs, the rest are absent."""
    calls = []

    def fetch(url, cache_dir, timeout=15):
        calls.append(url)
        if url.rsplit('/', 1)[1] in available:
            return b'%PDF-1.4 ' + url.encode(), '2026-11-03T19:59:00+00:00'
        raise ValueError(f'NBA source {url} returned HTTP 403')
    fetch.calls = calls
    return fetch


class ReportRecordTests(unittest.TestCase):
    def test_the_t30_edition_becomes_a_late_final_record_that_later_forecasts_read(self):
        fetch = pdf_fetch({'Injury-Report_2026-10-31_07_30PM.pdf'})
        with tempfile.TemporaryDirectory() as d:
            out = R.recover_report_records(d, SCHEDULE, ['0022600151', '0022600170', 'nope'], now=NOW, fetch=fetch, sleep=lambda s: None)
            self.assertEqual([o['status'] for o in out], ['recorded', 'not_tipped', 'unknown_game'])
            self.assertEqual(out[0]['anchor'][-28:], 'eport_2026-10-31_07_30PM.pdf')
            self.assertEqual(out[0]['published_at'], '2026-10-31T23:30:00+00:00')
            root = Path(d) / 'injuries' / 'games' / '0022600151'
            final = json.loads((root / 'final.json').read_text())
            self.assertEqual(final['forecast_stage'], 'final')
            self.assertEqual(final['late_capture']['recovered_at'], NOW)
            self.assertEqual(final['games'][0]['phase'], 'started')
            self.assertEqual(final['games'][0]['report_status'], 'captured_pending_review')
            self.assertEqual(final['games'][0]['anchor_report']['retrieved_at'], '2026-11-03T19:59:00+00:00')
            self.assertEqual(len(list((root / 'records').glob('*.json'))), 1)
            # A live record is never replaced, and the edition is not fetched again.
            again = R.recover_report_records(d, SCHEDULE, ['0022600151'], now=NOW, fetch=fetch, sleep=lambda s: None)
            self.assertEqual(again[0]['status'], 'exists')
            self.assertEqual(len(fetch.calls), 1)
            # The live path: a player the recovered edition lists out gets the game as a reported-out game.
            from nba_forecast.injury_capture import reported_unavailable
            roster = dict(players=[dict(player_id='p1', name='Giannis Antetokounmpo', team_id='1610612749')])
            text = ('Injury Report: 10/31/26 07:30 PM\n'
                    '10/31/2026   08:00 (ET)   CLE@MIL   Milwaukee Bucks   Antetokounmpo, Giannis   Out   Injury/Illness - Knee\n')
            history = [dict(game_id='0022600151', team_id='1610612749', season='2026-27')]
            with patch('nba_forecast.injury_parser.pdf_text', return_value=text):
                self.assertEqual(reported_unavailable(d, SCHEDULE, roster, history, LATER, {}), {'p1': ['0022600151']})

    def test_an_absent_slot_falls_back_to_the_previous_edition_and_no_edition_is_reported(self):
        fetch = pdf_fetch({'Injury-Report_2026-10-31_07_00PM.pdf'})
        with tempfile.TemporaryDirectory() as d:
            out = R.recover_report_records(d, SCHEDULE, ['0022600151'], now=NOW, fetch=fetch, sleep=lambda s: None)
            self.assertEqual((out[0]['status'], out[0]['report_status'], len(out[0]['failures'])), ('recorded', 'stale', 2))
            self.assertEqual([f['attempts'] for f in out[0]['failures']], [2, 2])  # a throttled slot is retried once
            self.assertEqual(out[0]['published_at'], '2026-10-31T23:00:00+00:00')
        # Two games anchored on the same edition fetch it once.
        fetch = pdf_fetch({'Injury-Report_2026-10-31_07_30PM.pdf'})
        twin = dict(GAME, game_id='0022600152', home_tricode='SAS', away_tricode='MIN')
        with tempfile.TemporaryDirectory() as d:
            out = R.recover_report_records(d, dict(SCHEDULE, games=[GAME, twin]), ['0022600151', '0022600152'], now=NOW, fetch=fetch, sleep=lambda s: None)
            self.assertEqual([o['status'] for o in out], ['recorded', 'recorded'])
            self.assertEqual(len(fetch.calls), 1)
        with tempfile.TemporaryDirectory() as d:
            out = R.recover_report_records(d, SCHEDULE, ['0022600151'], now=NOW, fetch=pdf_fetch(set()), sleep=lambda s: None)
            self.assertEqual(out[0]['status'], 'unavailable')
            self.assertFalse((Path(d) / 'injuries' / 'games' / '0022600151' / 'final.json').exists())


def odds_payload(spread='-8.5', total='234.5'):
    return dict(items=[dict(provider=dict(id=100, name='Draft Kings'), close=dict(total=dict(american=total)),
                            homeTeamOdds=dict(close=dict(pointSpread=dict(american=spread), moneyLine=dict(american='-350'))),
                            awayTeamOdds=dict(close=dict(pointSpread=dict(american='+8.5'), moneyLine=dict(american='+280'))))])


def capture(retrieved_at, spread=-9.5, total=235.5):
    side = lambda v: dict(current_line=v, current_odds=-110, open_line=v, open_odds=-110)  # noqa: E731
    return dict(schema_version=1, game_id='0022600151', start_time=TIP, event_id='401909900', home_team_id='1610612749',
                away_team_id='1610612739', provider_id='100', provider='Draft Kings', details='MIL -9.5',
                lines=dict(spread=dict(home=side(spread), away=side(-spread)), total=dict(over=side(total), under=side(total)),
                           moneyline=dict(home=dict(side(None), current_odds=-360), away=dict(side(None), current_odds=285))),
                source_url='https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=401909900',
                retrieved_at=retrieved_at, sha256='a' * 64)


class ClosingLineTests(unittest.TestCase):
    def test_the_recorded_close_is_appended_and_preferred_to_the_stale_capture(self):
        fetched = []

        def fetch(url, directory, max_age=300):
            fetched.append(url)
            return odds_payload(), dict(source_url=url, retrieved_at=NOW, sha256='c' * 64)
        with tempfile.TemporaryDirectory() as d:
            record_game_lines(d, [capture('2026-10-31T21:47:55+00:00')], '2026-10-31T21:47:55+00:00')
            out = R.recover_closing_lines(d, SCHEDULE, ['0022600151', '0022600170'], now=NOW, fetch=fetch)
            self.assertEqual([o['status'] for o in out], ['recorded', 'not_tipped'])
            self.assertEqual((out[0]['provider'], out[0]['home_spread'], out[0]['total'], out[0]['event_id']), ('Draft Kings', -8.5, 234.5, '401909900'))
            self.assertTrue(fetched[0].endswith('/events/401909900/competitions/401909900/odds'))
            records = read_game_lines(d, '0022600151')
            self.assertEqual(len(records), 2)
            close = closing_lines(records, TIP)
            self.assertEqual(list(close), ['100'])
            self.assertEqual(close['100']['lines']['spread']['home']['current_line'], -8.5)
            self.assertEqual(close['100']['lines']['moneyline']['away']['current_odds'], 280)
            self.assertEqual(close['100']['recovered_close']['recovered_at'], NOW)
            # Without a recovered close the last pregame capture is still the close.
            self.assertEqual(closing_lines(records[:1], TIP)['100']['lines']['spread']['home']['current_line'], -9.5)
            self.assertEqual(R.recover_closing_lines(d, SCHEDULE, ['0022600151'], now=NOW, fetch=fetch)[0]['status'], 'exists')

    def test_no_close_on_record_is_reported_not_guessed(self):
        def fetch(url, directory, max_age=300):
            return dict(items=[dict(provider=dict(id=1, name='Live'), homeTeamOdds=dict(current={}))]), dict(source_url=url, retrieved_at=NOW, sha256='c' * 64)
        with tempfile.TemporaryDirectory() as d:
            record_game_lines(d, [capture('2026-10-31T21:47:55+00:00')], '2026-10-31T21:47:55+00:00')
            out = R.recover_closing_lines(d, SCHEDULE, ['0022600151'], now=NOW, fetch=fetch)
            self.assertEqual(out[0]['status'], 'no_close')
            self.assertEqual(len(read_game_lines(d, '0022600151')), 1)


def kalshi_fetch(tip_ts):
    """Markets per series and a candle per market, the last one closing two minutes before tipoff."""
    def markets(series):
        if series == 'KXNBAGAME':
            return [dict(ticker='KXNBAGAME-26OCT31CLEMIL-CLE', status='settled'), dict(ticker='KXNBAGAME-26OCT31CLEMIL-MIL', status='settled')]
        if series == 'KXNBASPREAD':
            return [dict(ticker=f'KXNBASPREAD-26OCT31CLEMIL-CLE{s}', floor_strike=s - .5, status='settled') for s in (4, 8, 12)]
        return [dict(ticker=f'KXNBATOTAL-26OCT31CLEMIL-{t}', floor_strike=t - .5, status='settled') for t in (230, 236, 242)]

    def price(ticker):
        if ticker.endswith('-CLE'):
            return .77, .79
        if ticker.endswith('-MIL'):
            return .21, .23
        if 'SPREAD' in ticker:
            strike = int(ticker.rsplit('CLE', 1)[1])
            return .49 + (8 - strike) * .04, .51 + (8 - strike) * .04   # Cleveland by over 7.5 is a coin flip: home margin -7.5
        total = int(ticker.rsplit('-', 1)[1])
        return .49 - (total - 236) * .02, .51 - (total - 236) * .02

    def fetch(url):
        if '/markets?event_ticker=' in url:
            series = url.split('event_ticker=')[1].split('-')[0]
            return json.dumps(dict(markets=markets(series))).encode()
        ticker = url.split('/markets/')[1].split('/candlesticks')[0]
        bid, ask = price(ticker)
        candles = [dict(end_period_ts=tip_ts - 600, yes_bid=dict(close_dollars=f'{bid - .02:.2f}'), yes_ask=dict(close_dollars=f'{ask - .02:.2f}')),
                   dict(end_period_ts=tip_ts - 120, yes_bid=dict(close_dollars=f'{bid:.2f}'), yes_ask=dict(close_dollars=f'{ask:.2f}')),
                   dict(end_period_ts=tip_ts, yes_bid=dict(close_dollars='0.98'), yes_ask=dict(close_dollars='1.00')),  # the tipoff candle is not pregame
                   dict(end_period_ts=tip_ts + 600, yes_bid=dict(close_dollars='0.99'), yes_ask=dict(close_dollars='1.00'))]
        return json.dumps(dict(candlesticks=candles)).encode()
    return fetch


class KalshiTests(unittest.TestCase):
    def test_the_last_pretip_candle_becomes_the_close_timed_by_the_exchange(self):
        from nba_forecast.validation import parse_time
        tip_ts = int(parse_time(TIP).timestamp())
        with tempfile.TemporaryDirectory() as d:
            out = R.recover_kalshi_lines(d, SCHEDULE, ['0022600151', '0022600170'], now=NOW, fetch=kalshi_fetch(tip_ts), sleep=lambda s: None)
            self.assertEqual([o['status'] for o in out], ['recorded', 'not_tipped'])
            self.assertEqual(out[0]['captured_at'], '2026-10-31T23:58:00+00:00')
            self.assertAlmostEqual(out[0]['home_win_probability'], .22, places=6)
            self.assertAlmostEqual(out[0]['implied_home_margin'], -7.5, places=6)
            self.assertAlmostEqual(out[0]['implied_total'], 235.5, places=6)
            self.assertTrue(out[0]['is_close'])
            records = read_kalshi_lines(d, '0022600151')
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['recovered_from_candlesticks']['recovered_at'], NOW)
            self.assertEqual(closing_kalshi_line(records, TIP)['captured_at'], '2026-10-31T23:58:00+00:00')
            self.assertTrue(all(s['sha256'] for s in records[0]['sources']))
            self.assertEqual(R.recover_kalshi_lines(d, SCHEDULE, ['0022600151'], now=NOW, fetch=kalshi_fetch(tip_ts), sleep=lambda s: None)[0]['status'], 'exists')

    def test_only_nba_candlestick_urls_are_fetched(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
            R.fetch_candlesticks('KXNBAGAME', 'KXNBAGAME-26OCT31CLEMIL-MIL;rm', TIP, TIP, d, fetch=lambda url: b'{}')


class DailyReportTests(unittest.TestCase):
    def test_the_nine_am_pacific_edition_is_recorded_once(self):
        fetch = pdf_fetch({'Injury-Report_2026-11-01_12_00PM.pdf'})
        with tempfile.TemporaryDirectory() as d:
            out = R.recover_daily_report(d, '2026-27', '2026-11-01', now=NOW, fetch=fetch, sleep=lambda s: None)
            self.assertEqual((out['status'], out['published_at']), ('recorded', '2026-11-01T17:00:00+00:00'))
            saved = json.loads((Path(d) / 'injuries' / 'daily' / '2026-27-2026-11-01.json').read_text())
            self.assertEqual((saved['capture_date'], saved['scheduled_at'], saved['report']['source_url'].rsplit('/', 1)[1]),
                             ('2026-11-01', '2026-11-01T17:00:00+00:00', 'Injury-Report_2026-11-01_12_00PM.pdf'))
            self.assertEqual(saved['late_capture']['recovered_at'], NOW)
            self.assertEqual(R.recover_daily_report(d, '2026-27', '2026-11-01', now=NOW, fetch=fetch, sleep=lambda s: None)['status'], 'exists')
            self.assertEqual(len(fetch.calls), 1)


class RecoverTests(unittest.TestCase):
    def test_runs_under_the_tick_locks_and_lists_the_unresolved(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'schedule').mkdir()
            (Path(d) / 'schedule' / 'latest.json').write_text(json.dumps(SCHEDULE))
            with patch('nba_forecast.recover_evidence.recover_report_records', return_value=[dict(game_id='0022600151', status='unavailable')]),\
                 patch('nba_forecast.recover_evidence.recover_closing_lines', return_value=[dict(game_id='0022600151', status='recorded')]),\
                 patch('nba_forecast.recover_evidence.recover_kalshi_lines', return_value=[dict(game_id='0022600151', status='recorded')]),\
                 patch('nba_forecast.recover_evidence.recover_daily_report', return_value=dict(date='2026-11-01', status='recorded')):
                result = R.recover(d, ['0022600151'], daily_reports=['2026-11-01'], wait=1, now=NOW)
            self.assertEqual(result['unresolved'], ['0022600151'])
            self.assertEqual(result['daily_reports'][0]['status'], 'recorded')
            self.assertTrue((Path(d) / 'tick.lock').exists())


def at(minutes):
    """``minutes`` after the tipoff of GAME."""
    from datetime import timedelta
    from nba_forecast.validation import parse_time
    return (parse_time(TIP) + timedelta(minutes=minutes)).isoformat()


class AutoRecoverTests(unittest.TestCase):
    """The derived service recovers a missed game's evidence by itself once the game is over."""
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.calls = []
        self.status = dict(reports='recorded', closing_lines='recorded', kalshi='recorded')
        self.schedule = dict(SCHEDULE, games=[dict(GAME, status='scheduled'), LATER])
        self.miss(['0022600151'])

    def miss(self, game_ids, schedule=None):
        games = {g['game_id']: g for g in (schedule or self.schedule)['games']}
        (self.folder / 'missed-finals.json').write_text(json.dumps(dict(schema_version=1, games=[
            dict(game_id=gid, start_time=games[gid]['start_time'] if gid in games else TIP, first_seen_at=TIP) for gid in game_ids])))

    def fake(self, source):
        def run(folder, schedule, ids, now):
            self.calls.append((source, tuple(ids), now))
            status = self.status[source]
            if isinstance(status, Exception):
                raise status
            return [dict(game_id=gid, status=status) for gid in ids]
        return run

    def recover(self, minutes, schedule=None):
        return R.auto_recover(self.folder, schedule or self.schedule, now=at(minutes),
                              run={s: self.fake(s) for s in R.AUTO_SOURCES})

    def state(self, source='reports', gid='0022600151'):
        return json.loads((self.folder / R.AUTO_FILE).read_text())['games'][gid]['sources'][source]

    def test_a_missed_game_is_recovered_once_it_is_over_and_never_again(self):
        self.assertIsNone(self.recover(60))  # tipped, but the calendar has not seen the final yet
        self.assertEqual(self.calls, [])
        (self.folder / 'finals').mkdir()
        (self.folder / 'finals' / '0022600151.json').write_text('[]')
        summary = self.recover(61)
        self.assertEqual(summary['games'], ['0022600151'])
        self.assertEqual(summary['recorded'], ['0022600151:reports', '0022600151:closing_lines', '0022600151:kalshi'])
        self.assertEqual([c[0] for c in self.calls], list(R.AUTO_SOURCES))
        self.assertEqual((self.state()['status'], self.state()['attempts']), ('recorded', 1))
        self.assertIsNone(self.recover(600))
        self.assertEqual(len(self.calls), 3)

    def test_a_game_the_calendar_marks_final_or_six_hours_old_is_over(self):
        final = dict(self.schedule, games=[GAME, LATER])  # GAME is final in the calendar
        self.assertIsNotNone(self.recover(1, schedule=final))
        self.setUp()
        self.assertIsNone(self.recover(R.AUTO_OVER_HOURS * 60 - 1))
        self.assertIsNotNone(self.recover(R.AUTO_OVER_HOURS * 60))

    def test_a_failed_source_is_retried_after_each_wait_then_given_up_and_others_are_kept(self):
        self.status.update(reports='unavailable', kalshi=OSError('Kalshi down'))
        clock = R.AUTO_OVER_HOURS * 60
        summary = self.recover(clock)
        self.assertEqual(summary['recorded'], ['0022600151:closing_lines'])
        self.assertEqual(summary['retrying'], ['0022600151:reports', '0022600151:kalshi'])
        self.assertEqual(self.state('kalshi')['result']['error'], 'OSError: Kalshi down')
        self.status['kalshi'] = 'exists'
        for wait in R.AUTO_RETRY_MINUTES:
            self.assertIsNone(self.recover(clock + wait - 1))
            clock += wait
            summary = self.recover(clock)
            self.assertNotIn('closing_lines', [c[0] for c in self.calls[-2:]])
        self.assertEqual(summary['gave_up'], ['0022600151:reports'])
        self.assertEqual((self.state()['attempts'], self.state()['gave_up']), (len(R.AUTO_RETRY_MINUTES) + 1, True))
        self.assertEqual(self.state('kalshi')['status'], 'exists')
        before = len(self.calls)
        self.assertIsNone(self.recover(clock + 10 ** 5))
        self.assertEqual(len(self.calls), before)

    def test_two_games_a_run_oldest_first_and_a_rescheduled_game_starts_over(self):
        later = dict(GAME, game_id='0022600152', start_time=at(30))
        latest = dict(GAME, game_id='0022600153', start_time=at(60))
        schedule = dict(SCHEDULE, games=[GAME, later, latest])
        self.miss(['0022600153', '0022600151', '0022600152'], schedule)
        self.assertEqual(self.recover(600, schedule)['games'], ['0022600151', '0022600152'])
        self.assertEqual(self.recover(601, schedule)['games'], ['0022600153'])
        self.assertIsNone(self.recover(602, schedule))
        moved = dict(SCHEDULE, games=[GAME, later, dict(latest, start_time=at(120))])
        self.miss(['0022600151', '0022600152', '0022600153'], moved)
        self.assertEqual(self.recover(603, moved)['games'], ['0022600153'])

    def test_a_game_missing_from_the_calendar_is_given_up_without_a_fetch(self):
        self.miss(['0022699999'])
        summary = R.auto_recover(self.folder, self.schedule, now=at(600))  # the real recoveries: no network for an unknown game
        self.assertEqual(summary['gave_up'], ['0022699999:reports', '0022699999:closing_lines', '0022699999:kalshi'])
        self.assertIsNone(R.auto_recover(self.folder, self.schedule, now=at(601)))

    def test_no_missed_record_does_nothing(self):
        (self.folder / 'missed-finals.json').unlink()
        self.assertIsNone(self.recover(600))
        self.assertFalse((self.folder / R.AUTO_FILE).exists())

    def test_the_derived_service_recovers_writes_the_report_record_and_rebuilds(self):
        from nba_forecast import operations
        (self.folder / 'schedule').mkdir()
        (self.folder / 'schedule' / 'latest.json').write_text(json.dumps(dict(self.schedule, version='v1', games=[GAME, LATER])))
        fetch = pdf_fetch({'Injury-Report_2026-10-31_07_30PM.pdf'})
        builds = []

        def build(folder, db, schedule, active_model, model_digest, report):
            builds.append(1)
            for name in ('performance.json', 'benchmarks.json', 'daily-reviews/index.json', 'market/report.json'):
                (folder / name).parent.mkdir(parents=True, exist_ok=True)
                (folder / name).write_text('{}')

        real = R.recover_report_records

        def reports(folder, schedule, ids, *, now):
            return real(folder, schedule, ids, now=now, fetch=fetch, sleep=lambda s: None)

        def none(folder, schedule, ids, *, now):
            return [dict(game_id=gid, status='no_close') for gid in ids]

        def refresh(clock):
            with patch('nba_forecast.operations._derived_reports', side_effect=build), \
                 patch('nba_forecast.operations._now', return_value=clock), \
                 patch('nba_forecast.recover_evidence.recover_report_records', new=reports), \
                 patch('nba_forecast.recover_evidence.recover_closing_lines', new=none), \
                 patch('nba_forecast.recover_evidence.recover_kalshi_lines', new=none):
                return operations.run_derived(self.folder)

        first = refresh(NOW)
        self.assertEqual(first['status'], 'rebuilt')
        self.assertEqual(first['recovered_evidence']['recorded'], ['0022600151:reports'])
        self.assertEqual(first['recovered_evidence']['retrying'], ['0022600151:closing_lines', '0022600151:kalshi'])
        final = json.loads((self.folder / 'injuries' / 'games' / '0022600151' / 'final.json').read_text())
        self.assertEqual(final['late_capture']['recovered_at'], NOW)
        self.assertIn('0022600151:reports', json.loads((self.folder / 'derived-request.json').read_text())['reason'])
        second = refresh(NOW)
        self.assertNotIn('recovered_evidence', second)
        self.assertEqual((second['status'], len(builds)), ('unchanged', 1))


if __name__ == '__main__':
    unittest.main()
