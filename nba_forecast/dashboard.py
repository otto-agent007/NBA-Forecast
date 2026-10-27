"""Read-only dashboard projection of saved operational evidence; no network fetches."""
from contextlib import closing
from copy import deepcopy
import hashlib
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import sqlite3
import re
import threading
from urllib.parse import urlsplit,unquote
from .validation import parse_time
from .rotation import projected_lineups
from .totals import game_totals


def read_optional(path,default):
    return json.loads(path.read_text()) if path.exists() else default


def _current_schedule(folder):
    official=read_optional(folder/'schedule/latest.json',{})
    verified=read_optional(folder/'schedule/secondary/verified-latest.json',None)
    # Show the calendar the runner is using: the day's ESPN-checked copy when it
    # is in use (it is checked once a day), otherwise the saved NBA calendar.
    if verified and official.get('version')==verified.get('crosscheck',{}).get('nba_base_version'):
        last=read_optional(folder/'last-tick.json',{})
        if not last.get('schedule_source') or (last['schedule_source']=='espn_verified_cached_nba'
                                               and last.get('schedule_version')==verified.get('version')):
            return verified
    return official


_CACHE_LOCK=threading.Lock()
_VIEWS={}        # the assembled views for one ledger state and schedule
_RUN_VIEWS={}    # run_id -> the stage views of one immutable run, kept while a game still shows it
_ROSTERS={}      # run_id -> (players, observed_at) of the run whose roster is the newest
_PREDICTIONS={}  # the player pages' index, extended run by run
_PAYLOAD={}      # the page's serialized JSON and its ETag

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


def _forecast_views(folder,db,schedule,compact=False):
    """Latest pregame forecast per game and stage, and the newest saved roster, from caches.

    The assembly is keyed on the ledger's run count and newest rowid, the season,
    and the games' identities and statuses, so a tick that only scores games
    rebuilds nothing. Each chosen run is decoded once and read again only when
    its saved profiles change. Every caller receives its own copy.
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
        return deepcopy(_VIEWS['compact' if compact else 'full'])


def _final_score(folder,game):
    """Team points from the captured final box score (official team total, else player sum), or None."""
    rows=read_optional(folder/'finals'/(game['game_id']+'.json'),None)
    if not isinstance(rows,list):
        return None
    sides={game.get('home_team_id'):'home',game.get('away_team_id'):'away'}
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


def dashboard_data(directory,*,compact=False):
    """Everything the page reads, from saved evidence only.

    ``compact`` is the page's copy: each game's opening forecast is named by
    ``forecast_stage`` instead of repeated, predictions keep the components the
    page shows, and the performance groups drop their per-row detail.
    """
    folder=Path(directory)
    schedule=_current_schedule(folder)
    games=deepcopy(schedule.get('games',[]))
    by_id={g['game_id']:g for g in games}
    for g in games:
        if not compact:
            g['forecast']=None
        g['forecast_stage']=None
        g['forecasts']={}
        # The calendar is checked once a day; a captured final box score shows the game is over.
        if g.get('status') in ('scheduled','live') and (folder/'finals'/(g['game_id']+'.json')).exists():
            g['status']='final'
        g['final_score']=_final_score(folder,g) if g.get('status')=='final' else None
        capture=read_optional(folder/'injuries/games'/g['game_id']/'latest.json',None)
        item=next((i for i in (capture or {}).get('games',[]) if i['game_id']==g['game_id']),None)
        g['injury_capture']=capture if item and g.get('start_time') and parse_time(item['start_time'])==parse_time(g['start_time']) else None
        audit=read_optional(folder/'preflight'/g['game_id']/'latest.json',None)
        g['preflight']=audit if audit and g.get('start_time') and parse_time(audit['start_time'])==parse_time(g['start_time']) else None
        update=read_optional(folder/'availability'/g['game_id']/'latest.json',None)
        g['availability_update']=update if update and g.get('start_time') and parse_time(update['start_time'])==parse_time(g['start_time']) else None

    db=folder/'forecast.sqlite'
    players=[]
    roster_observed=None
    if db.exists():
        views,players,roster_observed=_forecast_views(folder,db,schedule,compact=compact)
        for gid,item in views.items():
            by_id[gid].update(item)
    if not players:
        roster=read_optional(folder/'roster.json',{})
        players=roster.get('players',[])
        roster_observed=roster.get('observed_at')
    # Roster status from another game's snapshot is not current availability.
    team_codes={g[side+'_team_id']:g.get(side+'_tricode') for g in games for side in ('home','away')}
    team_names={g[side+'_team_id']:g.get(side+'_name') for g in games for side in ('home','away')}
    player_index=read_optional(folder/'players/index.json',None)
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
    market_lines=read_optional(folder/'market/index.json',None)
    for g in games:
        g['market']=((market_lines or {}).get('games') or {}).get(g['game_id'])
    model_status=read_optional(folder/'model-status.json',None)
    if model_status:
        model_status['prospective']=read_optional(folder/'prospective-calibration-status.json',None)
        model_status['prospective_margin']=read_optional(folder/'prospective-margin-calibration-status.json',None)
    reviews={p.stem:read_optional(p,None) for p in (folder/'daily-reviews').glob('????-??-??.json')}
    performance=read_optional(folder/'performance.json',None)
    if compact and performance:
        # The page shows each group's metrics, counts and diagnostics, not its matched rows.
        performance=dict(performance,groups=[{k:v for k,v in g.items() if k not in ('matched','selected_forecasts','excluded','pending')}
                                             for g in performance.get('groups',[])])
    return dict(schema_version=1,generated_at=datetime.now(timezone.utc).isoformat(),source_mode='saved_operations',model_upgrade=model_status,daily_reviews=reviews,
                season=schedule.get('season'),schedule_observed_at=schedule.get('observed_at'),
                schedule_crosscheck=schedule.get('crosscheck'),coverage=schedule.get('coverage',{}),
                games=games,players=players,roster_observed_at=roster_observed,
                runner=read_optional(folder/'last-tick.json',None),
                performance=performance,benchmarks=read_optional(folder/'benchmarks.json',None),player_data=player_index,
                injury_capture=read_optional(folder/'injuries/status.json',None),
                market=read_optional(folder/'market/report.json',None),market_lines=market_lines,
                daily_review_recurrence=read_optional(folder/'daily-reviews/recurrence.json',None))


def dashboard_payload(directory):
    """The page's JSON and its ETag.

    ``generated_at`` is when the content last changed, not when it was asked
    for, so a poll that finds nothing new revalidates to 304 and sends no body.
    """
    value=dashboard_data(directory,compact=True)
    stamp=value.pop('generated_at')
    stable=json.dumps(value,allow_nan=False,sort_keys=True).encode()
    etag='"'+hashlib.sha256(stable).hexdigest()[:32]+'"'
    with _CACHE_LOCK:
        if _PAYLOAD.get('etag')!=etag:
            _PAYLOAD.clear()
            _PAYLOAD.update(etag=etag,body=b'{"generated_at":'+json.dumps(stamp).encode()+b','+stable[1:])
        return _PAYLOAD['etag'],_PAYLOAD['body']


def serve_dashboard(directory,assets,port=8765):
    """Loopback only. No arbitrary file access, directory listing, writes or CORS."""
    root=Path(assets).resolve()
    if not (root/'index.html').is_file():
        raise ValueError('Build the dashboard first: npm ci --prefix dashboard && npm run build --prefix dashboard')
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path=unquote(urlsplit(self.path).path)
            if path=='/api/dashboard' or re.fullmatch(r'/api/players/[0-9]+',path) or re.fullmatch(r'/api/postgame/002[0-9]{7}',path):
                etag=None
                try:
                    if path=='/api/dashboard':
                        etag,body=dashboard_payload(directory)
                    else:
                        if path.startswith('/api/postgame/'):
                            value=read_optional(Path(directory)/'postgame'/(path.rsplit('/',1)[1]+'.json'),dict(missing_reason='No verified postgame timeline captured yet. Run postgame after syncing player data.'))
                        else:
                            value=player_payload(directory,path.rsplit('/',1)[1])
                        body=json.dumps(value,allow_nan=False).encode()
                except (OSError,ValueError,KeyError,sqlite3.Error):
                    self.send_error(503,'Saved dashboard evidence is unavailable')
                    return
                if etag and self.headers.get('If-None-Match')==etag:
                    self.send_response(304)
                    self.send_header('ETag',etag)
                    self.send_header('Cache-Control','no-cache')
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Cache-Control','no-cache' if etag else 'no-store')
                if etag:
                    self.send_header('ETag',etag)
            else:
                target=(root/('index.html' if path=='/' else path.lstrip('/'))).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    self.send_error(404)
                    return
                body=target.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type',mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://cdn.nba.com; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,fmt,*args):
            pass
    with ThreadingHTTPServer(('127.0.0.1',port),Handler) as server:
        print(f'Dashboard: http://127.0.0.1:{port}',flush=True)
        server.serve_forever()


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
