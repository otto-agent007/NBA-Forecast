"""Has the live evidence on the team-total shortfall changed since PR #47 was put on hold (2026-11-10, 122 games)?
Constant stand-in: add k points per team (2k per game total) to the published model total; MAE vs the final total.
± one SE clustered by Pacific date. Final totals include overtime."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json, math, collections, datetime
R=OPERATIONS+'/market/report.json'
tz=datetime.timezone(datetime.timedelta(hours=-8))
G=[g for g in json.load(open(R))['games'] if g.get('model_total') is not None and g.get('total_line') is not None]
for g in G: g['date']=datetime.datetime.fromisoformat(g['start_time']).astimezone(tz).date().isoformat()
def cl(rs,f):
    d=collections.defaultdict(list)
    for r in rs: d[r['date']].append(f(r))
    n=sum(len(v) for v in d.values()); m=sum(sum(v) for v in d.values())/n; K=len(d)
    return m, math.sqrt(K/(K-1)*sum((sum(v)-m*len(v))**2 for v in d.values()))/n, n
for label,rs in (('through 11-09 (hold decision)',[g for g in G if g['date']<='2026-11-09']),('11-10 onward',[g for g in G if g['date']>='2026-11-10']),('all',G)):
    b,se,n=cl(rs,lambda g:g['model_total']-g['actual_total']); c,cse,_=cl(rs,lambda g:g['total_line']-g['actual_total'])
    print(f'{label}: n={n} model total - actual {b:+.2f}±{se:.2f}; close - actual {c:+.2f}±{cse:.2f}; model - close {cl(rs,lambda g:g["model_total"]-g["total_line"])[0]:+.2f}')
    for k in (1,2,3.2):
        d,dse,_=cl(rs,lambda g:abs(g['model_total']+2*k-g['actual_total'])-abs(g['model_total']-g['actual_total']))
        print(f'    +{k} per team: total MAE change {d:+.2f}±{dse:.2f}')
