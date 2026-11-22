"""Where the model loses to the Kalshi prop medians. Rows: scored median players joined to performance.json matched rows.
± is one SE clustered by game date (the props report's 'date')."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json, glob, math, collections
PROD=OPERATIONS
meds=[]
for f in sorted(glob.glob(PROD+'/market/kalshi-props/scored/*.json')):
    d=json.load(open(f))
    if not d['entry'].get('scored'): continue
    for m in d['medians']: m['run_id']=d['entry']['run_id']; meds.append(m)
perf=json.load(open(PROD+'/performance.json'))
idx={}
for g in perf['groups']:
    for r in g['matched']: idx[(r['game_id'],r['player_id'])]=r
def cl(rs,f):
    d=collections.defaultdict(list)
    for r in rs: d[r['date']].append(f(r))
    n=sum(len(v) for v in d.values()); m=sum(sum(v) for v in d.values())/n; G=len(d)
    se=math.sqrt(G/(G-1)*sum((sum(v)-m*len(v))**2 for v in d.values()))/n if G>1 else float('nan')
    return m,se,n
def show(label,rs):
    if len(rs)<5: print(f'{label:38s} n={len(rs)}'); return
    d,se,n=cl(rs,lambda r:r['model_abs_error']-r['market_abs_error'])
    mm,_,_=cl(rs,lambda r:r['model_abs_error']); mk,_,_=cl(rs,lambda r:r['market_abs_error'])
    bm,bse,_=cl(rs,lambda r:r['model_points']-r['actual_points']); bk,kse,_=cl(rs,lambda r:r['market_line']-r['actual_points'])
    print(f'{label:38s} n={n:4d} model {mm:.2f} market {mk:.2f} diff {d:+.3f}±{se:.3f} | bias model {bm:+.2f}±{bse:.2f} market {bk:+.2f}±{kse:.2f}')
print('medians',len(meds),'dates',len({m["date"] for m in meds}))
show('all medians',meds)
j=[dict(m,**{'p':idx[(m['game_id'],m['player_id'])]}) for m in meds if (m['game_id'],m['player_id']) in idx]
print('joined to performance rows',len(j),'same run',sum(1 for r in j if r['p']['run_id']==r['run_id']))
show('joined',j)
pl=[r for r in j if r['p']['played']]; show('played',pl); show('did not play',[r for r in j if not r['p']['played']])
# regression of (actual - market) on (model - market)
def slope(rs):
    x=[r['model_points']-r['market_line'] for r in rs]; y=[r['actual_points']-r['market_line'] for r in rs]
    sxx=sum(a*a for a in x); sxy=sum(a*b for a,b in zip(x,y)); b=sxy/sxx
    # cluster SE through origin
    d=collections.defaultdict(float)
    for r,a,c in zip(rs,x,y): d[r['date']]+=a*(c-b*a)
    G=len(d); se=math.sqrt(G/(G-1)*sum(v*v for v in d.values()))/sxx
    return b,se
b,se=slope(meds); print(f'\nslope of (actual-market) on (model-market), through origin: {b:+.3f}±{se:.3f}  (1 = model right, 0 = market right)')
b,se=slope(pl); print(f'  played only: {b:+.3f}±{se:.3f}')
print('mean |model-market|',sum(abs(r['model_points']-r['market_line']) for r in meds)/len(meds))
for w in (0,.1,.2,.3,.5,1):
    e,_,_=cl(meds,lambda r:abs(w*r['model_points']+(1-w)*r['market_line']-r['actual_points'])); print(f'  w_model={w}: MAE {e:.3f}',end='')
print('\n\n-- by size of disagreement')
for lo,hi in [(0,1),(1,2),(2,3),(3,5),(5,99)]:
    show(f'|model-market| [{lo},{hi})',[r for r in meds if lo<=abs(r['model_points']-r['market_line'])<hi])
show('model above market',[r for r in meds if r['model_points']>r['market_line']]); show('model below market',[r for r in meds if r['model_points']<=r['market_line']])
print('\n-- by market line level')
for lo,hi in [(0,10),(10,15),(15,20),(20,26),(26,60)]:
    show(f'line [{lo},{hi})',[r for r in meds if lo<=r['market_line']<hi])
print('\n-- played: oracle minutes (model rate x actual minutes) vs market')
om,se,n=cl(pl,lambda r:abs(r['p']['rate_component'])); mk,_,_=cl(pl,lambda r:r['market_abs_error']); mm,_,_=cl(pl,lambda r:r['model_abs_error'])
d,dse,_=cl(pl,lambda r:abs(r['p']['rate_component'])-r['market_abs_error'])
print(f'n={n} model {mm:.3f}  model w/ actual minutes {om:.3f}  market {mk:.3f}  (oracle-minutes minus market {d:+.3f}±{dse:.3f})')
mb,mbse,_=cl(pl,lambda r:r['p']['minutes_error']); ma,_,_=cl(pl,lambda r:abs(r['p']['minutes_error']))
print(f'minutes bias {mb:+.2f}±{mbse:.2f} MAE {ma:.2f}; rate component bias {cl(pl,lambda r:r["p"]["rate_component"])[0]:+.2f}')
# implied market minutes are unknown; split by minutes miss
print('\n-- played: by model minutes error (pred-actual)')
for lo,hi in [(-99,-6),(-6,-2),(-2,2),(2,6),(6,99)]:
    show(f'min err [{lo},{hi})',[r for r in pl if lo<=r['p']['minutes_error']<hi])
print('\n-- by participation probability at forecast')
for lo,hi in [(0,.9),(.9,.98),(.98,1.01)]:
    show(f'P [{lo},{hi})',[r for r in j if lo<=r['p']['participation_probability']<hi])
print('\n-- by date')
for dt in sorted({m['date'] for m in meds}):
    rs=[r for r in meds if r['date']==dt]; mm=sum(r['model_abs_error'] for r in rs)/len(rs); mk=sum(r['market_abs_error'] for r in rs)/len(rs)
    print(f'{dt} n={len(rs):3d} model {mm:.2f} market {mk:.2f} diff {mm-mk:+.2f}')
