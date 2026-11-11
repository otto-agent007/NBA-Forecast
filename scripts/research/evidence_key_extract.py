"""One read of the live runs in a window -> compact per-run summary for evidence_key_validate.py.

    python3 scripts/research/evidence_key_extract.py OPERATIONS_DIR SINCE UNTIL OUTPUT.json

SINCE/UNTIL are ISO times compared with created_at. Opens the ledger read-only and reads matching rows in
insertion (rowid) order, so SQLite never sorts rows that carry the JSON columns (a sort spill of those
filled the root disk on 2026-11-09). Prefer a copy of production; it reads every run of the window.
"""
import sqlite3, json, sys, hashlib
P=sys.argv[1].rstrip('/')+'/'
lo,hi,out=sys.argv[2],sys.argv[3],sys.argv[4]
conn=sqlite3.connect('file:'+P+'forecast.sqlite?mode=ro',uri=True)
def h(o): return hashlib.sha1(json.dumps(o,sort_keys=True,default=str).encode()).hexdigest()[:12]
rows=[]
for run_id,created,hist,sj,fj in conn.execute("SELECT run_id,created_at,history_sha256,snapshot_json,forecast_json FROM run_records WHERE created_at>=? AND created_at<? AND mode='live' ORDER BY rowid",(lo,hi)):
    s=json.loads(sj); f=json.loads(fj); g=s['games'][0]; teams={g['home_team_id'],g['away_team_id']}
    members=sorted((p['player_id'],p['team_id'],p.get('status'),p.get('reported_status')) for p in s['roster']['players'] if p['team_id'] in teams)
    ids={m[0] for m in members}
    esp=s.get('espn_injury_fallbacks')
    game_key=h(dict(m=members,u={k:v for k,v in (s.get('reported_unavailable') or {}).items() if k in ids},e=esp))
    reports=[[it.get(n,{}) and (it.get(n) or {}).get('sha256') for n in ('anchor_report','latest_report')] for it in s['injury_check']['games']] if s.get('injury_check') else None
    mf=(f.get('margin_forecasts') or [{}])[0]
    rows.append(dict(run_id=run_id,created=created,game=g['game_id'],stage=s.get('forecast_stage'),game_key=game_key,history=hist,
        model=f.get('model_key'),reports=h(reports),league_roster=h(sorted((p['player_id'],p['team_id'],p.get('status')) for p in s['roster']['players'])),
        margin=mf.get('mean_margin'),preds={p['player_id']:[p.get('status'),p.get('points_mean'),p.get('expected_minutes'),p.get('participation_probability')] for p in f['predictions'] if p.get('status')!='no_game'}))
json.dump(rows,open(out,'w'))
print(len(rows),'runs ->',out)
