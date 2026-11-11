"""Offline check of the per-game evidence key against the live runs of Nov 6-9 (read-only, small I/O).

    python3 scripts/research/evidence_key_validate.py EXTRACT.json OPERATIONS_DIR

OPERATIONS_DIR is read for file write times only (never written); the run times in EXTRACT.json must come
from the same directory. Input: the compact per-run extract (evidence_key_extract.py): for each live run its game,
stage, created_at, game_key (the two teams' roster members with applied status and reported status,
their reported-unavailable entries, ESPN fallbacks), history_sha256 (digest of the usable history the
run read), model key, margin and per-player (status, points, minutes, P).

The branch key = report rows for the game (status, reported status) + coverage + the two teams' roster
members + ESPN digest + league inputs (merged history, environment finals, tipped games' saved lines,
collected shot files, NBA box-score pages) + per-game inputs (schedule entry/context, earlier T-30
checkpoints). Reconstruction used here:
  - report rows + members + ESPN       -> game_key (applied statuses incl. reported status; the applied status
                                          is a function of the rows, coverage and ESPN list)
  - merged history                     -> history_sha256
  - environment finals, shot files,    -> production file write times (mtime) between the two runs; a file
    box-score pages, T-30 checkpoints     written in (previous run, this run] counts as a change. Only each file's
                                          LAST write is visible, so repeated writes are undercounted; a pending
                                          shot file (which the key ignores) is counted (overcount).
  - tipped games' saved lines          -> join the priced set with their environment final (same event);
                                          recovered closes after tip: game-lines mtime later than tip + 3 h.
Change classes (as key_variants.py): material = |d margin| >= 0.1 or a player |d points| >= 0.25 or a status
change; small = >= 0.01; trivial = otherwise. A miss = key unchanged but forecast changed (small or material).
"""
import json, re, sys, os
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from pathlib import Path
P=Path(sys.argv[2])
rows=json.load(open(sys.argv[1]))
ts=lambda s: datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
events=[]  # (time, kind)
for p in (P/'environment-finals/records').glob('*.json'): events.append((p.stat().st_mtime,'environment'))
for p in (P/'shots').glob('*.json'): events.append((p.stat().st_mtime,'shots'))
URL=re.compile(r'https://www\.nba\.com/game/(002\d{7})/box-score')
for p in (P/'raw').glob('*.provenance.json'):
    try:
        if URL.fullmatch(json.loads(p.read_bytes()).get('source_url','')): events.append((p.stat().st_mtime,'box_score_page'))
    except Exception: events.append((p.stat().st_mtime,'box_score_page'))
sched=json.load(open(P/'schedule/latest.json'))
starts={g['game_id']:ts(g['start_time']) for g in sched['games'] if g.get('start_time')}
for p in (P/'injuries/games').glob('*/final.json'):
    # A T-30 checkpoint written at its game's checkpoint joins the key with the game's final (a history
    # change); only a late write (recover-evidence, > 3 h after tip) is a separate change.
    if p.parent.name in starts and p.stat().st_mtime>starts[p.parent.name]+3*3600: events.append((p.stat().st_mtime,'late_checkpoint'))
for p in (P/'market/game-lines').glob('*.jsonl'):
    gid=p.stem; m=p.stat().st_mtime
    if gid in starts and m>starts[gid]+3*3600: events.append((m,'recovered_close'))
events.sort()
def league_changed(t0,t1):
    return sorted({k for t,k in events if t0<t<=t1})
by=defaultdict(list)
for r in rows: by[(r['game'],r['stage'])].append(r)
def cls(a,b):
    ids=set(a['preds'])|set(b['preds'])
    g=lambda r,i,j:(r['preds'].get(i) or [None,0,0,0])[j]
    dm=abs((b['margin'] or 0)-(a['margin'] or 0))
    dp=max([abs((g(b,i,1) or 0)-(g(a,i,1) or 0)) for i in ids] or [0])
    st=any(g(a,i,0)!=g(b,i,0) for i in ids)
    return 'material' if (dm>=0.1 or dp>=0.25 or st) else ('small' if (dm>=0.01 or dp>=0.01) else 'trivial')
table=Counter(); why=Counter(); misses=[]; made=0
for rs in by.values():
    made+=1
    for a,b in zip(rs,rs[1:]):
        c=cls(a,b)
        reasons=[]
        if a['game_key']!=b['game_key']: reasons.append('game evidence')
        if a['history']!=b['history']: reasons.append('history')
        if a['model']!=b['model']: reasons.append('model')
        reasons+=league_changed(ts(a['created']),ts(b['created']))
        changed=bool(reasons)
        made+=changed
        table[(c,changed)]+=1
        for x in set(reasons): why[x]+=1
        if not changed and c!='trivial': misses.append((b['game'],b['stage'],b['created'][5:16],c))
n=sum(table.values())
print('live runs %d in %d game/stage series; consecutive pairs %d'%(len(rows),len(by),n))
for c in ('material','small','trivial'):
    print('  %-8s key changed %4d   key same %4d'%(c,table[(c,True)],table[(c,False)]))
print('branch key would make %d runs instead of %d (%.0f%% fewer); forecast changes missed: %d'%(made,len(rows),100*(1-made/len(rows)),len(misses)))
print('pairs by reason (a pair can have several):',dict(why))
for m in misses: print('  MISS',m)
only=Counter()
for rs in by.values():
    for a,b in zip(rs,rs[1:]):
        r=set(league_changed(ts(a['created']),ts(b['created'])))
        if r and a['game_key']==b['game_key'] and a['history']==b['history'] and a['model']==b['model']:
            only[','.join(sorted(r))]+=1
print('pairs re-forecast for league file events alone:',dict(only))
# Drift a kept forecast accumulates while the key holds (time-decayed team ratings, ~1e-5 per 15 min):
drift_m=[]; drift_p=[]
for rs in by.values():
    start=rs[0]
    for a,b in zip(rs,rs[1:]):
        changed=a['game_key']!=b['game_key'] or a['history']!=b['history'] or a['model']!=b['model'] or league_changed(ts(a['created']),ts(b['created']))
        if changed: start=b; continue
        drift_m.append(abs((b['margin'] or 0)-(start['margin'] or 0)))
        ids=set(b['preds'])|set(start['preds'])
        drift_p.append(max([abs(((b['preds'].get(i) or [0,0])[1] or 0)-((start['preds'].get(i) or [0,0])[1] or 0)) for i in ids] or [0]))
print('drift of a kept forecast vs the run it replaced (pairs %d): margin max %.4f median %.5f; player points max %.4f'%(len(drift_m),max(drift_m),sorted(drift_m)[len(drift_m)//2],max(drift_p)))
