"""The user systemd units production runs, shared by ``scripts/install_services.py`` and ``nba_forecast.deploy``.

Four services and three timers: the tick (``nba-forecast``, every minute on the
minute), the derived-report service (``nba-derived``, every minute at :30,
rebuilding only when its inputs changed), the market-line capture
(``nba-capture``, every minute at :45) and the dashboard. ``deploy`` rewrites
each service's ``WorkingDirectory`` to the pinned release and writes the derived
and capture units for a host that does not have them yet.
"""
SERVICES=('nba-forecast.service','nba-derived.service','nba-capture.service','nba-dashboard.service')
TIMERS=('nba-forecast.timer','nba-derived.timer','nba-capture.timer')
# Units deploy writes when absent: they need only the operations directory.
CREATED_BY_DEPLOY=('nba-derived.service','nba-derived.timer','nba-capture.service','nba-capture.timer')


def quote(value):
    """Quote an ExecStart argument the way systemd parses it: reject controls, escape %."""
    s=str(value)
    if any(c in s for c in '\n\r\x00'):
        raise ValueError('invalid unit path')
    return '"'+s.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'


def _base(working_directory):
    # WorkingDirectory is a literal path setting, not an ExecStart argument list.
    quote(working_directory)  # Validate controls; quotes would become part of this setting's path.
    return f'WorkingDirectory={str(working_directory).replace("%","%%")}\nUMask=0077\nNoNewPrivileges=true\nPrivateTmp=true\n'


def derived_units(python,working_directory,directory):
    """The derived-report service and its timer."""
    common=f'{quote(python)} -m nba_forecast'
    return {
     'nba-derived.service':f'[Unit]\nDescription=NBA Forecast derived reports (performance, calibration, postgame, daily review, market)\n[Service]\nType=oneshot\n{_base(working_directory)}Nice=10\nExecStart={common} derived --directory {quote(directory)}\nTimeoutStartSec=30min\n',
     'nba-derived.timer':'[Unit]\nDescription=NBA Forecast derived reports, one-minute schedule\n[Timer]\nOnCalendar=*-*-* *:*:30\nAccuracySec=1s\nPersistent=true\nUnit=nba-derived.service\n[Install]\nWantedBy=timers.target\n'}


def capture_units(python,working_directory,directory):
    """The market-line capture service and its timer: at :45, after the derived run's :30 and fifteen seconds
    before each tick, which reads the ESPN injury-list digests a capture saves.

    A run stops starting requests after ``capture.RUN_SECONDS``; the start limit
    ends a run stuck on name resolution, which the request timeouts do not cover.
    """
    common=f'{quote(python)} -m nba_forecast'
    return {
     'nba-capture.service':f'[Unit]\nDescription=NBA Forecast market-line capture (ESPN and Kalshi game lines, Kalshi player props)\n[Service]\nType=oneshot\n{_base(working_directory)}ExecStart={common} capture --directory {quote(directory)}\nTimeoutStartSec=2min\n',
     'nba-capture.timer':'[Unit]\nDescription=NBA Forecast market-line capture, one-minute schedule\n[Timer]\nOnCalendar=*-*-* *:*:45\nAccuracySec=1s\nPersistent=true\nUnit=nba-capture.service\n[Install]\nWantedBy=timers.target\n'}


def created_units(python,working_directory,directory):
    """The units deploy writes when a host lacks them (``CREATED_BY_DEPLOY``)."""
    return {**derived_units(python,working_directory,directory),**capture_units(python,working_directory,directory)}


def unit_files(python,working_directory,directory,history,assets):
    """Every unit, keyed by file name."""
    common=f'{quote(python)} -m nba_forecast'
    base=_base(working_directory)
    files={
     'nba-forecast.service':f'[Unit]\nDescription=NBA Forecast evidence and feedback tick\n[Service]\nType=oneshot\n{base}ExecStart={common} tick --directory {quote(directory)} --history {quote(history)}\nTimeoutStartSec=10min\n',
     'nba-forecast.timer':'[Unit]\nDescription=NBA Forecast one-minute schedule\n[Timer]\nOnCalendar=*-*-* *:*:00\nAccuracySec=1s\nPersistent=true\nUnit=nba-forecast.service\n[Install]\nWantedBy=timers.target\n',
     'nba-dashboard.service':f'[Unit]\nDescription=NBA Forecast read-only dashboard\n[Service]\nType=simple\n{base}ExecStart={common} dashboard --directory {quote(directory)} --assets {quote(assets)}\nRestart=on-failure\nRestartSec=5\n[Install]\nWantedBy=default.target\n'}
    files.update(created_units(python,working_directory,directory))
    return files
