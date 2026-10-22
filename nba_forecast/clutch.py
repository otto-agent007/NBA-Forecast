"""Auditable regular-season clutch research; does not change forecast weights.

Request contract checked against swar/nba_api LeagueDashPlayerClutch.
Run: python -m nba_forecast.clutch --output data/clutch 2023-24 2024-25 2025-26
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from urllib.parse import urlencode


def parameters(season):
    if not re.fullmatch(r'20\d{2}-\d{2}', season) or int(season[-2:]) != (int(season[:4])+1)%100:
        raise ValueError('Expected consecutive NBA season years')
    values = dict.fromkeys(('College Conference Country DateFrom DateTo Division DraftPick '
        'DraftYear GameScope GameSegment Height Location Outcome PORound PlayerExperience '
        'PlayerPosition SeasonSegment ShotClockRange StarterBench TeamID VsConference '
        'VsDivision Weight ISTRound').split(), '')
    values.update(AheadBehind='Ahead or Behind',ClutchTime='Last 5 Minutes',LastNGames=0,
        MeasureType='Base',Month=0,OpponentTeamID=0,PaceAdjust='N',PerMode='Totals',Period=0,
        PlusMinus='N',PointDiff=5,Rank='N',Season=season,SeasonType='Regular Season',LeagueID='00')
    return values


def clutch_url(season):
    return 'https://stats.nba.com/stats/leaguedashplayerclutch?' + urlencode(sorted(parameters(season).items()))


def normalize(payload, season):
    expected = parameters(season)
    actual = payload.get('parameters', {})
    for key, value in expected.items():
        if key not in actual or (actual[key] != value and not (value == '' and actual[key] is None)):
            raise ValueError(f'Clutch response filter mismatch: {key}')
    tables = [t for t in payload.get('resultSets',[]) if t.get('name')=='LeagueDashPlayerClutch']
    if len(tables)!=1:
        raise ValueError('Expected one clutch result table')
    table=tables[0]; headers=table.get('headers',[])
    required={'PLAYER_ID','PLAYER_NAME','GP','MIN','FGM','FGA','FG3M','FG3A','FTM','FTA','PTS','AST','TOV'}
    if len(set(headers))!=len(headers) or not required.issubset(headers):
        raise ValueError('Clutch response schema changed')
    rows=[];seen=set()
    for values in table.get('rowSet',[]):
        if len(values)!=len(headers):raise ValueError('Clutch row width mismatch')
        row=dict(zip(headers,values));pid=row['PLAYER_ID']
        if type(pid) is not int or pid<=0 or pid in seen:raise ValueError('Invalid or duplicate player ID')
        seen.add(pid)
        for field in required-{'PLAYER_ID','PLAYER_NAME'}:
            value=row[field]
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
                raise ValueError(f'Invalid clutch count: {field}')
            if field!='MIN' and int(value)!=value:raise ValueError('Noninteger clutch count')
        if not isinstance(row['PLAYER_NAME'],str) or not row['PLAYER_NAME']:raise ValueError('Missing player name')
        if not (row['FG3M']<=row['FGM']<=row['FGA'] and row['FG3M']<=row['FG3A']<=row['FGA'] and row['FTM']<=row['FTA']):
            raise ValueError('Inconsistent clutch attempts')
        if row['PTS']!=2*row['FGM']+row['FG3M']+row['FTM']:raise ValueError('Clutch scoring identity mismatch')
        rows.append(row)
    if not rows:raise ValueError('Empty clutch table is not verified season coverage')
    return rows


def main():
    from .data import fetch_json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('seasons',nargs='+');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    for season in args.seasons:
        url=clutch_url(season)
        payload=fetch_json(url,cache_dir=args.output/'raw')
        rows=normalize(payload,season)
        result={'season':season,'source_url':url,'validated_at':datetime.now(timezone.utc).isoformat(),
                'definition':{'seconds_remaining':300,'max_score_margin':5,'season_type':'Regular Season'},
                'parameters':payload['parameters'],'rows':rows,
                'limitations':['Season aggregates are research evidence, not historical pregame observations.',
                               'Missing players are not zero-filled. No automatic model adjustment.']}
        path=args.output/f'{season}.json';tmp=path.with_suffix('.tmp')
        tmp.write_text(json.dumps(result,indent=2)+'\n');tmp.replace(path)
        print(f'{season}: {len(rows)} validated player rows')


if __name__=='__main__':main()
