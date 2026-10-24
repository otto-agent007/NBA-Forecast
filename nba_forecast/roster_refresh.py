"""Complete official-team-page fallback when the NBA player directory is offline."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re

from .reference import fetch_reference
from .validation import parse_time


def fetch_team_rosters(season, directory):
    if not re.fullmatch(r'20\d{2}-\d{2}',season):raise ValueError('Invalid roster season')
    teams=[str(t) for t in range(1610612737,1610612767)]
    def capture(tid):
        url=f'https://www.nba.com/team/{tid}/roster'
        try:payload,meta=fetch_reference(url,Path(directory)/'team-rosters',max_age=900)
        except (ValueError,OSError) as exc:
            raise ValueError(f'Official team roster unavailable for {tid}: {exc}') from exc
        info=payload['info'];members=payload['roster']
        if (str(info['TEAM_ID'])!=tid or info['SEASON_YEAR']!=season or not members
            or meta['source_url']!=url or not re.fullmatch('[a-f0-9]{64}',meta['sha256'])):
            raise ValueError('Official team roster identity, season or provenance mismatch')
        players=[]
        for p in members:
            pid=str(p['PLAYER_ID']);name=p['PLAYER']
            if str(p['TeamID'])!=tid or str(p['SEASON'])!=season[:4] or not pid.isdigit() or not isinstance(name,str) or not name.strip():
                raise ValueError('Official team roster member identity or season mismatch')
            players.append(dict(player_id=pid,name=name,team_id=tid,status='unknown',
                team_code=info.get('TEAM_ABBREVIATION'),
                bio={key:p.get(source) for key,source in (('position','POSITION'),('jersey','NUM'),('height','HEIGHT'),('weight','WEIGHT'),('college','SCHOOL'))}))
        return players,meta
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:captured=list(pool.map(capture,teams))
    except (KeyError,TypeError) as exc:raise ValueError('Incomplete official team roster response') from exc
    players=[p for rows,_ in captured for p in rows]
    if len({p['player_id'] for p in players})!=len(players):
        raise ValueError('Official team rosters contain duplicate player identities')
    sources=[meta for _,meta in captured]
    # Oldest actual capture bounds freshness for the complete assembled roster.
    return dict(observed_at=min((m['retrieved_at'] for m in sources),key=parse_time),
        source_url=sources[0]['source_url'],source_method='official_team_rosters',sources=sources,
        complete=True,team_ids=teams,players=sorted(players,key=lambda p:(p['team_id'],p['player_id'])))
