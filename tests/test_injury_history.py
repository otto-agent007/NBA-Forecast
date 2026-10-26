"""Historical injury reports: each game's T-minus-30 anchor in the naming of its date."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from nba_forecast import injury_history
from nba_forecast.data import _allowed_url
from nba_forecast.injuries import report_time

BASE = 'https://ak-static.cms.nba.com/referee/injury/Injury-Report_'


class ReportNamingTests(unittest.TestCase):
    def test_hourly_names_were_published_at_half_past_until_december_22_2025(self):
        self.assertEqual(report_time(BASE + '2025-01-15_07PM.pdf').isoformat(), '2025-01-16T00:30:00+00:00')
        self.assertEqual(report_time(BASE + '2025-12-21_12PM.pdf').isoformat(), '2025-12-21T17:30:00+00:00')
        self.assertIsNone(report_time(BASE + '2025-12-22_05PM.pdf'))  # the hourly naming had ended
        self.assertEqual(report_time(BASE + '2026-01-15_06_30PM.pdf').isoformat(), '2026-01-15T23:30:00+00:00')
        self.assertIsNone(report_time('https://example.test/Injury-Report_2025-01-15_07PM.pdf'))
        for url in (BASE + '2025-01-15_07PM.pdf', BASE + '2026-01-15_06_30PM.pdf'):
            _allowed_url(url)

    def test_the_anchor_is_the_newest_report_by_the_checkpoint_then_earlier_slots(self):
        # 7:00 PM ET tip: checkpoint 6:30, the hourly file published at 6:30, then 5:30 and 4:30.
        self.assertEqual(injury_history.candidates('2025-01-16T00:00:00Z'),
                         [BASE + '2025-01-15_06PM.pdf', BASE + '2025-01-15_05PM.pdf', BASE + '2025-01-15_04PM.pdf',
                          BASE + '2025-01-15_03PM.pdf'])
        # 7:30 PM tip, 7:00 checkpoint: still the 6:30 file.
        self.assertEqual(injury_history.candidates('2025-01-16T00:30:00Z')[0], BASE + '2025-01-15_06PM.pdf')
        # Quarter-hour reports from 2025-12-22: the checkpoint's own slot first, 13 within three hours.
        later = injury_history.candidates('2026-01-16T00:00:00Z')
        self.assertEqual(later[:2], [BASE + '2026-01-15_06_30PM.pdf', BASE + '2026-01-15_06_15PM.pdf'])
        self.assertEqual(len(later), 13)


    def test_a_midday_anchor_reads_the_game_day_report_unless_the_game_is_earlier(self):
        # 7:00 PM ET tip, 12:30 anchor: the hourly file published at 12:30 (named 12PM).
        self.assertEqual(injury_history.candidates('2025-01-16T00:00:00Z', '12:30')[0], BASE + '2025-01-15_12PM.pdf')
        # A 1:00 PM tip keeps its T-30 checkpoint, 12:30.
        self.assertEqual(injury_history.checkpoint_at('2026-01-15T18:00:00Z', '12:30').isoformat(), '2026-01-15T17:30:00+00:00')
        self.assertEqual(injury_history.checkpoint_at('2026-01-16T00:00:00Z', '12:30').isoformat(), '2026-01-15T17:30:00+00:00')
        self.assertEqual(injury_history.checkpoint_at('2026-01-16T00:00:00Z').isoformat(), '2026-01-15T23:30:00+00:00')

    def test_a_night_before_anchor_reads_the_previous_evening_report_in_that_dates_naming(self):
        # 7:00 PM ET tip on Jan 15, 2025: the hourly file of Jan 14 published at 8:30 PM (named 08PM).
        self.assertEqual(injury_history.checkpoint_at('2025-01-16T00:00:00Z', 'D-1 20:30').isoformat(),
                         '2025-01-15T01:30:00+00:00')
        self.assertEqual(injury_history.candidates('2025-01-16T00:00:00Z', 'D-1 20:30')[0], BASE + '2025-01-14_08PM.pdf')
        # A Dec 22, 2025 game reads Dec 21, the last hourly day; Dec 23 reads a quarter-hour file.
        self.assertEqual(injury_history.candidates('2025-12-23T00:00:00Z', 'D-1 20:30')[0], BASE + '2025-12-21_08PM.pdf')
        self.assertEqual(injury_history.candidates('2025-12-24T00:00:00Z', 'D-1 20:30')[0], BASE + '2025-12-22_08_30PM.pdf')
        # Across the fall-back change the evening stays 8:30 PM Eastern (EST, UTC-5).
        self.assertEqual(injury_history.checkpoint_at('2025-11-02T23:00:00Z', 'D-1 20:30').isoformat(),
                         '2025-11-02T00:30:00+00:00')
        with self.assertRaisesRegex(ValueError, 'not HH:MM or D-n HH:MM'):
            injury_history.checkpoint_at('2025-01-16T00:00:00Z', 'yesterday 20:30')

    def test_every_game_resolves_against_the_run_anchor_and_a_failed_slot_is_tried_once(self):
        calls = []

        def fetch(url, cache_dir, timeout):
            calls.append(url)
            if url.endswith('2025-01-15_12PM.pdf'):
                raise ValueError(f'NBA source {url} unavailable after 3 bounded attempts (TimeoutError)')
            return b'%PDF-1.4 ' + url.encode(), '2026-10-25T00:00:00+00:00'

        records = [dict(game_id='g1', game_time='2025-01-16T00:00:00Z'), dict(game_id='g2', game_time='2025-01-16T03:00:00Z'),
                   dict(game_id='g3', game_time='2025-01-17T00:00:00Z')]
        with tempfile.TemporaryDirectory() as d:
            m = injury_history.fetch_reports(records, d, fetch=fetch, sleep=lambda s: None, anchor='12:30')
        self.assertEqual(m['anchors'], dict(g3=BASE + '2025-01-16_12PM.pdf'))  # the midday file, not T-30
        self.assertEqual(set(m['failures']), {'g1', 'g2'})
        self.assertEqual(calls.count(BASE + '2025-01-15_12PM.pdf'), 1)

    def test_a_manifest_keeps_one_anchor_policy(self):
        with tempfile.TemporaryDirectory() as d:
            injury_history.fetch_reports([], d, anchor='12:30')
            with self.assertRaisesRegex(ValueError, 'anchors for 12:30'):
                injury_history.fetch_reports([], d)


class FetchTests(unittest.TestCase):
    def test_an_absent_slot_is_retried_once_then_the_previous_slot_is_used(self):
        calls = []

        def fetch(url, cache_dir, timeout):
            calls.append(url)
            if url.endswith('06PM.pdf'):
                raise ValueError(f'NBA source {url} returned HTTP 403; cached files can be used offline')
            return b'%PDF-1.4 ' + url.encode(), '2026-10-25T00:00:00+00:00'

        records = [dict(game_id='g1', game_time='2025-01-16T00:00:00Z'), dict(game_id='g2', game_time='2025-01-16T00:30:00Z')]
        with tempfile.TemporaryDirectory() as d:
            m = injury_history.fetch_reports(records, d, fetch=fetch, sleep=lambda s: None)
            self.assertEqual(m['anchors'], dict(g1=BASE + '2025-01-15_05PM.pdf', g2=BASE + '2025-01-15_05PM.pdf'))
            self.assertEqual(m['slots'][BASE + '2025-01-15_06PM.pdf']['status'], 'absent')
            self.assertEqual(calls, [BASE + '2025-01-15_06PM.pdf'] * 2 + [BASE + '2025-01-15_05PM.pdf'])
            # Resuming fetches nothing again.
            again = injury_history.fetch_reports(records, d, fetch=fetch, sleep=lambda s: None)
            self.assertEqual(len(calls), 3)
            self.assertEqual(json.loads((Path(d) / 'manifest.json').read_text())['anchors'], again['anchors'])

    def test_parallel_workers_fetch_first_choices_then_fall_back_in_order(self):
        calls = []

        def fetch(url, cache_dir, timeout):
            calls.append(url)
            if url.endswith('2025-01-15_06PM.pdf'):
                raise ValueError(f'NBA source {url} returned HTTP 404; cached files can be used offline')
            return b'%PDF-1.4 ' + url.encode(), '2026-10-25T00:00:00+00:00'

        records = [dict(game_id='g1', game_time='2025-01-16T00:00:00Z'), dict(game_id='g2', game_time='2025-01-16T03:00:00Z')]
        with tempfile.TemporaryDirectory() as d:
            m = injury_history.fetch_reports(records, d, fetch=fetch, sleep=lambda s: None, workers=3)
        self.assertEqual(m['anchors'], dict(g1=BASE + '2025-01-15_05PM.pdf', g2=BASE + '2025-01-15_09PM.pdf'))
        self.assertEqual(sorted(calls[:3]), sorted([BASE + '2025-01-15_06PM.pdf'] * 2 + [BASE + '2025-01-15_09PM.pdf']))
        self.assertEqual(calls[3:], [BASE + '2025-01-15_05PM.pdf'])

    def test_an_archived_report_missing_from_the_manifest_is_recovered_not_refetched(self):
        from nba_forecast.data import archive_payload
        url = BASE + '2025-01-15_06PM.pdf'
        with tempfile.TemporaryDirectory() as d:
            meta = archive_payload(b'%PDF-1.4 archived', url, '2026-10-25T00:00:00+00:00', Path(d) / 'raw')

            def fetch(url, cache_dir, timeout):
                raise AssertionError('refetched ' + url)

            m = injury_history.fetch_reports([dict(game_id='g1', game_time='2025-01-16T00:00:00Z')], d, fetch=fetch,
                                             sleep=lambda s: None, workers=2)
        self.assertEqual(m['anchors'], dict(g1=url))
        self.assertEqual(m['slots'][url], dict(status='ok', sha256=meta['sha256'], retrieved_at='2026-10-25T00:00:00+00:00'))

    def test_a_network_failure_leaves_the_game_for_the_next_run(self):
        import http.client
        for error in (ValueError('NBA source unavailable after 3 bounded attempts (TimeoutError)'),
                      http.client.RemoteDisconnected('Remote end closed connection without response')):
            def fetch(url, cache_dir, timeout):
                raise error

            with tempfile.TemporaryDirectory() as d:
                m = injury_history.fetch_reports([dict(game_id='g1', game_time='2025-01-16T00:00:00Z')], d,
                                                 fetch=fetch, sleep=lambda s: None, workers=2)
            self.assertEqual(m['anchors'], {})
            self.assertIn('g1', m['failures'])


class ExtractTests(unittest.TestCase):
    TEXT = '''                                         Injury Report: 01/15/25 06:30 PM
Game Date    Game Time    Matchup   Team                 Player Name          Current Status   Reason
01/15/2025   07:00 (ET)   NYK@PHI   New York Knicks      Brunson, Jalen       Questionable     Injury/Illness - Ankle
                                                         Towns, Karl-Anthony  Out              Injury/Illness - Thumb
                                    Philadelphia 76ers   Embiid, Joel         Out              Injury/Illness - Foot
                                                                                                       Page 1 of 1
'''

    def test_listed_players_keep_their_status_and_complete_teams_are_recorded(self):
        teams = {'1610612752': ('New York Knicks', 'NYK'), '1610612755': ('Philadelphia 76ers', 'PHI')}
        records = [dict(game_id='g1', game_time='2025-01-16T00:00:00Z', home_team_id='1610612755',
                        away_team_id='1610612752')]
        history = [dict(player_id='p1', name='Jalen Brunson', team_id='1610612752'),
                   dict(player_id='p2', name='Karl-Anthony Towns', team_id='1610612752'),
                   dict(player_id='p3', name='Joel Embiid', team_id='1610612755'),
                   dict(player_id='p4', name='Tyrese Maxey', team_id='1610612755')]
        url = BASE + '2025-01-15_06PM.pdf'
        manifest = dict(anchors=dict(g1=url), slots={url: dict(status='ok', sha256='a' * 64,
                                                                retrieved_at='2026-10-25T00:00:00+00:00')})
        with mock.patch.object(injury_history, 'pdf_text', return_value=self.TEXT):
            games = injury_history.extract(records, history, manifest, '/nonexistent', teams)
        g = games['g1']
        self.assertEqual(g['statuses'], dict(p1=['questionable', 'Questionable', '1610612752'],
                                             p2=['out', 'Out', '1610612752'], p3=['out', 'Out', '1610612755']))
        self.assertEqual(g['complete_team_ids'], ['1610612752', '1610612755'])
        self.assertEqual((g['age_minutes'], g['report']['published_at']), (0., '2025-01-15T23:30:00+00:00'))
        s = injury_history.summary(games)
        self.assertEqual((s['listed_out'], s['listed_questionable'], s['teams_complete']), (2, 1, 2))


if __name__ == '__main__':
    unittest.main()
