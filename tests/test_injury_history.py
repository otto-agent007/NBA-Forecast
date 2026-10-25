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

    def test_a_network_failure_leaves_the_game_for_the_next_run(self):
        def fetch(url, cache_dir, timeout):
            raise ValueError(f'NBA source {url} unavailable after 3 bounded attempts (TimeoutError)')

        with tempfile.TemporaryDirectory() as d:
            m = injury_history.fetch_reports([dict(game_id='g1', game_time='2025-01-16T00:00:00Z')], d, fetch=fetch,
                                             sleep=lambda s: None)
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
