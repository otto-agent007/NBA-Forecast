"""Kalshi's player prop ladders are captured, matched to roster players conservatively and scored against the forecasts."""
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from nba_forecast import kalshi_props as P

GAME = dict(game_id='0022600210', start_time='2026-11-10T03:00:00Z', home_team_id='1610612744', away_team_id='1610612740',
            home_tricode='GSW', away_tricode='NOP', status='scheduled')
ROSTER = [dict(player_id='201939', name='Stephen Curry', team_id='1610612744'),
          dict(player_id='203110', name='Draymond Green', team_id='1610612744'),
          dict(player_id='1630530', name='Trey Murphy III', team_id='1610612740'),
          dict(player_id='1642300', name='Karlo Matković', team_id='1610612740'),
          dict(player_id='1', name='Jalen Williams', team_id='1610612740'),
          dict(player_id='2', name='Jaylin Williams', team_id='1610612740'),
          dict(player_id='3', name='Nicolas Claxton', team_id='1610612744')]
RULES = ('Note: If a player is inactive, or active but never takes the court, the market settles to a fair price. '
         'Once a player enters the game the market settles based on the player\'s points recorded.')


def market(series, key, name, threshold, bid, ask, status='active', player=None, volume='10.00', label='points'):
    event = f'{series}-26NOV09NOPGSW'
    return dict(ticker=f'{event}-{key}-{int(threshold + .5)}', event_ticker=event, title=f'{name}: {int(threshold + .5)}+ {label}',
                yes_sub_title=f'{name}: {int(threshold + .5)}+', floor_strike=threshold, strike_type='greater', status=status,
                yes_bid_dollars=f'{bid:.4f}', yes_ask_dollars=f'{ask:.4f}', volume_fp=volume, open_interest_fp='5.00',
                custom_strike=dict(basketball_player=player or key.lower(), basketball_team='t'), rules_secondary=RULES)


def points(curry=(.89, .72, .45, .21), volume='10.00'):
    out = [market('KXNBAPTS', 'GSWSCURRY30', 'Stephen Curry', t, p - .005, p + .005, volume=volume) for t, p in zip((19.5, 24.5, 29.5, 34.5), curry)]
    out += [market('KXNBAPTS', 'GSWDGREEN23', 'Draymond Green', 9.5, .08, .09),
            market('KXNBAPTS', 'GSWDGREEN23', 'Draymond Green', 14.5, .02, .30),  # wide: no mid
            market('KXNBAPTS', 'NOPTMURPHY25', 'Trey Murphy', 14.5, .46, .50, status='inactive'),  # no mid while inactive
            market('KXNBAPTS', 'NOPKMATKOVIC17', 'Karlo Matkovic', 9.5, .57, .58),
            market('KXNBAPTS', 'NOPKMATKOVIC17', 'Karlo Matkovic', 14.5, .22, .23),
            market('KXNBAPTS', 'NOPXUNKNOWN1', 'Somebody Else', 9.5, .5, .52)]
    return out


def fetcher(data, calls=None):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        series = url.split('event_ticker=')[1].split('-')[0]
        return json.dumps(dict(markets=data.get(series, []), cursor='')).encode()
    return fetch


class MatchingTests(unittest.TestCase):
    def test_suffixes_diacritics_and_namesakes_on_one_team(self):
        names = [('Trey Murphy', '1610612740'), ('Karlo Matkovic', '1610612740'), ('Stephen Curry', '1610612744'),
                 ('Jalen Williams', '1610612740'), ('J. Williams', '1610612740'), ('Williams', '1610612740'),
                 ('Nic Claxton', '1610612744'), ('Stephen Curry', '1610612740')]
        out = P.match_players(names, ROSTER)
        self.assertEqual(out[('Trey Murphy', '1610612740')], ('1630530', 'suffix'))
        self.assertEqual(out[('Karlo Matkovic', '1610612740')], ('1642300', 'exact'), 'diacritics are not a difference')
        self.assertEqual(out[('Stephen Curry', '1610612744')], ('201939', 'exact'))
        self.assertEqual(out[('Jalen Williams', '1610612740')], ('1', 'exact'), 'the full name picks one of two Williamses')
        self.assertIsNone(out[('J. Williams', '1610612740')][0], 'an initial does not choose between namesakes')
        self.assertIsNone(out[('Williams', '1610612740')][0])
        self.assertEqual(out[('Nic Claxton', '1610612744')], ('3', 'first_name_prefix'))
        self.assertIsNone(out[('Stephen Curry', '1610612740')][0], 'a name matches only within its own team')

    def test_two_namesakes_matching_one_suffix_key_and_two_names_claiming_one_player_match_nobody(self):
        roster = [dict(player_id='10', name='Gary Payton II', team_id='T'), dict(player_id='11', name='Gary Payton III', team_id='T'),
                  dict(player_id='12', name='Marcus Morris Sr.', team_id='T')]
        out = P.match_players([('Gary Payton', 'T'), ('Gary Payton II', 'T'), ('Marcus Morris', 'T'), ('Marcus Morris Sr', 'T')], roster)
        self.assertIsNone(out[('Gary Payton', 'T')][0])
        self.assertIn('2 roster players', out[('Gary Payton', 'T')][1])
        self.assertEqual(out[('Gary Payton II', 'T')], ('10', 'exact'))
        self.assertEqual(out[('Marcus Morris', 'T')], (None, 'two market names match one roster player'))
        self.assertEqual(out[('Marcus Morris Sr', 'T')], (None, 'two market names match one roster player'))


class ExtractionTests(unittest.TestCase):
    def test_ladders_mids_widths_and_the_implied_median(self):
        record = P.extract_props(GAME, dict(points=points()), ROSTER, '2026-11-10T02:00:00Z', [])
        players = {p['name']: p for p in record['players']}
        curry = players['Stephen Curry']
        self.assertEqual((curry['player_id'], curry['match'], curry['team_id']), ('201939', 'exact', '1610612744'))
        ladder = curry['stats']['points']['ladder']
        self.assertEqual(record['ladder_fields'], list(P.LADDER_FIELDS))
        self.assertEqual([r[0] for r in ladder], [19.5, 24.5, 29.5, 34.5])
        self.assertAlmostEqual(ladder[0][3], .89)
        self.assertAlmostEqual(ladder[0][4], .01)
        self.assertEqual(ladder[0][5:], [10.0, 5.0, 'active'])
        # mids .72 at 24.5 and .45 at 29.5 cross one half at 24.5 + (.22/.27) * 5
        self.assertAlmostEqual(curry['stats']['points']['implied_median'], 24.5 + .22 / .27 * 5)
        green = players['Draymond Green']['stats']['points']
        self.assertIsNone(green['ladder'][1][3], 'a 0.02/0.30 book is not a price')
        self.assertIsNone(green['implied_median'], 'no rung above one half: not bracketed, not extrapolated')
        murphy = players['Trey Murphy']
        self.assertEqual(murphy['player_id'], '1630530')
        self.assertIsNone(murphy['stats']['points']['ladder'][0][3], 'an inactive market has no mid')
        self.assertEqual(players['Karlo Matkovic']['player_id'], '1642300')
        self.assertEqual(record['unmatched'], [dict(name='Somebody Else', team='NOP', reason='no roster player of this team has this name')])


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.folder / 'roster.json').write_text(json.dumps(dict(players=ROSTER)))

    def test_cadence_append_on_quote_change_only_and_raw_archived_with_its_record(self):
        calls = []
        first = P.capture_kalshi_props(self.folder, [GAME], '2026-11-10T01:00:00Z', fetch=fetcher(dict(KXNBAPTS=points()), calls))
        self.assertEqual((first['captured'], len(calls)), ([GAME['game_id']], 4), 'one request per series')
        self.assertEqual([u['name'] for u in first['unmatched']], ['Somebody Else'])
        record = P.read_props(self.folder, GAME['game_id'])[0]
        for source in record['sources']:
            raw = gzip.decompress((self.folder / 'market' / 'kalshi-props' / source['archive']).read_bytes())
            self.assertEqual((hashlib.sha256(raw).hexdigest(), len(raw)), (source['sha256'], source['bytes']))
        self.assertEqual(P.capture_kalshi_props(self.folder, [GAME], '2026-11-10T01:10:00Z', fetch=fetcher(dict(KXNBAPTS=points())))['skipped'],
                         [GAME['game_id']], 'one capture per 15-minute slot')
        busier = P.capture_kalshi_props(self.folder, [GAME], '2026-11-10T01:16:00Z', fetch=fetcher(dict(KXNBAPTS=points(volume='99.00'))))
        self.assertEqual(busier['unchanged'], [GAME['game_id']], 'volume alone appends nothing')
        archived = len(list((self.folder / 'market' / 'kalshi-props' / 'raw').iterdir()))
        self.assertEqual(archived, 2, 'the unchanged capture archived nothing; identical empty series are stored once')
        moved = P.capture_kalshi_props(self.folder, [GAME], '2026-11-10T02:31:00Z', fetch=fetcher(dict(KXNBAPTS=points(curry=(.9, .75, .5, .2)))))
        self.assertEqual(moved['captured'], [GAME['game_id']])
        self.assertEqual(P.capture_kalshi_props(self.folder, [GAME], '2026-11-10T02:34:00Z', fetch=fetcher(dict(KXNBAPTS=points())))['skipped'],
                         [GAME['game_id']])
        self.assertEqual(P.capture_kalshi_props(self.folder, [GAME], '2026-11-10T02:35:00Z', fetch=fetcher(dict(KXNBAPTS=points())))['captured'],
                         [GAME['game_id']], 'every five minutes in the last half hour')
        after = P.capture_kalshi_props(self.folder, [GAME], '2026-11-10T03:01:00Z', fetch=fetcher(dict(KXNBAPTS=points(curry=(.1, .1, .1, .1)))))
        self.assertEqual(sum(len(after[k]) for k in ('captured', 'unchanged', 'skipped', 'errors')), 0, 'nothing after tipoff')
        records = P.read_props(self.folder, GAME['game_id'])
        self.assertEqual(len(records), 3)
        self.assertEqual(P.closing_props(records, GAME['start_time'])['captured_at'], '2026-11-10T02:35:00Z')

    def test_unlisted_deferred_throttled_and_failed_games_record_nothing_and_never_raise(self):
        unlisted = P.capture_kalshi_props(self.folder, [GAME], '2026-11-09T12:00:00Z', fetch=fetcher({}))
        self.assertEqual(unlisted['unlisted'], [GAME['game_id']])
        self.assertEqual(P.read_props(self.folder, GAME['game_id']), [])
        clock = [time.monotonic()]
        def slow(url):
            clock[0] += 100
            return fetcher(dict(KXNBAPTS=points()))(url)
        with patch('nba_forecast.kalshi_props.time.monotonic', side_effect=lambda: clock[0]):
            deferred = P.capture_kalshi_props(self.folder, [GAME], '2026-11-10T01:00:00Z', fetch=slow, deadline=clock[0] + 60)
        self.assertEqual(deferred['deferred'], [GAME['game_id']])
        self.assertEqual(P.read_props(self.folder, GAME['game_id']), [], 'nothing partial is recorded')
        other = dict(GAME, game_id='0022600211', home_tricode='NYK', away_tricode='MIL')
        def throttled(url):
            raise HTTPError(url, 429, 'Too Many Requests', {}, io.BytesIO(b''))
        limited = P.capture_kalshi_props(self.folder, [GAME, other], '2026-11-10T01:00:00Z', fetch=throttled)
        self.assertEqual((len(limited['errors']), limited['deferred'], limited.get('throttled')), (1, ['0022600211'], True),
                         'a 429 leaves the remaining games for the next run')
        broken = P.capture_kalshi_props(self.folder, [GAME], '2026-11-10T01:00:00Z', fetch=lambda url: b'not json')
        self.assertIn('not JSON', broken['errors'][0]['error'])
        self.assertEqual(P.capture_kalshi_props(self.folder, [GAME], '2026-11-10T01:00:00Z', fetch=fetcher(dict(KXNBAPTS=points())))['captured'],
                         [GAME['game_id']], 'the slot stayed open')
        state = self.folder / 'market' / 'kalshi-props' / 'capture-state.json'
        before = state.stat().st_mtime_ns
        P.capture_kalshi_props(self.folder, [GAME], '2026-11-10T01:01:00Z', fetch=fetcher({}))
        self.assertEqual(state.stat().st_mtime_ns, before, 'a call with nothing due writes nothing')

    def test_only_kalshi_player_prop_urls_are_fetched(self):
        with self.assertRaisesRegex(ValueError, 'player-prop'):
            P.fetch_event('https://example.com/markets?event_ticker=KXNBAPTS-26NOV09NOPGSW&limit=1000', fetch=lambda url: b'{}')


def selection(predictions):
    return {(GAME['game_id'], 'final'): dict(run_id='r1', predictions=predictions)}


def projected(pid, mean, sd, low, high):
    return dict(player_id=pid, status='projected', points_mean=mean, points_sd=sd, points_lower80=low, points_upper80=high)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.folder / 'roster.json').write_text(json.dumps(dict(players=ROSTER)))
        self.schedule = dict(season='2026-27', games=[dict(GAME, status='final')])

    def test_no_finished_game_says_so_plainly(self):
        report = P.props_report(self.folder, self.schedule, '2026-11-10T09:00:00Z')
        self.assertEqual(report['summary']['status'], 'no_data')
        self.assertIn('No finished game', report['summary']['reason'])
        P.capture_kalshi_props(self.folder, [GAME], '2026-11-10T02:55:00Z', fetch=fetcher(dict(KXNBAPTS=points())))
        self.assertEqual(P.props_report(self.folder, self.schedule, '2026-11-10T09:00:00Z', selected={})['summary']['status'], 'no_data',
                         'a capture without a final box score is not scored')

    def test_rungs_and_medians_are_scored_on_players_who_entered_the_game(self):
        P.capture_kalshi_props(self.folder, [GAME], '2026-11-10T02:55:00Z', fetch=fetcher(dict(KXNBAPTS=points())))
        (self.folder / 'finals').mkdir()
        finals = [dict(player_id='201939', pts=31, minutes=34.5), dict(player_id='203110', pts=8, minutes=28.0),
                  dict(player_id='1642300', pts=0, minutes=0)]  # Matkovic did not take the court
        (self.folder / 'finals' / (GAME['game_id'] + '.json')).write_text(json.dumps(finals))
        predictions = [projected('201939', 27.0, 6.0, 18.0, 37.0), projected('203110', 7.0, 3.0, 2.0, 12.0),
                       projected('1642300', 9.0, 3.0, 5.0, 13.0), dict(player_id='1630530', status='out', points_mean=0.0)]
        report = P.props_report(self.folder, self.schedule, '2026-11-10T09:00:00Z', selected=selection(predictions))
        summary = report['summary']
        self.assertEqual(summary['status'], 'scored')
        self.assertEqual(summary['excluded'], dict(unmatched_players=1, players_without_forecast=1, players_not_entered=1,
                                                   rungs_without_mid=1, medians_not_bracketed=1, games_without_forecast=0))
        self.assertEqual(summary['rungs'], 5, "Curry's four rungs and Green's one quoted rung")
        curry = [r for r in report['rungs'] if r['player_id'] == '201939']
        self.assertEqual([r['outcome'] for r in curry], [True, True, True, False])
        self.assertAlmostEqual(curry[2]['market_brier'], (1 - .45) ** 2)
        # the 80% interval 18-37 around 27 puts 37 at the 90th percentile: P(more than 37) = 0.1
        self.assertAlmostEqual(P.model_probability(predictions[0], 37.0), .1)
        self.assertAlmostEqual(P.model_probability(predictions[0], 18.0), .9)
        self.assertAlmostEqual(curry[2]['model'], 1 - P._cdf(2.5 / (10 / P.Z80)))
        self.assertAlmostEqual(curry[2]['model_sd'], 1 - P._cdf(2.5 / 6))
        self.assertAlmostEqual(curry[2]['model_log_loss'], -math.log(curry[2]['model']))
        brier = summary['brier']['interval']
        self.assertEqual((brier['n'], brier['dates']), (5, 1))
        self.assertIsNone(brier['difference_se'], 'one date: no clustered error')
        self.assertAlmostEqual(brier['difference'], brier['model_brier'] - brier['market_brier'])
        median = report['medians'][0]
        self.assertEqual((median['player_id'], median['actual_points']), ('201939', 31))
        self.assertAlmostEqual(median['market_abs_error'], abs(24.5 + .22 / .27 * 5 - 31))
        self.assertAlmostEqual(median['model_abs_error'], 4.0)
        self.assertEqual(report['games'][0]['close_captured_at'], '2026-11-10T02:55:00Z')

    def test_a_capture_after_tipoff_is_not_the_close_and_the_error_is_clustered_by_date(self):
        P.record_props(self.folder, P.extract_props(GAME, dict(points=points()), ROSTER, '2026-11-10T02:55:00Z', []))
        P.record_props(self.folder, P.extract_props(GAME, dict(points=points(curry=(.99, .99, .99, .99))), ROSTER, '2026-11-10T03:20:00Z', []))
        self.assertEqual(P.closing_props(P.read_props(self.folder, GAME['game_id']), GAME['start_time'])['captured_at'], '2026-11-10T02:55:00Z')
        rows = [dict(date='d1', a=1., b=0.), dict(date='d1', a=3., b=0.), dict(date='d2', a=2., b=1.), dict(date='d3', a=0., b=0.)]
        result = P.paired(rows, 'a', 'b')
        diffs = [1., 3., 1., 0.]
        mean = sum(diffs) / 4
        clusters = [(1 - mean) + (3 - mean), 1 - mean, 0 - mean]
        self.assertAlmostEqual(result['difference'], mean)
        self.assertAlmostEqual(result['difference_se'], math.sqrt(3 / 2 * sum(c * c for c in clusters)) / 4)


if __name__ == '__main__':
    unittest.main()
