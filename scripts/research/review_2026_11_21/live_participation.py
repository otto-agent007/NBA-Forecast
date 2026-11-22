"""Live participation calibration by period (0.6.0 rows). ± one SE clustered by game date."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json, math, collections, datetime
P=OPERATIONS+'/performance.json'
g=[x for x in json.load(open(P))['groups'] if x['model_version'].endswith('0.6.0')][0]
rows=g['matched']; tz=datetime.timezone(datetime.timedelta(hours=-8))
for r in rows: r['date']=datetime.datetime.fromisoformat(r['created_at']).astimezone(tz).date().isoformat()
def cl(rs,f):
    d=collections.defaultdict(list)
    for r in rs: d[r['date']].append(f(r))
    n=sum(len(v) for v in d.values()); m=sum(sum(v) for v in d.values())/n; G=len(d)
    return m,(math.sqrt(G/(G-1)*sum((sum(v)-m*len(v))**2 for v in d.values()))/n if G>1 else float('nan')),n
for label,rs0 in (('through 11-05',[r for r in rows if r['date']<='2026-11-05']),('11-06..11-12',[r for r in rows if '2026-11-06'<=r['date']<='2026-11-12']),('11-13..11-20',[r for r in rows if r['date']>='2026-11-13'])):
    print('==',label,len(rs0))
    for lo,hi in [(0,.1),(.1,.3),(.3,.6),(.6,.8),(.8,.9),(.9,.95),(.95,.995),(.995,1.01)]:
        rs=[r for r in rs0 if lo<=r['participation_probability']<hi]
        if len(rs)>=20:
            mp=sum(r['participation_probability'] for r in rs)/len(rs); a,se,n=cl(rs,lambda r:1.0 if r['played'] else 0.0)
            print(f'  P[{lo:.3f},{hi:.3f}) n={n:4d} mean P {mp:.3f} played {a:.3f}±{se:.3f}  gap {a-mp:+.3f}')
    d,se,n=cl(rs0,lambda r:(1.0 if r['played'] else 0.0)-r['participation_probability']); b,bse,_=cl(rs0,lambda r:(r['participation_probability']-(1.0 if r['played'] else 0.0))**2)
    print(f'  all: played - P {d:+.4f}±{se:.4f}; Brier {b:.4f}')
