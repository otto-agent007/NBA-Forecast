"""Full-box statistical extension, independently versioned from point calibration.

The point mean/interval model is unchanged. Expected makes are a bounded
allocation of that mean across the already-projected shot opportunities.
Other counts use supported exposure, never missing-as-zero observations.
"""
from collections import defaultdict
from math import isfinite
from .historical import season_prior
from .validation import parse_time

VERSION = 'full-boxscore-1.0.0'
COUNTS = ('fgm','fg3a','ftm','oreb','dreb','pf','plus_minus')
PERCENTAGES = {'fg_pct':('fgm','fga'),'fg3_pct':('fg3m','fg3a'),'ft_pct':('ftm','fta')}
DEFAULTS = {'reb':.15,'ast':.09,'stl':.025,'blk':.02,'pf':.08,'plus_minus':0.}


def expected_makes(attempts, probabilities, points):
    """Proportional probability adjustment with saturation and exact scoring."""
    values=(2,3,1)
    maximum=sum(a*v for a,v in zip(attempts,values))
    if points < 0 or points > maximum+1e-8:
        raise ValueError('Scoring expectation exceeds available shot opportunities')
    if points == 0:return [0.,0.,0.]
    def made(scale):return [a*min(1.,p*scale) for a,p in zip(attempts,probabilities)]
    high=1.
    while sum(m*v for m,v in zip(made(high),values)) < points-1e-12:
        high*=2
        if high>1e12:raise ValueError('Scoring expectation has no supported shooting allocation')
    low=0.
    for _ in range(80):
        middle=(low+high)/2
        if sum(m*v for m,v in zip(made(middle),values)) < points:low=middle
        else:high=middle
    return made((low+high)/2)


def _weighted(rows, stat, season, historical):
    present=[r for r in rows if r['season']==season and r.get(stat) is not None and r['minutes']>0]
    prior=[r for r in rows if r['season']!=season and r.get(stat) is not None and r['minutes']>0]
    aggregate=(historical or {}).get('boxscore_totals',{}).get(stat)
    prior_minutes=aggregate['minutes'] if aggregate else sum(r['minutes'] for r in prior)
    prior_count=aggregate['count'] if aggregate else sum(r[stat] for r in prior)
    scale=min(1.,600/prior_minutes) if prior_minutes else 1.
    weights=[.97**age for age in reversed(range(len(present)))]
    exposure=prior_minutes*scale+sum(r['minutes']*w for r,w in zip(present,weights))
    count=prior_count*scale+sum(r[stat]*w for r,w in zip(present,weights))
    return dict(weighted_minutes=exposure,weighted_count=count,prior_minutes_cap=600,current_decay_per_game=.97,
        prior_source='verified_season_totals' if aggregate else 'game_history',
        prior_seasons=aggregate.get('seasons',[]) if aggregate else sorted({r['season'] for r in prior}),
        game_ids=[r['game_id'] for r in (present if aggregate else prior+present)],
        observed_games=(aggregate['games'] if aggregate else len(prior))+len(present))


def extend_boxscore(result,snapshot,history):
    cutoff=parse_time(snapshot['as_of']);season=snapshot['season'];year=int(season[:4])
    previous=f'{year-1}-{str(year)[-2:]}'
    excluded=set(snapshot.get('model_enhancements',{}).get('excluded_history_game_ids',[]))
    by_player=defaultdict(list)
    for r in history:
        if (r['season_type']=='Regular Season' and r['season'] in (season,previous)
                and r['game_id'] not in excluded and parse_time(r['game_time'])<cutoff
                and parse_time(r['known_at'])<=cutoff):by_player[r['player_id']].append(r)
    for rows in by_player.values():rows.sort(key=lambda r:(parse_time(r['game_time']),r['game_id']))
    for p in result['predictions']:
        for key in COUNTS:p['expected_'+key]=0. if p['status']=='out' else None
        for key in PERCENTAGES:p['expected_'+key]=None
        p['boxscore_model_version']=VERSION
        if p['status']!='projected':continue
        rows=by_player[p['player_id']];minutes=p['expected_minutes']
        shooting=p['components']['shooting'];scoring=p['components']['scoring']
        three=scoring['expected_three_point_attempts'];two=p['expected_fga']-three;free=p['expected_fta']
        made=expected_makes([two,three,free],[shooting[k]['percentage'] for k in ('two_point','three_point','free_throw')],p['points_mean'])
        p.update(expected_fg3a=three,expected_fgm=made[0]+made[1],expected_fg3m=made[1],expected_ftm=made[2])
        for key,(m,a) in PERCENTAGES.items():p['expected_'+key]=p['expected_'+m]/p['expected_'+a] if p['expected_'+a] else None
        audit=dict(version=VERSION,shooting_method='Bounded proportional makes consistent with the unchanged point-model mean.',
            point_model_changed=False,percentages='Ratios of expected makes to attempts; undefined when attempts are zero.',
            other_stats={},plus_minus_policy='Shrunk historical on-court score differential per minute; not an individual impact rating or calibrated interval.')
        historical=season_prior(snapshot,p['player_id'])
        pace=p['components'].get('game_environment',{}).get('pace_factor',1.)
        for stat,default in DEFAULTS.items():
            evidence=_weighted(rows,stat,season,historical)
            pseudo=300. if stat=='plus_minus' else 60.
            exposure=evidence['weighted_minutes']
            rate=(evidence['weighted_count']+pseudo*default)/(exposure+pseudo) if exposure else None
            # Fouls and on-court differential are time-based. Other counts
            # retain the point model's possession environment adjustment.
            factor=1. if stat in ('pf','plus_minus') else pace
            value=minutes*rate*factor if rate is not None else None
            if stat=='pf' and value is not None:value=min(6.,value)
            p['expected_'+stat]=value
            evidence.update(rate=rate,pseudo_minutes=pseudo,default_rate=default,pace_factor=factor,uncertainty='Uncalibrated count estimate')
            audit['other_stats'][stat]=evidence
        # Derive the split from paired evidence so both parts sum to the
        # supported total. No split is asserted from a league default alone.
        paired=[r for r in rows if all(r.get(k) is not None for k in ('oreb','dreb','reb'))
                and r['oreb']+r['dreb']==r['reb']]
        split_prior={'boxscore_totals':(historical or {}).get('boxscore_rebound_split',{})}
        ore=_weighted(paired,'oreb',season,split_prior);dre=_weighted(paired,'dreb',season,split_prior)
        mass=ore['weighted_count']+dre['weighted_count']
        supported=ore['weighted_minutes']>0 and dre['weighted_minutes']>0
        share=(ore['weighted_count']+4.)/(mass+20.) if supported else None
        if share is not None and p['expected_reb'] is not None:
            p['expected_oreb']=p['expected_reb']*share;p['expected_dreb']=p['expected_reb']-p['expected_oreb']
        audit['rebound_split']=dict(offensive_share=share,observed_weighted_rebounds=mass,pseudo_rebounds=20,
            default_offensive_share=.2,offensive=ore,defensive=dre)
        p['components']['boxscore']=audit
    result['boxscore_model_version']=VERSION
    result['coverage']['boxscore']='Full-box estimates use per-stat observed exposure; missing evidence remains null. Extra-stat intervals are not calibrated.'
    return result


def validate_boxscore(row):
    """Validate a present extension without imposing fields on frozen runs."""
    if not row.get('boxscore_model_version'):
        if any('expected_'+k in row for k in (*COUNTS,*PERCENTAGES)):
            raise ValueError('Full-box estimates require their model version')
        return
    if row['boxscore_model_version']!=VERSION:raise ValueError('Unsupported full-box model version')
    numeric=COUNTS+tuple(PERCENTAGES)
    for stat in numeric:
        value=row['expected_'+stat]
        if value is None:continue
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value):raise ValueError('Invalid full-box estimate')
        if stat!='plus_minus' and value<0:raise ValueError('Negative full-box count or ratio')
        if stat in PERCENTAGES and value>1:raise ValueError('Shooting ratio must be between zero and one')
    if row.get('expected_pf') is not None and row['expected_pf']>6:raise ValueError('Expected personal fouls exceed six')
    if row['status'] in ('needs_review','no_game'):
        if any(row['expected_'+k] is not None for k in numeric):raise ValueError('Unsupported full-box estimate must be null')
        return
    for pct,(m,a) in PERCENTAGES.items():
        made,attempts=row['expected_'+m],row['expected_'+a]
        if made is None or attempts is None or not 0<=made<=attempts+1e-8:raise ValueError('Projected makes exceed attempts')
        expected=made/attempts if attempts else None;value=row['expected_'+pct]
        if (expected is None)!=(value is None) or (expected is not None and abs(expected-value)>1e-8):raise ValueError('Projected percentage differs from shooting line')
    if row['expected_fg3m']>row['expected_fgm']+1e-8 or row['expected_fg3a']>row['expected_fga']+1e-8:raise ValueError('Projected threes exceed field goals')
    if row['expected_fgm']-row['expected_fg3m']>row['expected_fga']-row['expected_fg3a']+1e-8:raise ValueError('Projected two-point makes exceed attempts')
    if abs(row['points_mean']-(2*row['expected_fgm']+row['expected_fg3m']+row['expected_ftm']))>1e-7:raise ValueError('Projected shooting does not reconcile to points')
    if row['expected_oreb'] is not None and row['expected_dreb'] is not None:
        if row.get('expected_reb') is None or abs(row['expected_reb']-row['expected_oreb']-row['expected_dreb'])>1e-8:raise ValueError('Projected rebound split disagrees')
    if row['status']=='out' and any(row['expected_'+k]!=0 for k in COUNTS):raise ValueError('Out full-box counts must be zero')
