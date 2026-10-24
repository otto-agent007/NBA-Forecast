"""Materialize player evidence from verified finals; bounded, restartable shot fetches."""
from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
from .data import normalize_boxscore,fetch_roster
from .validation import validate_history,parse_time
from .schedule import atomic_json
from .player_data import build_player_pages
from .shots import fetch_game_shots,normalize_page_shots,SHOT_SOURCE_VERSION
from .full_boxscore_sources import (fetch_official_game_page,normalize_official_page,revise_final_rows,
                                   validate_complete_official_final,supplement_official_team_turnovers)


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


def _shot_state(result, rows):
    """Check cache counts again when corrected finals or the adapter change."""
    if not result or result.get('missing_reason') or result.get('source_version')!=SHOT_SOURCE_VERSION:
        return False,False
    expected={(r['team_id'],r['player_id']):[r[k] for k in ('fga','fgm','fg3a','fg3m')]
              for r in rows if r['status']=='played'}
    coverage=result.get('coverage',{});people=coverage.get('players',[])
    if (coverage.get('reconciled') is not True or len(people)!=len(expected)
            or {(p.get('team_id'),p.get('player_id')) for p in people}!=set(expected)):
        return False,False
    counts={key:[0,0,0,0] for key in expected};located=0;seen=set()
    for shot in result.get('shots',[]):
        key=shot.get('team_id'),shot.get('player_id');number=shot.get('action_number')
        if (key not in counts or shot.get('game_id')!=rows[0]['game_id'] or number in seen
                or shot.get('points') not in (2,3) or type(shot.get('made')) is not bool):
            return False,False
        seen.add(number);made=shot['made'];three=shot['points']==3
        for i,value in enumerate((1,int(made),int(three),int(made and three))):counts[key][i]+=value
        located+=int(shot.get('x') is not None and shot.get('y') is not None)
    if counts!=expected or coverage.get('attempts')!=sum(v[0] for v in expected.values()):return False,False
    for player in people:
        key=player['team_id'],player['player_id']
        if [player.get(k) for k in ('attempts','made','three_attempts','three_made')]!=expected[key]:return False,False
    return True,located==coverage['attempts'] and coverage.get('located_attempts')==located


def _needs_official_final(rows):
    fields=('reb','ast','stl','blk','oreb','dreb','pf','plus_minus')
    return any(r['status']=='played' and (not(r.get('full_boxscore_enrichment')
               or r.get('source_url')==f'https://www.nba.com/game/{r["game_id"]}/box-score')
               or r.get('minutes_precision')!='seconds' or any(r.get(k) is None for k in fields)) for r in rows)


def _save_official_final(folder,gid,rows,official,metadata):
    """Archive the exact previous cache bytes before any audited replacement."""
    compare=('status','team_id','opponent_id','minutes','minutes_precision','fgm','fga','fg3m','fg3a','ftm','fta','tov','pts',
             'reb','ast','stl','blk','oreb','dreb','pf','plus_minus','team_boxscore')
    index={r['player_id']:r for r in official}
    path=folder/'finals'/(gid+'.json')
    verified=all(r['status']=='dnp' or r.get('full_boxscore_enrichment') or
                 r.get('source_url')==metadata['source_url'] for r in rows)
    if verified and all(r['status']=='dnp' or
            (r['player_id'] in index and all(r.get(k)==index[r['player_id']].get(k) for k in compare)) for r in rows):
        if not path.exists():atomic_json(path,rows)
        return rows,None
    revised=revise_final_rows(rows,official)
    previous=path.read_bytes() if path.exists() else (json.dumps(rows,sort_keys=True,indent=2)+'\n').encode()
    digest=hashlib.sha256(previous).hexdigest()
    archive=folder/'final-revisions'/gid/(digest+'.json');archive.parent.mkdir(parents=True,exist_ok=True)
    if archive.exists():
        if archive.read_bytes()!=previous:raise ValueError('Final revision archive hash collision')
    else:
        with archive.open('xb') as stream:stream.write(previous)
    atomic_json(archive.with_suffix('.provenance.json'),dict(game_id=gid,sha256=digest,
        archive_kind='prior normalized final cache',source='final cache' if path.exists() else 'normalized history rows',
        replaced_by=dict(metadata)))
    atomic_json(path,revised)
    return revised,dict(game_id=gid,previous_sha256=digest,archive_path=str(archive),
        corrected_players=sum(bool(r.get('official_final_revision',{}).get('changes')) for r in revised))


def _pending_shots(gid,rows,now,reason):
    return dict(game_id=gid,season=rows[0]['season'],game_time=rows[0]['game_time'],known_at=now,
        shots=[],missing_reason=reason,source_version=SHOT_SOURCE_VERSION,attempt_count=0,
        coverage=dict(attempts=0,located_attempts=0,reconciled=False,
                      played_players=sum(r['status']=='played' for r in rows),covered_players=0,players=[]))


def sync_player_data(directory,history,*,roster=None,max_shot_games=16):
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
    targets={}
    for row in history:
        if row['season']==season and parse_time(row['known_at'])<=parse_time(now):targets.setdefault(row['game_id'],[]).append(row)
    games={g['game_id']:g for g in schedule.get('games',[])}
    shots_dir=folder/'shots';shots_dir.mkdir(parents=True,exist_ok=True)
    fetched=0;errors=[];revisions=[];queue=[];cached={}
    for gid,rows in targets.items():
        path=shots_dir/(gid+'.json');old=json.loads(path.read_text()) if path.exists() else None
        cached[gid]=old
        reconciled,complete=_shot_state(old,rows)
        if complete and (gid not in games or not _needs_official_final(rows)):continue
        current=old and old.get('source_version')==SHOT_SOURCE_VERSION
        if current and old.get('next_retry_at') and parse_time(now)<parse_time(old['next_retry_at']):continue
        attempted=current and old.get('attempt_count',0)>0
        # New games first; among retries the oldest attempt runs first, so a
        # persistently blocked recent game cannot starve the rest of the season.
        priority=(1,parse_time(old['known_at']).timestamp(),-parse_time(rows[0]['game_time']).timestamp()) if attempted else (0,0,-parse_time(rows[0]['game_time']).timestamp())
        queue.append((priority,gid))
        if old is None:
            cached[gid]=_pending_shots(gid,rows,now,'Shot evidence queued for collection')
            atomic_json(path,cached[gid])
    for _,gid in sorted(queue)[:max_shot_games]:
        rows=targets[gid];old=cached[gid];path=shots_dir/(gid+'.json');game=games.get(gid)
        fetched+=1
        attempts=1+(old.get('attempt_count',0) if old and old.get('source_version')==SHOT_SOURCE_VERSION else 0)
        try:
            if game is not None:
                try:page,metadata=fetch_official_game_page(game,cache_dir=folder/'raw')
                except (ValueError,OSError) as official_error:
                    try:result=fetch_game_shots(gid,folder/'raw',final_rows=rows)
                    except (ValueError,OSError) as primary_error:
                        raise ValueError(f'Official game page: {official_error}; NBA LiveData: {primary_error}') from primary_error
                else:
                    official=normalize_official_page(page,game,metadata)
                    completeness=validate_complete_official_final(official)
                    # Keep complete player boxes when event data is delayed;
                    # only reconciled events may supply team-turnover counts.
                    try:
                        verified=normalize_page_shots(page,game,metadata,final_rows=official)
                        official=supplement_official_team_turnovers(page,official,metadata,verified)
                    except ValueError as exc:
                        for item in official:
                            item['team_turnover_audit']=dict(status='unavailable',reason=str(exc))
                    rows,revision=_save_official_final(folder,gid,rows,official,metadata)
                    targets[gid]=rows
                    for row in rows:combined[(gid,row['player_id'])]=row
                    if revision:revisions.append(revision)
                    result=normalize_page_shots(page,game,metadata,final_rows=rows)
                    result['final_completeness']=completeness
            else:result=fetch_game_shots(gid,folder/'raw',final_rows=rows)
        except (ValueError,OSError) as exc:
            result=_pending_shots(gid,rows,now,str(exc))
            errors.append(dict(game_id=gid,error=str(exc)))
        result.update(source_version=SHOT_SOURCE_VERSION,attempt_count=attempts,last_attempt_at=now)
        if not _shot_state(result,rows)[1] or (game is not None and _needs_official_final(rows)):
            result['next_retry_at']=(parse_time(now)+timedelta(seconds=min(60*2**min(attempts-1,3),300))).isoformat()
        cached[gid]=result
        atomic_json(path,result)
    shots=[json.loads(p.read_text()) for p in sorted(shots_dir.glob('*.json'))]
    history=list(combined.values());validate_history(history)
    now=datetime.now(timezone.utc).isoformat()
    pages=build_player_pages(roster,history,season,now,shots)
    output=folder/'players';output.mkdir(parents=True,exist_ok=True)
    for pid,page in pages['players'].items():
        if not pid.isdigit():raise ValueError('Player page ID must be numeric')
        page.update(as_of=now,roster_observed_at=roster['observed_at'],season=season)
        atomic_json(output/(pid+'.json'),page)
    summary=dict(as_of=now,season=season,players=len(pages['players']),history_rows=len(history),
                 rotation_roles={pid:page['rotation_role'] for pid,page in pages['players'].items()},
                 player_summaries={pid:{'name':page['name'],'team_id':page['team_id'],
                     'roster_membership':page.get('roster_membership','current'),
                     'identity_basis':page.get('identity_basis'),
                     'season':s['season'],'games':s['gp'],'points_average':s['averages']['pts'],
                     'minutes_average':s['averages']['minutes'],'as_of':now}
                     for pid,page in pages['players'].items()
                     for s in [page['current_season'] if page['current_season']['gp'] else page['prior_season']]},
                 current_season_final_games=len(targets),shot_games_verified=sum(_shot_state(cached.get(gid),rows)[1] for gid,rows in targets.items()),
                 shot_games_reconciled=sum(_shot_state(cached.get(gid),rows)[0] for gid,rows in targets.items()),
                 shot_players_expected=sum(r['status']=='played' for rows in targets.values() for r in rows),
                 shot_players_verified=sum(sum(r['status']=='played' for r in rows) for gid,rows in targets.items() if _shot_state(cached.get(gid),rows)[0]),
                 shot_attempts_expected=sum(r['fga'] for rows in targets.values() for r in rows),
                 shot_attempts_verified=sum(cached[gid]['coverage']['attempts'] for gid,rows in targets.items() if _shot_state(cached.get(gid),rows)[0]),
                 shot_locations_verified=sum(cached[gid]['coverage']['located_attempts'] for gid,rows in targets.items() if _shot_state(cached.get(gid),rows)[0]),
                 shot_games_attempted=fetched,final_revisions=revisions,errors=errors,
                 policy='Every completed regular-season game is queued; every played player is reconciled, including zero attempts. Missing locations remain explicit and are retried.')
    summary['shot_games_pending']=len(targets)-summary['shot_games_verified']
    summary['full_box_games_pending']=sum(_needs_official_final(rows) for rows in targets.values())
    summary['full_box_games_complete']=len(targets)-summary['full_box_games_pending']
    summary['shot_queue']=[dict(game_id=gid,game_time=rows[0]['game_time'],
        status='complete' if _shot_state(cached.get(gid),rows)[1] and not _needs_official_final(rows) else 'pending',
        reason=(cached.get(gid) or {}).get('missing_reason') or ('Some attempt coordinates are unavailable' if not _shot_state(cached.get(gid),rows)[1] else
                'Full box score has unavailable statistics or imprecise minutes' if _needs_official_final(rows) else None),
        next_retry_at=(cached.get(gid) or {}).get('next_retry_at'))
        for gid,rows in sorted(targets.items(),key=lambda item:(item[1][0]['game_time'],item[0]),reverse=True)]
    atomic_json(output/'index.json',summary)
    return summary
