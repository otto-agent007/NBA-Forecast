import unittest
from unittest import mock

from nba_forecast import injuries
from nba_forecast.data import _allowed_url
from nba_forecast.injuries import INDEX_URL, fetch_injury_check, report_links

PDF = 'https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-11-20_{}.pdf'
# The shape of www.nba.com/players/injury-report on 2026-11-20: anchors plus a JSON copy of each link.
PAGE = ''.join(f'<a href="{PDF.format(t)}" target="_blank">Report</a>{{"url":"{PDF.format(t)}","label":"x"}}'
               for t in ('06_00PM', '06_15PM', '06_30PM'))


class InjuryIndexTests(unittest.TestCase):
    def test_index_is_the_page_the_old_one_redirects_to(self):
        # official.nba.com/nba-injury-report-2026-27-season/ answers 301 to this page since 2026-11-20.
        self.assertEqual(INDEX_URL, 'https://www.nba.com/players/injury-report')
        _allowed_url(INDEX_URL)
        with self.assertRaises(ValueError):
            _allowed_url('https://official.nba.com/nba-injury-report-2026-27-season/')
        with self.assertRaises(ValueError):
            _allowed_url(INDEX_URL + '?team=all')

    def test_new_page_links_parse_once_each(self):
        links = report_links(PAGE, '2026-11-20T23:40:00Z')
        self.assertEqual([l['published_at'] for l in links],
                         ['2026-11-20T23:00:00+00:00', '2026-11-20T23:15:00+00:00', '2026-11-20T23:30:00+00:00'])

    def test_check_fetches_the_new_index_and_its_pdfs(self):
        fetched = []

        def fake_fetch(url, cache_dir):
            fetched.append(url)
            return (PAGE.encode() if url == INDEX_URL else b'%PDF-1.7 x'), '2026-11-20T23:40:00+00:00'

        game = {'game_id': '0022600250', 'start_time': '2026-11-21T00:00:00Z'}
        with mock.patch.object(injuries, '_fetch', fake_fetch):
            check = fetch_injury_check({'season': '2026-27', 'games': [game], 'as_of': '2026-11-20T23:40:00Z'}, 'unused')
        self.assertEqual(fetched[0], INDEX_URL)
        self.assertEqual(check['index_url'], INDEX_URL)
        self.assertEqual(check['games'][0]['anchor_report']['source_url'], PDF.format('06_30PM'))


if __name__ == '__main__':
    unittest.main()
