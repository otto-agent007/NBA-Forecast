"""Capture recipe for this fixed-season dataset; writes new responses under /tmp."""
import pathlib,json,datetime,hashlib,urllib.request,unicodedata,re,concurrent.futures
ROOT=pathlib.Path(__file__).resolve().parents[3]; RAW=pathlib.Path('/tmp/history-raw');RAW.mkdir(exist_ok=True)
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def norm(s):return re.sub('[^a-z0-9]','',unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower())
teamcodes={'ATL':1610612737,'BOS':1610612738,'CLE':1610612739,'NOP':1610612740,'CHI':1610612741,'DAL':1610612742,'DEN':1610612743,'GSW':1610612744,'HOU':1610612745,'LAC':1610612746,'LAL':1610612747,'MIA':1610612748,'MIL':1610612749,'MIN':1610612750,'BKN':1610612751,'NYK':1610612752,'ORL':1610612753,'IND':1610612754,'PHI':1610612755,'PHX':1610612756,'POR':1610612757,'SAC':1610612758,'SAS':1610612759,'OKC':1610612760,'TOR':1610612761,'UTA':1610612762,'MEM':1610612763,'WAS':1610612764,'DET':1610612765,'CHA':1610612766}
alias={'NY':'NYK','GS':'GSW','NO':'NOP','SA':'SAS','UTAH':'UTA','WSH':'WAS','PHO':'PHX','BRK':'BKN'}
roster=json.loads((ROOT/'data/operations/production/roster.json').read_text())['players']; bykey={}
for p in roster:bykey.setdefault((p['team_id'],norm(p['name'])),[]).append(p)
failures=[];requests={}; refs=ROOT/'data/operations/production/references'
for f in refs.glob('*.json'):
 m=json.loads(f.read_text());url=m.get('source_url','')
 if not re.search(r'espn.com/.*/teams/\d+/roster$',url):continue
 j=json.loads((refs/'raw'/(m['sha256']+'.json')).read_text());code=j.get('team',{}).get('abbreviation');code=alias.get(code,code)
 if code in ('CHI','MIL'):continue
 if code not in teamcodes:failures.append({'roster_source':url,'error':'unknown team code '+str(code)});continue
 tid=str(teamcodes[code])
 for a in j.get('athletes',[]):
  name=a.get('fullName') or a.get('displayName');matches=bykey.get((tid,norm(name)),[])
  if len(matches)!=1:failures.append({'name':name,'team_id':tid,'error':'NBA roster exact normalized name match count '+str(len(matches))});continue
  p=matches[0]
  requests[str(a['id'])]=dict(player_id=p['player_id'],name=p['name'],team_id=tid,espn_id=str(a['id']),roster_source=m)
requests=sorted(requests.values(),key=lambda p:(p['name']!='Jalen Brunson',p['name']))
print('START',now(),'requests',len(requests),'mapping_failures',len(failures),flush=True)
seasons={'2023-24','2024-25','2025-26'}
def fetch(p):
 url='https://site.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/'+p['espn_id']+'/stats'
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'NBA-Forecast/0.1'}),timeout=12) as r:b=r.read(3000000)
  stamp=now();h=hashlib.sha256(b).hexdigest();(RAW/(h+'.json')).write_bytes(b)
  source=dict(source_url=url,retrieved_at=stamp,sha256=h)
  j=json.loads(b);cats={x['name']:x for x in j['categories']}
  avg=cats['averages'];tot=cats['totals']; avrows={}
  for row in avg.get('statistics',[]):
   sn=row.get('season',{}).get('displayName')
   if sn in seasons:
    key=(sn,row.get('teamId'),row.get('teamSlug'));avrows[key]=dict(zip(avg['names'],row['stats']))
  rows=[]
  for row in tot.get('statistics',[]):
   sn=row.get('season',{}).get('displayName');key=(sn,row.get('teamId'),row.get('teamSlug'))
   if sn not in seasons:continue
   if key not in avrows:raise ValueError('totals row lacks matching average row')
   a=avrows[key];t=dict(zip(tot['names'],row['stats']));gp=int(a['gamesPlayed']);mpg=float(a['avgMinutes'])
   rr=dict(season=sn,historical_team_espn_id=row.get('teamId'),historical_team_slug=row.get('teamSlug'),GP=gp,MIN=round(gp*mpg,3),MIN_per_game=mpg,minutes_precision='estimated_from_rounded_per_game',PTS=int(t['points']))
   for src,made,att in [('fieldGoalsMade-fieldGoalsAttempted','FGM','FGA'),('threePointFieldGoalsMade-threePointFieldGoalsAttempted','FG3M','FG3A'),('freeThrowsMade-freeThrowsAttempted','FTM','FTA')]:
    x,y=t[src].split('-');rr[made]=int(x);rr[att]=int(y)
   if 'turnovers' in t:rr['TOV']=int(t['turnovers'])
   if not(gp>0 and 0<mpg<=55 and rr['FGM']<=rr['FGA'] and rr['FG3M']<=rr['FG3A'] and rr['FTM']<=rr['FTA']):raise ValueError('invalid totals constraints')
   if rr['PTS']!=2*rr['FGM']+rr['FG3M']+rr['FTM']:raise ValueError('points do not equal made shots')
   rows.append(rr)
  deduplicated=[]
  for season in sorted(seasons):
   sr=[r for r in rows if r['season']==season]
   if len(sr)<=1:
    deduplicated.extend(sr);continue
   total=[r for r in sr if r['historical_team_slug']==season+' Totals']
   if len(total)!=1:raise ValueError('multiple team stints lack unique whole-season Totals row')
   whole=total[0];parts=[r for r in sr if r is not whole]
   for field in ['GP','PTS','FGM','FGA','FG3M','FG3A','FTM','FTA','TOV']:
    if sum(r[field] for r in parts)!=whole[field]:raise ValueError('whole-season Totals differs from team stints: '+field)
   deduplicated.append(whole)
  return dict(**p,rows=deduplicated,source=source),None
 except Exception as e:return None,dict(**p,error=repr(e),source_url=url)
result=dict(created_at=now(),requested_seasons=sorted(seasons),players=[],failures=failures)
def save():
 path=pathlib.Path('/tmp/historical-priors-full.json');temp=path.with_suffix('.tmp');temp.write_text(json.dumps(result,indent=2,sort_keys=True));temp.replace(path)
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
 futures={ex.submit(fetch,p):p for p in requests}
 for i,f in enumerate(concurrent.futures.as_completed(futures),1):
  p,error=f.result()
  if error:result['failures'].append(error)
  else:result['players'].append(p)
  if i%20==0 or p and p['name']=='Jalen Brunson':
   save();print('PROGRESS',i,'success',len(result['players']),'failures',len(result['failures']),now(),flush=True)
result['aggregation']='One row per player per season; ESPN whole-season Totals selected when traded across teams, verified equal sum of team stints for GP and all exact count totals. Minutes estimated from rounded season MIN_per_game.'
result['completed_at']=now();save();print('DONE',len(result['players']),len(result['failures']),now(),flush=True)
