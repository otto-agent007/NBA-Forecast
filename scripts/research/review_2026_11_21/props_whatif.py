"""First-order what-if on the live Kalshi median benchmark (853 player medians, 9 dates): scale every model points forecast by (1+d)
(the vacancy-turnover change lifts rates about 2.9% on the replay; live it moved 40 saved runs' totals about +1.8%) and count
role minutes above 35 at a quarter. Not a replay: teammates do not receive the freed minutes here. ± one SE by date."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json, glob, math, collections
PROD=OPERATIONS
meds=[]
for f in sorted(glob.glob(PROD+'/market/kalshi-props/scored/*.json')):
    d=json.load(open(f))
    if d['entry'].get('scored'): meds+=d['medians']
idx={}
for g in json.load(open(PROD+'/performance.json'))['groups']:
    for r in g['matched']: idx[(r['game_id'],r['player_id'])]=r
def cl(rs,f):
    d=collections.defaultdict(list)
    for r in rs: d[r['date']].append(f(r))
    n=sum(len(v) for v in d.values()); m=sum(sum(v) for v in d.values())/n; G=len(d)
    return m, math.sqrt(G/(G-1)*sum((sum(v)-m*len(v))**2 for v in d.values()))/n, n
def g(m,K=35.,s=.25): return m if m<=K else K+s*(m-K)
base=cl(meds,lambda r:r['model_abs_error']-r['market_abs_error'])
print(f'now: model - market MAE {base[0]:+.3f}±{base[1]:.3f} (n={base[2]})')
for d in (0,.01,.018,.029,.04):
    for comp in (False,True):
        def new(r):
            pm=idx[(r['game_id'],r['player_id'])]['predicted_minutes']
            f=(g(pm)/pm if comp and pm>0 else 1.)*(1+d)
            return abs(r['model_points']*f-r['actual_points'])
        a=cl(meds,lambda r:new(r)-r['market_abs_error']); c=cl(meds,lambda r:new(r)-r['model_abs_error'])
        print(f'  scale {1+d:.3f}{" + minutes ceiling" if comp else "":18s}: model - market {a[0]:+.3f}±{a[1]:.3f}; change vs today {c[0]:+.3f}±{c[1]:.3f}')
n35=sum(1 for r in meds if idx[(r['game_id'],r['player_id'])]['predicted_minutes']>35); print('medians with forecast minutes above 35:',n35)
