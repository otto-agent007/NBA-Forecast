"""Resumable regular-season backfill with per-game checkpoints and export hashes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re

from .data import boxscore_url, fetch_json, normalize_boxscore
from .validation import validate_history


def game_log_url(season):
    if not re.fullmatch(r'20\d{2}-\d{2}', season) or int(season[5:]) != (int(season[:4])+1)%100:
        raise ValueError('invalid season')
    return ('https://stats.nba.com/stats/leaguegamelog?Counter=0&DateFrom=&DateTo=&Direction=DESC'
            '&LeagueID=00&PlayerOrTeam=T&Season='+season+'&SeasonType=Regular%20Season&Sorter=DATE')


def game_ids(payload, season):
    """Require both final team rows; reject a mismatched season or game type."""
    game_log_url(season)
    try:
        result=next(r for r in payload['resultSets'] if r['name']=='LeagueGameLog')
        teams={}
        for values in result['rowSet']:
            row=dict(zip(result['headers'], values, strict=True))
            gid=row['GAME_ID']
            boxscore_url(gid)
            if gid[3:5] != season[2:4] or str(row['SEASON_ID']) != '2'+season[:4] or row['WL'] not in ('W','L',None):
                raise ValueError('index contains a nonfinal or wrong-season game')
            entries=teams.setdefault(gid,[])
            entries.append((str(row['TEAM_ID']),row['WL']))
        teams={g:v for g,v in teams.items() if all(w is not None for _,w in v)}
        if any(len(v)!=2 or len({t for t,_ in v})!=2 or {w for _,w in v}!={'W','L'} for v in teams.values()):
            raise ValueError('index must have two opposing final team rows per game')
        return sorted(teams)
    except (KeyError,TypeError,StopIteration) as exc:
        raise ValueError('NBA game index schema is incomplete') from exc


def _write(path, value):
    raw=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_bytes(raw)
    temp.replace(path)
    return hashlib.sha256(raw).hexdigest()


def backfill_games(ids, directory, workers=4):
    """Retrieve exactly the given indexed finals; interruptions resume per game.

    Known-at is actual retrieval time, never game date. A failed/corrupt game is
    recorded, not skipped into a successful export. One process per directory.
    """
    if isinstance(workers,bool) or not 1 <= workers <= 8:
        raise ValueError('workers must be between 1 and 8')
    ids=sorted(set(ids))
    for gid in ids: boxscore_url(gid)
    folder=Path(directory); folder.mkdir(parents=True,exist_ok=True)
    lock=folder/'backfill.lock'
    try:
        lock.open('x').close()
    except FileExistsError as exc:
        raise ValueError('backfill lock exists; verify no process is running before removing it') from exc
    try:
        return _run(ids,folder,workers)
    finally:
        lock.unlink()


def _run(ids,folder,workers):
    games=folder/'games'; games.mkdir(exist_ok=True)
    manifest_path=folder/'manifest.json'
    old=json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    cache_hashes=dict(old.get('cache_hashes',{}))
    cache_hashes.update({gid:info['sha256'] for gid,info in old.get('games',{}).items()})
    result=dict(cache_hashes=cache_hashes,schema_version=1,status='running',requested_games=len(ids),completed_games=0,
                resumed_games=0,started_at=datetime.now(timezone.utc).isoformat(),games={},failures={})
    export=folder/'history.json.gz'
    if export.exists(): export.unlink()  # Derived output; rebuilt only after all requested games pass.
    _write(manifest_path,result)

    def one(gid):
        path=games/(gid+'.json')
        if path.exists():
            raw=path.read_bytes(); rows=json.loads(raw)
            digest=hashlib.sha256(raw).hexdigest()
            expected=cache_hashes.get(gid)
            if expected and expected != digest: raise ValueError('cached normalized hash mismatch')
            resumed=True
        else:
            payload=fetch_json(boxscore_url(gid),cache_dir=folder/'raw')
            known=datetime.now(timezone.utc).isoformat()
            rows=normalize_boxscore(payload,boxscore_url(gid),known)
            digest=None; resumed=False
        validate_history(rows)
        if not rows or {r['game_id'] for r in rows}!={gid}:
            raise ValueError('cached game identity or rows invalid')
        if len({r['team_id'] for r in rows}) != 2:
            raise ValueError('final game lacks two teams')
        if digest is None: digest=_write(path,rows)
        return dict(sha256=digest,rows=len(rows),source_url=boxscore_url(gid),
                    known_at=max(r['known_at'] for r in rows)),resumed

    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending={executor.submit(one,gid):gid for gid in ids}
        for task in as_completed(pending):
            gid=pending[task]
            try:
                info,resumed=task.result()
                result['games'][gid]=info
                result['cache_hashes'][gid]=info['sha256']
                result['completed_games']+=1
                result['resumed_games']+=int(resumed)
            except Exception as exc:
                result['failures'][gid]=f'{type(exc).__name__}: {exc}'
            _write(manifest_path,result)
    result['finished_at']=datetime.now(timezone.utc).isoformat()
    result['status']='incomplete' if result['failures'] else 'complete'
    if not result['failures']:
        rows=[]
        for gid in ids: rows.extend(json.loads((games/(gid+'.json')).read_text()))
        validate_history(rows)
        raw=json.dumps(rows,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        zipped=gzip.compress(raw,mtime=0)
        temporary=export.with_suffix('.gz.tmp'); temporary.write_bytes(zipped); temporary.replace(export)
        result['export']={'path':'history.json.gz','sha256':hashlib.sha256(zipped).hexdigest(),
                          'rows':len(rows),'players':len({r['player_id'] for r in rows}),
                          'seasons':sorted({r['season'] for r in rows})}
    _write(manifest_path,result)
    return result


def backfill_seasons(seasons,directory,workers=4):
    folder=Path(directory); folder.mkdir(parents=True,exist_ok=True)
    ids=[]; indexes=[]
    for season in sorted(set(seasons)):
        url=game_log_url(season)
        payload=fetch_json(url,cache_dir=folder/'raw')
        found=game_ids(payload,season)
        ids.extend(found)
        indexes.append({'season':season,'source_url':url,'games':len(found),
                        'observed_at':datetime.now(timezone.utc).isoformat()})
    _write(folder/'indexes.json',indexes)
    return backfill_games(ids,folder,workers)
