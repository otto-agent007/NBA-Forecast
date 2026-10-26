"""Versioned official season calendar. TBD entries never become timed jobs."""
from collections import Counter
from copy import deepcopy
from datetime import timedelta,datetime,timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.error import HTTPError,URLError
import urllib.request
from zoneinfo import ZoneInfo
from .data import SCHEDULE_URL, _fetch, _NoRedirects
from .validation import parse_time, _season

# Venue locations, not home-team zones (neutral/international sites matter).
STATE_ZONES = {**dict.fromkeys(('NY','MA','GA','NC','OH','MI','IN','FL','PA','DC','ON'),'America/New_York'),
               **dict.fromkeys(('TX','IL','TN','WI','MN','LA','OK'),'America/Chicago'),
               **dict.fromkeys(('CO','UT'),'America/Denver'),
               **dict.fromkeys(('CA','OR'),'America/Los_Angeles'),'AZ':'America/Phoenix'}
CITY_ZONES = {'Manchester':'Europe/London','Paris':'Europe/Paris','Mexico City':'America/Mexico_City'}
EASTERN = ZoneInfo('America/New_York')
SECONDARY_HOST = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard'
SECONDARY_ALIASES = {'GS':'GSW','NO':'NOP','NY':'NYK','SA':'SAS','UTAH':'UTA','WSH':'WAS'}
SECONDARY_MAX_BYTES = 2 * 1024 * 1024


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


def secondary_url(day):
    return SECONDARY_HOST+'?dates='+day.strftime('%Y%m%d')


def _secondary_observation(day,directory):
    """Fetch and archive exact ESPN daily bytes; this URL is fixed by date."""
    url=secondary_url(day);opener=urllib.request.build_opener(_NoRedirects())
    try:
        with opener.open(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0',
                          'Accept':'application/json'}),timeout=12) as response:
            raw=response.read(SECONDARY_MAX_BYTES+1)
    except (HTTPError,URLError,TimeoutError) as exc:
        raise ValueError('Secondary scoreboard unavailable: '+type(exc).__name__+
                         (' '+str(exc.code) if isinstance(exc,HTTPError) else '')) from exc
    if len(raw)>SECONDARY_MAX_BYTES:raise ValueError('Secondary scoreboard exceeds size limit')
    try:payload=json.loads(raw)
    except (ValueError,UnicodeDecodeError) as exc:raise ValueError('Secondary scoreboard is not JSON') from exc
    if not isinstance(payload,dict):raise ValueError('Secondary scoreboard must be an object')
    observed=datetime.now(timezone.utc).isoformat();digest=hashlib.sha256(raw).hexdigest()
    archive=Path(directory)/'raw';archive.mkdir(parents=True,exist_ok=True)
    path=archive/(digest+'.json')
    if path.exists() and path.read_bytes()!=raw:raise ValueError('Secondary raw hash collision')
    if not path.exists():path.write_bytes(raw)
    meta=archive/(digest+'.provenance.json')
    if not meta.exists():atomic_json(meta,dict(source_url=url,retrieved_at=observed,sha256=digest,bytes=len(raw)))
    return dict(source_url=url,observed_at=observed,source_sha256=digest,payload=payload)


def _secondary_event(event,season):
    try:
        target_year=int(season[:4])+1
        if event['season']!={'year':target_year,'type':2,'slug':'regular-season'}:return None
        start=parse_time(event['date'])
        if len(event['competitions'])!=1:return None
        if event['competitions'][0].get('timeValid') is False:return None
        players=event['competitions'][0]['competitors']
        if len(players)!=2 or {p['homeAway'] for p in players}!={'home','away'}:return None
        teams={p['homeAway']:SECONDARY_ALIASES.get(p['team']['abbreviation'],p['team']['abbreviation']) for p in players}
        if not all(re.fullmatch('[A-Z]{2,3}',x) for x in teams.values()):return None
        state=event['status']['type'];name=state['name']
        status={'STATUS_SCHEDULED':'scheduled','STATUS_POSTPONED':'postponed',
                'STATUS_CANCELED':'cancelled','STATUS_CANCELLED':'cancelled',
                'STATUS_FINAL':'final'}.get(name)
        if status is None and state.get('state')=='in':status='live'
        if status is None:return None
        eid=str(event['id'])
        if not re.fullmatch('[0-9]+',eid):return None
        return dict(espn_id=eid,start=start,home=teams['home'],away=teams['away'],status=status)
    except (KeyError,ValueError,TypeError,IndexError):return None


def verify_secondary_schedule(cached,observations,as_of):
    """Refresh only unambiguous near-term NBA IDs against an independent slate."""
    cutoff=parse_time(as_of)
    if not observations or not cached.get('version') or not cached.get('games'):
        raise ValueError('A versioned NBA calendar and secondary observations are required')
    events=[];sources=[];invalid=0
    for item in observations:
        known=parse_time(item['observed_at'])
        if not 0 <= (cutoff-known).total_seconds() <= 300:
            raise ValueError('Secondary observation is future-known or stale')
        if not re.fullmatch(re.escape(SECONDARY_HOST)+r'\?dates=\d{8}',item['source_url']):
            raise ValueError('Unexpected secondary URL')
        if not re.fullmatch('[0-9a-f]{64}',item['source_sha256']):raise ValueError('Invalid secondary digest')
        if not isinstance(item['payload'],dict) or not isinstance(item['payload'].get('events'),list):
            raise ValueError('Invalid secondary events')
        sources.append({k:item[k] for k in ('source_url','observed_at','source_sha256')})
        for raw in item['payload']['events']:
            event=_secondary_event(raw,cached['season'])
            if event is None:invalid+=1
            else:event.update(source_url=item['source_url'],observed_at=item['observed_at'],
                              source_sha256=item['source_sha256']);events.append(event)
    result=deepcopy(cached);verified=[];blocked=[];changes=[];unmatched=[]
    # Start with the secondary slate. A game moved *into* the next 24 hours
    # would never be seen by a loop limited to old NBA tipoffs.
    candidates={}
    for event in events:
        if not cutoff-timedelta(hours=12)<event['start']<=cutoff+timedelta(hours=48):continue
        possible=[]
        finished=event['status'] in ('final','live')
        for game in result['games']:
            if game.get('status') not in (('scheduled','postponed','tbd','live','final') if finished
                                             else ('scheduled','postponed','tbd')) or not game.get('start_time'):continue
            if (game.get('home_tricode'),game.get('away_tricode'))!=(event['home'],event['away']):continue
            original=parse_time(game['start_time'])
            if finished and original>cutoff:continue
            delta=abs((original-event['start']).total_seconds())
            if delta<=(3600 if finished else 36*3600):possible.append((delta,game))
        close=[item for item in possible if item[0]<=3600]
        pool=close or possible
        if (len(pool)!=1 or (event['status']=='scheduled' and event['start']<=cutoff)
                or (finished and event['start']>=cutoff)):
            if cutoff<event['start']<=cutoff+timedelta(hours=24):
                unmatched.append(dict(espn_id=event['espn_id'],home=event['home'],away=event['away'],
                    start_time=event['start'].isoformat(),reason='No unique nearby NBA game ID'))
            continue
        candidates.setdefault(pool[0][1]['game_id'],[]).append(event)
    for game in result['games']:
        gid=game['game_id'];matches=candidates.get(gid,[])
        if len(matches)!=1:continue
        event=matches[0]
        new_start=event['start'].isoformat();old_status=game['status'];old_start=game['start_time']
        game['start_time']=new_start;game['status']=event['status']
        if game.get('venue',{}).get('timezone'):
            game['local_start_time']=event['start'].astimezone(ZoneInfo(game['venue']['timezone'])).isoformat()
        game['schedule_corroboration']={k:event[k] for k in ('source_url','observed_at','source_sha256','espn_id')}
        if parse_time(old_start)!=event['start'] or old_status!=event['status']:
            changes.append(dict(game_id=game['game_id'],old_start=old_start,new_start=new_start,
                                old_status=old_status,new_status=event['status']))
        verified.append(gid)
    verified_set=set(verified)
    for game in cached['games']:
        if game.get('status')!='scheduled' or not game.get('start_time'):continue
        original=parse_time(game['start_time'])
        if cutoff<original<=cutoff+timedelta(hours=24) and game['game_id'] not in verified_set:
            blocked.append(game['game_id'])
    # Two ESPN events competing for one NBA ID are also ambiguous, even when
    # that cached NBA game was not originally due today.
    for gid,matches in candidates.items():
        if len(matches)>1:
            for event in matches:
                if cutoff<event['start']<=cutoff+timedelta(hours=24):
                    unmatched.append(dict(espn_id=event['espn_id'],home=event['home'],away=event['away'],
                        start_time=event['start'].isoformat(),reason='Multiple events match one NBA game ID'))
    result['crosscheck']=dict(status='verified' if not blocked and not unmatched else 'partial',
        observed_at=max((s['observed_at'] for s in sources),key=parse_time),
        nba_base_version=cached['version'],verified_game_ids=sorted(verified),
        blocked_game_ids=sorted(blocked),unmatched_secondary_events=unmatched,
        invalid_secondary_events=invalid,sources=sources,changes=changes,
        matching_rule='same home/away teams; unique NBA ID within one hour or 36 hours; longer moves require review')
    result['version']=hashlib.sha256(canonical(dict(base=cached['version'],crosscheck=result['crosscheck']))).hexdigest()
    return result


def fetch_verified_secondary_schedule(cached,directory,as_of):
    cutoff=parse_time(as_of);today=cutoff.astimezone(EASTERN).date()
    observations=[_secondary_observation(today+timedelta(days=i),Path(directory)/'secondary') for i in (0,1)]
    result=verify_secondary_schedule(cached,observations,datetime.now(timezone.utc).isoformat())
    folder=Path(directory)/'secondary';version=folder/'versions'/(result['version']+'.json')
    if version.exists() and version.read_bytes()!=canonical(result):raise ValueError('Secondary version hash collision')
    if not version.exists():atomic_json(version,result)
    atomic_json(folder/'verified-latest.json',result)
    return result


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
