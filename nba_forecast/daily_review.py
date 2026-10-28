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

VERSION='daily-review-1.2'
PACIFIC=ZoneInfo('America/Los_Angeles')
STATS={'pts':'points_mean','minutes':'expected_minutes',**STAT_PROJECTIONS}
# Miss classes (_classify), first match wins. Noise is what the game's own events
# explain; model classes are what a forecast change could address; an exit that no
# archived evidence explains is neither.
NOISE=('early_exit','blowout','blowout_assumed','foul_trouble','shooting_variance','within_noise')
MODEL=('did_not_play','role_shift','role_shift_repeated','volume')
UNEXPLAINED=('exit_unexplained',)
REPORTED=('out','doubtful','questionable')
# Each of the two earlier games must miss by this many minutes the same way.
REPEAT_MINUTES=4
# Recurrence: a minutes streak counts misses of at least this size, a team-total streak errors of at least this size.
STREAK_MINUTES=6
STREAK_POINTS=5
# Recent nights are reviewed again every build: a miss's exit evidence comes from
# each team's next forecast, which can be saved a few days after the game.
REBUILT_DAYS=4


def _read(path,default=None):
    return json.loads(path.read_text()) if path.exists() else default


def _runs(conn,game_ids,columns):
    """Live league runs that saved one of these games; no other run is parsed."""
    return conn.execute(f"SELECT rowid AS insertion_order,{columns} FROM runs WHERE mode='live' AND scope='league' AND EXISTS "
        "(SELECT 1 FROM json_each(snapshot_json,'$.games') AS g WHERE json_extract(g.value,'$.game_id') IN (SELECT value FROM json_each(?)))",
        (json.dumps(sorted(game_ids)),))


def _selected(folder,games,as_of,season):
    """Freeze one entire run per game/stage before opening any outcome file.

    Runs are ranked from the ledger columns and the saved game identities; only
    the chosen run per game and stage is opened, verified and returned.
    """
    db=folder/'forecast.sqlite'
    chosen={}
    cutoff=parse_time(as_of)
    if not db.exists() or not games:
        return chosen
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as conn:
        conn.row_factory=sqlite3.Row
        ranked={}
        columns="created_at,json_extract(forecast_json,'$.season') AS season,json_extract(forecast_json,'$.forecast_stage') AS stage,json_extract(snapshot_json,'$.games') AS saved_games"
        for row in _runs(conn,games,columns):
            created=parse_time(row['created_at'])
            stage=row['stage'] or 'preliminary'
            if created>cutoff or row['season']!=season or stage not in ('preliminary','final'):
                continue
            for saved in json.loads(row['saved_games']):
                game=games.get(saved['game_id'])
                if not game or game.get('status') in ('postponed','cancelled','tbd'):
                    continue
                start=parse_time(game['start_time'])
                if created>=start or parse_time(saved['start_time'])!=start:
                    continue
                if any(saved[k]!=game[k] for k in ('home_team_id','away_team_id')):
                    continue
                rank=(created,row['insertion_order'])
                key=(game['game_id'],stage)
                if key not in ranked or rank>ranked[key]:
                    ranked[key]=rank
        for (gid,stage),rank in sorted(ranked.items()):
            game=games[gid]
            start=parse_time(game['start_time'])
            row=conn.execute('SELECT run_id,created_at,as_of,snapshot_json,forecast_json,snapshot_sha256,forecast_sha256 FROM runs WHERE rowid=?',(rank[1],)).fetchone()
            for kind in ('snapshot','forecast'):
                if hashlib.sha256(row[kind+'_json'].encode()).hexdigest()!=row[kind+'_sha256']:
                    raise ValueError('Frozen '+kind+' hash mismatch: '+row['run_id'])
            snapshot=json.loads(row['snapshot_json'])
            forecast=json.loads(row['forecast_json'])
            if forecast['season']!=snapshot['season']:
                raise ValueError('Frozen forecast season differs')
            if not parse_time(forecast['as_of'])==parse_time(snapshot['as_of'])==parse_time(row['as_of'])<=rank[0]:
                raise ValueError('Frozen forecast chronology differs: '+row['run_id'])
            predictions=[p for p in forecast['predictions'] if p['game_id']==gid]
            if any(p['team_id'] not in (game['home_team_id'],game['away_team_id']) or parse_time(p['start_time'])!=start for p in predictions):
                raise ValueError('Frozen prediction identity differs: '+row['run_id'])
            chosen[(gid,stage)]=dict(rank=rank,run_id=row['run_id'],created_at=row['created_at'],as_of=forecast['as_of'],
                model_version=forecast['model_version'],model_key=forecast.get('model_key'),season=forecast['season'],
                forecast_sha256=row['forecast_sha256'],predictions=predictions,
                totals=next((t for t in forecast.get('game_totals',[]) if t['game_id']==gid),None),
                margin_forecast=next((t for t in forecast.get('margin_forecasts',[]) if t['game_id']==gid),None),
                lineups=snapshot.get('preflight',{}).get('lineups'))
    return chosen


def _actuals(folder,game,season,as_of):
    path=folder/'finals'/(game['game_id']+'.json')
    if not path.exists():
        return None,dict(status='pending',reason='Final boxscore has not been reconciled.')
    raw=path.read_bytes()
    sha=hashlib.sha256(raw).hexdigest()
    try:
        rows=json.loads(raw)
        validate_history(rows)
        teams={game['home_team_id'],game['away_team_id']}
        start=parse_time(game['start_time'])
        cutoff=parse_time(as_of)
        if {r['team_id'] for r in rows}!=teams:
            raise ValueError('Final must cover both scheduled teams')
        if any(r['game_id']!=game['game_id'] or r['season']!=season or parse_time(r['game_time'])!=start or
               r['opponent_id'] not in teams-{r['team_id']} or not start<parse_time(r['known_at'])<=cutoff for r in rows):
            raise ValueError('Final identity, observation time or cutoff differs')
        for team in teams:
            members=[r for r in rows if r['team_id']==team]
            boxes=[r.get('team_boxscore') for r in members]
            if any(boxes):
                if any(b!=boxes[0] for b in boxes):
                    raise ValueError('Final team-stat evidence differs')
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
    if not matched:
        return out
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


def _clock(action):
    minutes,seconds=action['clock'][2:-1].split('M')
    return f"Q{action['period']} {int(minutes)}:{int(float(seconds)):02d}" if action['period']<=4 else f"OT{action['period']-4} {int(minutes)}:{int(float(seconds)):02d}"


def _pbp_exit(player,context):
    """A starter's last recorded exit with no later action, before the final two minutes and any garbage time.

    A reserve who sits for good is as often a coaching choice as an injury, so
    only a starter's exit counts without a report or a recap.
    """
    pbp=context['postgame']
    record=context['pbp_players'].get(player['player_id'])
    left=(record or {}).get('last_recorded_exit')
    if not pbp or not player.get('starter') or not left or record.get('last_action_elapsed') is None or record['last_action_elapsed']>left['elapsed']:
        return None
    low=pbp.get('low_leverage_start')
    if left['elapsed']>=pbp['duration']-120 or (low is not None and left['elapsed']>=low) or (player['actual'].get('pf') or 0)>=6:
        return None
    return dict(kind='play_by_play',detail=f"Last substitution out at {_clock(left)} with no later action")


def _classify(player,context):
    """Label a projected player's miss from the final box, the play-by-play and later reports."""
    feedback=player['feedback']
    if player['forecast_status']!='projected' or feedback['points_error'] is None:
        return dict(miss_class=None,miss_evidence=[])
    actual,predicted=player['actual'],player['predicted']
    minutes,projected,error=actual['minutes'],predicted['minutes'],feedback['minutes_error']
    if player['actual_status']=='dnp' or not minutes:
        return dict(miss_class='did_not_play',miss_evidence=[dict(kind='box',detail=f"Did not play at participation probability {player['participation_probability']:.2f}"
            if player.get('participation_probability') is not None else 'Did not play')])
    # Without a starter flag in the final, a projection of 24 minutes stands in for one.
    starter=player['starter'] if player.get('starter') is not None else projected>=24
    if minutes<=projected/2 and minutes<=20 and error<=-6:
        evidence=list(context['later'].get(player['player_id'],[]))
        exit=_pbp_exit(player,context)
        if exit:
            evidence.append(exit)
        if evidence:
            return dict(miss_class='early_exit',miss_evidence=evidence)
        # A reserve's short night without a report or recap is a rotation choice, judged below as a role miss.
        if starter:
            return dict(miss_class='exit_unexplained',miss_evidence=[])
    if (starter and error<=-6) or (not starter and error>=6):
        low=(context['postgame'] or {}).get('low_leverage_start')
        if low is not None:
            return dict(miss_class='blowout',miss_evidence=[dict(kind='play_by_play',detail=f'Sustained 20-point lead from minute {low/60:.0f}; '+('starter below' if starter else 'reserve above')+' projection')])
        if not context['postgame'] and (context['margin'] or 0)>=20:
            return dict(miss_class='blowout_assumed',miss_evidence=[dict(kind='box',detail=f"Final margin {context['margin']}; no play-by-play")])
    if (actual.get('pf') or 0)>=5 and error<=-6:
        return dict(miss_class='foul_trouble',miss_evidence=[dict(kind='box',detail=f"{actual['pf']} personal fouls")])
    if abs(error)>=8:
        earlier=[errors.get(player['player_id']) for errors in context['previous'].get(player['team_id'],[])]
        if len(earlier)==2 and all(e is not None and abs(e)>=REPEAT_MINUTES and (e>0)==(error>0) for e in earlier):
            return dict(miss_class='role_shift_repeated',miss_evidence=[dict(kind='earlier_games',detail='Minutes errors in the two previous team games: '+', '.join(f'{e:+.1f}' for e in earlier))])
        return dict(miss_class='role_shift',miss_evidence=[])
    if abs(error)<=5 and abs(feedback['fga_error'])>=5:
        return dict(miss_class='volume',miss_evidence=[])
    if abs(error)<=5 and abs(feedback['fga_error'])<=3:
        return dict(miss_class='shooting_variance',miss_evidence=[])
    return dict(miss_class='within_noise',miss_evidence=[])


def _stage(game,selection,actuals,evidence,context):
    rows={r['player_id']:r for r in actuals or []}
    players=[]
    teams={game[side+'_team_id']:game.get(side+'_tricode',game[side+'_team_id']) for side in ('home','away')}
    for p in selection['predictions']:
        a=rows.get(p['player_id'])
        if a and a['team_id']!=p['team_id']:
            a=None
        feedback=player_feedback(p,a) if a else player_feedback(None,{})
        lower,upper=p.get('points_lower80'),p.get('points_upper80')
        interval=None if not a or lower is None or upper is None else lower<=a['pts']<=upper
        rotation=p.get('components',{}).get('rotation',{})
        players.append(dict(game_id=game['game_id'],player_id=p['player_id'],name=p['name'],team_id=p['team_id'],team=teams[p['team_id']],
            forecast_status=p['status'],actual_status=a['status'] if a else None,
            predicted={stat:p.get(field) for stat,field in STATS.items()},
            actual={stat:a.get(stat) for stat in STATS} if a else None,
            feedback=feedback,interval_contains_actual=interval,flags=p.get('flags',[]),
            participation_probability=p.get('participation_probability'),starter=a.get('starter') if a else None,
            rotation=dict(scale=rotation.get('scale'),protected_minutes=rotation.get('protected_rotation_minutes'),
                protected_players=len(rotation.get('protected_rotation_player_ids',[])))))
        players[-1].update(_classify(players[-1],context))
    totals=selection['totals']
    origin='saved_forecast'
    if totals is None:
        totals=game_totals(game,selection['predictions'],lineups=selection['lineups'])
        origin='derived_from_frozen_players'
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


def _compact(p):
    return dict(player_id=p['player_id'],name=p['name'],game_id=p['game_id'],team=p['team'],predicted=p['predicted'],actual=p['actual'],
        feedback=p['feedback'],rotation=p['rotation'],participation_probability=p.get('participation_probability'),
        miss_class=p.get('miss_class'),miss_evidence=p.get('miss_evidence',[]))


def _participation(projected):
    """Participation against the model's own probabilities, so expected bench DNPs do not read as misses.

    Under calibration the played count has mean sum(p) and variance sum(p(1-p)),
    and the Brier sum has mean sum(p(1-p)) and variance sum(p(1-p)(1-2p)^2); the
    two z-scores say how far the night sits from what the forecasts expected.
    """
    rows=[p for p in projected if p['actual'] is not None and p.get('participation_probability') is not None]
    if not rows:
        return dict(count=0,played=0,played_share=None,mean_probability=None,brier=None,expected_brier=None,shortfall_z=None,brier_z=None,
            expected_minutes_lost=0,expected_dnp_minutes=0,likely_players_sat=[],unlikely_players_played=[])
    played=[p['actual_status']=='played' and p['actual']['minutes']>0 for p in rows]
    probability=[p['participation_probability'] for p in rows]
    minutes=[p['predicted']['minutes'] or 0 for p in rows]
    variance=sum(q*(1-q) for q in probability)
    brier=sum((q-y)**2 for q,y in zip(probability,played))
    spread=sum(q*(1-q)*(1-2*q)**2 for q in probability)
    return dict(count=len(rows),played=sum(played),played_share=sum(played)/len(rows),mean_probability=fmean(probability),
        brier=brier/len(rows),expected_brier=variance/len(rows),
        shortfall_z=(sum(probability)-sum(played))/math.sqrt(variance) if variance else None,
        brier_z=(brier-variance)/math.sqrt(spread) if spread else None,
        expected_minutes_lost=sum(q*m for q,m,y in zip(probability,minutes,played) if not y),
        expected_dnp_minutes=sum((1-q)*m for q,m in zip(probability,minutes)),
        likely_players_sat=[_compact(p) for p,y in zip(rows,played) if not y and p['participation_probability']>=.5],
        # A reserve who entered only in garbage time is the blowout's, not the forecast's.
        unlikely_players_played=[_compact(p) for p,y in zip(rows,played) if y and p['participation_probability']<.5 and p.get('miss_class') not in NOISE])


def _classes(projected):
    """Absolute points and minutes error by miss class; the shares add up to the whole night."""
    rows=[p for p in projected if p.get('miss_class')]
    points=sum(abs(p['feedback']['points_error']) for p in rows)
    minutes=sum(abs(p['feedback']['minutes_error']) for p in rows)
    def part(members):
        chosen=[p for p in rows if p['miss_class'] in members]
        pts=sum(abs(p['feedback']['points_error']) for p in chosen)
        mins=sum(abs(p['feedback']['minutes_error']) for p in chosen)
        return dict(count=len(chosen),points_abs_error=pts,minutes_abs_error=mins,
            points_share=pts/points if points else None,minutes_share=mins/minutes if minutes else None)
    return dict(count=len(rows),points_abs_error=points,minutes_abs_error=minutes,
        classes={c:part((c,)) for c in (*MODEL,*UNEXPLAINED,*NOISE) if any(p['miss_class']==c for p in rows)},
        groups=dict(model=part(MODEL),unexplained=part(UNEXPLAINED),noise=part(NOISE)))


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
        participation=_participation(projected),miss_classes=_classes(projected),
        out_player_played=[p for p in outs if p['actual_status']=='played' and p['actual']['minutes']>0],
        complete_game_totals=_errors([s['game_total_error'] for s in saved]),
        reconstructed_complete_game_totals=_errors([s['game_total_error'] for s in complete if s['totals_origin']!='saved_forecast']),
        complete_team_totals=_errors([t['error'] for s in selected if s['totals_origin']=='saved_forecast' and s['overtime'] is False for t in s['team_total_errors'] if t['status']=='complete' and t['error'] is not None]),
        largest_misses=sorted([p for p in projected if p['feedback']['points_error'] is not None],key=lambda p:abs(p['feedback']['points_error']),reverse=True)[:12])


def _paired(games):
    pairs=[]
    for game in games:
        stages=game['stages']
        if not all(s in stages for s in ('preliminary','final')):
            continue
        prior={p['player_id']:p for p in stages['preliminary']['players']}
        for final in stages['final']['players']:
            early=prior.get(final['player_id'])
            if not early or any(p['forecast_status']!='projected' or p['feedback']['points_error'] is None for p in (early,final)):
                continue
            pairs.append((abs(early['feedback']['points_error']),abs(final['feedback']['points_error']),early['predicted']['pts']!=final['predicted']['pts']))
    return dict(count=len(pairs),preliminary_mae=fmean(p[0] for p in pairs) if pairs else None,
        final_mae=fmean(p[1] for p in pairs) if pairs else None,changed_predictions=sum(p[2] for p in pairs),
        policy='Same projected players with observed outcomes in both stages; latest saved run selected before results.')


def _questions(games,summary):
    """Questions for future forecasts. Misses the game's own events explain are listed apart, never queued."""
    players=[p for g in games for p in g['stages'].get('final',{}).get('players',[])]
    candidates=[p for p in players if p['feedback']['minutes_error'] is not None]
    large=sorted([p for p in candidates if abs(p['feedback']['minutes_error'])>=8],key=lambda p:abs(p['feedback']['minutes_error']),reverse=True)
    volume=sorted([p for p in candidates if p['feedback']['fga_error'] is not None and abs(p['feedback']['fga_error'])>=5],key=lambda p:abs(p['feedback']['fga_error']),reverse=True)
    role=[p for p in large if p.get('miss_class') not in NOISE][:10]
    attempts=[p for p in volume if p.get('miss_class') not in NOISE][:10]
    explained=sorted({(p['game_id'],p['player_id']):p for p in large+volume if p.get('miss_class') in NOISE}.values(),key=lambda p:abs(p['feedback']['points_error']),reverse=True)
    queue=[]
    squeezed=[p for p in candidates if p['forecast_status']=='projected' and p['predicted']['minutes']==0 and p['actual']['minutes']>=5 and p['rotation']['scale']==0 and (p['rotation']['protected_minutes'] or 0)>=240
              and p.get('miss_class') not in NOISE]
    if squeezed:
        queue.append(dict(code='bench_squeezed',priority='high',title='Correct rotation protection that erases active bench roles',
        evidence=[_compact(p) for p in squeezed],action='Protected roles exceed the entire team budget. Test current-game starter evidence and recent participation so supported reserves are not forced to zero by stale protected roles.'))
    participation=summary['participation']
    measures={k:participation[k] for k in ('count','played_share','mean_probability','brier','expected_brier','shortfall_z','brier_z','expected_minutes_lost','expected_dnp_minutes')}
    if (participation['shortfall_z'] or 0)>=2 or (participation['brier_z'] or 0)>=2:
        queue.append(dict(code='participation',priority='high',title='Review active rotation and DNP risk',evidence=participation['likely_players_sat'],measures=measures,
            action='More projected players sat out, or sat out more surprisingly, than their probabilities allow. Separate availability from probability of entering the rotation; test role selection across multiple games before changing priors.'))
    elif (participation['shortfall_z'] or 0)<=-2:
        queue.append(dict(code='participation_low',priority='normal',title='Participation probabilities ran low',evidence=participation['unlikely_players_played'],measures=measures,
            action='More projected players entered than their probabilities expected. Compare the team probability goal and the role priors with how many players actually enter, across several nights.'))
    if summary['out_player_played']:
        queue.append(dict(code='availability',priority='high',title='Audit out players who played',evidence=[_compact(p) for p in summary['out_player_played']],
            action='Inspect original injury timestamps and late updates for each player; retain the original forecast miss.'))
    if role:
        queue.append(dict(code='minutes',priority='high',title='Check the largest minutes misses',evidence=[_compact(p) for p in role],
        action='Misses the game itself does not explain (no exit, blowout or foul trouble). Review current-team rotation, starter/bench evidence and recent appearances; a repeated role shift is the candidate for a gated change.'))
    if attempts:
        queue.append(dict(code='attempts',priority='normal',title='Check shot opportunity and team volume',evidence=[_compact(p) for p in attempts],
        action='Inspect attempts per minute and team possessions before changing scoring efficiency. Use repeated role evidence.'))
    if summary['projected']['interval80_coverage'] is not None and summary['projected']['interval80_coverage']<.7:
        queue.append(dict(code='intervals',priority='normal',title='Track scoring-range coverage by model version',
            evidence=dict(coverage=summary['projected']['interval80_coverage'],samples=summary['projected']['interval_count'],models=summary['model_versions']),
            action='Assess intervals from original saved forecasts. Earlier model outcomes do not validate the newly activated model; collect matching prospective residuals.'))
    return queue,[_compact(p) for p in explained]


def _playable(schedule):
    return [g for g in schedule.get('games',[]) if g.get('start_time') and g.get('status') not in ('postponed','cancelled','tbd')]


def _later(folder,games,schedule,as_of):
    """Reported statuses and recap injury exits from each team's next frozen forecast.

    Both are outcomes of the reviewed game (a player listed after it, a recap of
    it): they label a miss and never change what is scored. The latest run of the
    team's next game saved before its tip and by ``as_of`` is the one read.
    """
    cutoff=parse_time(as_of)
    playable=_playable(schedule)
    following={}
    for game in games.values():
        start=parse_time(game['start_time'])
        for team in (game['home_team_id'],game['away_team_id']):
            later=[g for g in playable if team in (g['home_team_id'],g['away_team_id']) and parse_time(g['start_time'])>start]
            if later:
                following[(game['game_id'],team)]=min(later,key=lambda g:(parse_time(g['start_time']),g['game_id']))
    found={gid:{} for gid in games}
    db=folder/'forecast.sqlite'
    if not following or not db.exists():
        return found
    targets={g['game_id']:g for g in following.values()}
    runs={}
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as conn:
        conn.row_factory=sqlite3.Row
        latest={}
        for row in _runs(conn,targets,"created_at,json_extract(snapshot_json,'$.games') AS saved_games"):
            created=parse_time(row['created_at'])
            for saved in json.loads(row['saved_games']):
                target=targets.get(saved['game_id'])
                if target and created<=cutoff and created<parse_time(target['start_time']):
                    rank=(created,row['insertion_order'])
                    latest[saved['game_id']]=max(latest.get(saved['game_id'],rank),rank)
        for gid,rank in sorted(latest.items()):
            row=conn.execute('SELECT run_id,snapshot_json,forecast_json,snapshot_sha256,forecast_sha256 FROM runs WHERE rowid=?',(rank[1],)).fetchone()
            for kind in ('snapshot','forecast'):
                if hashlib.sha256(row[kind+'_json'].encode()).hexdigest()!=row[kind+'_sha256']:
                    raise ValueError('Frozen '+kind+' hash mismatch: '+row['run_id'])
            runs[gid]=(row['run_id'],json.loads(row['snapshot_json']),json.loads(row['forecast_json']))
    for (gid,team),target in sorted(following.items()):
        if target['game_id'] not in runs:
            continue
        run_id,snapshot,forecast=runs[target['game_id']]
        for event in (snapshot.get('role_context') or {}).get('events',[]):
            if event['game_id']==gid and event['team_id']==team:
                found[gid].setdefault(event['player_id'],[]).append(dict(kind='recap_exit',detail=event['source_text'],source_url=event['source_url']))
        for p in forecast['predictions']:
            status=p.get('components',{}).get('availability',{})
            if p['team_id']==team and p['game_id']==target['game_id'] and status.get('status') in REPORTED:
                found[gid].setdefault(p['player_id'],[]).append(dict(kind='next_report',detail=f"Listed {status['status']} for the next game",
                    source_url=status.get('status_source_url'),run_id=run_id))
    return found


def _previous(folder,games,schedule,as_of,season):
    """Each team's minutes errors per player in its two previous games, newest first, from their frozen final forecasts."""
    playable=_playable(schedule)
    earlier={}
    for game in games.values():
        start=parse_time(game['start_time'])
        for team in (game['home_team_id'],game['away_team_id']):
            earlier[(game['game_id'],team)]=sorted([g for g in playable if team in (g['home_team_id'],g['away_team_id']) and parse_time(g['start_time'])<start],
                key=lambda g:(parse_time(g['start_time']),g['game_id']),reverse=True)[:2]
    wanted={g['game_id']:g for before in earlier.values() for g in before}
    chosen=_selected(folder,wanted,as_of,season)
    errors={}
    for gid,game in wanted.items():
        selection=chosen.get((gid,'final'))
        rows=_actuals(folder,game,season,as_of)[0] if selection else None
        box={r['player_id']:r for r in rows or []}
        errors[gid]={p['player_id']:box[p['player_id']]['minutes']-p['expected_minutes'] for p in (selection or {}).get('predictions',[])
            if p['status']=='projected' and p.get('expected_minutes') is not None and box.get(p['player_id'],{}).get('team_id')==p['team_id']}
    return {key:[errors[g['game_id']] for g in before] for key,before in earlier.items()}


def _market(folder,games,schedule,as_of,selected):
    """Model, ESPN close, Kalshi close and actual per game, read from the market report's own rows."""
    from .market_lines import market_report
    try:
        report=market_report(folder,dict(season=schedule.get('season'),games=list(games.values())),as_of,selected=selected)
    except (ValueError,KeyError,TypeError,OSError) as exc:
        return {},[dict(error=str(exc))]
    found={}
    error=lambda value,actual:abs(value-actual) if value is not None else None
    for row in report['games']:
        if row['game_id'] in found:
            continue
        margin=dict(model=row['model_margin'],close=row['market_margin'],kalshi=row.get('kalshi_margin'),actual=row['actual_margin'])
        total=dict(model=row['model_total'],close=row['total_line'],kalshi=row.get('kalshi_total'),actual=row['actual_total'])
        found[row['game_id']]=dict(provider=row['provider'],stage=row['stage'],run_id=row['run_id'],line_captured_at=row['line_captured_at'],
            kalshi_captured_at=row.get('kalshi_captured_at'),margin=margin,total=total,
            margin_abs_error={k:error(margin[k],margin['actual']) for k in ('model','close','kalshi')},
            total_abs_error={k:error(total[k],total['actual']) for k in ('model','close','kalshi')})
    return found,report['errors']


def _market_summary(reviewed,errors):
    """The night against the close, and where the model lost to it: team totals and the misses the game does not explain."""
    rows=[g for g in reviewed if g.get('market') and g['market']['margin_abs_error']['close'] is not None]
    lost=[]
    for g in rows:
        m=g['market']
        gap=m['margin_abs_error']['model']-m['margin_abs_error']['close']
        if gap<3:
            continue
        stage=g['stages'].get(m['stage'],{})
        teams=[]
        for t in stage.get('team_total_errors',[]):
            players=[p for p in stage['players'] if p['team_id']==t['team_id'] and p.get('miss_class')]
            signed=lambda members:sum(p['feedback']['points_error'] for p in players if p['miss_class'] in members)
            drivers=sorted([p for p in players if p['miss_class'] not in NOISE],key=lambda p:abs(p['feedback']['points_error']),reverse=True)[:5]
            teams.append(dict(team_id=t['team_id'],team=t['team'],forecast=t['forecast'],actual=t['actual'],error=t['error'],
                model_points=signed(MODEL+UNEXPLAINED),noise_points=signed(NOISE),
                unprojected_points=sum(a['points'] for a in stage['unprojected_actuals'] if a['team_id']==t['team_id']),
                drivers=[dict(player_id=p['player_id'],name=p['name'],miss_class=p['miss_class'],points_error=p['feedback']['points_error'],
                    minutes_error=p['feedback']['minutes_error'],miss_evidence=p['miss_evidence']) for p in drivers]))
        lost.append(dict(game_id=g['game_id'],matchup=g['matchup'],margin=m['margin'],lost_by=gap,teams=teams))
    mae=lambda key:fmean(g['market']['margin_abs_error'][key] for g in rows) if rows else None
    kalshi=[g['market']['margin_abs_error']['kalshi'] for g in rows if g['market']['margin_abs_error']['kalshi'] is not None]
    return dict(games=len(rows),model_margin_mae=mae('model'),close_margin_mae=mae('close'),
        kalshi_games=len(kalshi),kalshi_margin_mae=fmean(kalshi) if kalshi else None,
        lost_to_close=sorted(lost,key=lambda r:(-r['lost_by'],r['game_id'])),errors=errors,
        policy='The close is the last ESPN capture before tip and Kalshi its last pre-tip price. A game the model lost to the close by three points or more lists each team total with the signed points of model-class and noise-class misses and of unprojected players; '
            'the rest of a team error is out players and participation weighting.')


def review_day(directory,date,as_of,*,schedule=None):
    Date.fromisoformat(date)
    folder=Path(directory)
    if schedule is None:
        schedule=_read(folder/'schedule/latest.json',{})
    games={g['game_id']:g for g in schedule.get('games',[]) if g.get('start_time') and parse_time(g['start_time']).astimezone(PACIFIC).date().isoformat()==date and g.get('status') not in ('postponed','cancelled','tbd')}
    selected=_selected(folder,games,as_of,schedule.get('season'))
    later=_later(folder,games,schedule,as_of)
    previous=_previous(folder,games,schedule,as_of,schedule.get('season'))
    market,market_errors=_market(folder,games,schedule,as_of,selected)
    reviewed=[]
    identities={}
    for gid,game in sorted(games.items(),key=lambda item:(parse_time(item[1]['start_time']),item[0])):
        rows,evidence=_actuals(folder,game,schedule['season'],as_of)
        for row in rows or []:
            identities.setdefault(row['player_id'],[]).append({k:row[k] for k in ('game_id','player_id','name','team_id','status','minutes','source_url')})
        pbp=_read(folder/'postgame'/(gid+'.json'),{})
        verified_pbp=bool(pbp.get('coverage',{}).get('final_score_reconciled')) and parse_time(pbp.get('known_at',as_of))<=parse_time(as_of)
        if verified_pbp:
            verified_pbp=rows is not None and all(pbp.get(side,{}).get('id')==game[side+'_team_id'] and pbp[side]['score']==sum(r['pts'] for r in rows if r['team_id']==game[side+'_team_id']) for side in ('home','away'))
        score={t:sum(r['pts'] for r in rows if r['team_id']==t) for t in (game['away_team_id'],game['home_team_id'])} if rows else None
        context=dict(postgame=pbp if verified_pbp else None,pbp_players={p['player_id']:p for p in pbp.get('players',[])} if verified_pbp else {},
            margin=abs(score[game['home_team_id']]-score[game['away_team_id']]) if score else None,later=later[gid],
            previous={team:previous[(gid,team)] for team in (game['home_team_id'],game['away_team_id'])})
        stages={stage:_stage(game,selected[(gid,stage)],rows,evidence,context) for stage in ('preliminary','final') if (gid,stage) in selected}
        reviewed.append(dict(game_id=gid,start_time=game['start_time'],matchup=game.get('away_tricode',game['away_team_id'])+' @ '+game.get('home_tricode',game['home_team_id']),
            home_team_id=game['home_team_id'],away_team_id=game['away_team_id'],score=score,
            actuals=evidence,stages=stages,play_by_play_available=verified_pbp,
            game_pattern=pbp.get('pattern') if verified_pbp else None,market=market.get(gid) if evidence['status']=='verified' else None))
    stages={s:_summarize(reviewed,s) for s in ('preliminary','final')}
    queue,explained=_questions(reviewed,stages['final'])
    source_review=[dict(player_id=pid,name=records[0]['name'],reason='Same NBA identity appears for multiple teams on this Pacific date; verify reported nonparticipating roster entries.',appearances=records) for pid,records in identities.items() if len({r['team_id'] for r in records})>1]
    coverage=dict(scheduled_games=len(games),verified_finals=sum(g['actuals']['status']=='verified' for g in reviewed),
        final_forecasts=sum('final' in g['stages'] for g in reviewed),preliminary_forecasts=sum('preliminary' in g['stages'] for g in reviewed),
        play_by_play_games=sum(g['play_by_play_available'] for g in reviewed))
    result=dict(schema_version=1,analysis_version=VERSION,date=date,timezone='America/Los_Angeles',
        status='complete' if coverage['verified_finals']==len(games) and games else 'pending',coverage=coverage,
        stages=stages,paired_stages=_paired(reviewed),games=reviewed,source_review=source_review,improvement_queue=queue,explained_misses=explained,
        market=_market_summary(reviewed,market_errors),
        signed_error_convention='actual minus forecast; positive means underprediction',
        policy='Original live forecasts only; projected DNPs count, absent actual rows remain unscored. Partial totals and overtime are excluded from complete regulation-total metrics. Derived totals are separate from saved totals.',
        interpretation='One day identifies questions, not proven parameter improvements. Error components are accounting terms, not causes. New actuals feed history automatically; weights require multi-game validation.')
    result['input_signature']=hashlib.sha256(canonical(result)).hexdigest()
    result['generated_at']=as_of
    return result


def _source_watch(folder,date,game_ids,as_of,*,schedule=None):
    """Detect late supplied corrections without rebuilding every old date."""
    if schedule is None:
        schedule=_read(folder/'schedule/latest.json',{})
    games=[g for g in schedule.get('games',[]) if g.get('start_time') and parse_time(g['start_time']).astimezone(PACIFIC).date().isoformat()==date]
    sources={}
    for gid in sorted(set(game_ids)|{g['game_id'] for g in games}):
        for directory in ('finals','postgame'):
            path=folder/directory/(gid+'.json')
            key=directory+'/'+gid
            raw=path.read_bytes() if path.exists() else None
            sources[key]=hashlib.sha256(raw).hexdigest() if raw is not None else None
    return hashlib.sha256(canonical(dict(version=VERSION,games=games,sources=sources))).hexdigest()


def _streak(appearances,key,floor):
    """Most recent run of same-sign errors of at least ``floor``, newest first; noise-class nights neither extend nor break it."""
    run=[]
    for a in reversed(appearances):
        if a.get('miss_class') in NOISE:
            continue
        if abs(a[key])<floor or (run and (a[key]>0)!=(run[0][key]>0)):
            break
        run.append(a)
    return dict(length=len(run),direction=('over' if run[0][key]>0 else 'under') if run else None,nights=[a['date'] for a in run])


def _tail(probabilities,observed):
    """Chance of at least ``observed`` DNPs when each appearance is played with its probability."""
    counts=[1.]
    for q in probabilities:
        counts=[a*q+b*(1-q) for a,b in zip(counts+[0.],[0.]+counts)]
    return sum(counts[observed:])


def build_recurrence(directory,nights=14):
    """Thread the saved nightly reviews (never the ledger): which misses repeat across nights.

    A repeated miss is a candidate for one gated experiment; one night is only a question.
    """
    folder=Path(directory)/'daily-reviews'
    dates=sorted(p.stem for p in folder.glob('????-??-??.json'))[-nights:]
    players={}
    teams={}
    signatures=[]
    for date in dates:
        report=_read(folder/(date+'.json'),{})
        signatures.append(dict(date=date,input_signature=report.get('input_signature')))
        for game in report.get('games',[]):
            final=game.get('stages',{}).get('final')
            if not final:
                continue
            for p in final['players']:
                if p['forecast_status']!='projected' or p['feedback']['minutes_error'] is None:
                    continue
                played=p['actual_status']=='played' and p['actual']['minutes']>0
                players.setdefault(p['player_id'],[]).append(dict(date=date,game_id=game['game_id'],name=p['name'],team_id=p['team_id'],team=p['team'],
                    minutes_error=p['feedback']['minutes_error'],points_error=p['feedback']['points_error'],miss_class=p.get('miss_class'),
                    participation_probability=p.get('participation_probability'),played=played,predicted_minutes=p['predicted']['minutes']))
                if not played:
                    q=p.get('participation_probability')
                    team=teams.setdefault(p['team_id'],dict(team=p['team'],totals=[],expected_minutes_lost=0.))
                    team['expected_minutes_lost']+=(q if q is not None else 1)*p['predicted']['minutes']
            for t in final['team_total_errors']:
                if final['totals_origin']=='saved_forecast' and final['overtime'] is False and t['status']=='complete' and t['error'] is not None:
                    teams.setdefault(t['team_id'],dict(team=t['team'],totals=[],expected_minutes_lost=0.))['totals'].append(dict(date=date,game_id=game['game_id'],error=t['error']))
    week=(Date.fromisoformat(dates[-1])-timedelta(days=6)).isoformat() if dates else None
    player_rows=[]
    queue=[]
    for pid,rows in sorted(players.items()):
        streak=_streak(rows,'minutes_error',STREAK_MINUTES)
        recent=[r for r in rows if r['date']>=week and r['participation_probability'] is not None]
        sat=[r for r in recent if not r['played'] and r['participation_probability']>=.5]
        # A reserve at 0.52 sits twice in three games a quarter of the time; only a count the forecasts made unlikely repeats.
        chance=_tail([r['participation_probability'] for r in recent],sum(not r['played'] for r in recent)) if len(sat)>=2 else None
        player_rows.append(dict(player_id=pid,name=rows[-1]['name'],team_id=rows[-1]['team_id'],team=rows[-1]['team'],nights=len(rows),
            mean_minutes_error=fmean(r['minutes_error'] for r in rows),mean_points_error=fmean(r['points_error'] for r in rows),
            classes=dict(sorted(Counter(r['miss_class'] for r in rows if r['miss_class']).items())),minutes_streak=streak,likely_sat_this_week=len(sat),
            dnp_chance_this_week=chance))
        if streak['length']>=3:
            queue.append(dict(code='recurring_minutes',player_id=pid,name=rows[-1]['name'],team=rows[-1]['team'],
                title=f"{rows[-1]['name']}: minutes {streak['direction']} by {STREAK_MINUTES}+ on {streak['length']} straight nights",
                evidence=[r for r in rows if r['date'] in streak['nights']]))
        if chance is not None and chance<=.05:
            queue.append(dict(code='recurring_dnp',player_id=pid,name=rows[-1]['name'],team=rows[-1]['team'],chance=chance,
                title=f"{rows[-1]['name']}: sat out {len(sat)} times this week at probability 0.5 or more ({chance:.1%} chance)",evidence=sat))
    team_rows=[]
    for tid,team in sorted(teams.items()):
        totals=team['totals']
        run=[]
        for t in reversed(totals):
            if abs(t['error'])<STREAK_POINTS or (run and (t['error']>0)!=(run[0]['error']>0)):
                break
            run.append(t)
        streak=dict(length=len(run),direction=('over' if run[0]['error']>0 else 'under') if run else None,nights=[t['date'] for t in run])
        team_rows.append(dict(team_id=tid,team=team['team'],nights=len(totals),mean_total_error=fmean(t['error'] for t in totals) if totals else None,
            total_streak=streak,expected_minutes_lost=team['expected_minutes_lost']))
        if streak['length']>=3:
            queue.append(dict(code='recurring_team_total',team_id=tid,team=team['team'],
                title=f"{team['team']}: team points {streak['direction']} by {STREAK_POINTS}+ on {streak['length']} straight nights",evidence=list(reversed(run))))
    result=dict(schema_version=1,analysis_version=VERSION,window=dict(nights=nights,dates=dates),reviews=signatures,
        players=player_rows,teams=team_rows,queue=queue,
        signed_error_convention='actual minus forecast; over means the actual exceeded the forecast',
        policy=f'Final-stage forecasts in the saved nightly reviews. A minutes streak counts nights missed by {STREAK_MINUTES}+ minutes the same way and skips nights a noise class explains; '
            f'a team-total streak counts complete regulation totals missed by {STREAK_POINTS}+ points the same way. A repeated DNP is a player who sat out twice or more in the last seven days at probability 0.5 or more, when the forecasts gave that many DNPs over the week at most a 5% chance.')
    result['input_signature']=hashlib.sha256(canonical(result)).hexdigest()
    return result


def build_daily_reviews(directory,as_of=None,*,schedule=None):
    folder=Path(directory)
    as_of=as_of or datetime.now(timezone.utc).isoformat()
    today=parse_time(as_of).astimezone(PACIFIC).date()
    output=folder/'daily-reviews'
    reports=dict(_read(output/'index.json',{}).get('reports',{}))
    errors=[]
    dates={(today-timedelta(days=n)).isoformat() for n in range(REBUILT_DAYS)}
    for date,previous in reports.items():
        if date not in dates and previous.get('source_watch')!=_source_watch(folder,date,previous.get('game_ids',[]),as_of,schedule=schedule):
            dates.add(date)
    changed=False
    for date in sorted(dates):
        try:
            report=review_day(folder,date,as_of,schedule=schedule)
            if not report['coverage']['scheduled_games'] and date not in reports:
                continue
            path=output/(date+'.json')
            old=_read(path,{})
            if old.get('input_signature')!=report['input_signature']:
                atomic_json(output/'revisions'/date/(report['input_signature']+'.json'),report)
                atomic_json(path,report)
                changed=True
            ids=[g['game_id'] for g in report['games']]
            reports[date]=dict(status=report['status'],coverage=report['coverage'],input_signature=report['input_signature'],game_ids=ids,
                source_watch=_source_watch(folder,date,ids,as_of,schedule=schedule))
        except (ValueError,KeyError,TypeError,OSError,sqlite3.Error) as exc:
            errors.append(dict(date=date,error=str(exc)))
    recurrence=output/'recurrence.json'
    previous=_read(recurrence,{})
    if changed or previous.get('analysis_version')!=VERSION:
        try:
            threaded=build_recurrence(folder)
            if threaded['input_signature']!=previous.get('input_signature'):
                atomic_json(recurrence,threaded)
            previous=threaded
        except (ValueError,KeyError,TypeError,OSError) as exc:
            errors.append(dict(date='recurrence',error=str(exc)))
    index=dict(generated_at=as_of,analysis_version=VERSION,reports=reports,errors=errors,
        recurrence=dict(input_signature=previous.get('input_signature'),queue=len(previous.get('queue',[]))) if previous else None)
    atomic_json(output/'index.json',index)
    return index
