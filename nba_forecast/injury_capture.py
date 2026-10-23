"""Restart-safe daily capture and auditable per-game checkpoint archives."""
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .injuries import fetch_daily_report
from .schedule import atomic_json,canonical
from .validation import parse_time

PACIFIC=ZoneInfo('America/Los_Angeles')


def capture_daily(directory,season,now):
    folder=Path(directory)/'injuries'
    local=parse_time(now).astimezone(PACIFIC)
    scheduled=local.replace(hour=9,minute=0,second=0,microsecond=0)
    status=dict(daily_time='09:00',timezone='America/Los_Angeles',
                date=local.date().isoformat(),scheduled_at=scheduled.astimezone(ZoneInfo('UTC')).isoformat())
    path=folder/'daily'/f'{season}-{local.date().isoformat()}.json'
    if path.exists():
        capture=json.loads(path.read_text())
        status.update(state='captured',capture=capture)
    elif local<scheduled:
        status['state']='waiting'
    else:
        try:capture=fetch_daily_report(season,Path(directory)/'raw')
        except (ValueError,OSError) as exc:
            status.update(state='error',error=str(exc),last_attempt_at=now)
            atomic_json(folder/'status.json',status)
            raise
        capture.update(capture_date=local.date().isoformat(),scheduled_at=status['scheduled_at'])
        atomic_json(path,capture)
        status.update(state='captured',capture=capture)
    atomic_json(folder/'status.json',status)
    return status


def record_check(directory,check,stage):
    for game in check['games']:
        root=Path(directory)/'injuries/games'/game['game_id']
        payload={**check,'forecast_stage':stage}
        digest=hashlib.sha256(canonical(payload)).hexdigest()
        archive=root/'records'/(digest+'.json')
        if not archive.exists():atomic_json(archive,payload)
        atomic_json(root/'latest.json',payload)
        if stage=='final' and game['phase']=='final_review' and game['report_status']=='captured_pending_review':
            final=root/'final.json'
            old=json.loads(final.read_text()) if final.exists() else None
            # A rescheduled game gets its own checkpoint; old records remain archived.
            if old is None or old['games'][0]['start_time']!=game['start_time']:
                atomic_json(final,payload)
