import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from nba_forecast.runner_release import pin_release


class RunnerReleaseTests(unittest.TestCase):
    def test_running_package_is_unchanged_when_worktree_modules_change(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            package=root/'source/nba_forecast'
            package.mkdir(parents=True)
            (package/'__init__.py').write_text('')
            (package/'model.py').write_text('VERSION=1\n')
            (package/'late_import.py').write_text('VERSION=1\n')
            release=pin_release(package,root/'releases')
            (package/'late_import.py').write_text('VERSION=2\n')
            result=subprocess.run([sys.executable,'-c','from nba_forecast import model,late_import; print(model.VERSION,late_import.VERSION)'],cwd=release,text=True,capture_output=True,check=True)
            self.assertEqual(result.stdout.strip(),'1 1')
            self.assertNotEqual(pin_release(package,root/'releases'),release)
            self.assertEqual((release/'nba_forecast/late_import.py').read_text(),'VERSION=1\n')

    def test_existing_release_is_verified_before_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            package=root/'source'
            package.mkdir()
            (package/'__init__.py').write_text('')
            release=pin_release(package,root/'releases')
            self.assertEqual(pin_release(package,root/'releases'),release)
            (release/'nba_forecast/__init__.py').write_text('corrupt')
            with self.assertRaises(ValueError):
                pin_release(package,root/'releases')
