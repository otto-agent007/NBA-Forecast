"""First-order, post-hoc estimate of compressing high conditional-minute forecasts: m' = K + s*(m-K) above K.
Applied to the reference replay rows (points scaled with minutes); the freed minutes are NOT handed to teammates here,
so this understates what a replay with reallocation would show. Change in MAE over ALL scored rows of the gate
(expected-value convention unknown here, so played rows only are transformed and scored). ± one SE clustered by game."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json,gzip,math,collections,sys
D=DATASETS+(sys.argv[1] if len(sys.argv)>1 else 'backtest-2026-11-12-margins-v5')
r=json.load(gzip.open(D+'/rows.json.gz'))
P=[dict(zip(r['player_fields'],x)) for x in r['players']]
P=[p for p in P if p['expected_minutes'] is not None]
def season(gid):
    if gid.startswith('espn-4015') : return 'A'
    if gid.startswith('espn-'): return 'B'
    return 'C'
ids=collections.Counter((season(p['game_id']),p['game_id'][:9]) for p in P); print('id prefixes',dict(collections.Counter(season(p['game_id']) for p in P)), list(ids)[:6])
pl=[p for p in P if p['actual_status']=='played' and p['actual_minutes']>0]
def cl(rs,f):
    d=collections.defaultdict(float); n=0
    for x in rs: d[x['game_id']]+=f(x); n+=1
    m=sum(d.values())/n; K=len(d)
    # cluster SE of a mean-of-rows with cluster sums (centered)
    cnt=collections.Counter(x['game_id'] for x in rs)
    se=math.sqrt(K/(K-1)*sum((d[g]-m*cnt[g])**2 for g in d))/n
    return m,se,n
def g(m,K,s,top=None):
    return m if m<=K else K+s*(m-K)
def delta(rs,K,s):
    dm=lambda p:abs(g(p['expected_minutes'],K,s)-p['actual_minutes'])-abs(p['expected_minutes']-p['actual_minutes'])
    dp=lambda p:abs(p['points_mean']*g(p['expected_minutes'],K,s)/p['expected_minutes']-p['actual_points'])-abs(p['points_mean']-p['actual_points']) if p['expected_minutes']>0 else 0.
    return cl(rs,dm),cl(rs,dp)
grid=[(K,s) for K in (30,31,32,33,34,35) for s in (0,.2,.3,.4,.5,.6,.7)]
print('all seasons, played rows (n=%d): change in minutes MAE / points MAE'%len(pl))
best=sorted(((delta(pl,K,s),K,s) for K,s in grid),key=lambda x:x[0][0][0])[:8]
for (dm,dp),K,s in best: print(f'  K={K} s={s}: minutes {dm[0]:+.4f}±{dm[1]:.4f}  points {dp[0]:+.4f}±{dp[1]:.4f}')
print('leave-one-season-out: pick (K,s) by minutes MAE on two seasons, score the third')
for held in 'ABC':
    tr=[p for p in pl if season(p['game_id'])!=held]; te=[p for p in pl if season(p['game_id'])==held]
    (dm,dp),K,s=min(((delta(tr,K,s),K,s) for K,s in grid),key=lambda x:x[0][0][0])
    em,ep=delta(te,K,s)
    print(f'  held {held} (n={len(te)}): chose K={K} s={s}; held-out minutes {em[0]:+.4f}±{em[1]:.4f} points {ep[0]:+.4f}±{ep[1]:.4f}')
ref_m=cl(pl,lambda p:abs(p['expected_minutes']-p['actual_minutes']))[0]; ref_p=cl(pl,lambda p:abs(p['points_mean']-p['actual_points']))[0]
print(f'reference played-row MAE: minutes {ref_m:.3f} points {ref_p:.3f}; 0.5% floors {0.005*ref_m:.4f} / {0.005*ref_p:.4f}')
