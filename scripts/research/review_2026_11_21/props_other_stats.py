"""Model vs Kalshi implied medians for every captured stat (points, rebounds, assists, threes), closing capture of each scored game.
Model value = the stored forecast's predicted stat (conditional on playing), actual from the box score; played players only.
± one SE clustered by game date."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json, glob, math, collections
PROD=OPERATIONS
idx={}
for g in json.load(open(PROD+'/performance.json'))['groups']:
    for r in g['matched']: idx[(r['game_id'],r['player_id'])]=r
KEY={'points':None,'rebounds':'reb','assists':'ast','threes':'fg3m'}
rows=collections.defaultdict(list); nomid=collections.Counter()
for f in sorted(glob.glob(PROD+'/market/kalshi-props/scored/*.json')):
    d=json.load(open(f))
    if not d['entry'].get('scored'): continue
    cap=None
    for line in open(f'{PROD}/market/kalshi-props/{d["game_id"]}.jsonl'):
        c=json.loads(line)
        if c['captured_at']==d['entry']['close_captured_at']: cap=c
    for p in cap['players']:
        r=idx.get((d['game_id'],p['player_id']))
        if not r or not r['played'] or r['run_id']!=d['entry']['run_id']: continue
        for stat,key in KEY.items():
            s=p['stats'].get(stat) or {}
            med=s.get('implied_median')
            if med is None: nomid[stat]+=1; continue
            if key is None: model,actual=r['predicted_points'],r['actual_points']
            else:
                fb=r['stat_feedback'].get(key)
                if not fb: continue
                model,actual=fb['predicted'],fb['actual']
            rows[stat].append(dict(date=d['entry']['date'],model=model,market=med,actual=actual))
def cl(rs,f):
    d=collections.defaultdict(list)
    for r in rs: d[r['date']].append(f(r))
    n=sum(len(v) for v in d.values()); m=sum(sum(v) for v in d.values())/n; G=len(d)
    return m, math.sqrt(G/(G-1)*sum((sum(v)-m*len(v))**2 for v in d.values()))/n, n
for stat,rs in rows.items():
    mm=cl(rs,lambda r:abs(r['model']-r['actual'])); mk=cl(rs,lambda r:abs(r['market']-r['actual'])); d=cl(rs,lambda r:abs(r['model']-r['actual'])-abs(r['market']-r['actual']))
    x=[r['model']-r['market'] for r in rs]; y=[r['actual']-r['market'] for r in rs]; b=sum(a*c for a,c in zip(x,y))/sum(a*a for a in x)
    side=cl([r for r in rs if r['model']!=r['market'] and r['actual']!=r['market']],lambda r:1.0 if (r['model']>r['market'])==(r['actual']>r['market']) else 0.0)
    print(f'{stat:9s} n={d[2]:4d} MAE model {mm[0]:.3f} market {mk[0]:.3f} diff {d[0]:+.3f}±{d[1]:.3f} ({100*d[0]/mk[0]:+.1f}%) | model bias {cl(rs,lambda r:r["model"]-r["actual"])[0]:+.2f} market {cl(rs,lambda r:r["market"]-r["actual"])[0]:+.2f} | slope {b:+.2f} | model side of the median right {side[0]:.3f}±{side[1]:.3f} (n={side[2]}) | no median {nomid[stat]}')
