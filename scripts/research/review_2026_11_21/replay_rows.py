"""Do the live problems show in the three-season production-path replay rows? ± one SE clustered by game."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json,gzip,math,collections,sys
D=DATASETS+(sys.argv[1] if len(sys.argv)>1 else 'backtest-2026-11-12-production-path')
r=json.load(gzip.open(D+'/rows.json.gz'))
print(r['player_fields']); print(r['team_fields']); print(r['game_fields'])
P=[dict(zip(r['player_fields'],x)) for x in r['players']]
T=[dict(zip(r['team_fields'],x)) for x in r['teams']]
G=[dict(zip(r['game_fields'],x)) for x in r['games']]
print(P[0]); print(T[0]); print(G[0])

def cl(rs,f,key='game_id'):
    d=collections.defaultdict(list)
    for x in rs: d[x[key]].append(f(x))
    n=sum(len(v) for v in d.values()); m=sum(sum(v) for v in d.values())/n; K=len(d)
    se=math.sqrt(K/(K-1)*sum((sum(v)-m*len(v))**2 for v in d.values()))/n
    return m,se,n
def season(g):
    return 'all'
print('\nstatus counts',collections.Counter(p['actual_status'] for p in P))
print('rows without a forecast',sum(1 for p in P if p['expected_minutes'] is None)); P=[p for p in P if p['expected_minutes'] is not None and p['participation_probability'] is not None]
pl=[p for p in P if p['actual_status']=='played' and p['actual_minutes']>0]
print('played rows, by forecast minutes: minutes bias (forecast-actual), points bias')
for lo,hi in [(0,4),(4,8),(8,12),(12,16),(16,20),(20,24),(24,28),(28,30),(30,32),(32,34),(34,36),(36,38),(38,60)]:
    rs=[p for p in pl if lo<=p['expected_minutes']<hi]
    if len(rs)<20: continue
    b,se,n=cl(rs,lambda p:p['expected_minutes']-p['actual_minutes']); pb,pse,_=cl(rs,lambda p:p['points_mean']-p['actual_points'])
    a=cl(rs,lambda p:abs(p['expected_minutes']-p['actual_minutes']))[0]
    print(f'  [{lo:2d},{hi:2d}) n={n:6d} minutes bias {b:+.2f}±{se:.2f} MAE {a:.2f} | points bias {pb:+.2f}±{pse:.2f}')
rs=[p for p in pl if p['participation_probability']>=0.9]
mx=sum(p['expected_minutes'] for p in rs)/len(rs); my=sum(p['actual_minutes'] for p in rs)/len(rs)
print(' slope actual on forecast minutes (played, P>=.9): %.3f n=%d'%(sum((p['expected_minutes']-mx)*(p['actual_minutes']-my) for p in rs)/sum((p['expected_minutes']-mx)**2 for p in rs),len(rs)))
print('participation calibration (all rows)')
for lo,hi in [(0,.3),(.3,.6),(.6,.8),(.8,.9),(.9,.95),(.95,.98),(.98,.995),(.995,1.01)]:
    rs=[p for p in P if lo<=p['participation_probability']<hi]
    if rs:
        m=sum(p['participation_probability'] for p in rs)/len(rs); a,se,n=cl(rs,lambda p:1.0 if (p['actual_status']=='played' and p['actual_minutes']>0) else 0.0)
        print(f'  P[{lo:.3f},{hi:.3f}) n={n:6d} mean P {m:.3f} played {a:.3f}±{se:.3f}')
b,se,n=cl(T,lambda t:t['projected_points']-t['actual_points_regulation']); print(f'team points: projected - actual regulation {b:+.2f}±{se:.2f} n={n}; vs final incl OT {cl(T,lambda t:t["projected_points"]-t["actual_points"])[0]:+.2f}')
gm=cl(G,lambda g:abs(g['projected_margin']-g['actual_margin'])); print(f'margin MAE {gm[0]:.3f}±{gm[1]:.3f} n={gm[2]}')
mx=sum(g['projected_margin'] for g in G)/len(G); my=sum(g['actual_margin'] for g in G)/len(G)
b=sum((g['projected_margin']-mx)*(g['actual_margin']-my) for g in G)/sum((g['projected_margin']-mx)**2 for g in G)
print(f'slope actual on projected margin {b:.3f}; mean projected {mx:+.2f} actual {my:+.2f}')
for k in (0.8,0.85,0.9,0.95,1.0):
    e=cl(G,lambda g:abs(my+k*(g['projected_margin']-mx)-g['actual_margin'])-abs(g['projected_margin']-g['actual_margin']))
    print(f'  shrink {k}: margin MAE change {e[0]:+.4f}±{e[1]:.4f}')
