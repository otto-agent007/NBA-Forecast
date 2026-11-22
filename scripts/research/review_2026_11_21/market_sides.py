"""Would acting on the model have beaten each market? Descriptive, at recorded prices.
± one SE clustered by Pacific game date. Kalshi fee (taker) = 0.07*p*(1-p) per contract; DraftKings sides priced at -110 (break-even 52.4%)."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json, glob, math, collections, datetime
PROD=OPERATIONS
def cl(rs,f,key='date'):
    d=collections.defaultdict(list)
    for r in rs: d[r[key]].append(f(r))
    n=sum(len(v) for v in d.values())
    if not n: return float('nan'),float('nan'),0
    m=sum(sum(v) for v in d.values())/n; G=len(d)
    se=math.sqrt(G/(G-1)*sum((sum(v)-m*len(v))**2 for v in d.values()))/n if G>1 else float('nan')
    return m,se,n
tz=datetime.timezone(datetime.timedelta(hours=-8))
games=[g for g in json.load(open(PROD+'/market/report.json'))['games'] if g.get('model_margin') is not None and g.get('market_margin') is not None]
for g in games: g['date']=datetime.datetime.fromisoformat(g['start_time']).astimezone(tz).date().isoformat()
print('== DraftKings close (ESPN provider 100): spread and total sides the model leaned to')
def sides(rs,label):
    for t in (0,1,2,3,4):
        sp=[g for g in rs if abs(g['spread_edge'])>=t and g['actual_margin']-g['market_margin']!=0]
        w,se,n=cl(sp,lambda g:1.0 if (g['spread_edge']>0)==(g['actual_margin']>g['market_margin']) else 0.0)
        to=[g for g in rs if g.get('total_edge') is not None and abs(g['total_edge'])>=t and g['actual_total']!=g['total_line']]
        tw,tse,tn=cl(to,lambda g:1.0 if (g['total_edge']>0)==(g['actual_total']>g['total_line']) else 0.0)
        ov=sum(1 for g in to if g['total_edge']>0)
        print(f'  {label} edge>={t}: spread {w:.3f}±{se:.3f} (n={n}) | total {tw:.3f}±{tse:.3f} (n={tn}, model on the over in {ov})')
sides(games,'all 202     ')
sides([g for g in games if 'market_strength' in g['margin_method']],'v4 since 11-08')
sides([g for g in games if g['date']>='2026-11-15'],'since 11-15 ')
print('  winner: model Brier vs DraftKings no-vig moneyline, same games')
for label,rs in (('all',games),('v4',[g for g in games if 'market_strength' in g['margin_method']])):
    rs=[g for g in rs if g.get('model_home_win') is not None and g.get('market_home_win') is not None]
    d,se,n=cl(rs,lambda g:(g['model_home_win']-(g['actual_margin']>0))**2-(g['market_home_win']-(g['actual_margin']>0))**2)
    print(f'   {label}: model - market Brier {d:+.4f}±{se:.4f} n={n}')
print('\n== Kalshi game markets: fields available', sorted(k for k in games[-1] if 'kalshi' in k))
kg=[g for g in games if g.get('kalshi_home_win') is not None and g.get('model_home_win') is not None]
if kg:
    d,se,n=cl(kg,lambda g:(g['model_home_win']-(g['actual_margin']>0))**2-(g['kalshi_home_win']-(g['actual_margin']>0))**2)
    print(f'  winner Brier model - Kalshi {d:+.4f}±{se:.4f} n={n}')
    for t in (.0,.03,.06,.10):
        b=[g for g in kg if abs(g['model_home_win']-g['kalshi_home_win'])>=t]
        def pnl(g):
            p=g['kalshi_home_win']; y=1.0 if g['actual_margin']>0 else 0.0
            return (y-p) if g['model_home_win']>p else (p-y)
        m,se,n=cl(b,pnl); fee=sum(.07*g['kalshi_home_win']*(1-g['kalshi_home_win']) for g in b)/max(1,len(b))
        print(f'  buy the model side at the Kalshi mid when |model-Kalshi|>={t:.2f}: {100*m:+.1f}±{100*se:.1f} cents per contract before fees (fee {100*fee:.1f}c), n={n}')
print('\n== Kalshi player points rungs (last capture before tip)')
rungs=[]
for f in sorted(glob.glob(PROD+'/market/kalshi-props/scored/*.json')):
    d=json.load(open(f))
    if d['entry'].get('scored'): rungs+=d['rungs']
print('  rungs',len(rungs),'dates',len({r['date'] for r in rungs}))
for name,key in (('interval model','model'),('sd model','model_sd')):
    for t in (0,.03,.06,.10,.15):
        b=[r for r in rungs if abs(r[key]-r['market'])>=t and 0.02<r['market']<0.98]
        def pnl(r):
            y=1.0 if r['outcome'] else 0.0
            return (y-r['market']) if r[key]>r['market'] else (r['market']-y)
        m,se,n=cl(b,pnl); fee=sum(.07*r['market']*(1-r['market']) for r in b)/max(1,len(b))
        yes=sum(1 for r in b if r[key]>r['market'])
        print(f'  {name}: |model-market|>={t:.2f}: {100*m:+.2f}±{100*se:.2f} cents per contract before fees (fee {100*fee:.1f}c), n={n}, bought yes {yes}')
# by side
for side in ('yes','no'):
    b=[r for r in rungs if abs(r['model']-r['market'])>=.06 and 0.02<r['market']<0.98 and ((r['model']>r['market'])==(side=='yes'))]
    m,se,n=cl(b,lambda r:((1.0 if r['outcome'] else 0.0)-r['market'])*(1 if side=='yes' else -1))
    print(f'  interval model, |diff|>=.06, {side} side only: {100*m:+.2f}±{100*se:.2f}c n={n}')
m,se,n=cl(rungs,lambda r:(1.0 if r['outcome'] else 0.0)-r['market']); print(f'  all rungs: outcome - market price {100*m:+.2f}±{100*se:.2f}c (market over-price bias), model - outcome {100*cl(rungs,lambda r:r["model"]-(1.0 if r["outcome"] else 0.0))[0]:+.2f}c')
