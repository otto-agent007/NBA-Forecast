"""Materialize player evidence from verified finals; bounded, restartable shot fetches."""
from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
from .data import normalize_boxscore,fetch_roster
from .validation import validate_history,parse_time
from .files import archive_bytes, atomic_bytes, atomic_json, canonical
from .replay_cache import code_digest
from .player_data import build_player_pages
from .shots import fetch_game_shots,normalize_page_shots,SHOT_SOURCE_VERSION
from .full_boxscore_sources import (fetch_official_game_page,normalize_official_page,revise_final_rows,
                                   validate_complete_official_final,supplement_official_team_turnovers)


def enrich_archive(directory,skipped=None):
    """Re-normalize archived bytes with original retrieval times, verifying hashes.

    Without ``skipped`` a damaged archive or a final that does not normalize raises. With a
    list, that archive is left out and recorded there: enrichment only adds optional fields.
    """
    rows={}
    for path in sorted(Path(directory).glob('*.provenance.json')):
        try:
            meta=json.loads(path.read_text())
            if not isinstance(meta,dict) or '/liveData/boxscore/boxscore_002' not in meta.get('source_url',''):
                continue
            raw=(path.parent/(meta['sha256']+'.json')).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=meta['sha256']:
                raise ValueError('Archive hash mismatch: '+path.name)
            # Every response is archived before it is checked, including a box the shot collector
            # read before LiveData turned final (0022600182 at "END Q4"). One that is not a final
            # box score adds nothing here.
            try:
                payload=json.loads(raw)
            except (ValueError,UnicodeDecodeError):
                continue
            game=payload.get('game') if isinstance(payload,dict) else None
            if not isinstance(game,dict) or game.get('gameStatus')!=3:
                continue
            found=normalize_boxscore(payload,meta['source_url'],meta['retrieved_at'])
        except (ValueError,KeyError,TypeError,AttributeError,OSError) as exc:
            if skipped is None:
                raise
            skipped.append(dict(archive=path.name,error=f'{type(exc).__name__}: {exc}'))
            continue
        for row in found:
            key=(row['game_id'],row['player_id'])
            old=rows.get(key)
            if old is None or parse_time(old['known_at'])<parse_time(row['known_at']):
                rows[key]=row
    result=list(rows.values())
    validate_history(result)
    return sorted(result,key=lambda r:(r['game_time'],r['game_id'],r['player_id']))


def enrich_final_cache(folder,skipped=None):
    """Add optional fields from matching archived finals without changing counts.

    With ``skipped`` (a list), a damaged archive or captured final is left alone and
    recorded there instead of stopping enrichment of the rest.
    """
    folder=Path(folder)
    evidence={(r['game_id'],r['player_id']):r for r in enrich_archive(folder/'raw',skipped)}
    required=('fgm','fga','fg3m','fg3a','ftm','fta','tov','pts','minutes','team_id','opponent_id')
    for path in (folder/'finals').glob('*.json'):
        try:
            rows=json.loads(path.read_text())
            changed=False
            for row in rows:
                raw=evidence.get((row['game_id'],row['player_id']))
                if not raw or any(row[k]!=raw[k] for k in ('team_id','opponent_id','status')):
                    continue
                # Starting is identity evidence; a later stat correction (counts that no longer
                # match the cached final) does not make it doubtful.
                counted=all(row[k]==raw[k] for k in required)
                for field in ('reb','ast','stl','blk','starter') if counted else ('starter',):
                    if row.get(field) is None and raw.get(field) is not None:
                        row[field]=raw[field]
                        changed=True
                        row['known_at']=max((row['known_at'],raw['known_at']),key=parse_time)
            if changed:
                validate_history(rows)
                atomic_json(path,rows)
        except (ValueError,KeyError,TypeError,AttributeError,OSError) as exc:
            if skipped is None:
                raise
            skipped.append(dict(final=path.name,error=f'{type(exc).__name__}: {exc}'))
    return skipped


def _shot_state(result, rows):
    """Check cache counts again when corrected finals or the adapter change."""
    if not result or result.get('missing_reason') or result.get('source_version')!=SHOT_SOURCE_VERSION:
        return False,False
    expected={(r['team_id'],r['player_id']):[r[k] for k in ('fga','fgm','fg3a','fg3m')]
              for r in rows if r['status']=='played'}
    coverage=result.get('coverage',{})
    people=coverage.get('players',[])
    if (coverage.get('reconciled') is not True or len(people)!=len(expected)
            or {(p.get('team_id'),p.get('player_id')) for p in people}!=set(expected)):
        return False,False
    counts={key:[0,0,0,0] for key in expected}
    located=0
    seen=set()
    for shot in result.get('shots',[]):
        key=shot.get('team_id'),shot.get('player_id')
        number=shot.get('action_number')
        if (key not in counts or shot.get('game_id')!=rows[0]['game_id'] or number in seen
                or shot.get('points') not in (2,3) or type(shot.get('made')) is not bool):
            return False,False
        seen.add(number)
        made=shot['made']
        three=shot['points']==3
        for i,value in enumerate((1,int(made),int(three),int(made and three))):
            counts[key][i]+=value
        located+=int(shot.get('x') is not None and shot.get('y') is not None)
    if counts!=expected or coverage.get('attempts')!=sum(v[0] for v in expected.values()):
        return False,False
    for player in people:
        key=player['team_id'],player['player_id']
        if [player.get(k) for k in ('attempts','made','three_attempts','three_made')]!=expected[key]:
            return False,False
    return True,located==coverage['attempts'] and coverage.get('located_attempts')==located


# The official page lists a game's inactive players once it is republished, usually within
# hours of the final; a page still without them is fetched again, every half hour, for a day.
INACTIVE_WAIT=timedelta(hours=24)
INACTIVE_RETRY_SECONDS=1800
# A failed game is retried after 1, 2 and 4 minutes, then every 5 minutes while its
# sources are still being published. Six hours after tipoff a game still failing is
# usually waiting on a source correction (team minutes that do not add up, a played
# player without minutes); each attempt costs up to three 15 s page timeouts, so
# after the first three quick retries it is retried hourly, without end, instead.
RETRY_SECONDS=300
STALE_AFTER=timedelta(hours=6)
STALE_RETRY_SECONDS=3600


def _retry_wait(attempts,game_time,now):
    wait=min(60*2**min(attempts-1,3),RETRY_SECONDS)
    if wait==RETRY_SECONDS and parse_time(now)>=parse_time(game_time)+STALE_AFTER:
        return STALE_RETRY_SECONDS
    return wait


def _waiting_for_inactives(rows,now):
    return (all('official_inactives_listed' in r for r in rows) and not all(r['official_inactives_listed'] for r in rows)
            and parse_time(now)<parse_time(rows[0]['game_time'])+INACTIVE_WAIT)


def _needs_official_final(rows,now):
    fields=('reb','ast','stl','blk','oreb','dreb','pf','plus_minus')
    return (any(r['status']=='played' and (not(r.get('full_boxscore_enrichment')
               or r.get('source_url')==f'https://www.nba.com/game/{r["game_id"]}/box-score')
               or r.get('minutes_precision')!='seconds' or any(r.get(k) is None for k in fields)) for r in rows)
            or any('official_inactives_listed' not in r for r in rows) or _waiting_for_inactives(rows,now))


def _keep_final_players(combined,gid,rows):
    """A saved final lists the game's players; drop other rows for that game."""
    players={r['player_id'] for r in rows}
    for key in [k for k in combined if k[0]==gid and k[1] not in players]:
        del combined[key]


def _save_official_final(folder,gid,rows,official,metadata):
    """Archive the exact previous cache bytes before any audited replacement."""
    compare=('status','team_id','opponent_id','minutes','minutes_precision','fgm','fga','fg3m','fg3a','ftm','fta','tov','pts',
             'reb','ast','stl','blk','oreb','dreb','pf','plus_minus','team_boxscore','starter','official_inactives_listed')
    path=folder/'finals'/(gid+'.json')
    verified=all(r['status']=='dnp' or r.get('full_boxscore_enrichment') or
                 r.get('source_url')==metadata['source_url'] for r in rows)
    revised=revise_final_rows(rows,official)
    substance=lambda values:{r['player_id']:[r.get(k) for k in compare] for r in values}
    if verified and substance(rows)==substance(revised):
        if not path.exists():
            atomic_json(path,rows)
        return rows,None
    previous=path.read_bytes() if path.exists() else (json.dumps(rows,sort_keys=True,indent=2)+'\n').encode()
    digest=hashlib.sha256(previous).hexdigest()
    archive=folder/'final-revisions'/gid/(digest+'.json')
    archive_bytes(archive,previous)
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


def sync_player_data(directory,history,*,roster=None,max_shot_games=16,schedule=None,memo=None):
    """Complete the season's final boxes and shot evidence (bounded fetches), then the player pages.

    ``memo`` is the tick's ``tick_memo.HistoryMemo``: the enriched, merged history
    the tick already prepared, instead of enriching and merging again here, and
    the key that lets an unchanged history, roster and shot set skip rebuilding
    the pages. Pages and ``players/index.json`` are written only when their
    content changes; their ``as_of`` is when it last changed (``PAGE_RECORD``).
    """
    if not 0<=max_shot_games<=50:
        raise ValueError('max_shot_games must be 0..50')
    folder=Path(directory)
    if schedule is None:
        schedule=json.loads((folder/'schedule/latest.json').read_text())
    season=schedule['season']
    now=datetime.now(timezone.utc).isoformat()
    if roster is None:
        saved=folder/'roster.json'
        roster=json.loads(saved.read_text()) if saved.exists() else fetch_roster(season,cache_dir=folder/'raw')
    now=datetime.now(timezone.utc).isoformat()
    if parse_time(roster['observed_at'])>parse_time(now):
        raise ValueError('Roster is future-known')
    _write_changed(folder/'roster.json',roster)
    # One damaged archive or captured final costs that file, not player data for every game.
    # The tick reports the same files at its own merge; standalone runs see them here.
    skipped_sources=[]
    history_key=None
    if memo is None:
        try:
            enrich_final_cache(folder,skipped=skipped_sources)
        except ValueError as exc:
            skipped_sources.append(dict(error=f'Finals enrichment: {exc}'))
        from .operations import _merge_finals
        history=_merge_finals(history,folder/'finals',errors=skipped_sources)
    else:
        history,skipped,merge_errors,history_key,failure=memo.merged()
        skipped_sources.extend(skipped)
        if failure is not None:
            if not isinstance(failure,ValueError):
                raise failure
            skipped_sources.append(dict(error=f'Finals enrichment: {failure}'))
        # As a second merge of the tick's merged history reported them: a game-identity
        # disagreement the tick already reported is not repeated (``HistoryMemo.merged``).
        skipped_sources.extend(merge_errors)
    changed_rows=False
    combined={(r['game_id'],r['player_id']):r for r in history}
    targets={}
    for row in history:
        if row['season']==season and parse_time(row['known_at'])<=parse_time(now):
            targets.setdefault(row['game_id'],[]).append(row)
    games={g['game_id']:g for g in schedule.get('games',[])}
    shots_dir=folder/'shots'
    shots_dir.mkdir(parents=True,exist_ok=True)
    fetched=0
    errors=[]
    revisions=[]
    queue=[]
    cached={}
    for gid,rows in targets.items():
        path=shots_dir/(gid+'.json')
        old=json.loads(path.read_text()) if path.exists() else None
        cached[gid]=old
        reconciled,complete=_shot_state(old,rows)
        if complete and (gid not in games or not _needs_official_final(rows,now)):
            continue
        current=old and old.get('source_version')==SHOT_SOURCE_VERSION
        if current and old.get('next_retry_at') and parse_time(now)<parse_time(old['next_retry_at']):
            continue
        attempted=current and old.get('attempt_count',0)>0
        # New games first; among retries the oldest attempt runs first, so a
        # persistently blocked recent game cannot starve the rest of the season.
        priority=(1,parse_time(old['known_at']).timestamp(),-parse_time(rows[0]['game_time']).timestamp()) if attempted else (0,0,-parse_time(rows[0]['game_time']).timestamp())
        queue.append((priority,gid))
        if old is None:
            cached[gid]=_pending_shots(gid,rows,now,'Shot evidence queued for collection')
            atomic_json(path,cached[gid])
    for _,gid in sorted(queue)[:max_shot_games]:
        rows=targets[gid]
        old=cached[gid]
        path=shots_dir/(gid+'.json')
        game=games.get(gid)
        fetched+=1
        attempts=1+(old.get('attempt_count',0) if old and old.get('source_version')==SHOT_SOURCE_VERSION else 0)
        try:
            if game is not None:
                try:
                    page,metadata=fetch_official_game_page(game,cache_dir=folder/'raw')
                except (ValueError,OSError) as official_error:
                    try:
                        result=fetch_game_shots(gid,folder/'raw',final_rows=rows)
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
                    changed_rows=True
                    targets[gid]=rows
                    _keep_final_players(combined,gid,rows)
                    for row in rows:
                        combined[(gid,row['player_id'])]=row
                    if revision:
                        revisions.append(revision)
                    result=normalize_page_shots(page,game,metadata,final_rows=rows)
                    result['final_completeness']=completeness
            else:
                result=fetch_game_shots(gid,folder/'raw',final_rows=rows)
        except (ValueError,OSError) as exc:
            result=_pending_shots(gid,rows,now,str(exc))
            errors.append(dict(game_id=gid,error=str(exc)))
        result.update(source_version=SHOT_SOURCE_VERSION,attempt_count=attempts,last_attempt_at=now)
        if not _shot_state(result,rows)[1] or (game is not None and _needs_official_final(rows,now)):
            wait=(INACTIVE_RETRY_SECONDS if _shot_state(result,rows)[1] and _waiting_for_inactives(rows,now)
                  else _retry_wait(attempts,rows[0]['game_time'],now))
            result['next_retry_at']=(parse_time(now)+timedelta(seconds=wait)).isoformat()
        cached[gid]=result
        atomic_json(path,result)
    history=list(combined.values())
    if memo is None or changed_rows:
        # Otherwise these are the rows the tick's merge validated.
        validate_history(history)
    now=datetime.now(timezone.utc).isoformat()
    output=folder/'players'
    output.mkdir(parents=True,exist_ok=True)
    record=_page_record(folder)
    shot_files=sorted([p.name,*_stat(p)] for p in shots_dir.glob('*.json'))
    key=None
    if history_key and not changed_rows:
        horizon=max(filter(None,(memo.horizon(),record.get('shots_horizon'))),key=parse_time,default=None)
        if horizon is not None and parse_time(now)>parse_time(horizon):
            # Past every row's and shot game's time the cutoff selects the same evidence.
            key=_digest([code_digest(),history_key,_digest(roster),shot_files,season,'beyond'])
    previous=_read_json(output/'index.json')
    written_record=None
    if (key is not None and record.get('key')==key and isinstance(previous,dict)
            and all((output/(pid+'.json')).exists() for pid in record.get('pages',{}))):
        # Nothing the pages are built from changed: they and their summaries stand.
        players=len(record.get('pages',{}))
        rotation_roles,player_summaries=previous.get('rotation_roles',{}),previous.get('player_summaries',{})
    else:
        shots=[json.loads((shots_dir/name).read_text()) for name,*_ in shot_files]
        pages=build_player_pages(roster,history,season,now,shots)
        written={}
        for pid,page in pages['players'].items():
            if not pid.isdigit():
                raise ValueError('Player page ID must be numeric')
            page.update(roster_observed_at=roster['observed_at'],season=season)
            content=_digest(page)
            kept=record.get('pages',{}).get(pid)
            if kept and kept[0]==content and (output/(pid+'.json')).exists():
                page['as_of']=kept[1]
            else:
                page['as_of']=now
                atomic_json(output/(pid+'.json'),page)
            written[pid]=[content,page['as_of']]
        times=[t for g in shots for t in (g.get('known_at'),g.get('game_time')) if isinstance(t,str)]
        written_record=dict(version=PAGE_VERSION,code=code_digest(),key=key,pages=written,
                            shots_horizon=max(times,key=parse_time) if times else None)
        players=len(pages['players'])
        rotation_roles={pid:page['rotation_role'] for pid,page in pages['players'].items()}
        player_summaries={pid:{'name':page['name'],'team_id':page['team_id'],
                     'roster_membership':page.get('roster_membership','current'),
                     'identity_basis':page.get('identity_basis'),
                     'season':s['season'],'games':s['gp'],'points_average':s['averages']['pts'],
                     'minutes_average':s['averages']['minutes'],'as_of':page['as_of']}
                     for pid,page in pages['players'].items()
                     for s in [page['current_season'] if page['current_season']['gp'] else page['prior_season']]}
    summary=dict(as_of=now,season=season,players=players,history_rows=len(history),
                 rotation_roles=rotation_roles,player_summaries=player_summaries,
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
    summary['full_box_games_pending']=sum(_needs_official_final(rows,now) for rows in targets.values())
    summary['full_box_games_complete']=len(targets)-summary['full_box_games_pending']
    summary['skipped_sources']=skipped_sources
    summary['shot_queue']=[dict(game_id=gid,game_time=rows[0]['game_time'],
        status='complete' if _shot_state(cached.get(gid),rows)[1] and not _needs_official_final(rows,now) else 'pending',
        reason=(cached.get(gid) or {}).get('missing_reason') or ('Some attempt coordinates are unavailable' if not _shot_state(cached.get(gid),rows)[1] else
                'Full box score has unavailable statistics, imprecise minutes or no inactive list yet' if _needs_official_final(rows,now) else None),
        next_retry_at=(cached.get(gid) or {}).get('next_retry_at'))
        for gid,rows in sorted(targets.items(),key=lambda item:(item[1][0]['game_time'],item[0]),reverse=True)]
    if isinstance(previous,dict) and canonical({k:v for k,v in previous.items() if k!='as_of'})==canonical({k:v for k,v in summary.items() if k!='as_of'}):
        # Unchanged: the index keeps the time its content last changed.
        summary=previous
    else:
        atomic_json(output/'index.json',summary)
    if written_record is not None:
        # Recorded only after the pages and the index are written: a sync stopped in
        # between finds the old key and rebuilds. A record that cannot be written
        # costs the next sync a rebuild, never this one.
        try:
            _write_changed(folder/PAGE_RECORD,written_record)
        except OSError:
            pass
    return summary


PAGE_RECORD='cache/player-pages.json'
PAGE_VERSION=1


def _digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _stat(path):
    info=path.stat()
    return [info.st_size,info.st_mtime_ns]


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError,ValueError):
        return None


def _page_record(folder):
    """Each page's content digest (without ``as_of``) and ``as_of``, and the key of the inputs they were built from.

    A record of another release, or with a field of the wrong shape, is no record: the pages are rebuilt.
    """
    record=_read_json(folder/PAGE_RECORD)
    if not isinstance(record,dict) or record.get('version')!=PAGE_VERSION or record.get('code')!=code_digest():
        return {}
    pages=record.get('pages',{})
    text=lambda value:value is None or isinstance(value,str)
    if not (isinstance(pages,dict) and text(record.get('key')) and text(record.get('shots_horizon'))
            and all(isinstance(pid,str) and isinstance(kept,list) and len(kept)==2 and all(isinstance(v,str) for v in kept)
                    for pid,kept in pages.items())):
        return {}
    if record.get('shots_horizon') is not None:
        try:
            parse_time(record['shots_horizon'])
        except (ValueError,TypeError):
            return {}
    return record


def _write_changed(path,value):
    """Write ``value`` unless the file already holds exactly these bytes."""
    encoded=canonical(value)
    try:
        if Path(path).read_bytes()==encoded:
            return
    except OSError:
        pass
    atomic_bytes(path,encoded)
