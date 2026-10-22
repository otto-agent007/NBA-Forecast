"""Read-only dashboard projection of saved operational evidence; no network fetches."""
from copy import deepcopy
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit,unquote
from .validation import parse_time


def read_optional(path,default):
    return json.loads(path.read_text()) if path.exists() else default


def dashboard_data(directory):
    folder=Path(directory)
    schedule=read_optional(folder/'schedule/latest.json',{})
    games=deepcopy(schedule.get('games',[]));by_id={g['game_id']:g for g in games}
    for g in games:g['forecast']=None
    db=folder/'forecast.sqlite';players=[];roster_observed=None;roster_rank=None
    if db.exists():
        # URI mode=ro is important: opening the UI never initializes or edits a ledger.
        with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as conn:
            conn.row_factory=sqlite3.Row
            conn.execute('BEGIN')
            rows=conn.execute("SELECT rowid AS insertion_order,run_id,created_at,snapshot_json,forecast_json FROM runs WHERE mode='live' AND scope='league' ORDER BY created_at,rowid").fetchall()
            chosen={}
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
                    if game['game_id'] in chosen and rank<=chosen[game['game_id']]:continue
                    chosen[game['game_id']]=rank
                    predictions=[p for p in forecast['predictions'] if p['game_id']==game['game_id']]
                    check=next((c for c in snapshot.get('injury_check',{}).get('games',[]) if c['game_id']==game['game_id']),None)
                    statuses={p['player_id']:p for p in snapshot['roster']['players']}
                    for p in predictions:
                        evidence=statuses.get(p['player_id'],{})
                        p['availability']=evidence.get('reported_status') or evidence.get('status','unknown')
                        p['status_source_url']=evidence.get('status_source_url')
                    game['forecast']=dict(run_id=row['run_id'],created_at=row['created_at'],as_of=forecast['as_of'],
                        stage=forecast.get('forecast_stage','preliminary'),model_version=forecast['model_version'],predictions=predictions,
                        review_queue=[q for q in forecast['review_queue'] if q['game_id']==game['game_id']],
                        injury_check=check,context=snapshot.get('schedule_context',{}))
    # Roster status from another game's snapshot is not current availability.
    players=[{k:p[k] for k in ('player_id','name','team_id')} for p in players]
    return dict(schema_version=1,generated_at=datetime.now(timezone.utc).isoformat(),source_mode='saved_operations',
                season=schedule.get('season'),schedule_observed_at=schedule.get('observed_at'),coverage=schedule.get('coverage',{}),
                games=games,players=players,roster_observed_at=roster_observed,
                runner=read_optional(folder/'last-tick.json',None),
                performance=read_optional(folder/'performance.json',None),benchmarks=read_optional(folder/'benchmarks.json',None))


def serve_dashboard(directory,assets,port=8765):
    """Loopback only. No arbitrary file access, directory listing, writes or CORS."""
    root=Path(assets).resolve()
    if not (root/'index.html').is_file():raise ValueError('Build the dashboard first: npm ci --prefix dashboard && npm run build --prefix dashboard')
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path=unquote(urlsplit(self.path).path)
            if path=='/api/dashboard':
                try:body=json.dumps(dashboard_data(directory),allow_nan=False).encode()
                except (OSError,ValueError,KeyError,sqlite3.Error):
                    self.send_error(503,'Saved dashboard evidence is unavailable');return
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
            else:
                target=(root/('index.html' if path=='/' else path.lstrip('/'))).resolve()
                if not target.is_relative_to(root) or not target.is_file():self.send_error(404);return
                body=target.read_bytes();self.send_response(200)
                self.send_header('Content-Type',mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,fmt,*args):pass
    with ThreadingHTTPServer(('127.0.0.1',port),Handler) as server:
        print(f'Dashboard: http://127.0.0.1:{port}',flush=True);server.serve_forever()
