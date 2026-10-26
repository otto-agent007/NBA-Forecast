"""Postgame source fallback, contemporary environment updates and calibration."""
from datetime import timedelta
import hashlib,json,sqlite3
from pathlib import Path
import re
from .espn_actuals import fetch_espn_final,ActualsPending
from .full_boxscore_sources import (fetch_official_game_page,normalize_official_page,
    validate_complete_official_final,supplement_official_team_turnovers)
from .schedule import atomic_json,canonical
from .shots import normalize_page_shots
from .validation import parse_time,validate_history

DISCOVERY_SOURCE_VERSION='espn-and-verified-official-final-v1'


def environment_record(game,rows):
    teams=(game['home_team_id'],game['away_team_id']);stats={};periods=[]
    for team in teams:
        members=[r for r in rows if r['team_id']==team]
        if not members or any('game_period' not in r for r in members):raise ValueError('Explicit final period is required for environment duration')
        periods.extend(r['game_period'] for r in members)
        boxes=[r['team_boxscore'] for r in members]
        if any(b!=boxes[0] for b in boxes):raise ValueError('Final team statistics disagree')
        b=boxes[0];fields=('pts','fga','fta','oreb','total_turnovers','team_turnovers','tov')
        if any(type(b.get(k)) is not int or b[k]<0 for k in fields):raise ValueError('Missing final possession statistics')
        if b['total_turnovers']!=b['tov']+b['team_turnovers']:raise ValueError('Final turnover components disagree')
        stats[team]=dict(pts=b['pts'],fga=b['fga'],fta=b['fta'],oreb=b['oreb'],tov=b['total_turnovers'],
            player_tov=b['tov'],team_turnovers=b['team_turnovers'],total_turnovers=b['total_turnovers'])
    if len(set(periods))!=1 or type(periods[0]) is not int or periods[0]<4:raise ValueError('Final periods disagree')
    return dict(game_id=game['game_id'],game_time=game['start_time'],season=rows[0]['season'],
        home_team_id=teams[0],away_team_id=teams[1],team_stats=stats,game_minutes=48+5*(periods[0]-4),
        known_at=max((r['known_at'] for r in rows),key=parse_time),venue_timezone=rows[0].get('venue_timezone'),
        provenance=rows[0]['source_provenance'])


def _aliases(folder,game):
    db=folder/'forecast.sqlite'
    if not db.exists():return []
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as conn:
        row=conn.execute("SELECT snapshot_json FROM runs WHERE mode='live' AND json_extract(snapshot_json,'$.games[0].game_id')=? ORDER BY rowid DESC LIMIT 1",(game['game_id'],)).fetchone()
    if not row:return []
    snapshot=json.loads(row[0])
    if snapshot.get('preflight',{}).get('status')!='passed':return []
    return [a for r in snapshot.get('roster_reviews',[]) if r['game_id']==game['game_id'] for a in r.get('name_aliases',[])]


class _FinalSourcesPending(ActualsPending):
    def __init__(self,primary,official):
        self.source_failures=[primary,dict(source='Official NBA final',reason=str(official))]
        self.details=primary.get('evidence')
        super().__init__('; '.join(f'{item["source"]}: {item["reason"]}' for item in self.source_failures))


def _discover_final(folder,game,roster,history):
    """An exact NBA final needs no ESPN roster or athlete-ID crosswalk."""
    try:
        if roster is None:raise ValueError('No verified roster for ESPN outcome identities')
        rows=fetch_espn_final(game,roster,folder/'references',history=history,name_aliases=_aliases(folder,game))
        validate_history(rows)
        if not rows:raise ValueError('ESPN final contains no player observations')
        return rows,None,None
    except (ValueError,OSError,KeyError,TypeError) as exc:
        primary=dict(source='ESPN final',reason=str(exc),evidence=getattr(exc,'details',None))
    try:
        page,metadata=fetch_official_game_page(game,cache_dir=folder/'raw')
        rows=normalize_official_page(page,game,metadata)
        completeness=validate_complete_official_final(rows)
        shots=normalize_page_shots(page,game,metadata,final_rows=rows)
        rows=supplement_official_team_turnovers(page,rows,metadata,shots)
        validate_history(rows)
        audit=dict(primary_failure=primary,source_url=metadata['source_url'],
                   source_sha256=metadata['sha256'],retrieved_at=metadata['retrieved_at'])
        for row in rows:row['outcome_source_fallback']=dict(audit)
        shots.update(final_completeness=completeness,paired_final_rows=rows,source_failures=[primary])
        return rows,shots,audit
    except (ValueError,OSError,KeyError,TypeError) as exc:
        raise _FinalSourcesPending(primary,exc) from exc


def _update_environment(folder,game,rows,report):
    if not rows or not rows[0].get('source_provenance'):return
    try:
        record=environment_record(game,rows);digest=hashlib.sha256(canonical(record)).hexdigest()
        path=folder/'environment-finals/records'/(digest+'.json')
        if not path.exists():atomic_json(path,record);report['environment_updates'].append(game['game_id'])
    except (ValueError,KeyError,TypeError) as exc:
        report['environment_warnings'].append(dict(game_id=game['game_id'],error=str(exc)))


def _current_season_game(game,season):
    if game.get('season_type','Regular Season')!='Regular Season':return False
    if season is None:return True
    gid=game.get('game_id','')
    if not isinstance(gid,str) or not re.fullmatch(r'002\d{7}',gid):return False
    year=2000+int(gid[3:5])
    return season==f'{year}-{str(year+1)[-2:]}'


def _validate_cached_final_identity(game,rows):
    validate_history(rows)
    teams={str(game['home_team_id']),str(game['away_team_id'])}
    if len(teams)!=2 or not rows or {r['team_id'] for r in rows}!=teams:
        raise ValueError('Cached final teams do not match the scheduled game')
    start=parse_time(game['start_time'])
    if any(r['game_id']!=game['game_id'] or parse_time(r['game_time'])!=start
           or {r['team_id'],r['opponent_id']}!=teams for r in rows):
        raise ValueError('Cached final game identity, time, or opponent does not match the schedule')


def sync_finished_games(directory,schedule,history,as_of,*,max_source_games=4):
    """Discover all current-season finals with a persistent, bounded retry queue.

    Cached finals receive local environment updates regardless of their age.
    Uncached games remain eligible after an outage; at most four source games
    are attempted per tick by default, without making missing evidence zero.
    """
    if type(max_source_games) is not int or not 0<=max_source_games<=50:
        raise ValueError('max_source_games must be an integer from 0 to 50')
    folder=Path(directory);now=parse_time(as_of);finals=folder/'finals';finals.mkdir(parents=True,exist_ok=True)
    report=dict(imported=[],pending=[],pending_details=[],errors=[],environment_updates=[],environment_warnings=[],
                fallbacks=[],source_games_attempted=0)
    roster_path=folder/'roster.json'
    roster=json.loads(roster_path.read_text()) if roster_path.exists() else None
    queue_path=folder/'outcome-discovery/queue.json'
    saved=json.loads(queue_path.read_text()) if queue_path.exists() else {}
    previous=saved.get('games',{}) if saved.get('source_version')==DISCOVERY_SOURCE_VERSION else {}
    pending={};games={};eligible=[]
    for game in schedule['games']:
        if not _current_season_game(game,schedule.get('season')):continue
        gid=game['game_id'];target=finals/(gid+'.json')
        if target.exists():
            try:
                rows=json.loads(target.read_text());_validate_cached_final_identity(game,rows)
                _update_environment(folder,game,rows,report)
            except (ValueError,OSError,KeyError,TypeError) as exc:
                report['errors'].append(dict(game_id=gid,error=str(exc)))
            continue
        if not game.get('start_time') or game.get('status') in ('postponed','tbd','cancelled','canceled'):continue
        if now-parse_time(game['start_time'])<timedelta(hours=2):continue
        old=previous.get(gid,{})
        record=dict(old) if old else dict(attempt_count=0,first_queued_at=as_of)
        pending[gid]=record;games[gid]=game
        if record.get('next_retry_at') and now<parse_time(record['next_retry_at']):continue
        attempted=record.get('attempt_count',0)>0
        priority=(1,parse_time(record['last_attempt_at']).timestamp(),-parse_time(game['start_time']).timestamp()) if attempted else (0,0,-parse_time(game['start_time']).timestamp())
        eligible.append((priority,gid))
    for _,gid in sorted(eligible)[:max_source_games]:
        game=games[gid];record=pending[gid];record['attempt_count']+=1;record['last_attempt_at']=as_of
        report['source_games_attempted']+=1
        try:
            rows,shots,fallback=_discover_final(folder,game,roster,history)
            atomic_json(finals/(gid+'.json'),rows);report['imported'].append(gid)
            if shots is not None:atomic_json(folder/'shots'/(gid+'.json'),shots)
            if fallback is not None:report['fallbacks'].append(dict(game_id=gid,**fallback))
            _update_environment(folder,game,rows,report)
            del pending[gid]
        except ActualsPending as exc:
            record['failure']=dict(reason=str(exc),evidence=getattr(exc,'details',None),
                                   source_failures=getattr(exc,'source_failures',[]))
        except (ValueError,OSError,KeyError,TypeError) as exc:
            record['failure']=dict(reason=str(exc),evidence=None,source_failures=[])
            report['errors'].append(dict(game_id=gid,error=str(exc)))
        if gid in pending:
            record['next_retry_at']=(now+timedelta(seconds=min(60*2**min(record['attempt_count']-1,3),300))).isoformat()
    for gid,record in sorted(pending.items(),key=lambda item:(games[item[0]]['start_time'],item[0]),reverse=True):
        report['pending'].append(gid)
        detail=record.get('failure') or dict(reason='Final evidence queued for bounded collection',evidence=None,source_failures=[])
        report['pending_details'].append(dict(game_id=gid,**detail,attempt_count=record['attempt_count'],
                                             next_retry_at=record.get('next_retry_at')))
    atomic_json(queue_path,dict(source_version=DISCOVERY_SOURCE_VERSION,as_of=as_of,season=schedule.get('season'),games=pending))
    return report
