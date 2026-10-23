import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from nba_forecast.data import fetch_json, SCHEDULE_URL


class FetchDiagnosticsTests(unittest.TestCase):
    def test_http_failures_leave_timestamped_attempt_evidence(self):
        with tempfile.TemporaryDirectory() as directory, patch('urllib.request.OpenerDirector.open', side_effect=HTTPError(SCHEDULE_URL, 503, 'unavailable', {}, None)), patch('time.sleep'):
            with self.assertRaises(ValueError):
                fetch_json(SCHEDULE_URL, cache_dir=directory)
            path = Path(directory) / 'fetch-failures.jsonl'
            self.assertTrue(path.exists(), 'failed requests must leave diagnostic evidence')
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([row['attempt'] for row in rows], [1, 2, 3])
            self.assertTrue(all(row['status'] == 503 and row['source_url'] == SCHEDULE_URL and row['observed_at'] for row in rows))

    def test_network_error_identifies_endpoint_and_exception_class(self):
        with tempfile.TemporaryDirectory() as directory, patch('urllib.request.OpenerDirector.open', side_effect=TimeoutError('timed out')), patch('time.sleep'):
            with self.assertRaises(ValueError) as caught:
                fetch_json(SCHEDULE_URL, cache_dir=directory)
            self.assertIn(SCHEDULE_URL, str(caught.exception))
            self.assertIn('TimeoutError', str(caught.exception))

    def test_invalid_json_leaves_parse_evidence_and_raw_archive(self):
        with tempfile.TemporaryDirectory() as directory, patch('urllib.request.OpenerDirector.open') as request:
            request.return_value.__enter__.return_value.read.return_value = b'<html>unavailable</html>'
            with self.assertRaises(ValueError):
                fetch_json(SCHEDULE_URL, cache_dir=directory)
            path = Path(directory) / 'fetch-failures.jsonl'
            self.assertTrue(path.exists(), 'parse failures must leave diagnostic evidence')
            self.assertEqual(json.loads(path.read_text())['category'], 'invalid_json')
            self.assertEqual(len(list(Path(directory).glob('*.provenance.json'))), 1)
