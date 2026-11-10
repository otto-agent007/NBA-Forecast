"""Replay the derived service's trigger over 2026-11-07T10:11Z..2026-11-09T20:50Z under the old and new rules.

Inputs (read-only, small): run created_at and evaluation evaluated_at (run_records / evaluations columns only),
derived.jsonl (actual rebuild durations), and file write times of the other signature inputs: finals/*.json
(last write only), raw LiveData box-score/play-by-play provenance (written once), derived-request.json (last
write only), Pacific midnights. Schedule-content changes are not reconstructable (rare).
The timer fires each minute at :30; a rebuild's signature is taken at its start, its age counts from its end;
a rebuild's duration is the duration of the actual rebuild nearest in time.
old rule: rebuild when any input (runs included) changed since the last rebuild started, or after 6 h.
new rule: as old, but a change in runs alone waits until DERIVED_RUNS_REFRESH_SECONDS (1800 s) after the last rebuild ended.

    python3 scripts/research/derived_trigger_simulation.py OPERATIONS_DIR [FROM UNTIL]

Reads OPERATIONS_DIR (read-only): two timestamp columns of the ledger, derived.jsonl and file write times.
"""
import json, re, sqlite3, sys
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(sys.argv[1])
LO,HI=(sys.argv[2],sys.argv[3]) if len(sys.argv)>3 else ('2026-11-07T10:11:00+00:00','2026-11-09T20:50:00+00:00')
ts=lambda s: datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
lo,hi=ts(LO),ts(HI)
conn=sqlite3.connect(f'file:{P}/forecast.sqlite?mode=ro',uri=True)
runs=sorted(ts(r[0]) for r in conn.execute('SELECT created_at FROM run_records'))
evals=sorted(ts(r[0]) for r in conn.execute('SELECT evaluated_at FROM evaluations'))
hard=list(evals)
hard+=[p.stat().st_mtime for p in (P/'finals').glob('*.json')]
for p in (P/'raw').glob('*.provenance.json'):
    try:
        if re.search(r'/liveData/(boxscore|playbyplay)/',json.loads(p.read_bytes()).get('source_url','')): hard.append(p.stat().st_mtime)
    except Exception: pass
if (P/'derived-request.json').exists(): hard.append((P/'derived-request.json').stat().st_mtime)
day=datetime(2026,11,7,tzinfo=ZoneInfo('America/Los_Angeles'))
while day.timestamp()<hi:
    hard.append(day.timestamp()); day+=timedelta(days=1)
hard.sort()
der=[json.loads(l) for l in open(P/'derived.jsonl') if '"rebuilt"' in l]
actual=[(ts(r['started_at']),ts(r['finished_at'])-ts(r['started_at'])) for r in der]
starts=[a for a,_ in actual]
def duration(t):
    i=bisect_right(starts,t); cands=[actual[j] for j in (i-1,i) if 0<=j<len(actual)]
    return min(cands,key=lambda x:abs(x[0]-t))[1]
def any_in(series,a,b):
    i=bisect_right(series,a); return i<len(series) and series[i]<=b
def simulate(runs_wait):
    prev=[x for x in actual if x[0]<lo][-1]
    last_start,last_end=prev[0],prev[0]+prev[1]
    t=max(lo,last_end); t=t-(t%60)+30
    count=0; busy=0.
    while t<hi:
        age=t-last_end
        due=any_in(hard,last_start,t) or age>=6*3600 or (any_in(runs,last_start,t) and age>=runs_wait)
        if due:
            d=duration(t); count+=1; busy+=d
            last_start,last_end=t,t+d
            t=last_end; t=t-(t%60)+90
        else:
            t+=60
    return count,busy
actual_n=sum(1 for a,_ in actual if lo<=a<hi)
old=simulate(0); new=simulate(1800)
print('window %s..%s: actual rebuilds %d'%(LO,HI,actual_n))
print('simulated old rule: %d rebuilds, %.1f h busy'%(old[0],old[1]/3600))
print('simulated new rule: %d rebuilds, %.1f h busy (DERIVED_RUNS_REFRESH_SECONDS=1800)'%(new[0],new[1]/3600))
