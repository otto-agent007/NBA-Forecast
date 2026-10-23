"""Verified career season totals as priors, never invented game observations."""
import hashlib,json,re
from pathlib import Path
from .validation import parse_time
from .injury_parser import normalized


def season_prior(snapshot,player_id):
    item=next((p for p in snapshot.get('historical_priors',{}).get('players',[]) if p['player_id']==player_id),None)
    if not item or parse_time(item['source']['retrieved_at'])>parse_time(snapshot['as_of']):return None
    year=int(snapshot['season'][:4]);rows=[r for r in item['rows'] if 1<=year-int(r['season'][:4])<=3 and r['GP']>0 and r['MIN']>0]
    if not rows:return None
    weights=[min(r['GP'],20)*(0.65**(year-int(r['season'][:4])-1))/r['GP'] for r in rows]
    totals={k:sum(r[k]*w for r,w in zip(rows,weights)) for k in ('GP','MIN','PTS','FGM','FGA','FG3M','FG3A','FTM','FTA','TOV')}
    return dict(totals=totals,seasons=[dict(season=r['season'],games=r['GP'],ppg=r['PTS']/r['GP'],minutes_per_game=r['MIN_per_game'],weight=w) for r,w in zip(rows,weights)],
        source=item['source'],minutes_precision='Estimated from rounded season MPG; shot and point totals are exact.',policy='Prior season totals replace overlapping game-history priors; no double counting.')


def verify_priors(snapshot,directory):
    roster={p['player_id']:p for p in snapshot['roster']['players']}
    for item in snapshot.get('historical_priors',{}).get('players',[]):
        if item['player_id'] not in roster or item['name']!=roster[item['player_id']]['name']:raise ValueError('Historical prior player identity differs')
        linked=item['roster_source']
        if not re.fullmatch(r'https://site\.api\.espn\.com/apis/site/v2/sports/basketball/nba/teams/\d{1,3}/roster',linked['source_url']) or not re.fullmatch('[a-f0-9]{64}',linked['sha256']):raise ValueError('Historical identity roster source is invalid')
        if parse_time(linked['retrieved_at'])>parse_time(snapshot['as_of']):raise ValueError('Historical identity mapping is future-known')
        roster_raw=(Path(directory)/'references/raw'/(linked['sha256']+'.json')).read_bytes()
        if hashlib.sha256(roster_raw).hexdigest()!=linked['sha256']:raise ValueError('Historical identity archive hash mismatch')
        roster_payload=json.loads(roster_raw)
        matches=[p for p in roster_payload['athletes'] if str(p['id'])==item['espn_id'] and normalized(p.get('fullName') or p['displayName'])==normalized(item['name'])]
        if len(matches)!=1 or roster_payload['season']['displayName']!=snapshot['season']:raise ValueError('Historical ESPN to NBA player identity is not verified')
        meta=item['source']
        if not re.fullmatch(r'https://site\.api\.espn\.com/apis/common/v3/sports/basketball/nba/athletes/'+re.escape(item['espn_id'])+r'/stats',meta['source_url']):raise ValueError('Historical source identity differs')
        if parse_time(meta['retrieved_at'])>parse_time(snapshot['as_of']):raise ValueError('Historical prior is future-known')
        if not re.fullmatch('[a-f0-9]{64}',meta['sha256']):raise ValueError('Historical prior hash missing')
        raw=(Path(directory)/'historical-raw'/(meta['sha256']+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('Historical archive hash mismatch')
        categories={c['name']:c for c in json.loads(raw)['categories']};seen=set()
        for r in item['rows']:
            if r['season'] in seen or not 1<=int(snapshot['season'][:4])-int(r['season'][:4])<=3:raise ValueError('Duplicate or nonhistorical season prior')
            seen.add(r['season'])
            def matching(category):
                return [v for v in categories[category]['statistics'] if v.get('teamSlug')==r['historical_team_slug'] and str(v.get('teamId'))==str(r['historical_team_espn_id']) and (v.get('season',{}).get('displayName')==r['season'] or v.get('teamSlug')==r['season']+' Totals')]
            avg,total=matching('averages'),matching('totals')
            if len(avg)!=1 or len(total)!=1:raise ValueError('Historical season identity is ambiguous')
            a=dict(zip(categories['averages']['names'],avg[0]['stats']));t=dict(zip(categories['totals']['names'],total[0]['stats']))
            expected={'GP':int(a['gamesPlayed']),'MIN_per_game':float(a['avgMinutes']),'PTS':int(t['points']),'TOV':int(t['turnovers'])}
            for key,fields in [('fieldGoalsMade-fieldGoalsAttempted',('FGM','FGA')),('threePointFieldGoalsMade-threePointFieldGoalsAttempted',('FG3M','FG3A')),('freeThrowsMade-freeThrowsAttempted',('FTM','FTA'))]:
                expected.update(zip(fields,map(int,t[key].split('-'))))
            if any(r[k]!=v for k,v in expected.items()) or abs(r['MIN']-r['GP']*r['MIN_per_game'])>.00001 or r['PTS']!=2*r['FGM']+r['FG3M']+r['FTM']:
                raise ValueError('Historical prior differs from archived season totals')
