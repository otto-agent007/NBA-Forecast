import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nba_forecast import backfill

FIXTURE = Path(__file__).parent.parent / 'examples/verified/boxscore_0022600001.json'


def index(game='0022600001', season='22026'):
    return {'resultSets': [{'name': 'LeagueGameLog', 'headers': ['SEASON_ID','TEAM_ID','GAME_ID','WL'],
                           'rowSet': [[season, 1, game, 'W'], [season, 2, game, 'L']]}]}


class BackfillTests(unittest.TestCase):
    def test_index_deduplicates_team_rows_and_rejects_wrong_season(self):
        self.assertEqual(backfill.game_ids(index(), '2026-27'), ['0022600001'])
        with self.assertRaises(ValueError):
            backfill.game_ids(index(), '2025-26')
        broken=index(); broken['resultSets'][0]['rowSet'].pop()
        with self.assertRaises(ValueError): backfill.game_ids(broken,'2026-27')

    def test_unfinished_index_rows_are_not_backfilled(self):
        payload=index()
        for r in payload['resultSets'][0]['rowSet']: r[-1]=None
        self.assertEqual(backfill.game_ids(payload,'2026-27'),[])

    def test_resume_keeps_known_at_and_avoids_redownload(self):
        payload=json.loads(FIXTURE.read_text())
        with tempfile.TemporaryDirectory() as folder, patch('nba_forecast.backfill.fetch_json', return_value=payload) as fetch:
            first=backfill.backfill_games(['0022600001'], folder, workers=1)
            self.assertEqual(first['completed_games'],1)
            original=(Path(folder)/'games/0022600001.json').read_bytes()
            second=backfill.backfill_games(['0022600001'], folder, workers=1)
            self.assertEqual(second['resumed_games'],1)
            self.assertEqual(fetch.call_count,1)
            self.assertEqual((Path(folder)/'games/0022600001.json').read_bytes(), original)

    def test_failure_checkpoint_no_partial_success_and_retry(self):
        with tempfile.TemporaryDirectory() as folder, patch('nba_forecast.backfill.fetch_json',side_effect=ValueError('outage')):
            result=backfill.backfill_games(['0022600001'],folder,workers=1)
            self.assertEqual(result['status'],'incomplete')
            self.assertIn('0022600001',result['failures'])
            self.assertTrue((Path(folder)/'manifest.json').exists())

    def test_corrupt_cache_not_silently_used(self):
        with tempfile.TemporaryDirectory() as folder:
            games=Path(folder)/'games'; games.mkdir()
            (games/'0022600001.json').write_text('[]')
            with patch('nba_forecast.backfill.fetch_json') as fetch:
                result=backfill.backfill_games(['0022600001'],folder,workers=1)
                self.assertEqual(result['status'],'incomplete')
                fetch.assert_not_called()

    def test_repeated_resume_never_forgives_corruption(self):
        payload=json.loads(FIXTURE.read_text())
        with tempfile.TemporaryDirectory() as folder, patch('nba_forecast.backfill.fetch_json',return_value=payload):
            backfill.backfill_games(['0022600001'],folder,workers=1)
            path=Path(folder)/'games/0022600001.json'
            rows=json.loads(path.read_text());rows[0]['name']='ALTERED';path.write_text(json.dumps(rows))
            for _ in range(2):
                result=backfill.backfill_games(['0022600001'],folder,workers=1)
                self.assertEqual(result['status'],'incomplete')
                self.assertIn('hash mismatch',result['failures']['0022600001'])

    def test_preseason_ids_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            backfill.backfill_games(['0012600001'],folder)

    def test_concurrency_is_bounded_at_eight(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(backfill.backfill_games([],folder,workers=8)['status'],'complete')
            with self.assertRaises(ValueError):backfill.backfill_games([],folder,workers=9)
