import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def run_cli(self, *args, success=True):
        result = subprocess.run([sys.executable, '-m', 'nba_forecast', *map(str, args)],
                                cwd=ROOT, text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_offline_forecast_then_evaluation(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            made = subprocess.run([sys.executable, 'examples/make_demo.py', '--output', folder],
                                  cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(made.returncode, 0, made.stderr)
            db = directory / 'ledger.sqlite'
            result = self.run_cli('forecast', '--snapshot', directory / 'snapshot.json',
                                  '--history', directory / 'history.json', '--mode', 'demo',
                                  '--scope', 'subset', '--db', db)
            run = json.loads(result.stdout)
            self.assertEqual(len(run['forecast']['predictions']), 20)
            report = json.loads(self.run_cli('evaluate', '--db', db, '--run-id', run['run_id'],
                                            '--actuals', directory / 'actuals.json').stdout)
            self.assertEqual(report['mode'], 'demo')
            self.assertFalse(report['live_eligible'])
            self.assertGreater(report['sample_counts']['evaluated'], 0)
            self.assertGreaterEqual(report['metrics']['points_mae'], 0)
            aggregate = json.loads(self.run_cli('report', '--db', db).stdout)
            self.assertEqual(aggregate['groups'], [])

    def test_unverified_archive_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source = ROOT / 'examples/verified/boxscore_0022500001.json'
            meta = Path(folder) / 'metadata.json'
            meta.write_text(json.dumps({'sha256': '0'*64, 'source_url': 'https://www.nba.com',
                                        'retrieved_at': '2026-10-21T17:00:00Z'}))
            result = self.run_cli('normalize-boxscore', '--input', source, '--provenance', meta,
                                  '--output', Path(folder) / 'rows.json', success=False)
            self.assertIn('SHA256', result.stderr)

    def test_no_command_prints_usage(self):
        self.assertIn('usage:', self.run_cli(success=False).stderr)

    def test_real_full_pool_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            history = directory / 'history.json'
            self.run_cli('normalize-boxscore', '--input', ROOT / 'examples/verified/boxscore_0022600001.json',
                         '--provenance', ROOT / 'examples/verified/boxscore_0022600001.json.provenance.json',
                         '--output', history)
            result = json.loads(self.run_cli('forecast', '--snapshot', ROOT / 'examples/verified/snapshot.json',
                                             '--history', history, '--mode', 'replay',
                                             '--db', directory / 'ledger.sqlite').stdout)
            predictions = result['forecast']['predictions']
            self.assertEqual(len(predictions), 519)
            self.assertEqual(len({p['player_id'] for p in predictions}), 519)
            self.assertTrue(all(p['points_mean'] is None for p in predictions))


if __name__ == '__main__':
    unittest.main()
