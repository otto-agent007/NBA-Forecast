"""Real-data check of the per-game evidence key on a COPY of production (never point it at production).

Run from the checkout under test, after the slate:
  python3 scripts/research/evidence_key_verify_tick.py \
      --directory /path/to/production-copy \
      --history datasets/regular-season-expanded-2026-10-24-home/history.json.gz \
      --date 2026-11-09

It reads only (no forecast, no network, no writes) and checks:
 1. Report part, with the real parser: for each game of --date, every archived check of that game
    (injuries/games/GAME/records/*.json, in checked_at order) is keyed with operations._report_state against
    the saved PDF in raw/ and the copy's roster. Prints, per game and stage, how many checks / distinct
    editions there were and how many times the game's report state changed: the runs the branch would make
    for report changes alone, against one per edition before. Also lists each change (which rows changed).
 2. League part: operations._league_inputs on the merged history (input history + finals/, as the tick
    merges it) and the active artifact, timed, twice: the digest must be identical (deterministic) and the
    cost small (it runs once per tick when jobs are due).
 3. Every game's full evidence key is computed once with the last archived check (stable inputs), twice,
    and must be identical.
"""
import argparse, gzip, json, sys, time
from collections import defaultdict
from pathlib import Path

ap=argparse.ArgumentParser()
ap.add_argument('--directory',required=True); ap.add_argument('--history',nargs='*',default=[]); ap.add_argument('--date',required=True)
args=ap.parse_args()
folder=Path(args.directory)
assert 'data/operations/production' not in str(folder.resolve()), 'point this at a copy, not production'
sys.path.insert(0,'.')
from nba_forecast import operations
from nba_forecast.validation import parse_time
from zoneinfo import ZoneInfo

def load(path):
    opener=gzip.open if str(path).endswith('.gz') else open
    with opener(path,'rt') as f:
        return json.load(f)
history=[r for p in args.history for r in load(p)]
errors=[]
history=operations._merge_finals(history,folder/'finals',errors=errors)
schedule=json.loads((folder/'schedule/latest.json').read_text())
roster=json.loads((folder/'roster.json').read_text())
pointer=json.loads((folder/'model-upgrade-active.json').read_text())
artifact=json.loads((folder/'model-artifacts'/(pointer['artifact_sha256']+'.json')).read_text())  # unverified: the key reads team_games only
pacific=ZoneInfo('America/Los_Angeles')
games=[g for g in schedule['games'] if g.get('start_time') and parse_time(g['start_time']).astimezone(pacific).date().isoformat()==args.date]
print('merged history rows %d (finals errors %d); games on %s: %d'%(len(history),len(errors),args.date,len(games)))

print('\n1. Report part per game and stage (real parser, saved PDFs)')
parsed={}
total_checks=total_editions=total_changes=0
for g in games:
    records=[]
    for p in sorted((folder/'injuries/games'/g['game_id']/'records').glob('*.json')):
        r=json.loads(p.read_text()); records.append(r)
    records.sort(key=lambda r:parse_time(r['checked_at']))
    by_stage=defaultdict(list)
    for r in records: by_stage[r.get('forecast_stage')].append(r)
    for stage,rs in by_stage.items():
        editions=set(); last=None; changes=[]
        for r in rs:
            item=r['games'][0]; ev=item.get('latest_report') or item.get('anchor_report') or {}
            editions.add(ev.get('sha256'))
            state=operations._report_state(folder/'raw',r,g,schedule,roster,parsed,parse_time(r['checked_at']))
            if state!=last:
                if last is not None:
                    a={json.dumps(x,sort_keys=True) for x in last.get('rows',[])}; b={json.dumps(x,sort_keys=True) for x in state.get('rows',[])}
                    changes.append((r['checked_at'][11:16],len(b-a),len(a-b),sorted(k for k in state if state.get(k)!=last.get(k))))
                last=state
        total_checks+=len(rs); total_editions+=len(editions); total_changes+=1+len(changes)
        print('  %s %-11s checks %3d editions %3d -> report-state runs %2d %s'%(g['game_id'],stage,len(rs),len(editions),1+len(changes),changes[:6]))
print('  total: checks %d, distinct editions %d, report-state runs %d'%(total_checks,total_editions,total_changes))

print('\n2. League inputs')
for i in (1,2):
    t=time.monotonic(); d=operations._league_inputs(folder,history,artifact,operations._now()); print('  pass %d: %s in %.2f s'%(i,d[:16],time.monotonic()-t))

print('\n3. Full keys twice with each game\'s last archived check')
capture=json.loads((folder/'market/capture-state.json').read_text()) if (folder/'market/capture-state.json').exists() else {}
def keys():
    jobs=[];checks={}
    for g in games:
        recs=sorted((folder/'injuries/games'/g['game_id']/'records').glob('*.json'))
        if not recs: continue
        last=max((json.loads(p.read_text()) for p in recs),key=lambda r:parse_time(r['checked_at']))
        jobs.append(dict(game_id=g['game_id'],stage=last.get('forecast_stage','preliminary'),key=g['game_id']))
        checks[g['game_id']]=last
    t=time.monotonic()
    operations._job_keys(folder,jobs,checks,dict(evidence=capture.get('evidence',{})),roster,artifact,pointer['artifact_sha256'],schedule=schedule,history=history)
    return {j['game_id']:j['key'] for j in jobs},time.monotonic()-t
a,ta=keys(); b,tb=keys()
print('  %d games, identical: %s, %.2f s and %.2f s'%(len(a),a==b,ta,tb))
