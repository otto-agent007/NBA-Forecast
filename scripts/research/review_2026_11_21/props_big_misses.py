"""Who are the prop rows where the model's minutes miss is large? Live Kalshi medians joined to this season's box scores.
Context from earlier team games only (finals/): games the player missed just before, change in starting role, last-game minutes."""
import os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[3]
OPERATIONS = os.environ.get('NBA_OPERATIONS', str(REPO / 'data' / 'operations' / 'production'))  # read only
DATASETS = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
import json, glob, math, collections, os
PROD=OPERATIONS
meds=[]
for f in sorted(glob.glob(PROD+'/market/kalshi-props/scored/*.json')):
    d=json.load(open(f))
    if d['entry'].get('scored'):
        for m in d['medians']: m['start']=d['entry']['start_time']; meds.append(m)
idx={}
for g in json.load(open(PROD+'/performance.json'))['groups']:
    for r in g['matched']: idx[(r['game_id'],r['player_id'])]=r
fin={}
for f in glob.glob(PROD+'/finals/*.json'):
    rows=json.load(open(f))
    if rows: fin[rows[0]['game_id']]=rows
print('finals',len(fin),'fields',sorted(fin[next(iter(fin))][0].keys())[:40])
team_games=collections.defaultdict(list)
for gid,rows in fin.items():
    for t in {r['team_id'] for r in rows}: team_games[t].append((rows[0]['game_time'],gid))
for t in team_games: team_games[t].sort()
def context(m):
    r=idx[(m['game_id'],m['player_id'])]; t=r['team_id']
    prior=[gid for tm,gid in team_games[t] if tm<m['start']]
    seq=[]
    for gid in prior:
        row=next((x for x in fin[gid] if x['player_id']==m['player_id'] and x['team_id']==t),None)
        seq.append(row)
    played=[x for x in seq if x and x['status']=='played' and x['minutes']>0]
    missed_before=0
    for x in reversed(seq):
        if x and x['status']=='played' and x['minutes']>0: break
        missed_before+=1
    this=next((x for x in fin[m['game_id']] if x['player_id']==m['player_id']),None)
    last=played[-1] if played else None
    return dict(missed_before=missed_before,games=len(played),last_min=last['minutes'] if last else None,
                last_start=last.get('starter') if last else None,start=this.get('starter') if this else None,
                l5=sum(x['minutes'] for x in played[-5:])/len(played[-5:]) if played else None,
                sd5=(lambda v:(sum((a-sum(v)/len(v))**2 for a in v)/len(v))**.5)([x['minutes'] for x in played[-5:]]) if len(played)>=3 else None)
rows=[]
for m in meds:
    if (m['game_id'],m['player_id']) not in idx: continue
    r=idx[(m['game_id'],m['player_id'])]; c=context(m); rows.append(dict(m,**c,me=r['minutes_error'],pm=r['predicted_minutes'],am=r['actual_minutes'],d=m['model_abs_error']-m['market_abs_error'],team=r['team_id']))
def show(label,rs):
    if not rs: return
    n=len(rs); print(f'{label:46s} n={n:4d} ({100*n/len(rows):4.1f}%) model-market {sum(r["d"] for r in rs)/n:+.3f} (sum {sum(r["d"] for r in rs):+6.1f}) | minutes bias {sum(r["me"] for r in rs)/n:+.2f} MAE {sum(abs(r["me"]) for r in rs)/n:.2f} | model pts bias {sum(r["model_points"]-r["actual_points"] for r in rs)/n:+.2f} market {sum(r["market_line"]-r["actual_points"] for r in rs)/n:+.2f}')
print('total model-market sum %.1f over %d rows'%(sum(r['d'] for r in rows),len(rows)))
show('all',rows)
show('missed the game(s) just before (returning)',[r for r in rows if r['missed_before']>=1])
show('  missed 1-2',[r for r in rows if 1<=r['missed_before']<=2]); show('  missed 3+',[r for r in rows if r['missed_before']>=3])
show('played the previous game',[r for r in rows if r['missed_before']==0])
show('start changed vs last game: bench -> start',[r for r in rows if r['last_start'] is False and r['start'] is True])
show('start changed vs last game: start -> bench',[r for r in rows if r['last_start'] is True and r['start'] is False])
show('same role: starter',[r for r in rows if r['last_start'] is True and r['start'] is True]); show('same role: bench',[r for r in rows if r['last_start'] is False and r['start'] is False])
show('fewer than 5 games this season',[r for r in rows if r['games']<5])
show('last-5 minutes sd >= 6',[r for r in rows if r['sd5'] is not None and r['sd5']>=6]); show('last-5 minutes sd < 3',[r for r in rows if r['sd5'] is not None and r['sd5']<3])
show('forecast >= last-5 avg + 3',[r for r in rows if r['l5'] is not None and r['pm']>=r['l5']+3]); show('forecast <= last-5 avg - 3',[r for r in rows if r['l5'] is not None and r['pm']<=r['l5']-3])
# teammates: count of team regulars (l5>=24) who missed -> returned etc. left for the replay prototype
