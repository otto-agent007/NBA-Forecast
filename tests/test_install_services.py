import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


class ServiceInstallerTests(unittest.TestCase):
    def test_working_directory_is_a_literal_absolute_path_including_spaces(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d)/'Repo With Spaces';(repo/'scripts').mkdir(parents=True)
            shutil.copyfile(Path(__file__).parents[1]/'scripts/install_services.py',repo/'scripts/install_services.py')
            (repo/'dashboard/dist').mkdir(parents=True);(repo/'dashboard/dist/index.html').write_text('test')
            history=repo/'history.json';history.write_text('[]');units=repo/'units'
            result=subprocess.run([sys.executable,str(repo/'scripts/install_services.py'),'--directory',str(repo/'operations'),
                                   '--history',str(history),'--unit-directory',str(units)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            for name in ('nba-forecast.service','nba-dashboard.service'):
                settings=dict(line.split('=',1) for line in (units/name).read_text().splitlines() if '=' in line)
                self.assertEqual(settings['WorkingDirectory'],str(repo))
