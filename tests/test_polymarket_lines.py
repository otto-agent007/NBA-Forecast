"""Polymarket's order-book prices become a third closing line, archived and scored like Kalshi's.

The fixtures are the Gamma event and CLOB order books of Trail Blazers at Knicks
captured 2026-11-21 23:11 UTC, trimmed by ``scripts/research/polymarket_fixture.py``.
"""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from nba_forecast import kalshi_lines as K
from nba_forecast import market_lines as M
from nba_forecast import polymarket_lines as P

FIXTURES = Path(__file__).parent / 'fixtures' / 'polymarket'
EVENT = json.loads((FIXTURES / 'event.json').read_text())
BOOKS = json.loads((FIXTURES / 'books.json').read_text())
GAME = dict(game_id='0022600275', start_time='2026-11-22T00:30:00Z', home_team_id='1610612752', away_team_id='1610612757',
            home_tricode='NYK', away_tricode='POR', status='scheduled')
KNICKS = next(json.loads(m['clobTokenIds'])[1] for m in EVENT['events'][0]['markets'] if m['sportsMarketType'] == 'moneyline')


def books(home=None, width=.01):
    """The saved books, the Knicks winner book replaced by one bid and ask around ``home`` when given."""
    data = copy.deepcopy(BOOKS)
    if home is not None:
        book = next(b for b in data if b['asset_id'] == KNICKS)
        book.update(bids=[dict(price=f'{home - width / 2:.3f}', size='100')], asks=[dict(price=f'{home + width / 2:.3f}', size='100')])
    return data


def fetcher(event=EVENT, book_data=None, calls=None, zipped=False):
    def fetch(url, body=None):
        if calls is not None:
            calls.append((url, body))
        raw = json.dumps(event if body is None else book_data if book_data is not None else BOOKS).encode()
        return gzip.compress(raw, mtime=0) if zipped else raw
    return fetch


class PolymarketTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_event_slugs_use_the_eastern_game_date_and_lowercase_tricodes(self):
        self.assertEqual(P.event_slug(GAME), 'nba-por-nyk-2026-11-21')
        late = dict(GAME, start_time='2026-11-22T03:00:00Z', home_tricode='SAC', away_tricode='BOS')  # 10 PM Eastern on the 21st
        self.assertEqual(P.event_slug(late), 'nba-bos-sac-2026-11-21')

    def test_a_capture_reads_live_books_for_the_winner_and_the_lines_crossing_one_half(self):
        calls = []
        report = P.capture_polymarket_lines(self.folder, [GAME], '2026-11-21T23:11:30Z', fetch=fetcher(calls=calls))
        self.assertEqual(report['captured'], [GAME['game_id']], report)
        self.assertEqual([url for url, _ in calls], [P.EVENT_API.format('nba-por-nyk-2026-11-21'), P.BOOKS_API])
        tokens = [item['token_id'] for item in json.loads(calls[1][1])]
        self.assertEqual(len(tokens), 27, 'the winner market and the listed spreads and totals; no first-half market, no prop')
        self.assertIn(KNICKS, tokens, 'the winner book asked for is the home team\'s')
        record = P.read_polymarket_lines(self.folder, GAME['game_id'])[-1]
        self.assertAlmostEqual(record['home_win_probability'], .495)
        line = record['moneyline']
        self.assertEqual((line['bid'], line['ask'], line['width'], line['bid_size'], line['ask_size']), (.49, .5, .01, 225955.83, 651737.76))
        self.assertEqual((line['volume'], line['liquidity'], line['book_time']), (677679.751282, 733483.629, '2026-11-21T23:11:30.723000+00:00'))
        # Blazers -1.5 at 0.485 is the home margin over -1.5 at 0.515; Knicks -1.5 at 0.455: the half is crossed at -0.75.
        self.assertAlmostEqual(record['implied_home_margin'], -.75)
        self.assertAlmostEqual(record['lines']['spread']['home']['current_line'], .75)
        self.assertAlmostEqual(record['implied_total'], 214.0)
        quotes = {q['home_margin_over']: q for q in record['spread_quotes']}
        self.assertEqual((quotes[-1.5]['side'], quotes[-1.5]['probability'], quotes[-1.5]['bid'], quotes[-1.5]['ask']), ('away', .515, .48, .49))
        self.assertEqual((quotes[1.5]['side'], quotes[1.5]['probability']), ('home', .455))
        self.assertEqual(record['main_spread'], dict(home_line=1.5, home_cover_probability=.515, slug='nba-por-nyk-2026-11-21-spread-away-1pt5'))
        self.assertEqual(record['main_total'], dict(line=214.5, over_probability=.495, slug='nba-por-nyk-2026-11-21-total-214pt5'))
        self.assertEqual((record['provider'], record['event']['slug'], len(record['sources'])), ('Polymarket', 'nba-por-nyk-2026-11-21', 2))
        raw = self.folder / 'market' / 'polymarket' / 'raw'
        sources = {json.loads(p.read_text())['source_url']: json.loads(p.read_text()) for p in raw.glob('*.provenance.json')}
        self.assertEqual(sorted(sources), [P.BOOKS_API, P.EVENT_API.format('nba-por-nyk-2026-11-21')])
        self.assertEqual(sources[P.BOOKS_API]['request_sha256'], record['sources'][1]['request_sha256'])
        for source in record['sources']:
            self.assertEqual(hashlib.sha256((raw / (source['sha256'] + '.json')).read_bytes()).hexdigest(), source['sha256'])

    def test_gzip_responses_are_archived_as_received(self):
        report = P.capture_polymarket_lines(self.folder, [GAME], '2026-11-21T23:11:30Z', fetch=fetcher(zipped=True))
        self.assertEqual(report['captured'], [GAME['game_id']], report)
        record = P.read_polymarket_lines(self.folder, GAME['game_id'])[-1]
        self.assertAlmostEqual(record['home_win_probability'], .495)
        archive = self.folder / 'market' / 'polymarket' / 'raw' / (record['sources'][0]['sha256'] + '.json.gz')
        self.assertEqual(json.loads(gzip.decompress(archive.read_bytes())), EVENT)

    def test_an_empty_or_wide_book_is_not_a_price(self):
        self.assertEqual(P.quote(dict(bids=[], asks=[dict(price='0.5', size='10')]))['mid'], None)
        self.assertEqual(P.quote(None)['mid'], None)
        wide = P.quote(dict(bids=[dict(price='0.30', size='5')], asks=[dict(price='0.45', size='5')]))
        self.assertEqual((wide['bid'], wide['ask'], wide['width'], wide['mid']), (.3, .45, .15, None))
        best = P.quote(dict(bids=[dict(price='0.47', size='1'), dict(price='0.49', size='2')], asks=[dict(price='0.52', size='3'), dict(price='0.5', size='4')]))
        self.assertEqual((best['bid'], best['bid_size'], best['ask'], best['ask_size']), (.49, 2.0, .5, 4.0), 'the best prices, whatever the order')
        record = P.extract(GAME, EVENT, books(home=.5, width=.2), '2026-11-21T23:11:30Z', [])
        self.assertIsNone(record['home_win_probability'])
        self.assertAlmostEqual(record['implied_home_margin'], -.75, msg='the spreads keep their own books')

    def test_captures_follow_the_cadence_and_append_only_on_change(self):
        same = fetcher()
        P.capture_polymarket_lines(self.folder, [GAME], '2026-11-21T20:00:00Z', fetch=same)
        self.assertEqual(P.capture_polymarket_lines(self.folder, [GAME], '2026-11-21T20:05:00Z', fetch=same)['skipped'], [GAME['game_id']])
        self.assertEqual(P.capture_polymarket_lines(self.folder, [GAME], '2026-11-21T20:16:00Z', fetch=same)['unchanged'], [GAME['game_id']])
        moved = P.capture_polymarket_lines(self.folder, [GAME], '2026-11-22T00:27:30Z', fetch=fetcher(book_data=books(home=.44)))
        self.assertEqual(moved['captured'], [GAME['game_id']])
        again = P.capture_polymarket_lines(self.folder, [GAME], '2026-11-22T00:28:30Z', fetch=fetcher(book_data=books(home=.44)))
        self.assertEqual(again['unchanged'], [GAME['game_id']], 'a one-minute slot in the last five minutes')
        after = P.capture_polymarket_lines(self.folder, [GAME], '2026-11-22T00:31:00Z', fetch=same)
        self.assertEqual({k: after[k] for k in ('captured', 'unchanged', 'skipped', 'errors')}, dict(captured=[], unchanged=[], skipped=[], errors=[]))
        records = P.read_polymarket_lines(self.folder, GAME['game_id'])
        self.assertEqual([round(r['home_win_probability'], 3) for r in records], [.495, .44])
        self.assertEqual(P.closing_polymarket_line(records, GAME['start_time'])['captured_at'], '2026-11-22T00:27:30Z')

    def test_a_game_without_an_event_is_unlisted_and_a_mismatched_or_unpriced_one_is_an_error(self):
        other = dict(GAME, game_id='0022600299', home_tricode='LAL', away_tricode='POR')
        calls = []
        missing = P.capture_polymarket_lines(self.folder, [other], '2026-11-21T20:00:00Z', fetch=fetcher(event=dict(events=[]), calls=calls))
        self.assertEqual((missing['unlisted'], missing['errors'], len(calls)), ([other['game_id']], [], 1))
        again = P.capture_polymarket_lines(self.folder, [other], '2026-11-21T20:01:00Z', fetch=fetcher(event=dict(events=[]), calls=calls))
        self.assertEqual((again['skipped'], len(calls)), ([other['game_id']], 1), 'asked again at the next slot, not every minute')
        swapped = copy.deepcopy(EVENT)
        for team in swapped['events'][0]['teams']:
            team['ordering'] = 'home' if team['ordering'] == 'away' else 'away'
        wrong = P.capture_polymarket_lines(self.folder, [GAME], '2026-11-21T20:00:00Z', fetch=fetcher(event=swapped))
        self.assertIn('does not match', wrong['errors'][0]['error'])
        props = copy.deepcopy(EVENT)
        props['events'][0]['markets'] = [m for m in props['events'][0]['markets'] if m['sportsMarketType'] == 'points']
        unpriced = P.capture_polymarket_lines(self.folder, [GAME], '2026-11-21T20:00:00Z', fetch=fetcher(event=props))
        self.assertIn('no winner market', unpriced['errors'][0]['error'])
        failing = P.capture_polymarket_lines(self.folder, [GAME], '2026-11-21T20:00:00Z', fetch=lambda url, body=None: b'<html>blocked</html>')
        self.assertIn('not JSON', failing['errors'][0]['error'])
        self.assertEqual(P.read_polymarket_lines(self.folder, GAME['game_id']), [])
        only_winner = copy.deepcopy(EVENT)  # the spreads and totals are listed about a day before tip, the winner market earlier
        only_winner['events'][0]['markets'] = [m for m in only_winner['events'][0]['markets'] if m['sportsMarketType'] == 'moneyline']
        early = P.capture_polymarket_lines(self.folder, [GAME], '2026-11-21T20:00:00Z', fetch=fetcher(event=only_winner))
        self.assertEqual(early['captured'], [GAME['game_id']])
        record = P.read_polymarket_lines(self.folder, GAME['game_id'])[-1]
        self.assertEqual((record['home_win_probability'], record['implied_home_margin'], record['main_spread']), (.495, None, None))

    def test_only_the_two_polymarket_requests_are_made(self):
        with self.assertRaises(ValueError):
            P._fetch('https://gamma-api.polymarket.com/events/keyset?slug=nba-por-nyk-2026-11-21&closed=true', self.folder, fetcher())
        with self.assertRaises(ValueError):
            P._fetch('https://gamma-api.polymarket.com/events/keyset?slug=us-election-2028', self.folder, fetcher())
        with self.assertRaises(ValueError):
            P._fetch('https://clob.polymarket.com/order', self.folder, fetcher(), b'[{"token_id":"1"}]')
        with self.assertRaises(ValueError):
            P._fetch(P.BOOKS_API, self.folder, fetcher(), b'[{"token_id":"1","side":"BUY"}]')
        self.assertFalse((self.folder / 'market').exists(), 'a refused request fetches and archives nothing')

    def test_games_past_the_deadline_are_deferred_before_and_between_requests(self):
        calls = []
        late = P.capture_polymarket_lines(self.folder, [GAME], '2026-11-21T20:00:00Z', fetch=fetcher(calls=calls), deadline=time.monotonic() - 1)
        self.assertEqual((late['deferred'], late['captured'], calls), ([GAME['game_id']], [], []))
        clock = [time.monotonic()]
        def expiring(url, body=None):
            calls.append((url, body))
            clock[0] += 100  # the event request alone used up the budget
            return fetcher()(url, body)
        with patch('nba_forecast.polymarket_lines.time.monotonic', side_effect=lambda: clock[0]):
            mid = P.capture_polymarket_lines(self.folder, [GAME], '2026-11-21T20:00:00Z', fetch=expiring, deadline=clock[0] + 60)
        self.assertEqual((mid['deferred'], mid['captured'], len(calls)), ([GAME['game_id']], [], 1))
        self.assertEqual(P.read_polymarket_lines(self.folder, GAME['game_id']), [], 'nothing partial is recorded')
        done = P.capture_polymarket_lines(self.folder, [GAME], '2026-11-21T20:00:00Z', fetch=fetcher(calls=calls), deadline=time.monotonic() + 60)
        self.assertEqual((done['captured'], done['deferred']), ([GAME['game_id']], []), 'the slot stayed open for the next run')

    def test_a_game_that_tips_while_the_slate_is_captured_is_left_alone(self):
        clock = [time.monotonic()]
        with patch('nba_forecast.polymarket_lines.time.monotonic', side_effect=lambda: clock.__setitem__(0, clock[0] + 40) or clock[0]):
            report = P.capture_polymarket_lines(self.folder, [GAME], '2026-11-22T00:29:30Z', fetch=fetcher())
        self.assertEqual((report['captured'], report['errors']), ([], []), 'its books are cleared at the start')


def sportsbook(home_ml, away_ml, spread):
    """One closing DraftKings record as ``market_lines.extract_game_lines`` writes it."""
    side = lambda line, odds: dict(current_line=line, current_odds=odds, open_line=None, open_odds=None)  # noqa: E731
    return dict(schema_version=1, game_id=GAME['game_id'], start_time=GAME['start_time'], event_id='401910024', home_team_id=GAME['home_team_id'],
                away_team_id=GAME['away_team_id'], provider_id='100', provider='Draft Kings', details='',
                lines=dict(spread=dict(home=side(spread, -110), away=side(-spread, -110)), total=dict(over=side(213.5, -110), under=side(213.5, -110)),
                           moneyline=dict(home=side(None, home_ml), away=side(None, away_ml))),
                source_url='https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=401910024',
                retrieved_at='2026-11-22T00:25:15Z', sha256='b' * 64)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.folder / 'finals').mkdir()
        finals = [dict(game_id=GAME['game_id'], team_id=GAME['home_team_id'], pts=104), dict(game_id=GAME['game_id'], team_id=GAME['away_team_id'], pts=110)]
        (self.folder / 'finals' / (GAME['game_id'] + '.json')).write_text(json.dumps(finals))
        self.selected = {(GAME['game_id'], 'final'): dict(run_id='r1', totals=dict(status='complete', points_mean=216, teams=[
            dict(team_id=GAME['home_team_id'], side='home', points_mean=110), dict(team_id=GAME['away_team_id'], side='away', points_mean=106)]),
            margin_forecast=dict(home_win_probability=.62))}
        M.record_game_lines(self.folder, [sportsbook(-105, -112, 1.5)], '2026-11-22T00:25:45Z')

    def report(self):
        return M.market_report(self.folder, dict(season='2026-27', games=[GAME]), '2026-11-22T05:00:00Z', selected=self.selected)

    def kalshi(self, home):
        K.record_kalshi_line(self.folder, dict(game_id=GAME['game_id'], start_time=GAME['start_time'], captured_at='2026-11-22T00:29:45Z',
                                               home_win_probability=home, implied_home_margin=-1.0, implied_total=214.0, lines={}))

    def test_index_and_report_carry_the_third_close(self):
        P.capture_polymarket_lines(self.folder, [GAME], '2026-11-22T00:29:45Z', fetch=fetcher())
        providers = M.build_index(self.folder)['games'][GAME['game_id']]['providers']
        self.assertEqual(sorted(providers), ['100', 'polymarket'])
        self.assertEqual((providers['polymarket']['provider'], providers['polymarket']['home_win_probability']), ('Polymarket', .495))
        self.assertAlmostEqual(providers['polymarket']['latest']['spread']['home']['current_line'], .75)
        self.assertTrue(M.refresh_index(self.folder))
        P.capture_polymarket_lines(self.folder, [GAME], '2026-11-22T00:29:55Z', fetch=fetcher(book_data=books(home=.44)))
        self.assertFalse(M.refresh_index(self.folder), 'the same slot captured nothing')
        report = self.report()
        row = report['games'][0]
        self.assertEqual((row['polymarket_home_win'], row['polymarket_captured_at']), (.495, '2026-11-22T00:29:45Z'))
        self.assertAlmostEqual(row['polymarket_margin'], -.75)
        self.assertAlmostEqual(row['polymarket_total'], 214.0)
        self.assertAlmostEqual(row['polymarket_abs_error'], 5.25)  # the Knicks lost by 6
        summary = report['summary']
        self.assertAlmostEqual(summary['margin_abs_error']['polymarket'], 5.25)
        self.assertEqual(summary['margin_abs_error']['polymarket_n'], 1)
        self.assertEqual(summary['polymarket_winner']['n'], 1)
        self.assertAlmostEqual(summary['polymarket_winner']['brier'], .495 ** 2)
        self.assertNotIn('winner_by_source', summary, 'Kalshi has no price for this game')

    def test_a_capture_after_tipoff_is_not_the_close_and_a_game_without_polymarket_keeps_its_row(self):
        record = P.extract(GAME, EVENT, BOOKS, '2026-11-22T00:30:05Z', [])
        P.record_polymarket_line(self.folder, record)
        report = self.report()
        self.assertEqual(len(report['games']), 1)
        self.assertNotIn('polymarket_home_win', report['games'][0])
        self.assertNotIn('polymarket_winner', report['summary'])

    def test_the_sources_are_compared_on_the_games_all_of_them_priced(self):
        P.capture_polymarket_lines(self.folder, [GAME], '2026-11-22T00:29:45Z', fetch=fetcher())
        self.kalshi(.47)
        table = self.report()['summary']['winner_by_source']
        draftkings = (105 / 205) / (105 / 205 + 112 / 212)  # -105 / -112 without the vig
        self.assertEqual((table['games'], table['dates']), (1, 1))
        self.assertEqual(list(table['brier']), ['model', 'draftkings', 'kalshi', 'polymarket'])
        for name, probability in dict(model=.62, draftkings=draftkings, kalshi=.47, polymarket=.495).items():
            self.assertAlmostEqual(table['brier'][name], probability ** 2, msg=name)  # the Knicks lost
        self.assertEqual(table['best_market'], 'kalshi')
        self.assertAlmostEqual(table['model_minus']['kalshi']['difference'], .62 ** 2 - .47 ** 2)
        self.assertIsNone(table['model_minus']['kalshi']['difference_se'], 'one game has no standard error')

    def test_the_best_market_is_computed_over_every_common_game_and_only_those(self):
        rows = []
        for i, (won, model, dk, kalshi, poly) in enumerate([(True, .6, .7, .65, .8), (False, .6, .4, .45, .3), (True, .5, .6, .55, None),
                                                               (False, .9, None, .2, .1), (True, .7, .9, .6, .95)]):
            rows.append(dict(game_id=f'g{i}', start_time=f'2026-11-{20 + i // 2}T00:30:00Z', provider='Draft Kings', actual_margin=5 if won else -5,
                             model_home_win=model, market_home_win=dk, kalshi_home_win=kalshi, polymarket_home_win=poly))
        rows.append(dict(rows[0], provider='ESPN BET', market_home_win=.99))  # a second sportsbook's row never counts a game twice
        rows.append(dict(rows[1], game_id='tie', actual_margin=0))
        table = M.winner_by_source(rows)
        self.assertEqual((table['games'], table['dates']), (3, 2), 'g0, g1 and g4: g2 has no Polymarket price, g3 no DraftKings one')
        expected = dict(model=(.16 + .36 + .09) / 3, draftkings=(.09 + .16 + .01) / 3, kalshi=(.1225 + .2025 + .16) / 3, polymarket=(.04 + .09 + .0025) / 3)
        for name, value in expected.items():
            self.assertAlmostEqual(table['brier'][name], value, msg=name)
        self.assertEqual(table['best_market'], 'polymarket')
        self.assertAlmostEqual(table['model_minus']['polymarket']['difference'], expected['model'] - expected['polymarket'])
        self.assertGreater(table['model_minus']['polymarket']['difference_se'], 0)
        self.assertIsNone(M.winner_by_source([r for r in rows if r['game_id'] in ('g2', 'g3')]))


if __name__ == '__main__':
    unittest.main()
