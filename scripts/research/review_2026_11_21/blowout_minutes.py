"""Do forecast minutes ignore the expected margin? Replay rows (reference, baseline path): minutes error of played
players by the model's own projected margin for the game. bias = forecast - actual. ± one SE clustered by game."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json,gzip,math,collections,sys
D=DATASETS+(sys.argv[1] if len(sys.argv)>1 else 'backtest-2026-11-12-margins-v5')
r=json.load(gzip.open(D+'/rows.json.gz'))
P=[dict(zip(r['player_fields'],x)) for x in r['players']]; G={x[0]:dict(zip(r['game_fields'],x)) for x in r['games']}
P=[p for p in P if p['expected_minutes'] is not None and p['actual_status']=='played' and p['actual_minutes']>0 and p['game_id'] in G]
for p in P:
    g=G[p['game_id']]; m=g['projected_margin']; p['fav']=m if p['team_id']==g['home_team_id'] else -m; p['absm']=abs(m)
    p['actual_abs']=abs(g['actual_margin'])
def cl(rs,f):
    d=collections.defaultdict(float); c=collections.Counter()
    for x in rs: d[x['game_id']]+=f(x); c[x['game_id']]+=1
    n=sum(c.values()); m=sum(d.values())/n; K=len(d)
    return m, math.sqrt(K/(K-1)*sum((d[k]-m*c[k])**2 for k in d))/n, n
print('games',len(G),'| share of games by |projected margin|:',{f'{lo}-{hi}':round(sum(1 for g in G.values() if lo<=abs(g['projected_margin'])<hi)/len(G),3) for lo,hi in [(0,3),(3,6),(6,9),(9,12),(12,99)]})
for label,sel in (('forecast 30+ min',lambda p:p['expected_minutes']>=30),('forecast 24-30',lambda p:24<=p['expected_minutes']<30),('forecast 12-24',lambda p:12<=p['expected_minutes']<24),('forecast under 12',lambda p:p['expected_minutes']<12)):
    print(label)
    for lo,hi in [(0,3),(3,6),(6,9),(9,12),(12,99)]:
        rs=[p for p in P if sel(p) and lo<=p['absm']<hi]
        if len(rs)<50: continue
        b,se,n=cl(rs,lambda p:p['expected_minutes']-p['actual_minutes'])
        fav=[p for p in rs if p['fav']>0]; dog=[p for p in rs if p['fav']<=0]
        bf=cl(fav,lambda p:p['expected_minutes']-p['actual_minutes']); bd=cl(dog,lambda p:p['expected_minutes']-p['actual_minutes'])
        print(f'  |proj margin| [{lo:2d},{hi:2d}) n={n:5d} minutes bias {b:+.2f}±{se:.2f} | favourite {bf[0]:+.2f}±{bf[1]:.2f} underdog {bd[0]:+.2f}±{bd[1]:.2f}')
# regression slope of minutes error on |projected margin| for 30+ forecasts
rs=[p for p in P if p['expected_minutes']>=28]
x=[p['absm'] for p in rs]; y=[p['expected_minutes']-p['actual_minutes'] for p in rs]; mx=sum(x)/len(x); my=sum(y)/len(y)
b=sum((a-mx)*(c-my) for a,c in zip(x,y))/sum((a-mx)**2 for a in x)
print(f'slope of minutes bias on |projected margin|, forecasts 28+: {b:+.3f} min per point (n={len(rs)})')
print('for scale, by ACTUAL final margin (not knowable):')
for lo,hi in [(0,5),(5,10),(10,15),(15,20),(20,99)]:
    rs=[p for p in P if p['expected_minutes']>=30 and lo<=p['actual_abs']<hi]
    b,se,n=cl(rs,lambda p:p['expected_minutes']-p['actual_minutes']); print(f'  |actual margin| [{lo:2d},{hi:2d}) n={n:5d} minutes bias {b:+.2f}±{se:.2f}')
