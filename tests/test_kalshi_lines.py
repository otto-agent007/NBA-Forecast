"""Kalshi's traded probabilities become a second closing line, archived and compared like the sportsbook's."""
import json
from pathlib import Path
import tempfile
import unittest

from nba_forecast import kalshi_lines as K
from nba_forecast import market_lines as M

GAME = dict(game_id='0022600128', start_time='2026-10-27T23:30:00Z', home_team_id='H', away_team_id='A',
            home_tricode='BOS', away_tricode='BKN', status='scheduled')


def market(ticker, bid, ask, strike=None):
    item = dict(ticker=ticker, yes_bid_dollars=f'{bid:.4f}', yes_ask_dollars=f'{ask:.4f}', status='active')
    if strike is not None:
        item['floor_strike'] = strike
    return item


def payloads(home=.84, margin=12.5, total=231.5):
    game = [market('KXNBAGAME-26OCT27BKNBOS-BKN', 1 - home - .01, 1 - home), market('KXNBAGAME-26OCT27BKNBOS-BOS', home, home + .01)]
    spreads = [market(f'KXNBASPREAD-26OCT27BKNBOS-BOS{int(s + .5)}', .49 + (margin - s) * .04, .51 + (margin - s) * .04, s)
               for s in (margin - 3, margin - 1, margin + 1, margin + 3)]
    spreads.append(market('KXNBASPREAD-26OCT27BKNBOS-BKN2', .05, .07, 1.5))  # Brooklyn wins by over 1.5: home margin below -1.5
    spreads.append(market('KXNBASPREAD-26OCT27BKNBOS-BOS40', .01, 1.0, 39.5))  # an empty book is not a price
    totals = [market(f'KXNBATOTAL-26OCT27BKNBOS-{int(t + .5)}', .49 + (total - t) * .02, .51 + (total - t) * .02, t)
              for t in (total - 4, total - 2, total, total + 2)]
    return {'KXNBAGAME': game, 'KXNBASPREAD': spreads, 'KXNBATOTAL': totals}


def fetcher(data):
    def fetch(url):
        series = url.split('event_ticker=')[1].split('-')[0]
        return json.dumps(dict(markets=data[series], cursor='')).encode()
    return fetch


class KalshiTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_event_tickers_use_the_eastern_game_date_and_nba_tricodes(self):
        self.assertEqual(K.event_ticker('KXNBAGAME', GAME), 'KXNBAGAME-26OCT27BKNBOS')
        late = dict(GAME, start_time='2026-10-28T02:00:00Z')  # 10 PM Eastern on the 27th
        self.assertEqual(K.event_ticker('KXNBASPREAD', late), 'KXNBASPREAD-26OCT27BKNBOS')

    def test_implied_lines_cross_one_half_and_skip_empty_books(self):
        report = K.capture_kalshi_lines(self.folder, [GAME], '2026-10-27T20:00:00Z', fetch=fetcher(payloads()))
        self.assertEqual(report['captured'], [GAME['game_id']])
        record = K.read_kalshi_lines(self.folder, GAME['game_id'])[-1]
        self.assertAlmostEqual(record['home_win_probability'], .845)
        self.assertAlmostEqual(record['implied_home_margin'], 12.5)
        self.assertAlmostEqual(record['lines']['spread']['home']['current_line'], -12.5)
        self.assertAlmostEqual(record['implied_total'], 231.5)
        quotes = {q['home_margin_over']: q['probability'] for q in record['spread_quotes']}
        self.assertIsNone(quotes[39.5], 'a 0.01/1.00 book is not a quote')
        self.assertAlmostEqual(quotes[-1.5], .94, 'the away side is read as the home margin below minus the strike')
        self.assertEqual(len(record['sources']), 3)
        raw = list((self.folder / 'market' / 'kalshi' / 'raw').glob('*.provenance.json'))
        self.assertEqual(len(raw), 3)
        self.assertEqual(json.loads(raw[0].read_text())['source_url'].split('?')[0], K.API.split('?')[0])
        self.assertIsNone(K.crossing([(1, .8), (3, .7)]), 'no bracket, no line')

    def test_captures_follow_the_cadence_and_append_only_on_change(self):
        same = fetcher(payloads())
        K.capture_kalshi_lines(self.folder, [GAME], '2026-10-27T20:00:00Z', fetch=same)
        self.assertEqual(K.capture_kalshi_lines(self.folder, [GAME], '2026-10-27T20:05:00Z', fetch=same)['skipped'], [GAME['game_id']])
        self.assertEqual(K.capture_kalshi_lines(self.folder, [GAME], '2026-10-27T20:16:00Z', fetch=same)['unchanged'], [GAME['game_id']])
        moved = K.capture_kalshi_lines(self.folder, [GAME], '2026-10-27T23:27:30Z', fetch=fetcher(payloads(home=.80, margin=10.5)))
        self.assertEqual(moved['captured'], [GAME['game_id']])
        again = K.capture_kalshi_lines(self.folder, [GAME], '2026-10-27T23:28:30Z', fetch=fetcher(payloads(home=.80, margin=10.5)))
        self.assertEqual(again['unchanged'], [GAME['game_id']], 'a one-minute slot in the last five minutes')
        after = K.capture_kalshi_lines(self.folder, [GAME], '2026-10-27T23:31:00Z', fetch=same)
        self.assertEqual({k: after[k] for k in ('captured', 'unchanged', 'skipped', 'errors')}, dict(captured=[], unchanged=[], skipped=[], errors=[]))
        records = K.read_kalshi_lines(self.folder, GAME['game_id'])
        self.assertEqual([round(r['implied_home_margin'], 1) for r in records], [12.5, 10.5])
        closing = K.closing_kalshi_line(records, GAME['start_time'])
        self.assertEqual(closing['captured_at'], '2026-10-27T23:27:30Z')
        missing = K.capture_kalshi_lines(self.folder, [dict(GAME, game_id='0022600199', home_tricode='LAL', away_tricode='POR')],
                                         '2026-10-27T20:00:00Z', fetch=lambda url: b'{"markets": []}')
        self.assertIn('no winner market', missing['errors'][0]['error'])

    def test_index_and_report_carry_the_second_close(self):
        from tests.test_market_lines import GAME as ESPN_GAME, META, summary
        espn = dict(ESPN_GAME, home_tricode='BOS', away_tricode='NYK')
        M.record_game_lines(self.folder, M.extract_game_lines(summary(), espn, '2026-27', META), '2026-10-23T21:00:30Z')
        K.capture_kalshi_lines(self.folder, [espn], '2026-10-23T22:55:00Z', fetch=fetcher(payloads(home=.60, margin=3.5, total=221.5)))
        index = M.build_index(self.folder)['games'][espn['game_id']]
        self.assertEqual(sorted(index['providers']), ['100', 'kalshi'])
        self.assertAlmostEqual(index['providers']['kalshi']['latest']['spread']['home']['current_line'], -3.5)
        (self.folder / 'finals').mkdir()
        finals = [dict(game_id=espn['game_id'], team_id='H', pts=115), dict(game_id=espn['game_id'], team_id='A', pts=108)]
        (self.folder / 'finals' / (espn['game_id'] + '.json')).write_text(json.dumps(finals))
        selected = {(espn['game_id'], 'final'): dict(run_id='r1', totals=dict(points_mean=224, teams=[
            dict(team_id='H', side='home', points_mean=114), dict(team_id='A', side='away', points_mean=110)]),
            margin_forecast=dict(home_win_probability=.6))}
        report = M.market_report(self.folder, dict(season='2026-27', games=[espn]), '2026-10-24T05:00:00Z', selected=selected)
        row = report['games'][0]
        self.assertAlmostEqual(row['kalshi_margin'], 3.5)
        self.assertAlmostEqual(row['kalshi_abs_error'], 3.5)  # home won by 7
        self.assertAlmostEqual(report['summary']['margin_abs_error']['kalshi'], 3.5)
        self.assertEqual(report['summary']['margin_abs_error']['kalshi_n'], 1)
        self.assertAlmostEqual(report['summary']['kalshi_winner']['brier'], (.605 - 1) ** 2, places=3)


class KalshiBudgetTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_games_past_the_deadline_are_deferred_before_and_between_requests(self):
        import time
        calls = []
        def counting(url):
            calls.append(url)
            return fetcher(payloads())(url)
        late = K.capture_kalshi_lines(self.folder, [GAME], '2026-10-27T20:00:00Z', fetch=counting, deadline=time.monotonic() - 1)
        self.assertEqual((late['deferred'], late['captured'], calls), ([GAME['game_id']], [], []))
        clock = [time.monotonic()]
        def expiring(url):
            calls.append(url)
            clock[0] += 100  # the first market request alone used up the budget
            return fetcher(payloads())(url)
        with __import__('unittest').mock.patch('nba_forecast.kalshi_lines.time.monotonic', side_effect=lambda: clock[0]):
            mid = K.capture_kalshi_lines(self.folder, [GAME], '2026-10-27T20:00:00Z', fetch=expiring, deadline=clock[0] + 60)
        self.assertEqual((mid['deferred'], mid['captured'], len(calls)), ([GAME['game_id']], [], 1))
        self.assertEqual(K.read_kalshi_lines(self.folder, GAME['game_id']), [], 'nothing partial is recorded')
        done = K.capture_kalshi_lines(self.folder, [GAME], '2026-10-27T20:00:00Z', fetch=counting, deadline=time.monotonic() + 60)
        self.assertEqual((done['captured'], done['deferred']), ([GAME['game_id']], []), 'the slot stayed open for the next tick')

if __name__ == '__main__':
    unittest.main()
