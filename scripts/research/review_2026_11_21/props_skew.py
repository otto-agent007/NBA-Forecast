"""Is the rung probability hurt by centring the two-piece normal on the MEAN? Test on the live scored Kalshi rungs.
Variants of the model's probability of clearing a threshold, each from the same stored forecast (mean, 80% interval):
  today     two-piece normal with its join (the median) at points_mean
  skew      two-piece normal whose MEAN is points_mean: join = mean - sqrt(2/pi)*(s_up - s_low)
Reads the 65 scored runs' forecasts from the ledger by run_id (read-only, one row each). ± one SE by date."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json, glob, math, collections, sqlite3, os, sys
PROD=OPERATIONS
CACHE=os.environ.get('NBA_PROPS_FORECASTS','props_forecasts.json')
Z80=1.2815515655446004
scored=[json.load(open(f)) for f in sorted(glob.glob(PROD+'/market/kalshi-props/scored/*.json'))]
scored=[d for d in scored if d['entry'].get('scored')]
if os.path.exists(CACHE): fc=json.load(open(CACHE))
else:
    conn=sqlite3.connect('file:'+PROD+'/forecast.sqlite?mode=ro',uri=True,timeout=30); fc={}
    for d in scored:
        row=conn.execute('SELECT forecast_json FROM run_records WHERE run_id=?',(d['entry']['run_id'],)).fetchone()
        preds=json.loads(row[0])['predictions']; want={r['player_id'] for r in d['rungs']}
        fc[d['game_id']]={p['player_id']:{k:p.get(k) for k in ('points_mean','points_sd','points_lower80','points_upper80','expected_minutes')} for p in preds if p['player_id'] in want}
    conn.close(); json.dump(fc,open(CACHE,'w'))
cdf=lambda z:.5*math.erfc(-z/math.sqrt(2.))
def prob(p,t,variant,shift=0.):
    mean,low,high=p['points_mean'],p['points_lower80'],p['points_upper80']
    up=(high-mean)/Z80; lo=(mean-low)/Z80 if low>0 and mean>low else up
    join=mean
    if variant=='skew': join=mean-math.sqrt(2/math.pi)*(up-lo)
    if variant=='shift': join=mean-shift
    return 1-cdf((t-join)/(up if t>=join else lo))
rows=[]
for d in scored:
    for r in d['rungs']:
        p=fc[d['game_id']].get(r['player_id'])
        if p and r.get('model') is not None: rows.append(dict(r,p=p))
def cl(rs,f):
    d=collections.defaultdict(list)
    for r in rs: d[r['date']].append(f(r))
    n=sum(len(v) for v in d.values()); m=sum(sum(v) for v in d.values())/n; G=len(d)
    return m, math.sqrt(G/(G-1)*sum((sum(v)-m*len(v))**2 for v in d.values()))/n, n
chk=max(abs(prob(r['p'],r['threshold'],'today')-r['model']) for r in rows); print('rungs',len(rows),'| max difference from the stored model probability',chk)
asym=cl(rows,lambda r:((r['p']['points_upper80']-r['p']['points_mean'])-(r['p']['points_mean']-r['p']['points_lower80']))/Z80)
print('mean (s_up - s_low) %.3f'%asym[0])
mk=cl(rows,lambda r:r['market_brier'])[0]
def show(name,f):
    b=cl(rows,lambda r:(f(r)-r['outcome'])**2-r['market_brier']); c=cl(rows,lambda r:(f(r)-r['outcome'])**2-r['model_brier']); bias=cl(rows,lambda r:f(r)-r['outcome'])
    print(f'  {name:26s} Brier - market {b[0]:+.5f}±{b[1]:.5f}; vs today {c[0]:+.5f}±{c[1]:.5f}; mean(prob - outcome) {bias[0]:+.4f}')
print('market Brier %.4f; market mean(price - outcome) %+.4f'%(mk,cl(rows,lambda r:r['market']-r['outcome'])[0]))
show('today',lambda r:prob(r['p'],r['threshold'],'today'))
show('skew (mean-preserving)',lambda r:prob(r['p'],r['threshold'],'skew'))
for s in (-1,-.5,.3,.5,1):
    show(f'join = mean - {s}',lambda r:prob(r['p'],r['threshold'],'shift',s))
# median error: model median under each variant vs market median
meds=[]
for d in scored:
    for m in d['medians']:
        p=fc[d['game_id']].get(m['player_id'])
        if p: meds.append(dict(m,p=p))
for name,f in (('mean (today)',lambda p:p['points_mean']),('skew join',lambda p:p['points_mean']-math.sqrt(2/math.pi)*(((p['points_upper80']-p['points_mean'])/Z80)-(((p['points_mean']-p['points_lower80'])/Z80) if p['points_lower80']>0 else ((p['points_upper80']-p['points_mean'])/Z80))))):
    a=cl(meds,lambda m:abs(f(m['p'])-m['actual_points'])-m['market_abs_error']); print(f'  median line as {name}: abs error - market {a[0]:+.3f}±{a[1]:.3f} (n={a[2]})')
