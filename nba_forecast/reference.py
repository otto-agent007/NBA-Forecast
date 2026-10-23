"""Bounded, archived ESPN references for an independent input cross-check."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.request
from zoneinfo import ZoneInfo

from .data import _NoRedirects,MAX_BYTES,_NextDataParser,HEADERS
from .schedule import atomic_json
from .validation import parse_time

BASE='https://site.api.espn.com/apis/site/v2/sports/basketball/nba/'
ALIASES={'NY':'NYK','GS':'GSW','NO':'NOP','SA':'SAS','UTAH':'UTA','WSH':'WAS','PHO':'PHX','BRK':'BKN'}


def code(value):return ALIASES.get(value,value)


def decode_reference(raw,url):
    if url.startswith('https://www.nba.com/team/'):
        parser=_NextDataParser();parser.feed(raw.decode('utf-8'))
        return json.loads(''.join(parser.parts))['props']['pageProps']['team']
    return json.loads(raw)


def fetch_reference(url,directory,max_age=300):
    if not (re.fullmatch(re.escape(BASE)+r'(scoreboard\?dates=\d{8}|teams/\d{1,3}/roster|summary\?event=\d{6,12})',url) or
            re.fullmatch(r'https://www\.nba\.com/team/16106127(?:3[7-9]|[45]\d|6[0-6])/roster',url)):
        raise ValueError('reference URL is not an allowlisted public ESPN endpoint')
    folder=Path(directory);key=hashlib.sha256(url.encode()).hexdigest();pointer=folder/(key+'.json')
    now=datetime.now(timezone.utc)
    if pointer.exists():
        meta=json.loads(pointer.read_text());raw_path=folder/'raw'/(meta['sha256']+'.json')
        age=(now-parse_time(meta['retrieved_at'])).total_seconds()
        if 0<=age<max_age and raw_path.exists():
            raw=raw_path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('reference archive hash mismatch')
            return decode_reference(raw,url),meta
    opener=urllib.request.build_opener(_NoRedirects())
    headers=HEADERS if url.startswith('https://www.nba.com/team/') else {'User-Agent':'NBA-Forecast/0.1'}
    with opener.open(urllib.request.Request(url,headers=headers),timeout=12) as response:
        raw=response.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('reference response exceeds size limit')
    payload=decode_reference(raw,url)
    if not isinstance(payload,dict):raise ValueError('reference response is not an object')
    meta=dict(source_url=url,retrieved_at=datetime.now(timezone.utc).isoformat(),sha256=hashlib.sha256(raw).hexdigest())
    path=folder/'raw'/(meta['sha256']+'.json');path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():path.write_bytes(raw)
    atomic_json(pointer,meta)
    return payload,meta


def collect_references(snapshot,directory,roster_reviews=()):
    from .availability import needs_empty_report,summary_no_injuries
    games=[];rosters={};sources=[];boards={};empty=[];official={}
    for game in snapshot['games']:
        date=parse_time(game['start_time']).astimezone(ZoneInfo('America/New_York')).strftime('%Y%m%d')
        new_date=date not in boards
        if date not in boards:
            boards[date],meta=fetch_reference(BASE+'scoreboard?dates='+date,directory)
            sources.append(meta)
        candidates=[]
        for event in boards[date].get('events',[]):
            competition=event['competitions'][0]
            sides={c['homeAway']:c['team'] for c in competition['competitors']}
            if set(sides)!={'home','away'}:continue
            normalized=dict(home=code(sides['home']['abbreviation']),away=code(sides['away']['abbreviation']),
                            start_time=competition.get('date') or event['date'],state=competition.get('status',event.get('status',{})).get('type',{}).get('state'),
                            status_name=competition.get('status',event.get('status',{})).get('type',{}).get('name'),
                            provider_event_id=event.get('id') or competition.get('id'))
            if new_date:games.append(normalized)
            if (normalized['home'],normalized['away'])==(game.get('home_tricode'),game.get('away_tricode')):
                candidates.append((sides,normalized))
        if len(candidates)!=1:continue
        sides,matched=candidates[0]
        for side,team in sides.items():
            tid=game[side+'_team_id']
            if tid in rosters:continue
            payload,meta=fetch_reference(BASE+'teams/'+str(team['id'])+'/roster',directory,max_age=900)
            if str(payload.get('team',{}).get('id'))!=str(team['id']):raise ValueError('reference roster team identity mismatch')
            if payload.get('season',{}).get('displayName')!=snapshot['season']:raise ValueError('reference roster season mismatch')
            names=[p.get('fullName') or p['displayName'] for p in payload.get('athletes',[])]
            if not names:raise ValueError('reference roster is empty')
            rosters[tid]=names;sources.append(meta)
            if any(r.get('team_id')==tid and r.get('game_id')==game['game_id'] for r in roster_reviews):
                team_payload,team_meta=fetch_reference(f'https://www.nba.com/team/{tid}/roster',directory)
                info=team_payload['info'];members=team_payload['roster']
                if str(info['TEAM_ID'])!=tid or info['SEASON_YEAR']!=snapshot['season']:raise ValueError('Official team roster identity mismatch')
                if any(str(p['TeamID'])!=tid or str(p['SEASON'])!=snapshot['season'][:4] for p in members):raise ValueError('Official team roster season or membership mismatch')
                official[tid]=dict(team_id=tid,season=snapshot['season'],players=[dict(player_id=str(p['PLAYER_ID']),name=p['PLAYER']) for p in members],evidence=team_meta)
                sources.append(team_meta)
        if any(needs_empty_report(snapshot,game,game[s+'_team_id']) for s in ('home','away')):
            payload,meta=fetch_reference(BASE+'summary?event='+str(matched['provider_event_id']),directory)
            empty.extend(summary_no_injuries(payload,game,snapshot['season'],meta));sources.append(meta)
    return dict(games=games,rosters=rosters,sources=sources,no_injuries=empty,official_rosters=official)
