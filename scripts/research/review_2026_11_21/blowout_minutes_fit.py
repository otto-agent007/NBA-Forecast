"""Blowout term, compact form, sized on the replay rows with the PLAYER-SUM margin (what the baseline has in hand):
minutes shift = -s(m) * max(0, d - D0), s(m) = clamp(RATE*(m - PIVOT), LOW, HIGH), after the 35-minute ceiling.
Leave-one-season-out over a small grid; first-order (no reallocation). ± one SE clustered by game."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json,gzip,math,collections,sys,itertools
D=DATASETS+(sys.argv[1] if len(sys.argv)>1 else 'backtest-2026-11-12-margins-v5')
r=json.load(gzip.open(D+'/rows.json.gz'))
P=[dict(zip(r['player_fields'],x)) for x in r['players']]; T=[dict(zip(r['team_fields'],x)) for x in r['teams']]
ps=collections.defaultdict(dict)
for t in T: ps[t['game_id']]['h' if t['home'] else 'a']=t['projected_points']
G={x[0]:dict(zip(r['game_fields'],x)) for x in r['games']}
P=[p for p in P if p['expected_minutes'] is not None and p['actual_status']=='played' and p['actual_minutes']>0 and len(ps[p['game_id']])==2]
def season(gid): return 'A' if gid.startswith('espn-4015') else 'B' if gid.startswith('espn-') else 'C'
def knee(m,K=35.,s=.25): return m if m<=K else K+s*(m-K)
for p in P:
    p['d']=abs(ps[p['game_id']]['h']-ps[p['game_id']]['a']); p['dp']=abs(G[p['game_id']]['projected_margin']); p['s']=season(p['game_id'])
    p['m']=knee(p['expected_minutes']); p['e0']=abs(p['m']-p['actual_minutes'])
x=[p['d'] for p in P]; y=[p['dp'] for p in P]; mx=sum(x)/len(x); my=sum(y)/len(y)
print('player-sum |margin| mean %.2f, published %.2f, correlation %.3f'%(mx,my,sum((a-mx)*(b-my) for a,b in zip(x,y))/math.sqrt(sum((a-mx)**2 for a in x)*sum((b-my)**2 for b in y))))
def cl(rs,f):
    d=collections.defaultdict(float); c=collections.Counter()
    for q in rs: d[q['game_id']]+=f(q); c[q['game_id']]+=1
    n=sum(c.values()); m=sum(d.values())/n; K=len(d)
    return m, math.sqrt(K/(K-1)*sum((d[k]-m*c[k])**2 for k in d))/n, n
def shift(p,rate,pivot,low,high,d0,key='d'):
    s=max(low,min(high,rate*(p['m']-pivot)))
    return max(0.,p['m']-s*max(0.,p[key]-d0))
grid=list(itertools.product((.012,.018,.025),(22,24,26),(-.12,-.08,-.04,0.),(.12,.16,.20),(0,3,5)))
def score(rs,g,key='d'): return sum(abs(shift(p,*g,key=key)-p['actual_minutes'])-p['e0'] for p in rs)/len(rs)
for held in 'ABC':
    tr=[p for p in P if p['s']!=held]; te=[p for p in P if p['s']==held]
    best=min(grid,key=lambda g:score(tr,g))
    dm=cl(te,lambda p:abs(shift(p,*best)-p['actual_minutes'])-p['e0'])
    dp=cl(te,lambda p:abs(p['points_mean']*shift(p,*best)/p['expected_minutes']-p['actual_points'])-abs(p['points_mean']*p['m']/p['expected_minutes']-p['actual_points']) if p['expected_minutes']>0 else 0.)
    print(f'held {held}: chose rate,pivot,low,high,d0={best}; held-out on top of the ceiling: minutes {dm[0]:+.4f}±{dm[1]:.4f} points {dp[0]:+.4f}±{dp[1]:.4f}')
best=min(grid,key=lambda g:score(P,g)); print('all seasons best',best,'%.4f'%score(P,best))
for g in [(.018,24,-.08,.16,3),(.018,24,0.,.16,3),(.018,24,-.08,.16,0),(.025,24,-.08,.20,3)]:
    dm=cl(P,lambda p:abs(shift(p,*g)-p['actual_minutes'])-p['e0']); print('  ',g,f'minutes {dm[0]:+.4f}±{dm[1]:.4f}')
# residual bias after ceiling + chosen term, by margin bucket, 30+ forecasts
g=best
for lo,hi in [(0,3),(3,6),(6,9),(9,12),(12,99)]:
    rs=[p for p in P if p['expected_minutes']>=30 and lo<=p['d']<hi]
    b0=cl(rs,lambda p:p['expected_minutes']-p['actual_minutes']); b1=cl(rs,lambda p:shift(p,*g)-p['actual_minutes'])
    print(f'  30+ forecasts, player-sum |margin| [{lo},{hi}): n={b0[2]} bias today {b0[0]:+.2f} -> with ceiling and blowout term {b1[0]:+.2f}±{b1[1]:.2f}')
