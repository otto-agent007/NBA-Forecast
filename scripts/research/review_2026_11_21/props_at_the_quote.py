"""Kalshi player-points rungs at executable prices: buy yes at the ask when the model's probability exceeds it by t,
buy no at (1 - bid) when the model is below the bid by t. Fee per contract = ceil-free 0.07*p*(1-p) (taker schedule).
Rows: scored rungs joined to the closing capture's bid/ask. ± one SE clustered by game date. In-sample thresholds: descriptive."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json, glob, math, collections, os
PROD=OPERATIONS+'/market/kalshi-props'
def cl(rs,f):
    d=collections.defaultdict(list)
    for r in rs: d[r['date']].append(f(r))
    n=sum(len(v) for v in d.values())
    if not n: return float('nan'),float('nan'),0
    m=sum(sum(v) for v in d.values())/n; G=len(d)
    se=math.sqrt(G/(G-1)*sum((sum(v)-m*len(v))**2 for v in d.values()))/n if G>1 else float('nan')
    return m,se,n
rows=[]; fields=None; miss=0
for f in sorted(glob.glob(PROD+'/scored/*.json')):
    d=json.load(open(f))
    if not d['entry'].get('scored'): continue
    cap=None
    for line in open(f'{PROD}/{d["game_id"]}.jsonl'):
        c=json.loads(line)
        if c['captured_at']==d['entry']['close_captured_at']: cap=c
    if cap is None: miss+=1; continue
    fields=cap['ladder_fields']
    quote={}
    for p in cap['players']:
        lad=(p['stats'].get('points') or {}).get('ladder') or []
        for rung in lad:
            q=dict(zip(fields,rung)); quote[(p['player_id'],q[fields[0]])]=q
    for r in d['rungs']:
        q=quote.get((r['player_id'],r['threshold']))
        if q: rows.append(dict(r,**{'q':q}))
print('ladder fields',fields,'| rungs joined',len(rows),'| games without the closing capture',miss)
bk,ak=fields[1],fields[2]
sp=sorted(r['q'][ak]-r['q'][bk] for r in rows); print('bid-ask spread: median %.3f, 90th pct %.3f'%(sp[len(sp)//2],sp[int(.9*len(sp))]))
fee=lambda p:.07*p*(1-p)
def trade(r,key,t):
    y=1.0 if r['outcome'] else 0.0; b,a=r['q'][bk],r['q'][ak]
    if not (0<b<a<1): return None
    if r[key]>=a+t: return y-a-fee(a)
    if r[key]<=b-t: return (1-y)-(1-b)-fee(1-b)
    return None
for key,name in (('model','interval model'),('model_sd','sd model')):
    print(name)
    for t in (0,.02,.04,.06,.08,.10,.15):
        tr=[dict(date=r['date'],pnl=trade(r,key,t),yes=r[key]>=r['q'][ak]+t) for r in rows if trade(r,key,t) is not None]
        m,se,n=cl(tr,lambda x:x['pnl']); ny=sum(1 for x in tr if x['yes'])
        print(f'  edge over the quote >= {t:.2f}: {100*m:+.2f}±{100*se:.2f} cents per contract after fees, n={n} (yes {ny}, no {n-ny}), dates {len({x["date"] for x in tr})}')
# split-half by date parity as a guard against threshold picking
ds=sorted({r['date'] for r in rows})
for half,sel in (('odd dates',ds[0::2]),('even dates',ds[1::2])):
    tr=[dict(date=r['date'],pnl=trade(r,'model',.08)) for r in rows if r['date'] in sel and trade(r,'model',.08) is not None]
    m,se,n=cl(tr,lambda x:x['pnl']); print(f'  interval model, edge>=.08, {half}: {100*m:+.2f}±{100*se:.2f}c n={n}')
by=collections.defaultdict(list)
for r in rows:
    p=trade(r,'model',.08)
    if p is not None: by[r['date']].append(p)
print('  interval model, edge>=.08 by date:',{d[5:]:(len(v),round(100*sum(v)/len(v),1)) for d,v in sorted(by.items())})
