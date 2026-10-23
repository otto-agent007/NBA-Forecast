"""Materialize player evidence from verified finals; bounded, restartable shot fetches."""
from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
from .data import normalize_boxscore,fetch_roster
from .validation import validate_history,parse_time
from .schedule import atomic_json
from .player_data import build_player_pages
from .shots import fetch_game_shots


def enrich_archive(directory):
    """Re-normalize archived bytes with original retrieval times, verifying hashes."""
    rows={}
    for path in sorted(Path(directory).glob('*.provenance.json')):
        meta=json.loads(path.read_text())
        if '/liveData/boxscore/boxscore_002' not in meta.get('source_url',''):continue
        raw=(path.parent/(meta['sha256']+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('Archive hash mismatch: '+path.name)
        for row in normalize_boxscore(json.loads(raw),meta['source_url'],meta['retrieved_at']):
            key=(row['game_id'],row['player_id']);old=rows.get(key)
            if old is None or parse_time(old['known_at'])<parse_time(row['known_at']):rows[key]=row
    result=list(rows.values());validate_history(result)
    return sorted(result,key=lambda r:(r['game_time'],r['game_id'],r['player_id']))


def enrich_final_cache(folder):
    """Add optional fields from matching archived finals without changing counts."""
    folder=Path(folder)
    evidence={(r['game_id'],r['player_id']):r for r in enrich_archive(folder/'raw')}
    required=('fgm','fga','fg3m','fg3a','ftm','fta','tov','pts','minutes','team_id','opponent_id')
    for path in (folder/'finals').glob('*.json'):
        rows=json.loads(path.read_text());changed=False
        for row in rows:
            raw=evidence.get((row['game_id'],row['player_id']))
            if not raw or any(row[k]!=raw[k] for k in required):continue
            for field in ('reb','ast','stl','blk','starter'):
                if row.get(field) is None and raw.get(field) is not None:
                    row[field]=raw[field];changed=True
                    row['known_at']=max((row['known_at'],raw['known_at']),key=parse_time)
        if changed:
            validate_history(rows);atomic_json(path,rows)


def sync_player_data(directory,history,*,roster=None,max_shot_games=2):
    if not 0<=max_shot_games<=50:raise ValueError('max_shot_games must be 0..50')
    folder=Path(directory);schedule=json.loads((folder/'schedule/latest.json').read_text())
    season=schedule['season'];now=datetime.now(timezone.utc).isoformat()
    if roster is None:
        saved=folder/'roster.json'
        roster=json.loads(saved.read_text()) if saved.exists() else fetch_roster(season,cache_dir=folder/'raw')
    now=datetime.now(timezone.utc).isoformat()
    if parse_time(roster['observed_at'])>parse_time(now):raise ValueError('Roster is future-known')
    atomic_json(folder/'roster.json',roster)
    enrich_final_cache(folder)
    combined={(r['game_id'],r['player_id']):r for r in history}
    for path in sorted((folder/'finals').glob('*.json')):
        for row in json.loads(path.read_text()):
            key=(row['game_id'],row['player_id']);old=combined.get(key)
            if old is None or parse_time(old['known_at'])<=parse_time(row['known_at']):combined[key]=row
    history=list(combined.values());validate_history(history)
    targets={r['game_id']:r for r in history if r['season']==season and parse_time(r['known_at'])<=parse_time(now)}
    shots_dir=folder/'shots';shots_dir.mkdir(parents=True,exist_ok=True)
    fetched=0;errors=[]
    for gid,row in sorted(targets.items(),key=lambda kv:(kv[1]['game_time'],kv[0]),reverse=True):
        path=shots_dir/(gid+'.json');old=json.loads(path.read_text()) if path.exists() else None
        if old and not old.get('missing_reason'):continue
        if old and parse_time(now)-parse_time(old['known_at'])<timedelta(hours=6):continue
        if fetched>=max_shot_games:break
        fetched+=1
        try:result=fetch_game_shots(gid,folder/'raw')
        except (ValueError,OSError) as exc:
            result=dict(game_id=gid,season=season,game_time=row['game_time'],known_at=now,shots=[],missing_reason=str(exc))
            errors.append(dict(game_id=gid,error=str(exc)))
        atomic_json(path,result)
    shots=[json.loads(p.read_text()) for p in sorted(shots_dir.glob('*.json'))]
    now=datetime.now(timezone.utc).isoformat()
    pages=build_player_pages(roster,history,season,now,shots)
    output=folder/'players';output.mkdir(parents=True,exist_ok=True)
    for pid,page in pages['players'].items():
        if not pid.isdigit():raise ValueError('Player page ID must be numeric')
        page.update(as_of=now,roster_observed_at=roster['observed_at'],season=season)
        atomic_json(output/(pid+'.json'),page)
    summary=dict(as_of=now,season=season,players=len(pages['players']),history_rows=len(history),
                 rotation_roles={pid:page['rotation_role'] for pid,page in pages['players'].items()},
                 player_summaries={pid:{'season':s['season'],'games':s['gp'],'points_average':s['averages']['pts'],
                     'minutes_average':s['averages']['minutes'],'as_of':now}
                     for pid,page in pages['players'].items()
                     for s in [page['current_season'] if page['current_season']['gp'] else page['prior_season']]},
                 current_season_final_games=len(targets),shot_games_verified=sum(not s.get('missing_reason') for s in shots),
                 shot_games_attempted=fetched,errors=errors,policy='Observed final regular-season data; partial coverage is explicit.')
    atomic_json(output/'index.json',summary)
    return summary
