"""Live player-forecast review from performance.json (0.6.0 group). ± is one SE clustered by game date (UTC-8)."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json, math, collections, datetime, sys
P=OPERATIONS+'/performance.json'
p=json.load(open(P))
g=[x for x in p['groups'] if x['model_version'].endswith('0.6.0')][0]
rows=g['matched']
sel={ (s['run_id'],s['game_id'],s['player_id']):s for s in g['selected_forecasts']} if g['selected_forecasts'] and 'run_id' in g['selected_forecasts'][0] else {}
print('rows',len(rows),'keys',sorted(rows[0].keys()))
tz=datetime.timezone(datetime.timedelta(hours=-8))
def date(r): return datetime.datetime.fromisoformat(r['created_at']).astimezone(tz).date().isoformat()
def cl(vals_by_cluster):
    # mean and cluster SE of a per-row quantity
    tot=sum(sum(v) for v in vals_by_cluster.values()); n=sum(len(v) for v in vals_by_cluster.values())
    m=tot/n; G=len(vals_by_cluster)
    if G<2: return m,float('nan'),n
    s=sum((sum(v)-m*len(v))**2 for v in vals_by_cluster.values())
    se=math.sqrt(G/(G-1)*s)/n
    return m,se,n
def stat(rs,f):
    d=collections.defaultdict(list)
    for r in rs: d[date(r)].append(f(r))
    return cl(d)
def line(label,rs):
    if not rs: print(f'{label:34s} n=0'); return
    b,bse,n=stat(rs,lambda r:r['points_error']); a,ase,_=stat(rs,lambda r:abs(r['points_error']))
    mb,mbse,_=stat(rs,lambda r:r['minutes_error']); ma,_,_=stat(rs,lambda r:abs(r['minutes_error']))
    print(f'{label:34s} n={n:5d} pts bias {b:+.2f}±{bse:.2f} MAE {a:.2f} | min bias {mb:+.2f}±{mbse:.2f} MAE {ma:.2f}')
line('all matched',rows)
pl=[r for r in rows if r['played']]; dn=[r for r in rows if not r['played']]
line('played',pl); line('did not play',dn)
print('\n-- expected-value view: P*pred - actual (all rows)')
ev=lambda r:(r.get('participation_probability') or 1.0)*r['predicted_points']-r['actual_points']
b,se,n=stat(rows,ev); print(f'EV points bias {b:+.3f}±{se:.3f} n={n}')
evm=lambda r:(r.get('participation_probability') or 1.0)*r['predicted_minutes']-r['actual_minutes']
b,se,n=stat(rows,evm); print(f'EV minutes bias {b:+.3f}±{se:.3f}')
print('\n-- participation calibration')
for lo,hi in [(0,.3),(.3,.6),(.6,.8),(.8,.9),(.9,.95),(.95,.98),(.98,.995),(.995,1.01)]:
    rs=[r for r in rows if r.get('participation_probability') is not None and lo<=r['participation_probability']<hi]
    if rs:
        mp=sum(r['participation_probability'] for r in rs)/len(rs); pr,se,_=stat(rs,lambda r:1.0 if r['played'] else 0.0)
        print(f'P[{lo:.3f},{hi:.3f}) n={len(rs):5d} meanP {mp:.3f} played {pr:.3f}±{se:.3f}')
print('none P',sum(1 for r in rows if r.get('participation_probability') is None))
print('\n-- played, by predicted minutes')
for lo,hi in [(0,8),(8,16),(16,24),(24,30),(30,34),(34,60)]:
    line(f'pred min [{lo},{hi})',[r for r in pl if lo<=r['predicted_minutes']<hi])
print('\n-- played, by actual minutes')
for lo,hi in [(0,8),(8,16),(16,24),(24,30),(30,34),(34,60)]:
    line(f'actual min [{lo},{hi})',[r for r in pl if lo<=r['actual_minutes']<hi])
print('\n-- played, rate vs minutes component of the points error')
b,se,_=stat(pl,lambda r:r['minutes_component']); print(f'minutes component {b:+.3f}±{se:.3f}')
b,se,_=stat(pl,lambda r:r['rate_component']); print(f'rate component    {b:+.3f}±{se:.3f}')
print('\n-- by week (all matched / played)')
wk=collections.defaultdict(list)
for r in rows:
    d=datetime.date.fromisoformat(date(r)); wk[d.isocalendar()[1]].append(r)
for w in sorted(wk):
    rs=wk[w]; ds=sorted({date(r) for r in rs})
    line(f'wk{w} {ds[0][5:]}..{ds[-1][5:]} all',rs); line('     played',[r for r in rs if r['played']])
