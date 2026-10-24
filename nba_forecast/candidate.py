"""Audited, opt-in possession/context/rotation upgrade to the player baseline."""
from copy import deepcopy
from math import isfinite
from .model import _baseline_forecast, _project_numbers
from .profiles import build_profiles, eligible_history
from .rotation_allocation import redistribute
from .context_effects import context_adjustment
from .calibration import calibrated_interval
from .intervals import INTERVAL_FAMILY
from .totals import game_totals
from .validation import parse_time
from .model_versions import ENHANCED_VERSIONS

VERSION='regular-season-baseline-0.5.0'


def _strength(parameters,name):
    value=parameters.get(name,1.)
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value) or not 0<=value<=1:
        raise ValueError('Invalid enhancement strength: '+name)
    return value


def forecast_candidate(snapshot,history):
    enhancement=snapshot['model_enhancements'];cutoff=parse_time(snapshot['as_of'])
    if enhancement['model_version'] not in ENHANCED_VERSIONS or parse_time(enhancement['known_at'])>cutoff:
        raise ValueError('Unsupported or future-known model enhancement')
    environment=enhancement['environment'];parameters=enhancement['parameters']
    if environment.get('audit',{}).get('evidence_kind')!='live' and not snapshot.get('research_reconstruction'):
        raise ValueError('Historical reconstruction cannot supply a live environment')
    excluded=set(enhancement.get('excluded_history_game_ids',[]))
    history=[r for r in history if r['game_id'] not in excluded]
    result=_baseline_forecast(snapshot,history);profiles=build_profiles(snapshot,history)
    usable=eligible_history(snapshot,history);by_player={}
    for row in usable:by_player.setdefault(row['player_id'],[]).append(row)
    original={};adjustments={(a['player_id'],a['game_id']):a for a in snapshot.get('role_adjustments',[])}
    for p in result['predictions']:
        if p['status']!='projected':continue
        minutes=p['expected_minutes'];scoring=p['components']['scoring'];manual=adjustments.get((p['player_id'],p['game_id']),{})
        rates={k:0. for k in ('fga','fg3a','fta','tov')}
        if minutes>0:
            rates.update(fga=p['expected_fga']/minutes,fg3a=scoring['expected_three_point_attempts']/minutes,
                fta=p['expected_fta']/minutes,tov=p['expected_tov']/minutes)
        original[p['player_id']]=dict(minutes=minutes,rates=rates,minute_cap=manual.get('minutes_cap',48.),
            locked_rates=bool(manual),locked_minutes=bool(manual))
    if parameters.get('rotation_enabled',True):
        inputs,allocations=redistribute(snapshot['roster']['players'],profiles['players'],original,usable,snapshot['as_of'])
    else:inputs,allocations=deepcopy(original),{}
    # A full baseline rotation can already absorb some vacancy shots. Bound any
    # further automatic rate additions against supported team attempt volume.
    volume={}
    for team,env in environment['teams'].items():
        ids=[p['player_id'] for p in result['predictions'] if p['team_id']==team and p['player_id'] in inputs]
        record={}
        # Minute grants consume attempts even when every scoring rate stays
        # unchanged. Apply the tightest team volume limit to those grants first.
        minute_scale=1.
        for field,expected in (('fga','expected_fga'),('fta','expected_fta')):
            target=env.get(expected)
            if target is None or not env.get('pace_factor'):continue
            old=sum(original[pid]['minutes']*original[pid]['rates'][field] for pid in ids)
            target=max(old,target/env['pace_factor'])
            requested=sum(max(0.,inputs[pid]['minutes']-original[pid]['minutes'])*original[pid]['rates'][field] for pid in ids)
            if requested>0:minute_scale=min(minute_scale,max(0.,target-old)/requested)
        for pid in ids:
            extra=max(0.,inputs[pid]['minutes']-original[pid]['minutes'])
            if extra:inputs[pid]['minutes']=original[pid]['minutes']+extra*minute_scale
        for field,expected in (('fga','expected_fga'),('fta','expected_fta')):
            old=sum(original[pid]['minutes']*original[pid]['rates'][field] for pid in ids)
            minute_added=sum(inputs[pid]['minutes']*original[pid]['rates'][field] for pid in ids)
            rate_added=sum(inputs[pid]['minutes']*(inputs[pid]['rates'][field]-original[pid]['rates'][field]) for pid in ids)
            target=env.get(expected)
            if target is None or not env.get('pace_factor'):continue
            target=max(old,target/env['pace_factor'])
            scale=min(1.,max(0.,target-minute_added)/rate_added) if rate_added>0 else 1.
            for pid in ids:
                old_rate=original[pid]['rates'][field]
                inputs[pid]['rates'][field]=old_rate+scale*(inputs[pid]['rates'][field]-old_rate)
                if field=='fga':
                    prior=original[pid]['rates']['fg3a']
                    inputs[pid]['rates']['fg3a']=min(inputs[pid]['rates']['fga'],prior+scale*(inputs[pid]['rates']['fg3a']-prior))
            record[field]=dict(baseline=old,after_extra_minutes=minute_added,observed_volume_budget=target,
                additional_minutes_scale=minute_scale,additional_rate_scale=scale)
        volume[team]=record
    # Retain the allocator's first proposal separately, then reconcile its main
    # audit with the constrained inputs that will actually be projected.
    for team,audit in allocations.items():
        audit['initial_allocator_result']=deepcopy(audit)
        ids=[p['player_id'] for p in result['predictions'] if p['team_id']==team and p['player_id'] in inputs]
        fields=('fga','fg3a','fta','tov')
        after=sum(inputs[pid]['minutes'] for pid in ids)
        audit.update(team_minutes_after=after,allocated_minutes=max(0.,after-audit['team_minutes_before']),
            unallocated_vacancy_minutes=max(0.,audit['vacated_minutes']-(after-audit['team_minutes_before'])),
            unfilled_team_minutes=max(0.,240.-after),team_attempt_budget=volume.get(team,{}),
            projection_stage='constrained before game pace/defense/context factors')
        for pid,recipient in audit['recipients'].items():
            old=original[pid];item=inputs[pid]
            recipient.update(adjusted_minutes=item['minutes'],adjusted_rates=deepcopy(item['rates']),
                extra_minutes_attempts={field:max(0.,item['minutes']-old['minutes'])*old['rates'][field] for field in fields},
                rate_added_attempts={field:item['minutes']*(item['rates'][field]-old['rates'][field]) for field in fields})
        for name in ('extra_minutes_attempts','rate_added_attempts'):
            audit[name]={field:sum(r[name][field] for r in audit['recipients'].values()) for field in fields}
        audit['adjusted_attempts']={field:sum(inputs[pid]['minutes']*inputs[pid]['rates'][field] for pid in ids) for field in fields}
        remaining={field:audit['vacated_attempts'][field]-audit['extra_minutes_attempts'][field]-audit['rate_added_attempts'][field] for field in fields}
        audit['unallocated_attempts']={field:max(0.,remaining[field]) for field in fields}
        audit['attempt_mix_change']={field:max(0.,-remaining[field]) for field in fields}
    lookup={g['game_id']:g for g in snapshot['games']}
    for p in result['predictions']:
        pid=p['player_id']
        if pid not in inputs:continue
        game=lookup[p['game_id']];env=environment['teams'].get(p['team_id'],{})
        pace=env.get('pace_factor',1.)**_strength(parameters,'pace_strength')
        defense=env.get('defense_factor',1.)**_strength(parameters,'defense_strength')
        # The baseline's explicit home-court factor carries home/away; the fitted
        # context keeps rest and travel only, so the away feature is neutralized.
        side_factor=p['components'].get('home_court',{}).get('factor',1.)
        context=context_adjustment(enhancement.get('context_model'),snapshot.get('schedule_context',{}).get(p['team_id'],{}),
            home=True if 'home_court' in p['components'] else p['team_id']==game['home_team_id'],as_of=snapshot['as_of'])
        if 'home_court' in p['components']:context['away_feature']='replaced_by_home_court_term'
        context_factor=context['factor']**_strength(parameters,'context_strength')
        factor=defense*context_factor
        item=inputs[pid];old=original[pid];minutes=item['minutes']
        variance=p['components']['uncertainty']['minutes_variance']
        if minutes!=old['minutes']:
            variance=variance*(minutes/old['minutes'])**2 if old['minutes'] else variance
            variance+=(minutes-old['minutes'])**2/4
        if item!=old:p['flags']=sorted(set(p['flags']+['injury_rotation_adjustment']))
        rates={k:v*pace for k,v in item['rates'].items()}
        for evidence in p['components']['multistat'].values():
            if evidence['rate'] is not None:evidence['rate']*=pace
        _project_numbers(p,minutes,rates,p['components']['shooting'],by_player[pid],variance,factor,side_factor)
        interval=calibrated_interval(p['points_mean'],p['points_sd'],enhancement.get('calibration'),
            model_key=enhancement['model_key'],as_of=snapshot['as_of'],interval_family=INTERVAL_FAMILY)
        if interval['status']=='calibrated':p['points_lower80'],p['points_upper80']=interval['interval']
        p['components']['uncertainty']['calibration']=interval
        p['components']['game_environment']=dict(pace_factor=pace,defense_factor=defense,context_factor=context_factor,context=context,
            expected_possessions=env.get('expected_possessions'),reference_pace=env.get('reference_pace'),audit=env.get('audit',{}))
        p['components']['injury_allocation']=dict(recipient=allocations.get(p['team_id'],{}).get('recipients',{}).get(pid),
            team_attempt_budget=volume.get(p['team_id'],{}))
        p['components']['baseline_opponent']=p['components']['opponent']
        p['components']['opponent']=dict(method='possession_adjusted_defense',factor=defense,
            complete_games=env.get('audit',{}).get('opponent_games',0),opponent_id=p['components']['baseline_opponent']['opponent_id'])
    totals={}
    for p in result['predictions']:
        if p['status']=='projected':totals[p['team_id']]=totals.get(p['team_id'],0.)+p['expected_minutes']
    queue={q['player_id']:q for q in result['review_queue']}
    for p in result['predictions']:
        if p['status']=='projected':
            missing=max(0.,240.-totals[p['team_id']])
            p['components']['rotation'].update(team_minutes_after_adjustment=totals[p['team_id']],unallocated_minutes=missing)
            p['flags']=[f for f in p['flags'] if f!='team_minutes_unresolved']
            if missing>1e-8:p['flags'].append('team_minutes_unresolved')
            if 'injury_rotation_adjustment' in p['flags'] and p['player_id'] not in queue:
                queue[p['player_id']]=dict(player_id=p['player_id'],team_id=p['team_id'],game_id=p['game_id'],status=p['status'],
                    reason=p['reason'],tier='reasoning_review',priority='normal',action='Inspect sourced vacancy allocation and observed role caps.')
        if p['player_id'] in queue:queue[p['player_id']]['flags']=p['flags'].copy()
    result['review_queue']=list(queue.values())
    result.update(model_version=enhancement['model_version'],model_key=enhancement['model_key'],
        allocation_audit=allocations,environment_audit=environment.get('audit',{}))
    result['game_totals']=[game_totals(g,result['predictions'],lineups=snapshot.get('preflight',{}).get('lineups')) for g in snapshot['games'] if g['season_type']=='Regular Season']
    result['coverage']['calibration']='Empirical retrospective intervals when a matching artifact is ready; otherwise explicit heuristic fallback. Prospective coverage is monitored separately.'
    return result
