import json
import gzip
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

    def test_compressed_history_is_usable(self):
        from nba_forecast.__main__ import read_json
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'history.json.gz'
            path.write_bytes(gzip.compress(b'[1,2,3]'))
            self.assertEqual(read_json(path),[1,2,3])

    def test_final_stage_requires_injury_checkpoint(self):
        result=self.run_cli('forecast','--snapshot',ROOT/'examples/verified/snapshot.json',
                            '--db','unused.sqlite','--mode','replay','--stage','final',success=False)
        self.assertIn('injury',result.stderr)

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

    def test_backfill_cli_returns_nonzero_for_incomplete_data(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        from nba_forecast.__main__ import main
        result={'status':'incomplete','games':{},'failures':{'0022600001':'outage'}}
        with patch('nba_forecast.backfill.backfill_seasons',return_value=result), redirect_stdout(StringIO()) as stream:
            code=main(['backfill','--seasons','2026-27','--directory','unused'])
        self.assertEqual(code,2)
        self.assertEqual(json.loads(stream.getvalue())['status'],'incomplete')

    def test_final_window_cli_restricts_schedule(self):
        from contextlib import redirect_stdout
        from datetime import datetime,timezone,timedelta
        from io import StringIO
        from unittest.mock import patch
        from nba_forecast.__main__ import main
        with patch('nba_forecast.data.fetch_json',return_value={'leagueSchedule':{'seasonYear':'2026-27'}}), \
                patch('nba_forecast.data.fetch_roster',return_value={}), \
                patch('nba_forecast.data.build_snapshot',return_value={}) as build, redirect_stdout(StringIO()), tempfile.TemporaryDirectory() as folder:
            self.assertEqual(main(['fetch-slate','--minutes-ahead','30','--output',str(Path(folder)/'snapshot.json')]),0)
            args=build.call_args.args
            self.assertEqual(datetime.fromisoformat(args[3])-datetime.fromisoformat(args[2]),timedelta(minutes=30))

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

    def test_profiles_and_benchmark_commands_work_offline(self):
        profiles=json.loads(self.run_cli('profiles','--snapshot',ROOT/'examples/verified/snapshot.json').stdout)
        self.assertEqual(len(profiles['players']),519)
        with tempfile.TemporaryDirectory() as d:
            result=json.loads(self.run_cli('benchmarks','--db',Path(d)/'empty.sqlite').stdout)
            self.assertEqual(result['groups'],[])
