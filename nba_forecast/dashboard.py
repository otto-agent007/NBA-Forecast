"""Read-only dashboard projection of saved operational evidence; no network fetches."""
from copy import deepcopy
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import sqlite3
import re
from urllib.parse import urlsplit,unquote
from .validation import parse_time
from .rotation import projected_lineups


def read_optional(path,default):
    return json.loads(path.read_text()) if path.exists() else default


def dashboard_data(directory):
    folder=Path(directory)
    schedule=read_optional(folder/'schedule/latest.json',{})
    games=deepcopy(schedule.get('games',[]));by_id={g['game_id']:g for g in games}
    for g in games:
        g['forecast']=None
        g['forecasts']={}
        capture=read_optional(folder/'injuries/games'/g['game_id']/'latest.json',None)
        item=next((i for i in (capture or {}).get('games',[]) if i['game_id']==g['game_id']),None)
        g['injury_capture']=capture if item and g.get('start_time') and parse_time(item['start_time'])==parse_time(g['start_time']) else None
        audit=read_optional(folder/'preflight'/g['game_id']/'latest.json',None)
        g['preflight']=audit if audit and g.get('start_time') and parse_time(audit['start_time'])==parse_time(g['start_time']) else None

    db=folder/'forecast.sqlite';players=[];roster_observed=None;roster_rank=None
    if db.exists():
        # URI mode=ro is important: opening the UI never initializes or edits a ledger.
        with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as conn:
            conn.row_factory=sqlite3.Row
            conn.execute('BEGIN')
            rows=conn.execute("SELECT rowid AS insertion_order,run_id,created_at,snapshot_json,forecast_json FROM runs WHERE mode='live' AND scope='league' ORDER BY created_at,rowid")
            chosen={};stage_chosen={}
            for row in rows:
                snapshot=json.loads(row['snapshot_json']);forecast=json.loads(row['forecast_json'])
                if snapshot['season']!=schedule.get('season'):continue
                roster=snapshot['roster'];rank=(parse_time(roster['observed_at']),row['insertion_order'])
                if roster_rank is None or rank>roster_rank:
                    players=deepcopy(roster['players']);roster_observed=roster['observed_at'];roster_rank=rank
                for saved in snapshot['games']:
                    game=by_id.get(saved['game_id'])
                    if not game or not game.get('start_time') or game.get('status') in ('postponed','cancelled','tbd'):continue
                    if (parse_time(saved['start_time'])!=parse_time(game['start_time']) or
                        any(saved[k]!=game[k] for k in ('home_team_id','away_team_id')) or
                        parse_time(row['created_at'])>=parse_time(game['start_time'])):continue
                    rank=(parse_time(row['created_at']),row['insertion_order'])
                    stage=forecast.get('forecast_stage','preliminary')
                    stage_key=(game['game_id'],stage)
                    if stage_key in stage_chosen and rank<=stage_chosen[stage_key]:continue
                    stage_chosen[stage_key]=rank
                    predictions=[p for p in forecast['predictions'] if p['game_id']==game['game_id']]
                    check=next((c for c in snapshot.get('injury_check',{}).get('games',[]) if c['game_id']==game['game_id']),None)
                    statuses={p['player_id']:p for p in snapshot['roster']['players']}
                    for p in predictions:
                        evidence=statuses.get(p['player_id'],{})
                        p['availability']=evidence.get('reported_status') or evidence.get('status','unknown')
                        p['status_source_url']=evidence.get('status_source_url')
                    roles=read_optional(folder/'runs'/row['run_id']/'profiles.json',{})
                    player_roles={p['player_id']:p for p in roles.get('players',[])}
                    lineups=projected_lineups(snapshot['roster']['players'],roles.get('players',[]))
                    for p in predictions:
                        p['rotation_role']=player_roles.get(p['player_id'],{}).get('rotation_role')
                        p['projected_starter']=p['player_id'] in lineups.get(p['team_id'],{}).get('player_ids',[])
                    view=dict(team_roles=[t for t in roles.get('teams',[]) if t['team_id'] in (game['home_team_id'],game['away_team_id'])],run_id=row['run_id'],created_at=row['created_at'],as_of=forecast['as_of'],
                        stage=forecast.get('forecast_stage','preliminary'),model_version=forecast['model_version'],predictions=predictions,
                        review_queue=[q for q in forecast['review_queue'] if q['game_id']==game['game_id']],
                        injury_check=check,context=snapshot.get('schedule_context',{}),lineups=lineups,
                        preflight=snapshot.get('preflight'),injury_coverage_warnings=snapshot.get('injury_coverage_warnings',[]))
                    game['forecasts'][stage]=view
                    if game['game_id'] not in chosen or rank>chosen[game['game_id']]:
                        chosen[game['game_id']]=rank;game['forecast']=view
    if not players:
        roster=read_optional(folder/'roster.json',{})
        players=roster.get('players',[]);roster_observed=roster.get('observed_at')
    # Roster status from another game's snapshot is not current availability.
    team_codes={g[side+'_team_id']:g.get(side+'_tricode') for g in games for side in ('home','away')}
    team_names={g[side+'_team_id']:g.get(side+'_name') for g in games for side in ('home','away')}
    player_index=read_optional(folder/'players/index.json',None)
    rotations=(player_index or {}).get('rotation_roles',{})
    summaries=(player_index or {}).get('player_summaries',{})
    players=[{**{k:p[k] for k in ('player_id','name','team_id')},'team_code':team_codes.get(p['team_id']),
              'team_name':team_names.get(p['team_id']),'rotation_role':rotations.get(p['player_id']),
              'observed_stats':summaries.get(p['player_id'])} for p in players]
    pool_lineups=projected_lineups(players,players)
    for p in players:p['projected_starter']=p['player_id'] in pool_lineups[p['team_id']]['player_ids']
    return dict(schema_version=1,generated_at=datetime.now(timezone.utc).isoformat(),source_mode='saved_operations',
                season=schedule.get('season'),schedule_observed_at=schedule.get('observed_at'),coverage=schedule.get('coverage',{}),
                games=games,players=players,roster_observed_at=roster_observed,
                runner=read_optional(folder/'last-tick.json',None),
                performance=read_optional(folder/'performance.json',None),benchmarks=read_optional(folder/'benchmarks.json',None),player_data=player_index,
                injury_capture=read_optional(folder/'injuries/status.json',None))


def serve_dashboard(directory,assets,port=8765):
    """Loopback only. No arbitrary file access, directory listing, writes or CORS."""
    root=Path(assets).resolve()
    if not (root/'index.html').is_file():raise ValueError('Build the dashboard first: npm ci --prefix dashboard && npm run build --prefix dashboard')
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path=unquote(urlsplit(self.path).path)
            if path=='/api/dashboard' or re.fullmatch(r'/api/players/[0-9]+',path) or re.fullmatch(r'/api/postgame/002[0-9]{7}',path):
                try:
                    if path.startswith('/api/postgame/'):
                        value=read_optional(Path(directory)/'postgame'/(path.rsplit('/',1)[1]+'.json'),dict(missing_reason='No verified postgame timeline captured yet. Run postgame after syncing player data.'))
                    else:
                        value=dashboard_data(directory) if path=='/api/dashboard' else player_payload(directory,path.rsplit('/',1)[1])
                    body=json.dumps(value,allow_nan=False).encode()
                except (OSError,ValueError,KeyError,sqlite3.Error):
                    self.send_error(503,'Saved dashboard evidence is unavailable');return
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
            else:
                target=(root/('index.html' if path=='/' else path.lstrip('/'))).resolve()
                if not target.is_relative_to(root) or not target.is_file():self.send_error(404);return
                body=target.read_bytes();self.send_response(200)
                self.send_header('Content-Type',mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://cdn.nba.com; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,fmt,*args):pass
    with ThreadingHTTPServer(('127.0.0.1',port),Handler) as server:
        print(f'Dashboard: http://127.0.0.1:{port}',flush=True);server.serve_forever()


def player_payload(directory,player_id):
    if not re.fullmatch(r'[0-9]+',str(player_id)):raise ValueError('Invalid player ID')
    folder=Path(directory);page=read_optional(folder/'players'/(player_id+'.json'),None)
    if page is None:return dict(player_id=player_id,missing_reason='Player history has not been synchronized yet.',prediction_history=[])
    page=deepcopy(page);selected={};db=folder/'forecast.sqlite'
    if db.exists():
        with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as conn:
            conn.row_factory=sqlite3.Row;conn.execute('BEGIN')
            for run in conn.execute("SELECT rowid,run_id,created_at,forecast_json FROM runs WHERE mode='live' AND scope='league' ORDER BY created_at,rowid"):
                f=json.loads(run['forecast_json'])
                if f['season']!=page['season']:continue
                for pred in f['predictions']:
                    if pred['player_id']!=player_id or not pred.get('game_id') or parse_time(run['created_at'])>=parse_time(pred['start_time']):continue
                    # Select before looking at outcomes, even when latest abstains.
                    selected[pred['game_id']]=dict(run_id=run['run_id'],saved_at=run['created_at'],model_version=f['model_version'],prediction=pred)
            for item in selected.values():
                row=conn.execute('SELECT report_json FROM evaluations WHERE run_id=? ORDER BY revision DESC LIMIT 1',(item['run_id'],)).fetchone()
                matched=json.loads(row['report_json'])['matched'] if row else []
                item['actual']=next((r for r in matched if r['player_id']==player_id and r['game_id']==item['prediction']['game_id']),None)
    page['prediction_history']=sorted(selected.values(),key=lambda r:parse_time(r['prediction']['start_time']),reverse=True)[:20]
    return page
