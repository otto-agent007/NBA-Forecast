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


if __name__ == '__main__':
    unittest.main()
