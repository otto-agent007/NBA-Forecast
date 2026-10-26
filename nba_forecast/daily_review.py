"""Date-scoped prospective review of frozen forecasts and verified final caches."""
from collections import Counter
from datetime import date as Date,datetime,timedelta,timezone
import hashlib,json,math,sqlite3
from pathlib import Path
from statistics import fmean
from zoneinfo import ZoneInfo

from .postgame import player_feedback
from .schedule import atomic_json,canonical
from .store import STAT_PROJECTIONS
from .totals import game_totals
from .validation import parse_time,validate_history

VERSION='daily-review-1.0'
PACIFIC=ZoneInfo('America/Los_Angeles')
STATS={'pts':'points_mean','minutes':'expected_minutes',**STAT_PROJECTIONS}


def _read(path,default=None):
    return json.loads(path.read_text()) if path.exists() else default


def _selected(folder,games,as_of,season):
    """Freeze one entire run per game/stage before opening any outcome file."""
    db=folder/'forecast.sqlite';chosen={};cutoff=parse_time(as_of)
    if not db.exists():return chosen
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as conn:
        conn.row_factory=sqlite3.Row
        for row in conn.execute("SELECT rowid AS insertion_order,run_id,created_at,as_of,snapshot_json,forecast_json,snapshot_sha256,forecast_sha256 FROM runs WHERE mode='live' AND scope='league'"):
            created=parse_time(row['created_at'])
            if created>cutoff:continue
            for kind in ('snapshot','forecast'):
                if hashlib.sha256(row[kind+'_json'].encode()).hexdigest()!=row[kind+'_sha256']:
                    raise ValueError('Frozen '+kind+' hash mismatch: '+row['run_id'])
            snapshot=json.loads(row['snapshot_json']);forecast=json.loads(row['forecast_json'])
            if forecast['season']!=snapshot['season']:raise ValueError('Frozen forecast season differs')
            if forecast['season']!=season:continue
            if not parse_time(forecast['as_of'])==parse_time(snapshot['as_of'])==parse_time(row['as_of'])<=created:
                raise ValueError('Frozen forecast chronology differs: '+row['run_id'])
            stage=forecast.get('forecast_stage','preliminary')
            if stage not in ('preliminary','final'):continue
            for saved in snapshot['games']:
                game=games.get(saved['game_id'])
                if not game or game.get('status') in ('postponed','cancelled','tbd'):continue
                start=parse_time(game['start_time'])
                if created>=start or parse_time(saved['start_time'])!=start:continue
                if any(saved[k]!=game[k] for k in ('home_team_id','away_team_id')):continue
                predictions=[p for p in forecast['predictions'] if p['game_id']==game['game_id']]
                if any(p['team_id'] not in (game['home_team_id'],game['away_team_id']) or parse_time(p['start_time'])!=start for p in predictions):
                    raise ValueError('Frozen prediction identity differs: '+row['run_id'])
                rank=(created,row['insertion_order']);key=(game['game_id'],stage)
                if key not in chosen or rank>chosen[key]['rank']:
                    chosen[key]=dict(rank=rank,run_id=row['run_id'],created_at=row['created_at'],as_of=forecast['as_of'],
                        model_version=forecast['model_version'],model_key=forecast.get('model_key'),season=forecast['season'],
                        forecast_sha256=row['forecast_sha256'],predictions=predictions,
                        totals=next((t for t in forecast.get('game_totals',[]) if t['game_id']==game['game_id']),None),
                        margin_forecast=next((t for t in forecast.get('margin_forecasts',[]) if t['game_id']==game['game_id']),None),
                        lineups=snapshot.get('preflight',{}).get('lineups'))
    return chosen


def _actuals(folder,game,season,as_of):
    path=folder/'finals'/(game['game_id']+'.json')
    if not path.exists():return None,dict(status='pending',reason='Final boxscore has not been reconciled.')
    raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    try:
        rows=json.loads(raw);validate_history(rows)
        teams={game['home_team_id'],game['away_team_id']};start=parse_time(game['start_time']);cutoff=parse_time(as_of)
        if {r['team_id'] for r in rows}!=teams:raise ValueError('Final must cover both scheduled teams')
        if any(r['game_id']!=game['game_id'] or r['season']!=season or parse_time(r['game_time'])!=start or
               r['opponent_id'] not in teams-{r['team_id']} or not start<parse_time(r['known_at'])<=cutoff for r in rows):
            raise ValueError('Final identity, observation time or cutoff differs')
        for team in teams:
            members=[r for r in rows if r['team_id']==team]
            boxes=[r.get('team_boxscore') for r in members]
            if any(boxes):
                if any(b!=boxes[0] for b in boxes):raise ValueError('Final team-stat evidence differs')
                for field in ('pts','fga','fgm','fta','ftm','fg3a','fg3m','tov'):
                    if boxes[0].get(field) is not None and sum(r[field] for r in members)!=boxes[0][field]:
                        raise ValueError('Final player/team '+field+' differs')
        periods={r.get('game_period') for r in rows}
        period=next(iter(periods)) if len(periods)==1 and None not in periods else None
        return rows,dict(status='verified',sha256=sha,known_at=max((r['known_at'] for r in rows),key=parse_time),
            source_urls=sorted({r['source_url'] for r in rows}),period=period)
    except (ValueError,KeyError,TypeError) as exc:
        return None,dict(status='pending',sha256=sha,reason=str(exc))


def _metric(rows):
    matched=[p for p in rows if p['feedback']['points_error'] is not None]
    out=dict(count=len(matched),points_mae=None,points_bias=None,points_rmse=None,minutes_mae=None,interval_count=0,interval80_coverage=None,per_stat={})
    if not matched:return out
    errors=[p['feedback']['points_error'] for p in matched]
    out.update(points_mae=fmean(abs(e) for e in errors),points_bias=fmean(errors),points_rmse=math.sqrt(fmean(e*e for e in errors)),
        minutes_mae=fmean(abs(p['feedback']['minutes_error']) for p in matched))
    intervals=[p['interval_contains_actual'] for p in matched if p['interval_contains_actual'] is not None]
    out.update(interval_count=len(intervals),interval80_coverage=fmean(intervals) if intervals else None)
    for stat in STATS:
        values=[p['actual'][stat]-p['predicted'][stat] for p in matched if p['actual'].get(stat) is not None and p['predicted'].get(stat) is not None]
        out['per_stat'][stat]=dict(count=len(values),mae=fmean(abs(v) for v in values) if values else None,bias=fmean(values) if values else None)
    return out


def _errors(values):
    return dict(count=len(values),mae=fmean(abs(v) for v in values) if values else None,bias=fmean(values) if values else None)


def _stage(game,selection,actuals,evidence):
    rows={r['player_id']:r for r in actuals or []};players=[]
    teams={game[side+'_team_id']:game.get(side+'_tricode',game[side+'_team_id']) for side in ('home','away')}
    for p in selection['predictions']:
        a=rows.get(p['player_id'])
        if a and a['team_id']!=p['team_id']:a=None
        feedback=player_feedback(p,a) if a else player_feedback(None,{})
        lower,upper=p.get('points_lower80'),p.get('points_upper80')
        interval=None if not a or lower is None or upper is None else lower<=a['pts']<=upper
        rotation=p.get('components',{}).get('rotation',{})
        players.append(dict(game_id=game['game_id'],player_id=p['player_id'],name=p['name'],team_id=p['team_id'],team=teams[p['team_id']],
            forecast_status=p['status'],actual_status=a['status'] if a else None,
            predicted={stat:p.get(field) for stat,field in STATS.items()},
            actual={stat:a.get(stat) for stat in STATS} if a else None,
            feedback=feedback,interval_contains_actual=interval,flags=p.get('flags',[]),
            rotation=dict(scale=rotation.get('scale'),protected_minutes=rotation.get('protected_rotation_minutes'),
                protected_players=len(rotation.get('protected_rotation_player_ids',[])))))
    totals=selection['totals'];origin='saved_forecast'
    if totals is None:
        totals=game_totals(game,selection['predictions'],lineups=selection['lineups']);origin='derived_from_frozen_players'
    actual_team={t:sum(r['pts'] for r in actuals if r['team_id']==t) for t in teams} if actuals else {}
    score=sum(actual_team.values()) if actual_team else None
    prediction=totals.get('points_mean')
    from .margin_feedback import score_game_margin
    margin=score_game_margin(selection.get('margin_forecast'),game,actuals)
    return dict(**{k:selection[k] for k in ('run_id','created_at','as_of','model_version','model_key','forecast_sha256')},
        players=players,totals=totals,totals_origin=origin,margin_evaluation=margin,
        game_total_error=score-prediction if score is not None and prediction is not None else None,
        overtime=evidence['period']>4 if evidence.get('period') is not None else None,
        team_total_errors=[dict(team_id=t['team_id'],team=teams[t['team_id']],forecast=t.get('points_mean'),actual=actual_team.get(t['team_id']),status=t['status'],
            error=actual_team[t['team_id']]-t['points_mean'] if t.get('points_mean') is not None and t['team_id'] in actual_team else None) for t in totals.get('teams',[])],
        unprojected_actuals=[dict(player_id=a['player_id'],name=a['name'],team_id=a['team_id'],minutes=a['minutes'],points=a['pts']) for a in actuals or [] if a['player_id'] not in {p['player_id'] for p in selection['predictions']}])


def _summarize(games,stage):
    selected=[g['stages'][stage] for g in games if stage in g['stages']]
    players=[p for s in selected for p in s['players']]
    projected=[p for p in players if p['forecast_status']=='projected']
    outs=[p for p in players if p['forecast_status']=='out']
    dnp=[p for p in projected if p['actual_status']=='dnp']
    complete=[s for s in selected if s['totals']['status']=='complete' and s['overtime'] is False and s['game_total_error'] is not None]
    saved=[s for s in complete if s['totals_origin']=='saved_forecast']
    margins=[s['margin_evaluation'] for s in selected if s.get('margin_evaluation',{}).get('status')=='evaluated']
    return dict(forecasts=len(selected),model_versions=dict(Counter(s['model_version'] for s in selected)),
        margin_probabilities=dict(count=len(margins),home_win_brier=sum(r['home_win_brier'] for r in margins)/len(margins) if margins else None,
            ranked_probability_score=sum(r['ranked_probability_score'] for r in margins)/len(margins) if margins else None,
            calibration_status=(next(iter({r['calibration_status'] for r in margins})) if len({r['calibration_status'] for r in margins})==1 else 'mixed' if margins else 'pending'),
            selection='Latest pregame forecast per game and stage, selected before reading outcomes.'),
        projected=_metric(projected),out=_metric(outs),all_numeric=_metric(projected+outs),
        projected_count=len(projected),missing_actuals=sum(p['actual'] is None for p in players),
        missing_projected_actuals=sum(p['actual'] is None for p in projected),missing_out_actuals=sum(p['actual'] is None for p in outs),
        withheld_count=sum(p['forecast_status'] not in ('projected','out') for p in players),
        projected_dnp=dict(count=len(dnp),minutes=sum(p['predicted']['minutes'] for p in dnp),points=sum(p['predicted']['pts'] for p in dnp)),
        out_player_played=[p for p in outs if p['actual_status']=='played' and p['actual']['minutes']>0],
        complete_game_totals=_errors([s['game_total_error'] for s in saved]),
        reconstructed_complete_game_totals=_errors([s['game_total_error'] for s in complete if s['totals_origin']!='saved_forecast']),
        complete_team_totals=_errors([t['error'] for s in selected if s['totals_origin']=='saved_forecast' and s['overtime'] is False for t in s['team_total_errors'] if t['status']=='complete' and t['error'] is not None]),
        largest_misses=sorted([p for p in projected if p['feedback']['points_error'] is not None],key=lambda p:abs(p['feedback']['points_error']),reverse=True)[:12])


def _paired(games):
    pairs=[]
    for game in games:
        stages=game['stages']
        if not all(s in stages for s in ('preliminary','final')):continue
        prior={p['player_id']:p for p in stages['preliminary']['players']}
        for final in stages['final']['players']:
            early=prior.get(final['player_id'])
            if not early or any(p['forecast_status']!='projected' or p['feedback']['points_error'] is None for p in (early,final)):continue
            pairs.append((abs(early['feedback']['points_error']),abs(final['feedback']['points_error']),early['predicted']['pts']!=final['predicted']['pts']))
    return dict(count=len(pairs),preliminary_mae=fmean(p[0] for p in pairs) if pairs else None,
        final_mae=fmean(p[1] for p in pairs) if pairs else None,changed_predictions=sum(p[2] for p in pairs),
        policy='Same projected players with observed outcomes in both stages; latest saved run selected before results.')


def _questions(games,summary):
    players=[p for g in games for p in g['stages'].get('final',{}).get('players',[])]
    candidates=[p for p in players if p['feedback']['minutes_error'] is not None]
    role=sorted([p for p in candidates if abs(p['feedback']['minutes_error'])>=8],key=lambda p:abs(p['feedback']['minutes_error']),reverse=True)[:10]
    attempts=sorted([p for p in candidates if p['feedback']['fga_error'] is not None and abs(p['feedback']['fga_error'])>=5],key=lambda p:abs(p['feedback']['fga_error']),reverse=True)[:10]
    compact=lambda p:dict(player_id=p['player_id'],name=p['name'],game_id=p['game_id'],team=p['team'],predicted=p['predicted'],actual=p['actual'],feedback=p['feedback'],rotation=p['rotation'])
    queue=[]
    squeezed=[p for p in candidates if p['forecast_status']=='projected' and p['predicted']['minutes']==0 and p['actual']['minutes']>=5 and p['rotation']['scale']==0 and (p['rotation']['protected_minutes'] or 0)>=240]
    if squeezed:queue.append(dict(code='bench_squeezed',priority='high',title='Correct rotation protection that erases active bench roles',
        evidence=[compact(p) for p in squeezed],action='Protected roles exceed the entire team budget. Test current-game starter evidence and recent participation so supported reserves are not forced to zero by stale protected roles.'))
    if summary['projected_dnp']['count']:
        queue.append(dict(code='participation',priority='high',title='Review active rotation and DNP risk',
            evidence=summary['projected_dnp'],action='Separate availability from probability of entering the rotation; test role selection across multiple games before changing priors.'))
    if summary['out_player_played']:
        queue.append(dict(code='availability',priority='high',title='Audit out players who played',evidence=[compact(p) for p in summary['out_player_played']],
            action='Inspect original injury timestamps and late updates for each player; retain the original forecast miss.'))
    if role:queue.append(dict(code='minutes',priority='high',title='Check the largest minutes misses',evidence=[compact(p) for p in role],
        action='Review current-team rotation, starter/bench evidence and recent appearances. Confirm injury, foul or blowout explanations with play-by-play or a sourced report.'))
    if attempts:queue.append(dict(code='attempts',priority='normal',title='Check shot opportunity and team volume',evidence=[compact(p) for p in attempts],
        action='Inspect attempts per minute and team possessions before changing scoring efficiency. Use repeated role evidence.'))
    if summary['projected']['interval80_coverage'] is not None and summary['projected']['interval80_coverage']<.7:
        queue.append(dict(code='intervals',priority='normal',title='Track scoring-range coverage by model version',
            evidence=dict(coverage=summary['projected']['interval80_coverage'],samples=summary['projected']['interval_count'],models=summary['model_versions']),
            action='Assess intervals from original saved forecasts. Earlier model outcomes do not validate the newly activated model; collect matching prospective residuals.'))
    return queue


def review_day(directory,date,as_of,*,schedule=None):
    Date.fromisoformat(date);folder=Path(directory)
    if schedule is None:schedule=_read(folder/'schedule/latest.json',{})
    games={g['game_id']:g for g in schedule.get('games',[]) if g.get('start_time') and parse_time(g['start_time']).astimezone(PACIFIC).date().isoformat()==date and g.get('status') not in ('postponed','cancelled','tbd')}
    selected=_selected(folder,games,as_of,schedule.get('season'));reviewed=[];identities={}
    for gid,game in sorted(games.items(),key=lambda item:(parse_time(item[1]['start_time']),item[0])):
        rows,evidence=_actuals(folder,game,schedule['season'],as_of)
        for row in rows or []:
            identities.setdefault(row['player_id'],[]).append({k:row[k] for k in ('game_id','player_id','name','team_id','status','minutes','source_url')})
        stages={stage:_stage(game,selected[(gid,stage)],rows,evidence) for stage in ('preliminary','final') if (gid,stage) in selected}
        pbp=_read(folder/'postgame'/(gid+'.json'),{})
        verified_pbp=bool(pbp.get('coverage',{}).get('final_score_reconciled')) and parse_time(pbp.get('known_at',as_of))<=parse_time(as_of)
        if verified_pbp:
            verified_pbp=rows is not None and all(pbp.get(side,{}).get('id')==game[side+'_team_id'] and pbp[side]['score']==sum(r['pts'] for r in rows if r['team_id']==game[side+'_team_id']) for side in ('home','away'))
        reviewed.append(dict(game_id=gid,start_time=game['start_time'],matchup=game.get('away_tricode',game['away_team_id'])+' @ '+game.get('home_tricode',game['home_team_id']),
            home_team_id=game['home_team_id'],away_team_id=game['away_team_id'],
            score={t:sum(r['pts'] for r in rows if r['team_id']==t) for t in (game['away_team_id'],game['home_team_id'])} if rows else None,
            actuals=evidence,stages=stages,play_by_play_available=verified_pbp,
            game_pattern=pbp.get('pattern') if verified_pbp else None))
    stages={s:_summarize(reviewed,s) for s in ('preliminary','final')}
    source_review=[dict(player_id=pid,name=records[0]['name'],reason='Same NBA identity appears for multiple teams on this Pacific date; verify reported nonparticipating roster entries.',appearances=records) for pid,records in identities.items() if len({r['team_id'] for r in records})>1]
    coverage=dict(scheduled_games=len(games),verified_finals=sum(g['actuals']['status']=='verified' for g in reviewed),
        final_forecasts=sum('final' in g['stages'] for g in reviewed),preliminary_forecasts=sum('preliminary' in g['stages'] for g in reviewed),
        play_by_play_games=sum(g['play_by_play_available'] for g in reviewed))
    result=dict(schema_version=1,analysis_version=VERSION,date=date,timezone='America/Los_Angeles',
        status='complete' if coverage['verified_finals']==len(games) and games else 'pending',coverage=coverage,
        stages=stages,paired_stages=_paired(reviewed),games=reviewed,source_review=source_review,improvement_queue=_questions(reviewed,stages['final']),
        signed_error_convention='actual minus forecast; positive means underprediction',
        policy='Original live forecasts only; projected DNPs count, absent actual rows remain unscored. Partial totals and overtime are excluded from complete regulation-total metrics. Derived totals are separate from saved totals.',
        interpretation='One day identifies questions, not proven parameter improvements. Error components are accounting terms, not causes. New actuals feed history automatically; weights require multi-game validation.')
    result['input_signature']=hashlib.sha256(canonical(result)).hexdigest();result['generated_at']=as_of
    return result


def _source_watch(folder,date,game_ids,as_of,*,schedule=None):
    """Detect late supplied corrections without rebuilding every old date."""
    if schedule is None:schedule=_read(folder/'schedule/latest.json',{})
    games=[g for g in schedule.get('games',[]) if g.get('start_time') and parse_time(g['start_time']).astimezone(PACIFIC).date().isoformat()==date]
    sources={}
    for gid in sorted(set(game_ids)|{g['game_id'] for g in games}):
        for directory in ('finals','postgame'):
            path=folder/directory/(gid+'.json');key=directory+'/'+gid
            raw=path.read_bytes() if path.exists() else None
            sources[key]=hashlib.sha256(raw).hexdigest() if raw is not None else None
    return hashlib.sha256(canonical(dict(version=VERSION,games=games,sources=sources))).hexdigest()


def build_daily_reviews(directory,as_of=None,*,schedule=None):
    folder=Path(directory);as_of=as_of or datetime.now(timezone.utc).isoformat();today=parse_time(as_of).astimezone(PACIFIC).date()
    output=folder/'daily-reviews';reports=dict(_read(output/'index.json',{}).get('reports',{}));errors=[]
    dates={(today-timedelta(days=1)).isoformat(),today.isoformat()}
    for date,previous in reports.items():
        if date not in dates and previous.get('source_watch')!=_source_watch(folder,date,previous.get('game_ids',[]),as_of,schedule=schedule):dates.add(date)
    for date in sorted(dates):
        try:
            report=review_day(folder,date,as_of,schedule=schedule)
            if not report['coverage']['scheduled_games'] and date not in reports:continue
            path=output/(date+'.json');old=_read(path,{})
            if old.get('input_signature')!=report['input_signature']:
                atomic_json(output/'revisions'/date/(report['input_signature']+'.json'),report);atomic_json(path,report)
            ids=[g['game_id'] for g in report['games']]
            reports[date]=dict(status=report['status'],coverage=report['coverage'],input_signature=report['input_signature'],game_ids=ids,
                source_watch=_source_watch(folder,date,ids,as_of,schedule=schedule))
        except (ValueError,KeyError,TypeError,OSError,sqlite3.Error) as exc:errors.append(dict(date=date,error=str(exc)))
    index=dict(generated_at=as_of,analysis_version=VERSION,reports=reports,errors=errors)
    atomic_json(output/'index.json',index);return index
