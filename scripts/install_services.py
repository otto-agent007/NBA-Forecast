#!/usr/bin/env python3
"""Generate user-level systemd units for an existing persistent Linux host."""
import argparse
from pathlib import Path
import sys

repo=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(repo))
from nba_forecast.units import unit_files  # noqa: E402

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--directory',required=True,help='Persistent operations directory')
p.add_argument('--history',required=True,help='Enriched normalized history JSON or gzip')
p.add_argument('--unit-directory',default=str(Path.home()/'.config/systemd/user'))
a=p.parse_args()
history=Path(a.history).resolve()
directory=Path(a.directory).resolve()
if not history.is_file():
    p.error('history file must exist')
if not (repo/'dashboard/dist/index.html').exists():
    p.error('build dashboard first')
folder=Path(a.unit_directory)
folder.mkdir(parents=True,exist_ok=True)
files=unit_files(sys.executable,repo,directory,history,repo/'dashboard/dist')
for name in files:
    target=folder/name
    if target.exists():
        p.error(f'{target} already exists; review it before replacing')
for name,content in files.items():
    (folder/name).write_text(content)
print('Wrote units to',folder)
print('On the persistent host, enable with: systemctl --user daemon-reload && systemctl --user enable --now nba-forecast.timer nba-derived.timer nba-capture.timer nba-dashboard.service')
print('Check: systemctl --user status nba-forecast.timer nba-derived.timer nba-capture.timer nba-dashboard.service; journalctl --user -u nba-forecast.service -u nba-derived.service -u nba-capture.service')
print('Dashboard listens on localhost:8765. Use SSH forwarding or your authenticated reverse proxy. Enable user lingering if required by your host.')
