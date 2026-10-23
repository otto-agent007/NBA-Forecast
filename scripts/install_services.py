#!/usr/bin/env python3
"""Generate user-level systemd units for an existing persistent Linux host."""
import argparse
from pathlib import Path
import sys
import shlex

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--directory',required=True,help='Persistent operations directory')
p.add_argument('--history',required=True,help='Enriched normalized history JSON or gzip')
p.add_argument('--unit-directory',default=str(Path.home()/'.config/systemd/user'))
a=p.parse_args();repo=Path(__file__).resolve().parents[1]
history=Path(a.history).resolve();directory=Path(a.directory).resolve()
if not history.is_file():p.error('history file must exist')
if not (repo/'dashboard/dist/index.html').exists():p.error('build dashboard first')
# systemd has its own quoting and specifier syntax: reject controls, escape %.
def quote(value):
    s=str(value)
    if any(c in s for c in '\n\r\x00'):raise ValueError('invalid unit path')
    return '"'+s.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
folder=Path(a.unit_directory);folder.mkdir(parents=True,exist_ok=True)
# WorkingDirectory is a literal path setting, not an ExecStart argument list.
quote(repo)  # Validate controls; quotes would become part of this setting's path.
base=f'WorkingDirectory={str(repo).replace("%","%%")}\nUMask=0077\nNoNewPrivileges=true\nPrivateTmp=true\n'
common=f'{quote(sys.executable)} -m nba_forecast'
files={
 'nba-forecast.service':f'[Unit]\nDescription=NBA Forecast evidence and feedback tick\n[Service]\nType=oneshot\n{base}ExecStart={common} tick --directory {quote(directory)} --history {quote(history)}\nTimeoutStartSec=10min\n',
 'nba-forecast.timer':'[Unit]\nDescription=NBA Forecast one-minute schedule\n[Timer]\nOnCalendar=*-*-* *:*:00\nAccuracySec=1s\nPersistent=true\nUnit=nba-forecast.service\n[Install]\nWantedBy=timers.target\n',
 'nba-dashboard.service':f'[Unit]\nDescription=NBA Forecast read-only dashboard\n[Service]\nType=simple\n{base}ExecStart={common} dashboard --directory {quote(directory)} --assets {quote(repo/"dashboard/dist")}\nRestart=on-failure\nRestartSec=5\n[Install]\nWantedBy=default.target\n'}
for name,content in files.items():
    target=folder/name
    if target.exists():p.error(f'{target} already exists; review it before replacing')
for name,content in files.items():(folder/name).write_text(content)
print('Wrote units to',folder)
print('On the persistent host, enable with: systemctl --user daemon-reload && systemctl --user enable --now nba-forecast.timer nba-dashboard.service')
print('Check: systemctl --user status nba-forecast.timer nba-dashboard.service; journalctl --user -u nba-forecast.service')
print('Dashboard listens on localhost:8765. Use SSH forwarding or your authenticated reverse proxy. Enable user lingering if required by your host.')
