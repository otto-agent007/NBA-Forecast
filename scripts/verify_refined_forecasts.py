#!/usr/bin/env python3
"""Real-source diagnostics only: never create live forecasts or change availability."""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nba_forecast.enhancements import load_active, install_artifact, prepare, verify_enhancements, _read
from nba_forecast.model_versions import REFINED_VERSION
from nba_forecast.model import forecast
from nba_forecast.profiles import eligible_history
from nba_forecast.refinement_upgrade import build_artifact
from nba_forecast.role_context import prepare_role_context, verify_role_context
from nba_forecast.shot_profiles import prepare_shot_profiles, verify_shot_profiles
from nba_forecast.shot_quality import prepare_shot_quality, verify_shot_quality
from nba_forecast.schedule import canonical, atomic_json
from nba_forecast.validation import parse_time
from nba_forecast.store import _validate_forecast


def read(path):
    raw=Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True)
    parser.add_argument('--history',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--artifact',help='Use an already prepared refined artifact')
    args=parser.parse_args()
    folder=Path(args.directory)
    output=Path(args.output)
    if output.exists():
        parser.error('Diagnostic output already exists')
    output.mkdir(parents=True)
    now=datetime.now(timezone.utc)
    as_of=now.isoformat()
    base,parent=load_active(folder,as_of)
    if base['model_version']==REFINED_VERSION:
        artifact=read(args.artifact) if args.artifact else base
        parent=base['parent_artifact_sha256']
        base=_read(folder,parent,as_of)
    else:
        artifact=read(args.artifact) if args.artifact else build_artifact(base,parent,as_of)
    digest=install_artifact(folder,artifact)
    atomic_json(output/'candidate-artifact.json',artifact)
    history={(r['game_id'],r['player_id']):r for r in read(args.history)}
    for path in sorted((folder/'finals').glob('*.json')):
        for row in read(path):
            key=row['game_id'],row['player_id']
            previous=history.get(key)
            if previous is None or parse_time(row['known_at'])>=parse_time(previous['known_at']):
                history[key]=row
    history=list(history.values())
    roster=read(folder/'roster.json')
    schedule=read(folder/'schedule/latest.json')
    games=[g for g in schedule['games'] if g.get('start_time') and now<parse_time(g['start_time'])<now+timedelta(hours=24)
        and g.get('status') not in ('cancelled','postponed','tbd')]
    summary=dict(purpose='DIAGNOSTIC ONLY; assumes every roster player is available. These are not pregame forecasts or accuracy measurements.',
        created_at=as_of,artifact_sha256=digest,model_key=artifact['model_key'],games=[])
    for game in games:
        cutoff=datetime.now(timezone.utc).isoformat()
        s=dict(schema_version=1,season=schedule['season'],as_of=cutoff,roster=deepcopy(roster),games=[game])
        for person in s['roster']['players']:
            person.update(status='available',status_observed_at=cutoff,status_source_url='https://example.invalid/diagnostic-assumption')
        s['historical_priors']=read(folder/'historical-priors.json')
        usable=eligible_history(s,history)
        s['model_enhancements']=prepare(artifact,digest,game,cutoff,directory=folder)
        s['role_context']=prepare_role_context(folder,s,usable)
        s['shot_quality']=prepare_shot_quality(folder,s,usable)
        s['shot_profiles']=prepare_shot_profiles(folder,s,usable)
        verify_enhancements(s,folder)
        verify_role_context(s,usable,folder)
        verify_shot_quality(s,usable,folder)
        verify_shot_profiles(s,usable,folder)
        new=forecast(s,usable)
        _validate_forecast(s,new)
        old_snapshot=deepcopy(s)
        old_snapshot['model_enhancements']=prepare(base,parent,game,cutoff,directory=folder)
        old=forecast(old_snapshot,usable)
        before={p['player_id']:p for p in old['predictions']}
        rows=[p for p in new['predictions'] if p['game_id']==game['game_id']]
        impacts=[]
        for p in rows:
            if p['status']!='projected':
                continue
            q=p['components'].get('shot_quality',{})
            prior=before[p['player_id']]
            if p['components'].get('role_context') or q.get('scoring_changed'):
                impacts.append(dict(player_id=p['player_id'],name=p['name'],old_minutes=prior['expected_minutes'],new_minutes=p['expected_minutes'],
                    old_points=prior['points_mean'],new_points=p['points_mean'],location_points_delta=q.get('points_delta'),
                    injury_game_ids=p['components'].get('role_context',{}).get('excluded_game_ids',[])))
        entry=dict(game_id=game['game_id'],matchup=game.get('away_tricode','')+' @ '+game.get('home_tricode',''),
            source_checks='passed',totals_status=new['game_totals'][0]['status'],
            margin_status=new['margin_forecasts'][0]['status'],margin_reason=new['margin_forecasts'][0]['reason'],
            projected_players=sum(p['status']=='projected' for p in rows),role_coverage=s['role_context']['coverage'],
            shot_quality_coverage=s['shot_quality']['coverage'],impacts=impacts)
        summary['games'].append(entry)
        for name,value in [('snapshot',s),('forecast',new)]:
            (output/(game['game_id']+'-'+name+'.json.gz')).write_bytes(gzip.compress(canonical(value),mtime=0))
        print(json.dumps({k:entry[k] for k in ('matchup','totals_status','margin_status','projected_players')}),flush=True)
    atomic_json(output/'summary.json',summary)


if __name__=='__main__':
    main()
