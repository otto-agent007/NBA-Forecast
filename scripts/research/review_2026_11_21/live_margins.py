"""Live margins and totals vs the close, from market/report.json. ± one SE clustered by Pacific game date."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json, math, collections, datetime
R=OPERATIONS+'/market/report.json'
games=json.load(open(R))['games']
tz=datetime.timezone(datetime.timedelta(hours=-8))
for g in games: g['date']=datetime.datetime.fromisoformat(g['start_time']).astimezone(tz).date().isoformat()
def cl(rs,f):
    d=collections.defaultdict(list)
    for r in rs: d[r['date']].append(f(r))
    n=sum(len(v) for v in d.values()); m=sum(sum(v) for v in d.values())/n; G=len(d)
    se=math.sqrt(G/(G-1)*sum((sum(v)-m*len(v))**2 for v in d.values()))/n if G>1 else float('nan')
    return m,se,n
ok=[g for g in games if g.get('model_margin') is not None and g.get('market_margin') is not None]
def show(label,rs):
    if len(rs)<5: print(f'{label:30s} n={len(rs)}'); return
    d,se,n=cl(rs,lambda g:abs(g['model_margin']-g['actual_margin'])-abs(g['market_margin']-g['actual_margin']))
    mm=cl(rs,lambda g:abs(g['model_margin']-g['actual_margin']))[0]; mk=cl(rs,lambda g:abs(g['market_margin']-g['actual_margin']))[0]
    hb,hse,_=cl(rs,lambda g:g['model_margin']-g['actual_margin']); kb,kse,_=cl(rs,lambda g:g['market_margin']-g['actual_margin'])
    td,tse,_=cl(rs,lambda g:abs(g['model_total']-g['actual_total'])-abs(g['total_line']-g['actual_total']))
    tb,tbse,_=cl(rs,lambda g:g['model_total']-g['actual_total']); tk,tkse,_=cl(rs,lambda g:g['total_line']-g['actual_total'])
    print(f'{label:30s} n={n:3d} margin MAE model {mm:.2f} close {mk:.2f} diff {d:+.2f}±{se:.2f} home bias {hb:+.2f}±{hse:.2f} (close {kb:+.2f}) | total diff {td:+.2f}±{tse:.2f} bias model {tb:+.2f}±{tbse:.2f} close {tk:+.2f}±{tkse:.2f}')
ok=[g for g in ok if g.get('model_total') is not None and g.get('total_line') is not None]
show('all',ok)
for fam in sorted({g['margin_method'] for g in ok}):
    show(fam.replace('paired_final_score_outcome_dispersion_','').replace('_discrete_normal','')[:30],[g for g in ok if g['margin_method']==fam])
print()
for lo,hi in [('2026-10-24','2026-10-31'),('2026-11-01','2026-11-07'),('2026-11-08','2026-11-14'),('2026-11-15','2026-11-21')]:
    show(f'{lo[5:]}..{hi[5:]}',[g for g in ok if lo<=g['date']<=hi])
v4=[g for g in ok if 'market_strength' in g['margin_method']]
print('\n-- v4 games: by |model - close|')
for lo,hi in [(0,2),(2,4),(4,6),(6,99)]:
    show(f'|edge| [{lo},{hi})',[g for g in v4 if lo<=abs(g['model_margin']-g['market_margin'])<hi])
def slope(rs):
    x=[g['model_margin']-g['market_margin'] for g in rs]; y=[g['actual_margin']-g['market_margin'] for g in rs]
    sxx=sum(a*a for a in x); b=sum(a*c for a,c in zip(x,y))/sxx
    d=collections.defaultdict(float)
    for g,a,c in zip(rs,x,y): d[g['date']]+=a*(c-b*a)
    G=len(d); return b, math.sqrt(G/(G-1)*sum(v*v for v in d.values()))/sxx
print('slope (actual-close) on (model-close): all %.2f±%.2f, v4 %.2f±%.2f'%(slope(ok)+slope(v4)))
print('mean |model-close| v4 %.2f'%(sum(abs(g['model_margin']-g['market_margin']) for g in v4)/len(v4)))
# is the model too extreme or too timid? slope of actual on model
def reg(rs,xf,yf):
    x=[xf(g) for g in rs]; y=[yf(g) for g in rs]; mx=sum(x)/len(x); my=sum(y)/len(y)
    return sum((a-mx)*(b-my) for a,b in zip(x,y))/sum((a-mx)**2 for a in x)
print('slope actual on model margin: v4 %.2f ; actual on close %.2f ; sd model %.2f sd close %.2f'%(reg(v4,lambda g:g['model_margin'],lambda g:g['actual_margin']),reg(v4,lambda g:g['market_margin'],lambda g:g['actual_margin']),
   (sum(g['model_margin']**2 for g in v4)/len(v4))**.5,(sum(g['market_margin']**2 for g in v4)/len(v4))**.5))
print('player-sum margin vs published (v4): MAE player-sum %.2f published %.2f'%(sum(abs(g['player_sum_margin']-g['actual_margin']) for g in v4 if g.get('player_sum_margin') is not None)/len(v4),sum(abs(g['model_margin']-g['actual_margin']) for g in v4)/len(v4)))
