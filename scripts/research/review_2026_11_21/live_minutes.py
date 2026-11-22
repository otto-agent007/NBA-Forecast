"""Minutes calibration, live 0.6.0 rows from performance.json. ± one SE clustered by game date."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json, math, collections, datetime
P=OPERATIONS+'/performance.json'
g=[x for x in json.load(open(P))['groups'] if x['model_version'].endswith('0.6.0')][0]
rows=g['matched']
tz=datetime.timezone(datetime.timedelta(hours=-8))
for r in rows: r['date']=datetime.datetime.fromisoformat(r['created_at']).astimezone(tz).date().isoformat()
def cl(rs,f):
    d=collections.defaultdict(list)
    for r in rs: d[r['date']].append(f(r))
    n=sum(len(v) for v in d.values()); m=sum(sum(v) for v in d.values())/n; G=len(d)
    se=math.sqrt(G/(G-1)*sum((sum(v)-m*len(v))**2 for v in d.values()))/n if G>1 else float('nan')
    return m,se,n
import sys
since=sys.argv[1] if len(sys.argv)>1 else '2026-11-06'
for label,rs0 in (('all dates',rows),(f'since {since}',[r for r in rows if r['date']>=since])):
    print('==',label)
    pl=[r for r in rs0 if r['played']]
    print(' played rows, by predicted minutes (bias = predicted - actual)')
    for lo,hi in [(0,4),(4,8),(8,12),(12,16),(16,20),(20,24),(24,28),(28,30),(30,32),(32,34),(34,36),(36,38),(38,60)]:
        rs=[r for r in pl if lo<=r['predicted_minutes']<hi]
        if len(rs)<10: continue
        b,se,n=cl(rs,lambda r:r['minutes_error']); pb,pse,_=cl(rs,lambda r:r['points_error']); a,_,_=cl(rs,lambda r:abs(r['minutes_error']))
        print(f'  pred [{lo:2d},{hi:2d}) n={n:4d} minutes bias {b:+.2f}±{se:.2f} MAE {a:.2f} | points bias {pb:+.2f}±{pse:.2f}')
    print(' all rows incl. DNP, expected minutes P*pred - actual, by P*pred')
    for lo,hi in [(0,2),(2,6),(6,12),(12,18),(18,24),(24,30),(30,34),(34,60)]:
        rs=[r for r in rs0 if lo<=r['participation_probability']*r['predicted_minutes']<hi]
        if len(rs)<10: continue
        b,se,n=cl(rs,lambda r:r['participation_probability']*r['predicted_minutes']-r['actual_minutes'])
        pb,pse,_=cl(rs,lambda r:r['participation_probability']*r['predicted_points']-r['actual_points'])
        print(f'  E[min] [{lo:2d},{hi:2d}) n={n:4d} minutes bias {b:+.2f}±{se:.2f} | points bias {pb:+.2f}±{pse:.2f}')
    # team-game sums
    tg=collections.defaultdict(lambda:[0,0,0,0,None])
    for r in rs0:
        k=(r['game_id'],r['team_id']); t=tg[k]; p=r['participation_probability']
        t[0]+=p*r['predicted_minutes']; t[1]+=r['actual_minutes']; t[2]+=p*r['predicted_points']; t[3]+=r['actual_points']; t[4]=r['date']
    T=[dict(date=v[4],em=v[0],am=v[1],ep=v[2],ap=v[3]) for v in tg.values()]
    m,se,n=cl(T,lambda t:t['em']-t['am']); print(f' team-games {n}: expected minutes - actual {m:+.2f}±{se:.2f} (actual mean {cl(T,lambda t:t["am"])[0]:.1f})')
    m,se,n=cl(T,lambda t:t['ep']-t['ap']); print(f' team-games {n}: expected points - actual {m:+.2f}±{se:.2f} (actual mean {cl(T,lambda t:t["ap"])[0]:.1f})')
    # slope of actual on predicted, played & P>=.9
    rs=[r for r in pl if r['participation_probability']>=0.9]
    mx=sum(r['predicted_minutes'] for r in rs)/len(rs); my=sum(r['actual_minutes'] for r in rs)/len(rs)
    b=sum((r['predicted_minutes']-mx)*(r['actual_minutes']-my) for r in rs)/sum((r['predicted_minutes']-mx)**2 for r in rs)
    print(f' played, P>=0.9: n={len(rs)} slope of actual on predicted minutes {b:.3f} (means {mx:.1f} pred, {my:.1f} actual)')
