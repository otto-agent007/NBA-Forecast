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


def reported_unavailable(directory,schedule,roster,history,game,parsed=None):
    """Earlier games each of a game's players was reported out of, for the participation window.

    Each earlier current-season game in ``history`` contributes its final-stage
    checkpoint (``final.json``, the T-minus-30 anchor report), parsed with the
    production parser against the current roster. A game without one, a report
    that fails to parse and a name that does not join contribute nothing, which
    leaves that game counted as before. ``parsed`` caches reports by hash.
    """
    from .injury_parser import parse_report,pdf_text
    folder=Path(directory);teams={game['home_team_id'],game['away_team_id']}
    parsed={} if parsed is None else parsed
    earlier=sorted({r['game_id'] for r in history if r['team_id'] in teams and r['season']==schedule['season']})
    result={}
    for gid in earlier:
        path=folder/'injuries/games'/gid/'final.json'
        if not path.exists():continue
        check=json.loads(path.read_text())
        evidence=next((g.get('anchor_report') for g in check.get('games',[]) if g.get('game_id')==gid),None)
        if not evidence:continue
        if evidence['sha256'] not in parsed:
            try:
                text=pdf_text(folder/'raw'/(evidence['sha256']+'.pdf'),evidence['sha256'])
                parsed[evidence['sha256']]=parse_report(text,schedule,roster,evidence)
            except (ValueError,OSError):
                parsed[evidence['sha256']]=None
        report=parsed[evidence['sha256']]
        for row in (report or {}).get('rows',[]):
            if row['game_id']==gid and row['status']=='out' and row['team_id'] in teams:
                result.setdefault(row['player_id'],[]).append(gid)
    return dict(sorted(result.items()))
