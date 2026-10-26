"""Chronological reconstruction research; never an original pregame forecast."""
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import gzip,hashlib,itertools,json,math
from pathlib import Path
from functools import lru_cache
from statistics import fmean
from zoneinfo import ZoneInfo

from .model import _baseline_forecast
from .game_environment import build_environment
from .context_effects import fit_context,context_features,context_adjustment
from .calibration import fit_calibration,score_calibration,RETROSPECTIVE
from .schedule import atomic_json,canonical
from .validation import parse_time

@lru_cache(maxsize=4096)
def _day(value):return parse_time(value).astimezone(ZoneInfo('America/New_York')).date()


def prior_game_days(records,target):
    day=_day(target['game_time'])
    return [r for r in records if _day(r['game_time'])<day]


def calendar_games(history,records):
    calendar={r['game_id']:r for r in records};grouped=defaultdict(list)
    for r in history:grouped[r['game_id']].append(r)
    for gid,rows in grouped.items():
        if gid not in calendar:
            calendar[gid]=dict(game_id=gid,game_time=rows[0]['game_time'],season=rows[0]['season'],
                known_at=max((r['known_at'] for r in rows),key=parse_time),
                team_ids=sorted({r['team_id'] for r in rows}),venue_timezone=None)
    return sorted(calendar.values(),key=lambda r:(parse_time(r['game_time']),r['game_id']))


def split_games(records):
    dates=sorted({parse_time(r['game_time']).astimezone(ZoneInfo('America/New_York')).date().isoformat() for r in records})
    cuts=[int(len(dates)*x) for x in (.5,.65,.8)]
    groups={name:set(part) for name,part in zip(('train','selection','calibration','holdout'),
        (dates[:cuts[0]],dates[cuts[0]:cuts[1]],dates[cuts[1]:cuts[2]],dates[cuts[2]:]))}
    return {name:[r['game_id'] for r in records if parse_time(r['game_time']).astimezone(ZoneInfo('America/New_York')).date().isoformat() in days] for name,days in groups.items()}


def historical_context(records,game,team):
    start=parse_time(game['game_time']);day=start.astimezone(ZoneInfo('America/New_York')).date()
    previous=sorted((r for r in records if team in r.get('team_ids',(r.get('home_team_id'),r.get('away_team_id')))
        and (not game.get('season') or r.get('season')==game['season']) and parse_time(r['game_time'])<start),key=lambda r:parse_time(r['game_time']))
    last=previous[-1] if previous else None
    rest=(day-parse_time(last['game_time']).astimezone(ZoneInfo('America/New_York')).date()).days-1 if last else None
    recent=[r for r in previous if 0<=(day-parse_time(r['game_time']).astimezone(ZoneInfo('America/New_York')).date()).days<=3]
    old=last.get('venue_timezone') if last else None;new=game.get('venue_timezone')
    shift=((start.astimezone(ZoneInfo(new)).utcoffset()-start.astimezone(ZoneInfo(old)).utcoffset()).total_seconds()/3600) if old and new else None
    return dict(rest_days=rest,back_to_back=rest==0,three_in_four=len(recent)>=2,timezone_shift_hours=shift,
        previous_game_id=last['game_id'] if last else None)


def reconstruct(history,records,as_of):
    """Use earlier game events, retaining the real later source observation times.

    Target boxscore identities approximate that game's roster. All are treated
    eligible, including DNP rows: no target participation/injury label changes a
    prediction. Player metrics are conditional on actual positive participation;
    historical injury allocation cannot be validated without pregame reports.
    """
    by_game=defaultdict(list)
    eligible_ids={r['game_id'] for r in records}
    for r in history:
        if r['game_id'] in eligible_ids:by_game[r['game_id']].append(r)
    ordered=sorted(records,key=lambda r:(parse_time(r['game_time']),r['game_id']))
    calendar=calendar_games(history,records)
    prior=[];players=[];teams=[];exclusions=defaultdict(int)
    for i,game in enumerate(ordered):
        target=by_game[game['game_id']]
        if not target:exclusions['missing_player_history']+=1;continue
        g=dict(game_id=game['game_id'],season_type='Regular Season',start_time=game['game_time'],
            home_team_id=game['home_team_id'],away_team_id=game['away_team_id'])
        roster=[dict(player_id=r['player_id'],name=r['name'],team_id=r['team_id'],status='available',
            status_source_url=r['source_url'],status_observed_at=r['known_at']) for r in target]
        snap=dict(schema_version=1,as_of=as_of,season=game['season'],forecast_stage='preliminary',research_reconstruction=True,
            roster=dict(players=roster,observed_at=as_of,source_url=target[0]['source_url'],complete=False,team_ids=[g['home_team_id'],g['away_team_id']]),games=[g])
        base=_baseline_forecast(snap,prior_game_days(prior,game))
        past_games=prior_game_days(ordered[:i],game)
        past_calendar=prior_game_days(calendar,game)
        env=build_environment(past_games,g,game['game_time'],context={'evidence_kind':'historical_reconstruction'})
        actual={r['player_id']:r for r in target}
        for tid,e in env['teams'].items():
            context=historical_context(past_calendar,game,tid);features,_=context_features(context,home=tid==g['home_team_id'])
            offense=e['audit'].get('own_offense_per100')
            if offense and e.get('expected_possessions') and not e['audit']['fallback']:
                duration=game['team_minutes'][tid]/5
                teams.append(dict(game_id=g['game_id'],team_id=tid,game_time=game['game_time'],known_at=game['known_at'],
                    baseline_points=offense*e['expected_possessions']/100*e['defense_factor'],
                    actual_points=game['team_stats'][tid]['pts']*48/duration,features=features,
                    player_baseline_points=sum(p['points_mean'] for p in base['predictions'] if p['team_id']==tid and p['status']=='projected'),
                    scoring_before_opponent=sum(p['components']['scoring']['points_before_opponent'] for p in base['predictions'] if p['team_id']==tid and p['status']=='projected'),
                    pace_factor=e['pace_factor'],defense_factor=e['defense_factor'],context=context,home=tid==g['home_team_id']))
        for p in base['predictions']:
            a=actual[p['player_id']]
            if p['status']!='projected' or a['status']!='played' or a['minutes']<=0:
                exclusions['unsupported_or_nonplaying']+=1;continue
            e=env['teams'][p['team_id']];u=p['components']['uncertainty']
            factor=p['components']['scoring']['opponent_multiplier']*p['components']['scoring'].get('home_court_multiplier',1.)
            players.append(dict(game_id=g['game_id'],player_id=p['player_id'],team_id=p['team_id'],game_time=game['game_time'],
                actual_known_at=a['known_at'],actual_points=a['pts'],baseline_points=p['points_mean'],
                scoring_before_opponent=p['points_mean']/factor,old_factor=factor,
                shot_variance=u['shot_variance'],excess_variance=u['shrunk_excess_residual_variance'],minute_variance=u['minutes_points_variance'],
                pace_factor=e['pace_factor'],defense_factor=e['defense_factor'],home=p['team_id']==g['home_team_id'],
                context=historical_context(past_calendar,game,p['team_id']),
                evidence_kind=RETROSPECTIVE,reconstruction_origin='chronological-statistical-reconstruction-v1',feature_as_of=game['game_time']))
        prior.extend(target)
        if i%100==0:print(json.dumps(dict(reconstructed_games=i,player_samples=len(players))),flush=True)
    return players,teams,dict(exclusions)


def candidate_value(row,parameters,context_model,as_of):
    pace=row['pace_factor']**parameters['pace_strength'];defense=row['defense_factor']**parameters['defense_strength']
    context=context_adjustment(context_model,row['context'],home=row['home'],as_of=as_of)['factor']**parameters['context_strength']
    factor=defense*context;ratio=factor/row['old_factor']
    mean=row['scoring_before_opponent']*pace*factor
    sd=math.sqrt(max(0.,row['shot_variance']*pace+row['excess_variance']+row['minute_variance']*pace*pace))*ratio
    return mean,sd


def metrics(rows,predict):
    errors=[predict(r)-r['actual_points'] for r in rows]
    return dict(n=len(errors),mae=fmean(abs(e) for e in errors) if errors else None,
        rmse=math.sqrt(fmean(e*e for e in errors)) if errors else None,bias=fmean(errors) if errors else None)


def run_research(history,records,directory,as_of,*,cache=None):
    cutoff=parse_time(as_of)
    if any(parse_time(r['known_at'])>cutoff for r in history+records):
        raise ValueError('Research creation cutoff cannot precede future-known sources')
    folder=Path(directory);folder.mkdir(parents=True,exist_ok=True)
    splits=split_games(records);source_hash=hashlib.sha256(canonical(dict(method='prior-game-days-full-calendar-v4',history=history,team_games=records))).hexdigest()
    cache=Path(cache) if cache else folder/'reconstruction-v4.json.gz'
    if cache.exists():
        captured=json.loads(gzip.decompress(cache.read_bytes()))
        if captured['source_hash']!=source_hash:raise ValueError('Research cache source differs')
        players,team_rows,exclusions=captured['players'],captured['teams'],captured['exclusions']
    else:
        players,team_rows,exclusions=reconstruct(history,records,as_of)
        cache.write_bytes(gzip.compress(canonical(dict(source_hash=source_hash,players=players,teams=team_rows,exclusions=exclusions)),mtime=0))
    ids={name:set(gids) for name,gids in splits.items()}
    selection=[r for r in players if r['game_id'] in ids['selection']]
    team_selection=[r for r in team_rows if r['game_id'] in ids['selection']]
    player_reference=metrics(selection,lambda r:r['baseline_points'])
    team_reference=metrics(team_selection,lambda r:r['player_baseline_points'])
    choices=[]
    for pace,defense in itertools.product((0.,.25,.5,.75,1.),repeat=2):
        training=[dict(r,baseline_points=r['scoring_before_opponent']*r['pace_factor']**pace*r['defense_factor']**defense) for r in team_rows if r['game_id'] in ids['train']]
        fitted=fit_context(training,as_of=as_of)
        for use_features,rest in itertools.product((False,True),(0.,.5,1.)):
            context=json.loads(json.dumps(fitted))
            if not use_features:
                context=fit_context([dict(r,features={k:0. for k in r['features']}) for r in training],as_of=as_of)
                context['unselected_full_fit']=dict(intercept=fitted.get('intercept'),coefficients=fitted['coefficients'])
            params=dict(pace_strength=pace,defense_strength=defense,context_strength=rest,rotation_enabled=True,
                context_features_enabled=use_features)
            score=metrics(selection,lambda r:candidate_value(r,params,context,as_of)[0])
            team_score=metrics(team_selection,lambda r:r['scoring_before_opponent']*r['pace_factor']**pace*r['defense_factor']**defense*context_adjustment(context,r['context'],home=r['home'],as_of=as_of)['factor']**rest)
            objective=score['mae']/player_reference['mae']+team_score['mae']/team_reference['mae']
            choices.append(dict(parameters=params,metrics=score,team_metrics=team_score,selection_objective=objective,context_model=context))
    best=min(choices,key=lambda c:(c['selection_objective'],sum(c['parameters'][k] for k in ('pace_strength','defense_strength','context_strength'))))
    ablation_best=best
    full=min((c for c in choices if c['parameters']['pace_strength']>0 and c['parameters']['defense_strength']>0
        and c['parameters']['context_strength']>0 and c['parameters']['context_features_enabled']),key=lambda c:c['selection_objective'])
    # Prefer the requested physical components only inside a declared, small
    # selection-set noninferiority window; never choose them from holdout scores.
    if (full['selection_objective']<=best['selection_objective']*1.005 and full['metrics']['mae']<=player_reference['mae']*1.01
        and full['team_metrics']['mae']<=team_reference['mae']*1.01):best=full
    parameters=best['parameters'];context=best['context_model']
    key=hashlib.sha256(canonical(dict(version='regular-season-baseline-0.5.0',parameters=parameters,context_model=context,source_hash=source_hash))).hexdigest()
    def samples(name):
        return [dict(r,model_key=key,points_mean=candidate_value(r,parameters,context,as_of)[0],points_sd=candidate_value(r,parameters,context,as_of)[1]) for r in players if r['game_id'] in ids[name]]
    calibration=fit_calibration(samples('calibration'),model_key=key,as_of=as_of)
    holdout=samples('holdout');baseline=metrics(holdout,lambda r:r['baseline_points']);candidate=metrics(holdout,lambda r:r['points_mean'])
    team_holdout=[r for r in team_rows if r['game_id'] in ids['holdout']]
    team_baseline=metrics(team_holdout,lambda r:r['player_baseline_points'])
    team_candidate=metrics(team_holdout,lambda r:r['scoring_before_opponent']*r['pace_factor']**parameters['pace_strength']*r['defense_factor']**parameters['defense_strength']*context_adjustment(context,r['context'],home=r['home'],as_of=as_of)['factor']**parameters['context_strength'])
    report=dict(schema_version=1,created_at=as_of,evidence_kind=RETROSPECTIVE,source_hash=source_hash,
        split_games={k:len(v) for k,v in splits.items()},split_ids=splits,
        selection=dict(chosen=best,best_ablation=ablation_best,baseline=player_reference,team_baseline=team_reference,
            policy='Full component candidate preferred only within 0.5% of best combined selection objective and within 1% of each baseline selection MAE. Holdout not used for this choice.'),
        context_fit_target='Full team projected totals against regulation-equivalent final team points; coefficients selected against an intercept-only alternative. Equal relative player/team MAE objective.',
        holdout=dict(baseline=baseline,candidate=candidate,team_baseline=team_baseline,team_candidate=team_candidate,intervals=score_calibration(holdout,calibration,model_key=key,as_of=as_of)),
        exclusions=exclusions,scope='Conditional on actual positive participation and reconstructed boxscore roster. Injury-allocation effects and authentic historical pregame availability are not validated by this research.',
        development_note='Chronological holdout excluded from parameter selection within this run; earlier pilot aggregate results were inspected during method development. Prospective confirmation remains separate.',
        eligible_for_promotion=bool(candidate['mae']<=baseline['mae']*1.01 and team_candidate['mae']<=team_baseline['mae'] and calibration['status']=='ready'))
    artifact=dict(schema_version=1,known_at=as_of,model_version='regular-season-baseline-0.5.0',model_key=key,
        parameters=parameters,context_model=context,calibration=calibration,team_games=records,
        excluded_history_game_ids=sorted({r['game_id'] for r in history}-{r['game_id'] for r in records}),
        source_hash=source_hash,validation_report=report)
    atomic_json(folder/'report.json',report);atomic_json(folder/'artifact.json',artifact)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history',required=True);parser.add_argument('--team-games',required=True);parser.add_argument('--directory',required=True)
    args=parser.parse_args();raw=Path(args.history).read_bytes()
    history=json.loads(gzip.decompress(raw) if args.history.endswith('.gz') else raw)
    records=json.loads(Path(args.team_games).read_text())
    report=run_research(history,records,args.directory,datetime.now(timezone.utc).isoformat())
    print(json.dumps({k:v for k,v in report.items() if k not in ('split_ids',)},indent=2))
