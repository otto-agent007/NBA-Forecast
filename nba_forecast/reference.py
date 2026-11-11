"""Bounded, archived ESPN references for an independent input cross-check."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.request
from zoneinfo import ZoneInfo

from .data import _NoRedirects,MAX_BYTES,_NextDataParser,HEADERS
from .files import archive_bytes, atomic_json
from .validation import parse_time

BASE='https://site.api.espn.com/apis/site/v2/sports/basketball/nba/'
ALIASES={'NY':'NYK','GS':'GSW','NO':'NOP','SA':'SAS','UTAH':'UTA','WSH':'WAS','PHO':'PHX','BRK':'BKN'}


def code(value):
    return ALIASES.get(value,value)


def decode_reference(raw,url):
    if url.startswith('https://www.nba.com/team/'):
        parser=_NextDataParser()
        parser.feed(raw.decode('utf-8'))
        return json.loads(''.join(parser.parts))['props']['pageProps']['team']
    return json.loads(raw)


def fetch_reference(url,directory,max_age=300):
    if not (re.fullmatch(re.escape(BASE)+r'(scoreboard\?dates=\d{8}|teams/\d{1,3}/roster|summary\?event=\d{6,12}|injuries|teams/\d{1,3}/depthcharts)',url) or
            re.fullmatch(r'https://www\.nba\.com/team/16106127(?:3[7-9]|[45]\d|6[0-6])/roster',url) or
            re.fullmatch(r'https://sports\.core\.api\.espn\.com/v2/sports/basketball/leagues/nba/events/(\d{6,12})/competitions/\1/odds',url)):
        raise ValueError('reference URL is not an allowlisted public ESPN endpoint')
    folder=Path(directory)
    key=hashlib.sha256(url.encode()).hexdigest()
    pointer=folder/(key+'.json')
    now=datetime.now(timezone.utc)
    if pointer.exists():
        meta=json.loads(pointer.read_text())
        raw_path=folder/'raw'/(meta['sha256']+'.json')
        age=(now-parse_time(meta['retrieved_at'])).total_seconds()
        if 0<=age<max_age and raw_path.exists():
            raw=raw_path.read_bytes()
            # A damaged archive is not evidence: fetch again, and the write below repairs it.
            if hashlib.sha256(raw).hexdigest()==meta['sha256']:
                return decode_reference(raw,url),meta
    opener=urllib.request.build_opener(_NoRedirects())
    headers=HEADERS if url.startswith('https://www.nba.com/team/') else {'User-Agent':'NBA-Forecast/0.1'}
    with opener.open(urllib.request.Request(url,headers=headers),timeout=12) as response:
        raw=response.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:
        raise ValueError('reference response exceeds size limit')
    payload=decode_reference(raw,url)
    if not isinstance(payload,dict):
        raise ValueError('reference response is not an object')
    meta=dict(source_url=url,retrieved_at=datetime.now(timezone.utc).isoformat(),sha256=hashlib.sha256(raw).hexdigest())
    archive_bytes(folder/'raw'/(meta['sha256']+'.json'),raw)
    atomic_json(pointer,meta)
    return payload,meta


def board_events(board):
    """(sides, normalized event) for each two-team event on an ESPN daily scoreboard."""
    out=[]
    for event in board.get('events',[]):
        competition=event['competitions'][0]
        sides={c['homeAway']:c['team'] for c in competition['competitors']}
        if set(sides)!={'home','away'}:
            continue
        status=competition.get('status',event.get('status',{})).get('type',{})
        out.append((sides,dict(home=code(sides['home']['abbreviation']),away=code(sides['away']['abbreviation']),
                               start_time=competition.get('date') or event['date'],state=status.get('state'),
                               status_name=status.get('name'),provider_event_id=event.get('id') or competition.get('id'))))
    return out


def scoreboard_event(game,directory):
    """The unique ESPN event for a scheduled game, or None; the day's board is cached for five minutes."""
    date=parse_time(game['start_time']).astimezone(ZoneInfo('America/New_York')).strftime('%Y%m%d')
    board,meta=fetch_reference(BASE+'scoreboard?dates='+date,directory)
    matches=[(sides,normalized) for sides,normalized in board_events(board)
             if (normalized['home'],normalized['away'])==(game.get('home_tricode'),game.get('away_tricode'))]
    return (matches[0][1],meta) if len(matches)==1 else (None,meta)


ESPN_TEAM_IDS=tuple(range(1,31))


def archive_daily_references(directory,day):
    """Archive ESPN's league injury list and every team's depth chart once a day.

    This is evidence for a future participation backtest, not an input to any
    forecast. Each response keeps its URL, hash and retrieval time; identical
    bytes are stored once. Failures are recorded, never raised.
    """
    folder=Path(directory)/'daily'
    folder.mkdir(parents=True,exist_ok=True)
    record=folder/(day+'.json')
    if record.exists():
        return json.loads(record.read_text())
    captured=[]
    failures=[]
    for url in [BASE+'injuries']+[f'{BASE}teams/{tid}/depthcharts' for tid in ESPN_TEAM_IDS]:
        try:
            _,meta=fetch_reference(url,directory,max_age=6*3600)
            captured.append(meta)
        except (ValueError,OSError,KeyError,TypeError) as exc:
            failures.append(dict(source_url=url,error=str(exc)))
    result=dict(day=day,captured_at=datetime.now(timezone.utc).isoformat(),sources=captured,failures=failures,
                policy='Daily ESPN injury list and depth charts, archived for participation research; not forecast inputs.')
    atomic_json(record,result)
    return result


def espn_roster(team,season,directory):
    """ESPN's roster names for one scoreboard team, cached for fifteen minutes: ``(names, meta)``."""
    payload,meta=fetch_reference(BASE+'teams/'+str(team['id'])+'/roster',directory,max_age=900)
    if str(payload.get('team',{}).get('id'))!=str(team['id']):
        raise ValueError('reference roster team identity mismatch')
    if payload.get('season',{}).get('displayName')!=season:
        raise ValueError('reference roster season mismatch')
    names=[p.get('fullName') or p['displayName'] for p in payload.get('athletes',[])]
    if not names:
        raise ValueError('reference roster is empty')
    return names,meta


def official_roster(tid,season,directory):
    """The official NBA team roster page's members, cached for fifteen minutes, for a roster review."""
    team_payload,team_meta=fetch_reference(f'https://www.nba.com/team/{tid}/roster',directory,max_age=900)
    info=team_payload['info']
    members=team_payload['roster']
    if str(info['TEAM_ID'])!=tid or info['SEASON_YEAR']!=season:
        raise ValueError('Official team roster identity mismatch')
    if any(str(p['TeamID'])!=tid or str(p['SEASON'])!=season[:4] for p in members):
        raise ValueError('Official team roster season or membership mismatch')
    return dict(team_id=tid,season=season,players=[dict(player_id=str(p['PLAYER_ID']),name=p['PLAYER']) for p in members],evidence=team_meta)


def collect_references(snapshot,directory,roster_reviews=()):
    from .availability import official_coverage,summary_injuries,summary_no_injuries
    games=[]
    rosters={}
    sources=[]
    boards={}
    empty=[]
    espn=[]
    official={}
    failures=[]
    for game in snapshot['games']:
        date=parse_time(game['start_time']).astimezone(ZoneInfo('America/New_York')).strftime('%Y%m%d')
        new_date=date not in boards
        if date not in boards:
            boards[date],meta=fetch_reference(BASE+'scoreboard?dates='+date,directory)
            sources.append(meta)
        candidates=[]
        for sides,normalized in board_events(boards[date]):
            if new_date:
                games.append(normalized)
            if (normalized['home'],normalized['away'])==(game.get('home_tricode'),game.get('away_tricode')):
                candidates.append((sides,normalized))
        if len(candidates)!=1:
            continue
        sides,matched=candidates[0]
        for side,team in sides.items():
            tid=game[side+'_team_id']
            if tid in rosters:
                continue
            # One provider's roster problem loses only that comparison, never the game.
            try:
                rosters[tid],meta=espn_roster(team,snapshot['season'],directory)
                sources.append(meta)
                if any(r.get('team_id')==tid and r.get('game_id')==game['game_id'] for r in roster_reviews):
                    official[tid]=official_roster(tid,snapshot['season'],directory)
                    sources.append(official[tid]['evidence'])
            except (ValueError,OSError,KeyError,TypeError) as exc:
                failures.append(dict(kind='roster',team_id=tid,error=str(exc)))
        # Any team the official report does not yet cover gets ESPN's game injury list.
        if any(not official_coverage(snapshot,game,game[s+'_team_id']) for s in ('home','away')):
            try:
                payload,meta=fetch_reference(BASE+'summary?event='+str(matched['provider_event_id']),directory)
                empty.extend(summary_no_injuries(payload,game,snapshot['season'],meta))
                espn.extend(summary_injuries(payload,game,snapshot['season'],meta))
                sources.append(meta)
            except (ValueError,OSError,KeyError,TypeError) as exc:
                failures.append(dict(kind='injury_summary',game_id=game.get('game_id'),error=str(exc)))
    return dict(games=games,rosters=rosters,sources=sources,no_injuries=empty,espn_injuries=espn,
                official_rosters=official,failures=failures)
