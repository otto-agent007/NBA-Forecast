import json
import gzip
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_blocked_live_preflight_does_not_execute_model_or_create_ledger(self):
        from unittest.mock import patch
        from nba_forecast.__main__ import main
        from tests.test_store import inputs
        s,_,_=inputs()
        with tempfile.TemporaryDirectory() as d:
            snapshot=Path(d)/'snapshot.json'
            snapshot.write_text(json.dumps(s))
            db=Path(d)/'ledger.sqlite'
            with patch('nba_forecast.preflight.run_preflight',return_value={'status':'blocked','issues':[{'detail':'Missing coverage'}]}),patch('nba_forecast.model.forecast') as model:
                code=main(['forecast','--snapshot',str(snapshot),'--db',str(db),'--mode','live'])
            self.assertNotEqual(code,0)
            model.assert_not_called()
            self.assertFalse(db.exists())
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

    def test_derived_command_waits_for_a_calendar_and_writes_its_report(self):
        with tempfile.TemporaryDirectory() as d:
            result = self.run_cli('derived', '--directory', d)
            self.assertEqual(json.loads(result.stdout)['status'], 'waiting')
            self.assertEqual(json.loads((Path(d) / 'last-derived.json').read_text())['status'], 'waiting')

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

    def tick_report(self):
        return {'started_at':'2026-11-04T09:07:00+00:00','finished_at':'2026-11-04T09:13:03+00:00',
                'status':'partial','created_runs':0,'evaluated_runs':2658,'recovered_runs':0,
                'missed_final_games':['0022600157'],'errors':[],
                'warnings':[{'game_id':'0022600142','task':'shot_data','error':'Still inconsistent'}],
                'timings':{'evaluate_runs':354.2},'waiting_jobs':[{'game_id':'0022600173'}],'deferred_jobs':[],
                'player_sync':{'player_summaries':{str(i):{'name':'x'*40} for i in range(500)}}}

    def test_tick_prints_a_one_line_summary_for_the_journal(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        from nba_forecast.__main__ import main
        with patch('nba_forecast.operations.run_tick',return_value=self.tick_report()), redirect_stdout(StringIO()) as stream:
            code=main(['tick','--directory','unused'])
        self.assertEqual(code,2)
        lines=stream.getvalue().splitlines()
        self.assertEqual(len(lines),1)
        line=json.loads(lines[0])
        self.assertEqual((line['status'],line['evaluated_runs'],line['missed_final_games'],line['waiting_jobs']),
                         ('partial',2658,['0022600157'],1))
        self.assertEqual(line['warnings'][0]['game_id'],'0022600142')
        self.assertNotIn('player_sync',line)

    def test_tick_full_report_flag_and_output_file_keep_the_whole_report(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        from nba_forecast.__main__ import main
        report=self.tick_report()
        with patch('nba_forecast.operations.run_tick',return_value=report), redirect_stdout(StringIO()) as stream:
            main(['tick','--directory','unused','--full-report'])
        self.assertEqual(json.loads(stream.getvalue()),report)
        with tempfile.TemporaryDirectory() as folder, patch('nba_forecast.operations.run_tick',return_value=report), \
                redirect_stdout(StringIO()) as stream:
            output=Path(folder)/'tick.json'
            main(['tick','--directory','unused','--output',str(output)])
            self.assertEqual(json.loads(output.read_text()),report)
        self.assertEqual(stream.getvalue(),'')

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


# Run in a fresh interpreter: SQLite reads SQLITE_TMPDIR once, when the library loads.
# Forces a sort to spill (25 MB of rows, a 200 KB page cache), stops part-way through
# its rows and prints the directories of SQLite's open temporary files, which SQLite
# unlinks as it creates them, so only /proc/self/fd shows them.
SPILL = """
import json, os, sys
from nba_forecast.__main__ import _ledger_home, _parser, sqlite_tmpdir
chosen = sqlite_tmpdir(_ledger_home(_parser().parse_args(sys.argv[1:])))
import sqlite3
conn = sqlite3.connect(':memory:')
conn.execute('PRAGMA temp_store = FILE')
conn.execute('PRAGMA cache_size = -200')
conn.execute('CREATE TABLE t (x TEXT)')
conn.executemany('INSERT INTO t VALUES (?)', ((os.urandom(8).hex() * 60,) for _ in range(25000)))
rows = conn.execute('SELECT x FROM t ORDER BY x')
rows.fetchone()
folders = set()
for fd in os.listdir('/proc/self/fd'):
    try:
        link = os.readlink('/proc/self/fd/' + fd)
    except OSError:
        continue
    if os.path.basename(link).startswith('etilqs_'):
        folders.add(os.path.dirname(link))
print(json.dumps(dict(chosen=chosen, environment=os.environ.get('SQLITE_TMPDIR'), folders=sorted(folders))))
"""


class SqliteTemporaryFileTests(unittest.TestCase):
    """SQLite's temporary files go beside the ledger, not to /var/tmp on the small root disk."""

    def spill(self, *args, environment=None):
        env = {k: v for k, v in __import__('os').environ.items() if k not in ('SQLITE_TMPDIR', 'TMPDIR')}
        env.update(environment or {})
        result = subprocess.run([sys.executable, '-c', SPILL, *map(str, args)], cwd=ROOT, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    @unittest.skipUnless(Path('/proc/self/fd').is_dir(), 'reads /proc/self/fd')
    def test_a_spilled_sort_writes_its_temporary_files_beside_the_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = str(Path(d).resolve() / 'tmp')
            for args in (('derived', '--directory', d), ('report', '--db', Path(d) / 'forecast.sqlite')):
                with self.subTest(command=args[0]):
                    self.assertEqual(self.spill(*args), dict(chosen=tmp, environment=tmp, folders=[tmp]))
            with tempfile.TemporaryDirectory() as other:
                kept = self.spill('derived', '--directory', d, environment=dict(SQLITE_TMPDIR=other))
                self.assertEqual(kept, dict(chosen=None, environment=other, folders=[other]), 'a value already set is kept')
                elsewhere = self.spill('backfill', '--seasons', '2026-27', '--directory', d, environment=dict(TMPDIR=other))
                self.assertEqual(elsewhere, dict(chosen=None, environment=None, folders=[other]), "a backfill's directory holds no ledger")

    def test_the_commands_set_it_before_they_start(self):
        import os
        env = {k: v for k, v in os.environ.items() if k not in ('SQLITE_TMPDIR', 'TMPDIR')}
        with tempfile.TemporaryDirectory() as d:
            result = subprocess.run([sys.executable, '-m', 'nba_forecast', 'derived', '--directory', d],
                                    cwd=ROOT, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(d) / 'tmp').is_dir())

    def test_importing_the_command_line_does_not_load_sqlite(self):
        result = subprocess.run([sys.executable, '-c', "import sys, nba_forecast.__main__; print('_sqlite3' in sys.modules)"],
                                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.stdout.strip(), 'False', result.stderr)

    def test_each_command_that_opens_a_ledger_names_its_folder(self):
        from nba_forecast.__main__ import _ledger_home, _parser
        def home(*args):
            return _ledger_home(_parser().parse_args(list(args)))
        for command in ('tick', 'derived', 'dashboard', 'postgame', 'sync-players', 'compact-ticks', 'dedupe-histories', 'daily-review'):
            self.assertEqual(home(command, '--directory', 'ops'), Path('ops'), command)
        self.assertEqual(home('recover-evidence', '--directory', 'ops'), Path('ops'))
        for command in ('report', 'benchmarks'):
            self.assertEqual(home(command, '--db', 'ops/forecast.sqlite'), Path('ops'), command)
        self.assertEqual(home('evaluate', '--db', 'ops/forecast.sqlite', '--run-id', 'r', '--actuals', 'a.json'), Path('ops'))
        self.assertIsNone(home('backfill', '--seasons', '2026-27', '--directory', 'archive'))
        self.assertIsNone(home('fetch-schedule', '--directory', 'schedule'))
        self.assertIsNone(home('validate', '--snapshot', 's.json'))

    def test_nothing_changes_once_sqlite_is_loaded(self):
        import os
        import sqlite3  # noqa: F401  (loaded, as in every test process)
        from nba_forecast.__main__ import sqlite_tmpdir
        before = os.environ.get('SQLITE_TMPDIR')
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sqlite_tmpdir(d))
            self.assertFalse((Path(d) / 'tmp').exists())
        self.assertEqual(os.environ.get('SQLITE_TMPDIR'), before)


class CompactTicksCommandTests(unittest.TestCase):
    def test_compact_ticks_rewrites_the_log_and_reports_the_sizes(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from nba_forecast.__main__ import main
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'ticks.jsonl').write_text(json.dumps({'status': 'complete', 'player_sync': {'errors': [], 'player_summaries': {str(i): {'name': 'x' * 40} for i in range(500)}}}) + '\n')
            with redirect_stdout(StringIO()) as stream:
                code = main(['compact-ticks', '--directory', d])
            self.assertEqual(code, 0)
            result = json.loads(stream.getvalue())
            self.assertEqual(result['rows'], 1)
            self.assertLess(result['bytes_after'], result['bytes_before'] / 10)
            row = json.loads((Path(d) / 'ticks.jsonl').read_text())
            self.assertEqual(row['player_sync'], {'errors': [], 'detail': 'players/index.json'})

if __name__ == '__main__':
    unittest.main()

    def test_profiles_and_benchmark_commands_work_offline(self):
        profiles=json.loads(self.run_cli('profiles','--snapshot',ROOT/'examples/verified/snapshot.json').stdout)
        self.assertEqual(len(profiles['players']),519)
        with tempfile.TemporaryDirectory() as d:
            result=json.loads(self.run_cli('benchmarks','--db',Path(d)/'empty.sqlite').stdout)
            self.assertEqual(result['groups'],[])
