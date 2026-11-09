"""Read-only dashboard projection of saved operational evidence; no network fetches."""
from collections import OrderedDict
from contextlib import closing
from copy import deepcopy
import gzip
import hashlib
from datetime import date as calendar_date,datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import sqlite3
import re
import threading
import time
from urllib.parse import parse_qs,urlsplit,unquote
from zoneinfo import ZoneInfo
from .validation import parse_time
from .rotation import projected_lineups
from .totals import game_totals
from . import close_teacher_reference


def read_optional(path,default):
    return json.loads(path.read_text()) if path.exists() else default


def _stamp(path):
    """A file's size, modification time and inode, or None when absent: every saved file is replaced whole, so a rewrite changes it."""
    try:
        info=os.stat(path)
    except OSError:
        return None
    return info.st_size,info.st_mtime_ns,info.st_ino


_READS={}        # (path, transform name) -> (stamp, parsed value): the server's reads, shared and never modified


def _shared_read(path,default,transform=None):
    """``read_optional`` parsed once per file version. The value is shared between payloads; callers must not modify it."""
    slot=(str(path),transform.__name__ if transform else None)
    stamp=_stamp(path)
    if stamp is None:
        _READS.pop(slot,None)
        return default
    saved=_READS.get(slot)
    if saved is None or saved[0]!=stamp:
        value=json.loads(Path(path).read_text())
        saved=_READS[slot]=(stamp,transform(value) if transform else value)
    return saved[1]


def _current_schedule(folder,read=read_optional):
    official=read(folder/'schedule/latest.json',{})
    verified=read(folder/'schedule/secondary/verified-latest.json',None)
    # Show the calendar the runner is using: the day's ESPN-checked copy when it
    # is in use (it is checked once a day), otherwise the saved NBA calendar.
    if verified and official.get('version')==verified.get('crosscheck',{}).get('nba_base_version'):
        last=read(folder/'last-tick.json',{})
        if not last.get('schedule_source') or (last['schedule_source']=='espn_verified_cached_nba'
                                               and last.get('schedule_version')==verified.get('version')):
            return verified
    return official


_CACHE_LOCK=threading.Lock()
_VIEWS={}        # the assembled views for one ledger state and schedule
_RUN_VIEWS={}    # run_id -> the stage views of one immutable run, kept while a game still shows it
_ROSTERS={}      # run_id -> (players, observed_at) of the run whose roster is the newest
_PREDICTIONS={}  # the player pages' index, extended run by run

# The forecast components the page shows (PlayerDetail); the ledger holds every
# audit field, and the full view keeps them for Python callers such as postgame.
VIEW_COMPONENTS={'availability':None,'uncertainty':None,'rotation_role':None,'manual_adjustment':None,
    'role_context':None,'historical_prior':None,
    'game_environment':('expected_possessions','pace_factor','defense_factor','context_factor','reference_pace'),
    'shot_quality':('status','points_delta','points_without_location_adjustment','scoring_changed','method'),
    'opportunities':('minutes_before_adjustment','role_prior_effective_games','prior_effective_games'),
    'opponent':('method','factor','complete_games','opponent_id','caution'),
    'participation':('probability','role_rate','status_factor','reported_status','played','games','prior','method')}
# What a player page's prediction history shows of each saved prediction.
HISTORY_FIELDS=('game_id','player_id','team_id','start_time','status','reason','flags','points_mean','points_sd',
                'points_lower80','points_upper80','expected_minutes','participation_probability')


def _connect(db):
    # URI mode=ro is important: opening the UI never initializes or edits a ledger.
    conn=sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    return conn


def _ledger_key(db,conn):
    """Runs are append-only: their count and newest rowid identify the ledger's forecasts, whatever else was written."""
    count,last=conn.execute("SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM runs WHERE mode='live' AND scope='league'").fetchone()
    return str(Path(db).resolve()),count,last


def _run_summaries(conn,season):
    """One cheap row per live run of the season: when it was saved, its roster time, its stage and the games it forecast."""
    rows=conn.execute("SELECT rowid AS insertion_order,run_id,created_at,"
                      "json_extract(snapshot_json,'$.roster.observed_at') AS roster_observed_at,"
                      "json_extract(snapshot_json,'$.forecast_stage') AS stage,json_extract(snapshot_json,'$.games') AS games "
                      "FROM runs WHERE mode='live' AND scope='league' AND json_extract(snapshot_json,'$.season')=? "
                      "ORDER BY created_at,rowid",(season,))
    return [dict(r,games=json.loads(r['games'])) for r in rows]


def _choose_runs(summaries,by_id):
    """(game_id, stage) -> (rank, run_id) of the newest pregame run that forecast the game as the schedule knows it; the newest roster's run."""
    chosen={}
    roster=None
    for s in summaries:
        rank=(parse_time(s['created_at']),s['insertion_order'])
        if s['roster_observed_at']:
            roster_rank=(parse_time(s['roster_observed_at']),s['insertion_order'])
            if roster is None or roster_rank>roster[0]:
                roster=(roster_rank,s['run_id'])
        for saved in s['games']:
            game=by_id.get(saved['game_id'])
            if not game or not game.get('start_time') or game.get('status') in ('postponed','cancelled','tbd'):
                continue
            if (parse_time(saved['start_time'])!=parse_time(game['start_time']) or
                any(saved[k]!=game[k] for k in ('home_team_id','away_team_id')) or
                rank[0]>=parse_time(game['start_time'])):
                continue
            key=(game['game_id'],s['stage'] or 'preliminary')
            if key not in chosen or rank>chosen[key][0]:
                chosen[key]=(rank,s['run_id'])
    return chosen,(roster[1] if roster else None)


def _profiles_stat(folder,run_id):
    path=folder/'runs'/run_id/'profiles.json'
    if not path.exists():
        return None
    stat=path.stat()
    return stat.st_size,stat.st_mtime_ns


def _decode_run(folder,conn,run_id):
    """Every stage view one immutable run holds, built once; the schedule decides later which still apply."""
    row=conn.execute('SELECT created_at,snapshot_json,forecast_json FROM runs WHERE run_id=?',(run_id,)).fetchone()
    snapshot=json.loads(row['snapshot_json'])
    forecast=json.loads(row['forecast_json'])
    stage=forecast.get('forecast_stage','preliminary')
    roles=read_optional(folder/'runs'/run_id/'profiles.json',{})
    player_roles={p['player_id']:p for p in roles.get('players',[])}
    lineups=projected_lineups(snapshot['roster']['players'],roles.get('players',[]))
    statuses={p['player_id']:p for p in snapshot['roster']['players']}
    views={}
    for game in snapshot['games']:
        predictions=[p for p in forecast['predictions'] if p['game_id']==game['game_id']]
        check=next((c for c in snapshot.get('injury_check',{}).get('games',[]) if c['game_id']==game['game_id']),None)
        for p in predictions:
            evidence=statuses.get(p['player_id'],{})
            p['availability']=evidence.get('reported_status') or evidence.get('status','unknown')
            p['status_source_url']=evidence.get('status_source_url')
            p['rotation_role']=player_roles.get(p['player_id'],{}).get('rotation_role')
            p['projected_starter']=p['player_id'] in lineups.get(p['team_id'],{}).get('player_ids',[])
        views[game['game_id']]=dict(team_roles=[t for t in roles.get('teams',[]) if t['team_id'] in (game['home_team_id'],game['away_team_id'])],
            run_id=run_id,created_at=row['created_at'],as_of=forecast['as_of'],stage=stage,model_version=forecast['model_version'],
            boxscore_model_version=forecast.get('boxscore_model_version'),predictions=predictions,
            totals=game_totals(game,predictions,lineups=snapshot.get('preflight',{}).get('lineups')),
            saved_totals=next((t for t in forecast.get('game_totals',[]) if t['game_id']==game['game_id']),None),
            margin_forecast=next((t for t in forecast.get('margin_forecasts',[]) if t['game_id']==game['game_id']),None),
            review_queue=[q for q in forecast['review_queue'] if q['game_id']==game['game_id']],
            injury_check=check,context=snapshot.get('schedule_context',{}),lineups=lineups,
            preflight=snapshot.get('preflight'),injury_coverage_warnings=snapshot.get('injury_coverage_warnings',[]),
            availability_supplements=snapshot.get('availability_supplements',[]))
    return dict(stage=stage,views=views,profiles_stat=_profiles_stat(folder,run_id))


def _roster(conn,run_id):
    """The newest saved roster: that run's players and observation time, decoded once."""
    if run_id is None:
        return [],None
    if run_id not in _ROSTERS:
        row=conn.execute("SELECT json_extract(snapshot_json,'$.roster.players'),json_extract(snapshot_json,'$.roster.observed_at') "
                         "FROM runs WHERE run_id=?",(run_id,)).fetchone()
        _ROSTERS.clear()
        _ROSTERS[run_id]=(json.loads(row[0]),row[1])
    return _ROSTERS[run_id]


def _compact_prediction(p):
    components=p.get('components') or {}
    kept={}
    for name,fields in VIEW_COMPONENTS.items():
        value=components.get(name)
        if value is None:
            continue
        kept[name]=value if fields is None or not isinstance(value,dict) else {k:value[k] for k in fields if k in value}
    rotation=p.get('rotation_role')
    if isinstance(rotation,dict):
        rotation={k:v for k,v in rotation.items() if k!='game_ids'}
    return dict(p,components=kept,rotation_role=rotation)


def _compact_views(views):
    """The page's copy: the opening forecast as a stage name, predictions trimmed to the components it shows."""
    return {gid:dict(forecast_stage=item['forecast_stage'],
                     forecasts={stage:dict(view,predictions=[_compact_prediction(p) for p in view['predictions']])
                                for stage,view in item['forecasts'].items()}) for gid,item in views.items()}


def _forecast_views(folder,db,schedule,compact=False,only=None,copy=True):
    """Latest pregame forecast per game and stage, and the newest saved roster, from caches.

    The assembly is keyed on the ledger's run count and newest rowid, the season,
    and the games' identities and statuses, so a tick that only scores games
    rebuilds nothing. Each chosen run is decoded once and read again only when
    its saved profiles change. ``only`` limits the views to those game IDs.
    Every caller receives its own copy, unless ``copy`` is false: the server's
    payloads serialize the shared views without modifying them.
    """
    games=[[g['game_id'],g.get('start_time'),g.get('status'),g.get('home_team_id'),g.get('away_team_id')] for g in schedule.get('games',[])]
    by_id={g['game_id']:g for g in schedule.get('games',[])}
    with closing(_connect(db)) as conn,_CACHE_LOCK:
        key=(_ledger_key(db,conn),schedule.get('season'),hashlib.sha256(json.dumps(games).encode()).hexdigest())
        fresh=_VIEWS.get('key')==key and all(_profiles_stat(folder,r)==_RUN_VIEWS[r]['profiles_stat'] for r in _VIEWS['runs'])
        if not fresh:
            chosen,roster_run=_choose_runs(_run_summaries(conn,schedule.get('season')),by_id)
            needed={run_id for _,run_id in chosen.values()}
            for run_id in list(_RUN_VIEWS):
                if run_id not in needed:
                    del _RUN_VIEWS[run_id]
            for run_id in sorted(needed):
                if run_id not in _RUN_VIEWS or _RUN_VIEWS[run_id]['profiles_stat']!=_profiles_stat(folder,run_id):
                    _RUN_VIEWS[run_id]=_decode_run(folder,conn,run_id)
            views={}
            for (gid,stage),(rank,run_id) in sorted(chosen.items()):
                view=_RUN_VIEWS[run_id]['views'].get(gid)
                if view is not None:
                    views.setdefault(gid,dict(forecast=None,forecast_stage=None,forecasts={}))['forecasts'][stage]=view
            for gid,item in views.items():
                # The page opens on the final-stage forecast when one is saved, else the newest.
                item['forecast_stage']=max(item['forecasts'],key=lambda stage:(stage=='final',chosen[(gid,stage)][0]))
                item['forecast']=item['forecasts'][item['forecast_stage']]
            players,observed=_roster(conn,roster_run)
            _VIEWS.clear()
            _VIEWS.update(key=key,runs=sorted(needed),full=(views,players,observed),
                                         compact=(_compact_views(views),players,observed))
        views,players,observed=_VIEWS['compact' if compact else 'full']
        if only is not None:
            views={gid:item for gid,item in views.items() if gid in only}
        return deepcopy((views,players,observed)) if copy else (views,list(players),observed)


def _final_score(folder,game,read=read_optional):
    """Team points from the captured final box score (official team total, else player sum), or None."""
    rows=read(folder/'finals'/(game['game_id']+'.json'),None)
    return _score(rows,game.get('home_team_id'),game.get('away_team_id'))


def _score(rows,home_team_id,away_team_id):
    if not isinstance(rows,list):
        return None
    sides={home_team_id:'home',away_team_id:'away'}
    official,summed={},{}
    periods=0
    for r in rows:
        side=sides.get(r.get('team_id'))
        if not side:
            continue
        summed[side]=summed.get(side,0)+(r.get('pts') or 0)
        periods=max(periods,r.get('game_period') or 0)
        pts=(r.get('team_boxscore') or {}).get('pts')
        if isinstance(pts,int):
            official.setdefault(side,pts)
    if set(summed)!={'home','away'}:
        return None
    return dict(home=official.get('home',summed['home']),away=official.get('away',summed['away']),periods=periods or None)


_SCORES={}       # finals path -> (stamp, team ids, final score): the server's final scores, one parse per box score version


def _shared_final_score(folder,game,read=None):
    path=folder/'finals'/(game['game_id']+'.json')
    stamp=_stamp(path)
    teams=(game.get('home_team_id'),game.get('away_team_id'))
    saved=_SCORES.get(str(path))
    if saved is None or saved[:2]!=(stamp,teams):
        saved=_SCORES[str(path)]=(stamp,teams,_score(read_optional(path,None),*teams) if stamp else None)
    return deepcopy(saved[2])


PACIFIC=ZoneInfo('America/Los_Angeles')
EASTERN=ZoneInfo('America/New_York')
# The per-game fields a game day's page needs and the season's calendar does not:
# the page's payload carries them for one day, /api/forecasts for any other.
DETAIL_FIELDS=('forecast','forecast_stage','forecasts','injury_capture','preflight','availability_update')


def game_day(game):
    """The page's calendar day for a game (``gameDay`` in data.js): the Pacific date of
    its tipoff, or the New York date of the source time while tipoff is unconfirmed."""
    if game.get('start_time'):
        return parse_time(game['start_time']).astimezone(PACIFIC).date().isoformat()
    if game.get('source_start_time'):
        return parse_time(game['source_start_time']).astimezone(EASTERN).date().isoformat()
    return None


def default_day(games,now=None):
    """The day the page opens on (``initialDay`` in data.js): the next scheduled tipoff's,
    else today when it has games, else the season's latest day."""
    now=now or datetime.now(timezone.utc)
    today=now.astimezone(PACIFIC).date().isoformat()
    days=sorted({d for d in map(game_day,games) if d})
    upcoming=sorted((g for g in games if g.get('status')=='scheduled' and g.get('start_time') and parse_time(g['start_time'])>now),
                    key=lambda g:g['start_time'])
    return game_day(upcoming[0]) if upcoming else today if today in days else days[-1] if days else today


def _compact_performance(performance):
    """The page shows each group's metrics, counts and diagnostics, not its matched rows."""
    if not performance:
        return performance
    return dict(performance,groups=[{k:v for k,v in g.items() if k not in ('matched','selected_forecasts','excluded','pending')}
                                    for g in performance.get('groups',[])])


def _games(folder,schedule,*,compact,day=None,day_only=False,shared=False):
    """The schedule's games with their status and final score, and the detail fields (forecasts,
    injury capture, preflight audit, availability update) for every game, or for ``day``'s only.
    ``day_only`` leaves the other days' games out. Returns (games, detailed IDs, players, roster time)."""
    read=_shared_read if shared else read_optional
    score=_shared_final_score if shared else _final_score
    games=[deepcopy(g) for g in schedule.get('games',[]) if not day_only or game_day(g)==day]
    by_id={g['game_id']:g for g in games}
    detailed=set()
    for g in games:
        detail=day is None or game_day(g)==day
        if detail:
            if not compact:
                g['forecast']=None
            g['forecast_stage']=None
            g['forecasts']={}
        # The calendar is checked once a day; a captured final box score shows the game is over.
        if g.get('status') in ('scheduled','live') and (folder/'finals'/(g['game_id']+'.json')).exists():
            g['status']='final'
        g['final_score']=score(folder,g,read) if g.get('status')=='final' else None
        if not detail:
            continue
        detailed.add(g['game_id'])
        capture=read(folder/'injuries/games'/g['game_id']/'latest.json',None)
        item=next((i for i in (capture or {}).get('games',[]) if i['game_id']==g['game_id']),None)
        g['injury_capture']=capture if item and g.get('start_time') and parse_time(item['start_time'])==parse_time(g['start_time']) else None
        audit=read(folder/'preflight'/g['game_id']/'latest.json',None)
        g['preflight']=audit if audit and g.get('start_time') and parse_time(audit['start_time'])==parse_time(g['start_time']) else None
        update=read(folder/'availability'/g['game_id']/'latest.json',None)
        g['availability_update']=update if update and g.get('start_time') and parse_time(update['start_time'])==parse_time(g['start_time']) else None
    db=folder/'forecast.sqlite'
    players=[]
    roster_observed=None
    if db.exists():
        views,players,roster_observed=_forecast_views(folder,db,schedule,compact=compact,
                                                      only=None if day is None else detailed,copy=not shared)
        for gid,item in views.items():
            by_id[gid].update(item)
    return games,detailed,players,roster_observed


def dashboard_data(directory,*,compact=False,day=None,reviews=True,shared=False):
    """Everything the page reads, from saved evidence only.

    ``compact`` is the page's copy: each game's opening forecast is named by
    ``forecast_stage`` instead of repeated, predictions keep the components the
    page shows, and the performance groups drop their per-row detail.

    The server's payload passes ``day`` (only that day's games carry
    ``DETAIL_FIELDS``; ``detail_days`` names it), ``reviews=False`` (the daily
    reviews are listed in ``daily_review_dates`` and fetched one night at a time)
    and ``shared`` (files parsed once per version, views not copied: the value
    is serialized, never modified). The defaults return every game's details and
    every review, as Python callers and the static capture expect.
    """
    folder=Path(directory)
    read=_shared_read if shared else read_optional
    schedule=_current_schedule(folder,read)
    games,_,players,roster_observed=_games(folder,schedule,compact=compact,day=day,shared=shared)
    if not players:
        roster=read(folder/'roster.json',{})
        players=list(roster.get('players',[]))
        roster_observed=roster.get('observed_at')
    # Roster status from another game's snapshot is not current availability.
    team_codes={g[side+'_team_id']:g.get(side+'_tricode') for g in games for side in ('home','away')}
    team_names={g[side+'_team_id']:g.get(side+'_name') for g in games for side in ('home','away')}
    player_index=read(folder/'players/index.json',None)
    rotations=(player_index or {}).get('rotation_roles',{})
    summaries=(player_index or {}).get('player_summaries',{})
    current_ids={p['player_id'] for p in players}
    for pid,summary in summaries.items():
        if pid not in current_ids and summary.get('roster_membership')=='historical_appearance' and summary.get('name') and summary.get('team_id'):
            players.append(dict(player_id=pid,name=summary['name'],team_id=summary['team_id'],roster_membership='historical_appearance'))
    players=[{**{k:p[k] for k in ('player_id','name','team_id')},'roster_membership':p.get('roster_membership','current'),'team_code':team_codes.get(p['team_id']),
              'team_name':team_names.get(p['team_id']),'rotation_role':rotations.get(p['player_id']),
              'observed_stats':summaries.get(p['player_id'])} for p in players]
    pool_lineups=projected_lineups([p for p in players if p['roster_membership']!='historical_appearance'],players)
    for p in players:
        p['projected_starter']=p['player_id'] in pool_lineups.get(p['team_id'],{}).get('player_ids',[])
    market_lines=read(folder/'market/index.json',None)
    for g in games:
        g['market']=((market_lines or {}).get('games') or {}).get(g['game_id'])
    model_status=read(folder/'model-status.json',None)
    if model_status:
        model_status=dict(model_status)
        model_status['prospective']=read(folder/'prospective-calibration-status.json',None)
        model_status['prospective_margin']=read(folder/'prospective-margin-calibration-status.json',None)
    review_paths=sorted((folder/'daily-reviews').glob('????-??-??.json'))
    daily_reviews={p.stem:read(p,None) for p in review_paths} if reviews else {}
    if compact:
        performance=(_shared_read(folder/'performance.json',None,_compact_performance) if shared else
                     (lambda value:_compact_performance(value) if value else value)(read_optional(folder/'performance.json',None)))
    else:
        performance=read(folder/'performance.json',None)
    value=dict(schema_version=1,generated_at=datetime.now(timezone.utc).isoformat(),source_mode='saved_operations',model_upgrade=model_status,daily_reviews=daily_reviews,
               season=schedule.get('season'),schedule_observed_at=schedule.get('observed_at'),
               schedule_crosscheck=schedule.get('crosscheck'),coverage=schedule.get('coverage',{}),
               games=games,players=players,roster_observed_at=roster_observed,
               runner=read(folder/'last-tick.json',None),
               performance=performance,benchmarks=read(folder/'benchmarks.json',None),player_data=player_index,
               injury_capture=read(folder/'injuries/status.json',None),
               market=read(folder/'market/report.json',None),market_lines=market_lines,
               close_teacher=read(folder/'market/close-teacher.json',close_teacher_reference.REPORT),
               daily_review_recurrence=read(folder/'daily-reviews/recurrence.json',None))
    if day is not None:
        value['detail_days']=[day]
    if not reviews:
        value['daily_review_dates']=[p.stem for p in review_paths]
    return value


# The server's payloads: each is rebuilt only when a stamp of a file it reads
# changes, serialized and hashed once, and gzip-compressed once when asked.
_PAYLOAD_LOCK=threading.Lock()
_PAYLOADS=OrderedDict()   # (folder, kind, day) -> entry
PAYLOAD_ENTRIES=16
# The page payload's files besides the per-game ones; the ledger's files cover runs and scoring alike.
BASE_FILES=('schedule/latest.json','schedule/secondary/verified-latest.json','last-tick.json','roster.json',
            'players/index.json','market/index.json','model-status.json','prospective-calibration-status.json',
            'prospective-margin-calibration-status.json','performance.json','benchmarks.json','injuries/status.json',
            'market/report.json','market/close-teacher.json','daily-reviews/recurrence.json')
DAY_FILES=('schedule/latest.json','schedule/secondary/verified-latest.json','last-tick.json')
DAY_PATTERN=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')


class BadRequest(ValueError):
    """A malformed request parameter: the server answers 400, not 503."""


def valid_day(value):
    """A calendar date in YYYY-MM-DD form, or BadRequest."""
    if not isinstance(value,str) or not DAY_PATTERN.fullmatch(value):
        raise BadRequest('Expected a date as YYYY-MM-DD')
    try:
        calendar_date.fromisoformat(value)
    except ValueError as exc:
        raise BadRequest('Expected a calendar date') from exc
    return value


def _ledger_stamps(folder):
    """The ledger file and its write-ahead log; an empty log (as a reader leaves it) is the same as none."""
    wal=_stamp(folder/'forecast.sqlite-wal')
    return _stamp(folder/'forecast.sqlite'),wal if wal and wal[0] else None


def _detail_files(folder,game_id):
    return (folder/'injuries/games'/game_id/'latest.json',folder/'preflight'/game_id/'latest.json',
            folder/'availability'/game_id/'latest.json')


def _run_files(folder,games):
    """The profiles files of the runs a built payload shows; a rewritten one changes its views."""
    return sorted({folder/'runs'/view['run_id']/'profiles.json' for g in games
                   for view in (g.get('forecasts') or {}).values() if isinstance(view,dict) and view.get('run_id')})


def _details(game):
    return {k:game[k] for k in DETAIL_FIELDS if k in game}


def _cached(slot,key,build,stamped=True):
    """The entry for ``slot`` when its key still matches, else a rebuilt one.

    ``build()`` returns the value and the files whose stamps the next check
    adds. A ``stamped`` value's ``generated_at`` stays out of the ETag and is
    replaced only when the rest of the content changes.
    """
    entry=_PAYLOADS.get(slot)
    if entry is not None and entry['key']==key and all(_stamp(p)==s for p,s in entry['extra']):
        _PAYLOADS.move_to_end(slot)
        return entry
    started=time.time_ns()
    value,extra=build()
    stamp=value.pop('generated_at',None) if stamped else None
    stable=json.dumps(value,allow_nan=False,sort_keys=True).encode()
    etag='"'+hashlib.sha256(stable).hexdigest()[:32]+'"'
    if entry is None or entry['etag']!=etag:
        # ``generated_at`` is when the content last changed, not when it was asked for.
        body=b'{"generated_at":'+json.dumps(stamp).encode()+b','+stable[1:] if stamp else stable
        entry=dict(etag=etag,body=body,gzip=None)
    else:
        entry=dict(entry)
    # The extra files are known only after the build. One written during it (a run's profiles
    # are saved just after the run) may postdate what the build read: it is checked again next time.
    entry.update(key=key,extra=[(p,_unseen(p,started)) for p in extra])
    _PAYLOADS[slot]=entry
    _PAYLOADS.move_to_end(slot)
    while len(_PAYLOADS)>PAYLOAD_ENTRIES:
        _PAYLOADS.popitem(last=False)
    return entry


def _unseen(path,started):
    """``path``'s stamp, or None when it was modified within a second of the build starting or later."""
    stamp=_stamp(path)
    return None if stamp is not None and stamp[1]>=started-1_000_000_000 else stamp


def _light_games(folder):
    """The calendar's games with the final status a captured box score gives them, and the stamps of those box scores."""
    schedule=_current_schedule(folder,_shared_read)
    games=[]
    finals=[]
    for g in schedule.get('games',[]):
        stamp=_stamp(folder/'finals'/(g['game_id']+'.json'))
        finals.append(stamp)
        status='final' if g.get('status') in ('scheduled','live') and stamp else g.get('status')
        games.append(dict(g,status=status))
    return schedule,games,tuple(finals)


def _base_entry(folder,now=None):
    _,games,finals=_light_games(folder)
    day=default_day(games,now)
    details=tuple(_stamp(p) for g in games if game_day(g)==day for p in _detail_files(folder,g['game_id']))
    reviews=tuple(p.name for p in sorted((folder/'daily-reviews').glob('????-??-??.json')))
    key=(tuple(_stamp(folder/name) for name in BASE_FILES),_ledger_stamps(folder),finals,day,details,reviews)
    def build():
        value=dashboard_data(folder,compact=True,day=day,reviews=False,shared=True)
        value['default_day']=day
        return value,_run_files(folder,value['games'])
    return _cached((str(folder),'page',None),key,build)


def _full_entry(folder):
    """The whole payload in main's shape (every game's details and every review), for a page that
    does not ask for one day: a tab opened before a deploy, or a page whose build was skipped."""
    _,games,finals=_light_games(folder)
    details=tuple(_stamp(p) for g in games for p in _detail_files(folder,g['game_id']))
    reviews=tuple((p.name,_stamp(p)) for p in sorted((folder/'daily-reviews').glob('????-??-??.json')))
    key=(tuple(_stamp(folder/name) for name in BASE_FILES),_ledger_stamps(folder),finals,details,reviews)
    def build():
        value=dashboard_data(folder,compact=True,shared=True)
        return value,_run_files(folder,value['games'])
    return _cached((str(folder),'full',None),key,build)


def _day_entry(folder,day):
    _,games,finals=_light_games(folder)
    ids=[g['game_id'] for g in games if game_day(g)==day]
    details=tuple(_stamp(p) for gid in ids for p in _detail_files(folder,gid))
    finals=tuple(_stamp(folder/'finals'/(gid+'.json')) for gid in ids)
    key=(tuple(_stamp(folder/name) for name in DAY_FILES),_ledger_stamps(folder),ids,finals,details)
    def build():
        schedule=_current_schedule(folder,_shared_read)
        games,_,_,_=_games(folder,schedule,compact=True,day=day,day_only=True,shared=True)
        return (dict(schema_version=1,generated_at=datetime.now(timezone.utc).isoformat(),date=day,
                     games={g['game_id']:_details(g) for g in games}),_run_files(folder,games))
    return _cached((str(folder),'day',day),key,build)


def _review_entry(folder,day):
    path=folder/'daily-reviews'/(day+'.json')
    stamp=_stamp(path)
    if stamp is None:
        return None
    def build():
        return json.loads(path.read_text()),[]
    return _cached((str(folder),'review',day),stamp,build,stamped=False)


def dashboard_payload(directory,now=None,full=False):
    """The page's JSON and its ETag: the season's calendar, one game day's details (the day the
    page opens on), the review dates and every summary report (``/api/dashboard?view=day``).
    ``full`` is plain ``/api/dashboard``: every game's details and every review, as before.

    ``generated_at`` is when the content last changed, not when it was asked
    for, so a poll that finds nothing new revalidates to 304 and sends no body.
    """
    with _PAYLOAD_LOCK:
        entry=_full_entry(Path(directory)) if full else _base_entry(Path(directory),now)
        return entry['etag'],entry['body']


def forecasts_payload(directory,day):
    """One game day's detail fields by game ID (``/api/forecasts?date=``) and its ETag."""
    with _PAYLOAD_LOCK:
        entry=_day_entry(Path(directory),valid_day(day))
        return entry['etag'],entry['body']


def review_payload(directory,day):
    """One night's daily review (``/api/daily-reviews/<date>``) and its ETag, or None when none is saved."""
    with _PAYLOAD_LOCK:
        entry=_review_entry(Path(directory),valid_day(day))
        return (entry['etag'],entry['body']) if entry else None


def _gzip(entry):
    with _PAYLOAD_LOCK:
        if entry['gzip'] is None:
            entry['gzip']=gzip.compress(entry['body'],compresslevel=6,mtime=0)
        return entry['gzip']


def _etag_matches(header,etag):
    """Weak comparison (RFC 9110): a gzip response's ETag is the same validator marked weak."""
    if not header:
        return False
    tags={t.strip().removeprefix('W/') for t in header.split(',')}
    return '*' in tags or etag in tags


def serve_dashboard(directory,assets,port=8765):
    """Loopback only. No arbitrary file access, directory listing, writes or CORS."""
    with make_server(directory,assets,port) as server:
        print(f'Dashboard: http://127.0.0.1:{server.server_address[1]}',flush=True)
        server.serve_forever()


def make_server(directory,assets,port=8765):
    """The dashboard's HTTP server bound to the loopback address (port 0 picks a free one), not yet serving."""
    root=Path(assets).resolve()
    folder=Path(directory)
    if not (root/'index.html').is_file():
        raise ValueError('Build the dashboard first: npm ci --prefix dashboard && npm run build --prefix dashboard')
    class Handler(BaseHTTPRequestHandler):
        def _entry(self,path,query):
            """The cached entry for a payload path, None for a missing review; ValueError for a malformed request."""
            if path=='/api/dashboard':
                views=query.get('view',[])
                if views not in ([],['day']):
                    raise BadRequest('Expected view=day or no view')
                with _PAYLOAD_LOCK:
                    return _base_entry(folder) if views else _full_entry(folder)
            if path=='/api/forecasts':
                days=query.get('date',[])
                if len(days)!=1:
                    raise BadRequest('Expected one date parameter')
                day=valid_day(days[0])
                with _PAYLOAD_LOCK:
                    return _day_entry(folder,day)
            day=valid_day(path.rsplit('/',1)[1])
            with _PAYLOAD_LOCK:
                return _review_entry(folder,day)
        def do_GET(self):
            parts=urlsplit(self.path)
            path=unquote(parts.path)
            accepts_gzip='gzip' in (self.headers.get('Accept-Encoding') or '')
            encoding=None
            if path in ('/api/dashboard','/api/forecasts') or re.fullmatch(r'/api/daily-reviews/[^/]+',path):
                try:
                    entry=self._entry(path,parse_qs(parts.query,keep_blank_values=True))
                except BadRequest as exc:
                    self.send_error(400,str(exc))
                    return
                except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
                    self.send_error(503,'Saved dashboard evidence is unavailable')
                    return
                if entry is None:
                    self.send_error(404,'No daily review saved for this date')
                    return
                etag=entry['etag']
                if accepts_gzip:
                    etag='W/'+etag
                if _etag_matches(self.headers.get('If-None-Match'),entry['etag']):
                    self.send_response(304)
                    self.send_header('ETag',etag)
                    self.send_header('Cache-Control','no-cache')
                    self.send_header('Vary','Accept-Encoding')
                    self.end_headers()
                    return
                if accepts_gzip:
                    body=_gzip(entry)
                    encoding='gzip'
                else:
                    body=entry['body']
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Cache-Control','no-cache')
                self.send_header('ETag',etag)
                self.send_header('Vary','Accept-Encoding')
            elif re.fullmatch(r'/api/players/[0-9]+',path) or re.fullmatch(r'/api/postgame/002[0-9]{7}',path):
                try:
                    if path.startswith('/api/postgame/'):
                        value=read_optional(folder/'postgame'/(path.rsplit('/',1)[1]+'.json'),dict(missing_reason='No verified postgame timeline captured yet. Run postgame after syncing player data.'))
                    else:
                        value=player_payload(directory,path.rsplit('/',1)[1])
                    body=json.dumps(value,allow_nan=False).encode()
                except (OSError,ValueError,KeyError,sqlite3.Error):
                    self.send_error(503,'Saved dashboard evidence is unavailable')
                    return
                if accepts_gzip and len(body)>1024:
                    body=gzip.compress(body,compresslevel=6,mtime=0)
                    encoding='gzip'
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Cache-Control','no-store')
                self.send_header('Vary','Accept-Encoding')
            else:
                target=(root/('index.html' if path=='/' else path.lstrip('/'))).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    self.send_error(404)
                    return
                body=target.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type',mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            if encoding:
                self.send_header('Content-Encoding',encoding)
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://cdn.nba.com; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,fmt,*args):
            pass
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def _selected_predictions(db,season,player_id):
    """One player's latest pregame prediction per game, from an index extended run by run.

    Selection is made before any outcome is read, even when the latest abstains.
    Each saved prediction keeps the fields a player page shows.
    """
    with closing(_connect(db)) as conn,_CACHE_LOCK:
        key=_ledger_key(db,conn)
        if _PREDICTIONS.get('path')!=key[0]:
            _PREDICTIONS.clear()
            _PREDICTIONS.update(path=key[0],seen=0,index={})
        if _PREDICTIONS.get('key')!=key:
            index=_PREDICTIONS['index']
            for run in conn.execute("SELECT rowid,run_id,created_at,forecast_json FROM runs WHERE mode='live' AND scope='league' "
                                    "AND rowid>? ORDER BY rowid",(_PREDICTIONS['seen'],)):
                f=json.loads(run['forecast_json'])
                rank=(parse_time(run['created_at']),run['rowid'])
                for pred in f['predictions']:
                    if not pred.get('game_id') or rank[0]>=parse_time(pred['start_time']):
                        continue
                    slot=index.setdefault((f['season'],pred['player_id']),{})
                    previous=slot.get(pred['game_id'])
                    if previous is None or rank>previous['rank']:
                        slot[pred['game_id']]=dict(rank=rank,run_id=run['run_id'],saved_at=run['created_at'],model_version=f['model_version'],
                                                   prediction={k:pred.get(k) for k in HISTORY_FIELDS})
                _PREDICTIONS['seen']=max(_PREDICTIONS['seen'],run['rowid'])
            _PREDICTIONS['key']=key
        return deepcopy({gid:{k:v for k,v in item.items() if k!='rank'}
                         for gid,item in _PREDICTIONS['index'].get((season,player_id),{}).items()})


def player_payload(directory,player_id):
    if not re.fullmatch(r'[0-9]+',str(player_id)):
        raise ValueError('Invalid player ID')
    folder=Path(directory)
    page=read_optional(folder/'players'/(player_id+'.json'),None)
    if page is None:
        return dict(player_id=player_id,missing_reason='Player history has not been synchronized yet.',prediction_history=[])
    page=deepcopy(page)
    selected={}
    db=folder/'forecast.sqlite'
    if db.exists():
        selected=_selected_predictions(db,page['season'],player_id)
        with closing(_connect(db)) as conn:
            for item in selected.values():
                row=conn.execute('SELECT report_json FROM evaluations WHERE run_id=? ORDER BY revision DESC LIMIT 1',(item['run_id'],)).fetchone()
                matched=json.loads(row['report_json'])['matched'] if row else []
                item['actual']=next((r for r in matched if r['player_id']==player_id and r['game_id']==item['prediction']['game_id']),None)
    page['prediction_history']=sorted(selected.values(),key=lambda r:parse_time(r['prediction']['start_time']),reverse=True)[:20]
    return page
