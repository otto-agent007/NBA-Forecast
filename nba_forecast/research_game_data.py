"""Archived ESPN team totals, joined conservatively to normalized NBA history.

Run ``python -m nba_forecast.research_game_data --help`` for resumable collection.
Archives retain real observation times; this does not create point-in-time history.
"""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from .reference import BASE, code, fetch_reference
from .schedule import CITY_ZONES, STATE_ZONES, atomic_json
from .validation import parse_time


def _date(value):
    return parse_time(value).astimezone(ZoneInfo('America/New_York')).strftime('%Y%m%d')


def _count(value):
    number=float(value)
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        raise ValueError('missing or invalid nonnegative count')
    return int(number)


def _history_game(rows):
    if not rows or len({r['game_id'] for r in rows}) != 1:
        raise ValueError('history must describe one game')
    if len({(r['team_id'],r['player_id']) for r in rows}) != len(rows):
        raise ValueError('duplicate player rows')
    if len({r['game_time'] for r in rows}) != 1 or len({r['season'] for r in rows}) != 1:
        raise ValueError('inconsistent history game time or season')
    teams={str(r['team_id']) for r in rows}
    if len(teams)!=2 or any(str(r['opponent_id']) not in teams-{str(r['team_id'])} for r in rows):
        raise ValueError('history does not contain two reciprocal teams')
    sums={t:{k:sum(_count(r[k]) for r in rows if str(r['team_id'])==t) for k in ('pts','fga','fta','tov')} for t in teams}
    minutes={t:sum(float(r['minutes']) for r in rows if str(r['team_id'])==t) for t in teams}
    durations=[]
    for total in minutes.values():
        periods=round((total-240)/25)
        if not math.isfinite(total) or periods<0 or abs(total-(240+25*periods))>0.15:
            raise ValueError('incomplete team minutes')
        durations.append(periods)
    if len(set(durations))!=1:raise ValueError('team minutes disagree on overtime')
    return teams,sums,minutes


def join_summary(rows, summary, team_codes, metadata, board_metadata=None):
    """Return one verified game or raise; never fill a missing count with zero."""
    teams,sums,minutes=_history_game(rows)
    header=summary['header'];competitions=header['competitions']
    if len(competitions)!=1:raise ValueError('summary competition is not unique')
    comp=competitions[0]
    if not comp.get('status',{}).get('type',{}).get('completed'):
        raise ValueError('summary is not final')
    season=rows[0]['season']
    if header.get('season',{}).get('year')!=int(season[:4])+1 or header['season'].get('type')!=2:
        raise ValueError('summary season mismatch')
    if _date(comp['date'])!=_date(rows[0]['game_time']):raise ValueError('basketball date mismatch')
    sides={c['homeAway']:c for c in comp['competitors']}
    if len(comp['competitors'])!=2 or set(sides)!={'home','away'}:raise ValueError('summary team sides not unique')
    by_code={code(team_codes[t]):t for t in teams}
    if len(by_code)!=2:raise ValueError('NBA team codes not unique')
    if {code(c['team']['abbreviation']) for c in sides.values()}!=set(by_code):
        raise ValueError('summary team pair mismatch')
    result_stats={};ids={}
    for side,c in sides.items():
        tid=by_code.get(code(c['team']['abbreviation']))
        if tid is None:raise ValueError('summary team pair mismatch')
        ids[side]=tid
        boxes=[b for b in summary['boxscore']['teams'] if str(b['team']['id'])==str(c['team']['id'])]
        if len(boxes)!=1:raise ValueError('summary team boxscore not unique')
        stats={s['name']:s['displayValue'] for s in boxes[0]['statistics']}
        try:
            counts=dict(pts=_count(c['score']),fga=_count(stats['fieldGoalsMade-fieldGoalsAttempted'].split('-')[1]),
                        fta=_count(stats['freeThrowsMade-freeThrowsAttempted'].split('-')[1]),
                        oreb=_count(stats['offensiveRebounds']),tov=_count(stats['turnovers']))
        except (KeyError,IndexError,TypeError,ValueError) as exc:
            raise ValueError('missing or invalid required boxscore counts') from exc
        if any(counts[k]!=sums[tid][k] for k in sums[tid]):
            raise ValueError(f'NBA player sums mismatch for {tid}: {sums[tid]} vs {counts}')
        # Reconcile player counts first, then use all possession-ending turnovers.
        # A missing total never silently falls back to the player-only series.
        try:
            counts['player_tov']=counts['tov']
            counts['team_turnovers']=_count(stats['teamTurnovers'])
            counts['tov']=counts['total_turnovers']=_count(stats['totalTurnovers'])
        except (KeyError,TypeError,ValueError) as exc:
            raise ValueError('missing or invalid total/team turnover counts') from exc
        if counts['tov']!=counts['player_tov']+counts['team_turnovers']:
            raise ValueError('total turnover arithmetic mismatch')
        result_stats[tid]=counts
    observed=[metadata['retrieved_at']]+[r['known_at'] for r in rows]
    if board_metadata:observed.append(board_metadata['retrieved_at'])
    known_at=max(observed,key=parse_time)
    provenance=dict(summary=metadata,nba_player_source_urls=sorted({r['source_url'] for r in rows if r.get('source_url')}),
                    nba_player_known_at=max((r['known_at'] for r in rows),key=parse_time),
                    reconciled_fields=['pts','fga','fta','player_tov'],turnover_definition='total turnovers; ESPN totalTurnovers = turnovers + teamTurnovers')
    if board_metadata:provenance['scoreboard']=board_metadata
    venue=summary.get('gameInfo',{}).get('venue') or comp.get('venue') or {}
    address=venue.get('address') or {}
    zone=CITY_ZONES.get(address.get('city')) or STATE_ZONES.get(address.get('state'))
    return dict(game_id=rows[0]['game_id'],game_time=rows[0]['game_time'],known_at=known_at,
                home_team_id=ids['home'],away_team_id=ids['away'],team_stats=result_stats,
                team_minutes=minutes,season=season,provider_event_id=str(header['id']),provenance=provenance,
                venue_timezone=zone,venue=venue)


def collect_team_games(history, team_codes, directory, workers=4, fetch=None):
    """Bounded collection; content-addressed fetch cache makes interruption resumable.

    ``team_codes`` maps NBA team IDs to NBA abbreviations. Every failure is recorded.
    Returns (verified_records, manifest) and writes both atomically.
    """
    if not 1<=workers<=8:raise ValueError('workers must be between 1 and 8')
    fetch=fetch or fetch_reference
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    grouped=defaultdict(list)
    for row in history:grouped[str(row['game_id'])].append(row)
    dates=sorted({_date(rows[0]['game_time']) for rows in grouped.values()})
    def board_job(date):
        try:return date,fetch(BASE+'scoreboard?dates='+date,root/'espn',max_age=10**10),None
        except Exception as exc:return date,None,str(exc)
    with ThreadPoolExecutor(max_workers=workers) as pool:boards={d:(p,e) for d,p,e in pool.map(board_job,dates)}
    def game_job(item):
        gid,rows=item
        try:
            teams,_,_=_history_game(rows);date=_date(rows[0]['game_time']);board,error=boards[date]
            if error:raise ValueError('scoreboard fetch failed: '+error)
            payload,board_meta=board;candidates=[]
            for event in payload.get('events',[]):
                for comp in event.get('competitions',[]):
                    pair={code(c['team']['abbreviation']) for c in comp.get('competitors',[])}
                    if pair=={code(team_codes[t]) for t in teams} and _date(comp.get('date') or event['date'])==date:
                        candidates.append(str(event.get('id') or comp['id']))
            if len(candidates)!=1:raise ValueError('scoreboard join is not unique: '+str(len(candidates)))
            summary,meta=fetch(BASE+'summary?event='+candidates[0],root/'espn',max_age=10**10)
            if str(summary.get('header',{}).get('id'))!=candidates[0]:raise ValueError('summary event identity mismatch')
            return join_summary(rows,summary,team_codes,meta,board_meta),None
        except Exception as exc:return None,dict(game_id=gid,reason=str(exc))
    with ThreadPoolExecutor(max_workers=workers) as pool:out=list(pool.map(game_job,sorted(grouped.items())))
    records=sorted((r for r,e in out if r),key=lambda r:(r['game_time'],r['game_id']))
    manifest=dict(created_at=datetime.now(timezone.utc).isoformat(),input_player_rows=len(history),input_games=len(grouped),
                  matched_games=len(records),failed_games=sum(e is not None for r,e in out),workers=workers,
                  evidence_kind='retrospectively_collected_finals',failures=[e for r,e in out if e],
                  raw_archive=str(root/'espn'/'raw'),known_at_policy='latest real observation among summary, scoreboard, and NBA rows')
    atomic_json(root/'team-games.json',records);atomic_json(root/'manifest.json',manifest)
    return records,manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history',required=True);parser.add_argument('--roster',required=True)
    parser.add_argument('--directory',required=True);parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    opener=gzip.open if args.history.endswith('.gz') else open
    with opener(args.history,'rt') as handle:history=json.load(handle)
    roster=json.loads(Path(args.roster).read_text());players=roster['players'] if isinstance(roster,dict) else roster
    team_codes={str(p['team_id']):p['team_code'] for p in players}
    _,manifest=collect_team_games(history,team_codes,args.directory,args.workers)
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
