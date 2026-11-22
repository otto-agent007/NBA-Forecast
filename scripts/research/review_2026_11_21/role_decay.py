"""Is the role-minutes decay (0.75 a game, about four games of memory) too fast? Offline on the reference replay rows.
For each projected player who played, with three or more earlier games this season for this team:
  plain(d) = his earlier played minutes weighted d^age. Substitution: m' = m_model + plain(d) - plain(0.75).
Also the raw predictors alone, and the lineup-context weights on top. Decay picked on two seasons, scored on the third.
± one SE clustered by game. Only earlier games are used."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json,gzip,math,collections,sys
REPO=DATASETS
ref=json.load(gzip.open(REPO+(sys.argv[1] if len(sys.argv)>1 else 'backtest-2026-11-12-margins-v5')+'/rows.json.gz'))
R=[dict(zip(ref['player_fields'],x)) for x in ref['players']]
H=[]
for f in ('espn-season-2023-24','espn-season-2024-25','regular-season-expanded-2026-10-22'):
    H+=json.load(gzip.open(REPO+f+'/history.json.gz'))
H=[r for r in H if r['season_type']=='Regular Season']
games=collections.defaultdict(dict); gtime={}; season_of={}
for r in H:
    games[(r['season'],r['team_id'])].setdefault(r['game_id'],{})[r['player_id']]=r['minutes'] if r['status']=='played' else 0.
    gtime[r['game_id']]=r['game_time']; season_of[r['game_id']]=r['season']
order={k:sorted(v,key=lambda g:gtime[g]) for k,v in games.items()}
byteam=collections.defaultdict(list)
for p in R: byteam[(p['game_id'],p['team_id'])].append(p)
feats=[]
for (g,t),rows in byteam.items():
    k=(season_of[g],t); seq=order[k]; prior=seq[:seq.index(g)]
    if len(prior)<3: continue
    tot=collections.Counter()
    for j in prior:
        for pid,m in games[k][j].items(): tot[pid]+=m
    state={}
    for p in rows:
        if p['expected_minutes'] is None or (p['participation_probability'] or 0)<.3: state[p['player_id']]=0
        elif p['participation_probability']>=.5: state[p['player_id']]=1
    for p in rows:
        if p['expected_minutes'] is None or p['actual_status']!='played' or not p['actual_minutes']>0: continue
        pid=p['player_id']; key=[x for x,_ in tot.most_common(9) if x!=pid][:8]; key=[x for x in key if x in state]
        hist=[(games[k][j][pid],sum(1 for x in key if (games[k][j].get(x,0)>0)!=bool(state[x]))) for j in reversed(prior) if games[k][j].get(pid,0)>0]
        if len(hist)>=3: feats.append((p,hist))
def season(gid): return 'A' if gid.startswith('espn-4015') else 'B' if gid.startswith('espn-') else 'C'
def mean(hist,d,lam=1.):
    n=s=0.
    for age,(m,k) in enumerate(hist):
        w=d**age*lam**k; n+=w; s+=w*m
    return s/n
def cl(items,f):
    d=collections.defaultdict(float); c=collections.Counter()
    for it in items: d[it[0]['game_id']]+=f(it); c[it[0]['game_id']]+=1
    n=sum(c.values()); m=sum(d.values())/n; K=len(d)
    return m, math.sqrt(K/(K-1)*sum((d[k]-m*c[k])**2 for k in d))/n, n
DEC=(.75,.8,.85,.9,.93,.96,1.)
base=[mean(h,.75) for _,h in feats]
alt={(d,lam):[mean(h,d,lam) for _,h in feats] for d in DEC for lam in (1.,.5,.3)}
err0=[abs(p['expected_minutes']-p['actual_minutes']) for p,_ in feats]
print('rows',len(feats),'| reference minutes MAE %.3f'%(sum(err0)/len(err0)))
print('raw predictors alone (no 240 budget, no priors): MAE by decay:',{d:round(sum(abs(alt[(d,1.)][i]-feats[i][0]['actual_minutes']) for i in range(len(feats)))/len(feats),3) for d in DEC})
def new(i,key,beta=1.): return max(0.,min(48.,feats[i][0]['expected_minutes']+beta*(alt[key][i]-base[i])))
idx={s:[i for i,(p,_) in enumerate(feats) if season(p['game_id'])==s] for s in 'ABC'}
def gain(ids,key,beta=1.): return sum(abs(new(i,key,beta)-feats[i][0]['actual_minutes'])-err0[i] for i in ids)/len(ids)
print('substitution gain by decay, all seasons (no context):',{d:round(gain(range(len(feats)),(d,1.)),4) for d in DEC})
for lams,label in (((1.,),'decay only'),((1.,.5,.3),'decay + lineup context')):
    for held in 'ABC':
        tr=[i for s in 'ABC' if s!=held for i in idx[s]]
        best=min(((d,lam,b) for d in DEC for lam in lams for b in (.5,.75,1.)),key=lambda g:gain(tr,(g[0],g[1]),g[2]))
        te=[(feats[i][0],i) for i in idx[held]]; key=(best[0],best[1]); b=best[2]
        dm=cl(te,lambda it:abs(new(it[1],key,b)-it[0]['actual_minutes'])-err0[it[1]])
        dp=cl(te,lambda it:abs(it[0]['points_mean']*new(it[1],key,b)/it[0]['expected_minutes']-it[0]['actual_points'])-abs(it[0]['points_mean']-it[0]['actual_points']) if it[0]['expected_minutes']>0 else 0.)
        print(f'{label}: held {held}: chose decay,lambda,beta={best}; held-out minutes MAE {dm[0]:+.4f}±{dm[1]:.4f} points MAE {dp[0]:+.4f}±{dp[1]:.4f} (n={dm[2]})')
# does the slower decay remove the high-minute bias?
key=(.9,1.)
for lo,hi in [(0,8),(8,16),(16,24),(24,30),(30,34),(34,36),(36,38),(38,60)]:
    ids=[i for i,(p,_) in enumerate(feats) if lo<=p['expected_minutes']<hi]
    if len(ids)<50: continue
    b0=sum(feats[i][0]['expected_minutes']-feats[i][0]['actual_minutes'] for i in ids)/len(ids); b1=sum(new(i,key)-feats[i][0]['actual_minutes'] for i in ids)/len(ids)
    print(f'  forecast [{lo},{hi}) n={len(ids)} minutes bias today {b0:+.2f} -> decay 0.9 {b1:+.2f}; MAE change {gain(ids,key):+.3f}')
