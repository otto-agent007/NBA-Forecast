"""Versioned official season calendar. TBD entries never become timed jobs."""
from collections import Counter
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from .data import SCHEDULE_URL, _fetch
from .validation import parse_time, _season

# Venue locations, not home-team zones (neutral/international sites matter).
STATE_ZONES = {**dict.fromkeys(('NY','MA','GA','NC','OH','MI','IN','FL','PA','DC','ON'),'America/New_York'),
               **dict.fromkeys(('TX','IL','TN','WI','MN','LA','OK'),'America/Chicago'),
               **dict.fromkeys(('CO','UT'),'America/Denver'),
               **dict.fromkeys(('CA','OR'),'America/Los_Angeles'),'AZ':'America/Phoenix'}
CITY_ZONES = {'Manchester':'Europe/London','Paris':'Europe/Paris','Mexico City':'America/Mexico_City'}
EASTERN = ZoneInfo('America/New_York')


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_bytes(canonical(value));tmp.replace(path)


def normalize_season(payload,observed_at):
    parse_time(observed_at)
    source=payload['leagueSchedule'];season=source['seasonYear'];_season(season)
    games=[];seen=set()
    for day in source['gameDates']:
        for g in day['games']:
            gid=g['gameId']
            if not isinstance(gid,str):raise ValueError('invalid schedule game ID')
            if not gid.startswith('002'):continue
            if len(gid)!=10 or not gid.isdigit() or gid[3:5]!=season[2:4] or gid in seen:
                raise ValueError('duplicate or wrong-season schedule game')
            seen.add(gid)
            teams={side:str(g[side+'Team']['teamId']) for side in ('home','away')}
            for team in teams.values():
                if team!='0' and team not in {str(t) for t in range(1610612737,1610612767)}:
                    raise ValueError('unknown NBA team')
            if teams['home']==teams['away']!='0':raise ValueError('team opposes itself')
            status=g['gameStatus']
            if type(status)!=int or status not in (1,2,3):raise ValueError('unknown game status')
            text=g['gameStatusText'].strip();postponed=g.get('postponedStatus','N')
            if postponed not in ('N','Y'):raise ValueError('unknown postponed status')
            tbd='TBD' in text.upper() or '0' in teams.values() or not g.get('gameDateTimeUTC')
            start=None if tbd else parse_time(g['gameDateTimeUTC'])
            city=g.get('arenaCity','');state=g.get('arenaState','')
            zone=CITY_ZONES.get(city) or STATE_ZONES.get(state)
            state_name='postponed' if postponed=='Y' or 'POSTPON' in text.upper() else 'tbd' if tbd else {1:'scheduled',2:'live',3:'final'}[status]
            games.append(dict(game_id=gid,season_type='Regular Season',start_time=start.isoformat() if start else None,
                              source_start_time=g.get('gameDateTimeUTC'),status=state_name,status_text=text,
                              home_team_id=teams['home'],away_team_id=teams['away'],
                              home_name=(g['homeTeam'].get('teamCity','')+' '+g['homeTeam'].get('teamName','')).strip(),
                              away_name=(g['awayTeam'].get('teamCity','')+' '+g['awayTeam'].get('teamName','')).strip(),
                              home_tricode=g['homeTeam'].get('teamTricode'),away_tricode=g['awayTeam'].get('teamTricode'),
                              venue=dict(name=g.get('arenaName'),city=city,state=state,timezone=zone),
                              local_start_time=start.astimezone(ZoneInfo(zone)).isoformat() if start and zone else None))
    games.sort(key=lambda g:(g['start_time'] or '9999',g['game_id']))
    counts=Counter(t for g in games for t in (g['home_team_id'],g['away_team_id']) if t!='0')
    return dict(schema_version=1,season=season,observed_at=observed_at,source_url=SCHEDULE_URL,games=games,
                coverage=dict(entries=len(games),assigned_games=sum('0' not in (g['home_team_id'],g['away_team_id']) for g in games),
                              by_status=dict(Counter(g['status'] for g in games)),games_by_team=dict(sorted(counts.items())),
                              complete_1230=len(games)==1230 and len(counts)==30 and all(n==82 for n in counts.values())))


def save_schedule(payload,observed_at,directory):
    result=normalize_season(payload,observed_at);folder=Path(directory)
    raw_hash=hashlib.sha256(canonical(payload)).hexdigest()
    result['source_content_sha256']=raw_hash
    result['version']=hashlib.sha256(canonical(result)).hexdigest()
    current=folder/'latest.json';old=json.loads(current.read_text()) if current.exists() else {'games':[]}
    before={g['game_id']:g for g in old['games']};after={g['game_id']:g for g in result['games']}
    result['previous_version']=old.get('version')
    result['changes']={'added':sorted(after.keys()-before.keys()),'removed':sorted(before.keys()-after.keys()),
                       'changed':sorted(k for k in before.keys()&after.keys() if before[k]!=after[k])}
    version=folder/'versions'/(result['version']+'.json')
    if not version.exists():atomic_json(version,result)
    atomic_json(current,result)
    return result


def fetch_schedule(directory):
    raw,observed=_fetch(SCHEDULE_URL,Path(directory)/'raw')
    return save_schedule(json.loads(raw),observed,directory)


def due_jobs(schedule,now,*,include_waiting=False):
    now=parse_time(now);jobs=[]
    for g in schedule['games']:
        if g['status']!='scheduled' or not g['start_time']:continue
        start=parse_time(g['start_time']);remaining=start-now
        if timedelta(0)<remaining<=timedelta(hours=24):
            stage='final' if remaining<=timedelta(minutes=30) else 'preliminary'
            morning=start.astimezone(ZoneInfo('America/Los_Angeles')).replace(hour=9,minute=0,second=0,microsecond=0)
            ready=min(morning,start-timedelta(minutes=30))
            waiting=now<ready
            if waiting and not include_waiting:continue
            # Stable through unrelated schedule changes; a changed tipoff gets a new key.
            key=f"{g['game_id']}:{g['start_time']}:{stage}"
            jobs.append(dict(game_id=g['game_id'],stage=stage,key=key,checkpoint_at=(start-timedelta(minutes=30)).isoformat()))
            if waiting:jobs[-1].update(waiting=True,ready_at=ready.isoformat())
    return jobs


def game_context(schedule,game_id):
    target=next(g for g in schedule['games'] if g['game_id']==game_id)
    if not target['start_time']:return {}
    start=parse_time(target['start_time']);day=start.astimezone(EASTERN).date();out={}
    for team in (target['home_team_id'],target['away_team_id']):
        previous=[g for g in schedule['games'] if g['start_time'] and g['status'] not in ('postponed','tbd')
                  and team in (g['home_team_id'],g['away_team_id']) and parse_time(g['start_time'])<start]
        previous.sort(key=lambda g:parse_time(g['start_time']))
        last=previous[-1] if previous else None
        rest=(day-parse_time(last['start_time']).astimezone(EASTERN).date()).days-1 if last else None
        recent=[g for g in previous if 0<=(day-parse_time(g['start_time']).astimezone(EASTERN).date()).days<=3]
        road=0
        if target['away_team_id']==team:
            road=1
            for g in reversed(previous):
                if g['away_team_id']!=team:break
                road+=1
        old_zone=last['venue']['timezone'] if last else None;new_zone=target['venue']['timezone']
        shift=None
        if old_zone and new_zone:
            shift=(start.astimezone(ZoneInfo(new_zone)).utcoffset()-start.astimezone(ZoneInfo(old_zone)).utcoffset()).total_seconds()/3600
        out[team]=dict(previous_game_id=last['game_id'] if last else None,rest_days=rest,
                       back_to_back=rest==0,three_in_four=len(recent)>=2,consecutive_road_games=road,
                       previous_venue=last['venue'] if last else None,venue=target['venue'],timezone_shift_hours=shift,
                       basis='Scheduled regular-season itinerary; not actual travel or an automatic model penalty.')
    return out
