"""Regulation point sums from one saved game's supported player projections."""
from math import fsum, isfinite


def game_totals(game, predictions, *, schema_version=3, lineups=None):
    """Team and game point sums. Schema 3 weights each projection by its participation probability."""
    if type(schema_version) is not int or schema_version not in (1,2,3):
        raise ValueError('Unsupported game totals schema version')
    def weight(p):
        if schema_version<3:return 1.
        value=p.get('participation_probability')
        if value is None:return 1.
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value) or not 0<=value<=1:
            raise ValueError('Game totals require participation probabilities in [0, 1]')
        return value
    starters={pid for team in (lineups or {}).values() for pid in team.get('player_ids',[])}
    teams=[]
    for side in ('away','home'):
        tid=game[side+'_team_id']
        rows=[p for p in predictions if p.get('game_id')==game['game_id'] and p['team_id']==tid]
        if len({p['player_id'] for p in rows})!=len(rows):
            raise ValueError('Duplicate player in game totals')
        projected=[p for p in rows if p['status']=='projected']
        for p in projected:
            for key in ('points_mean','expected_minutes'):
                value=p[key]
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value) or value<0:
                    raise ValueError('Game totals require finite nonnegative player projections')
        minutes=fsum(weight(p)*p['expected_minutes'] for p in projected)
        if minutes>240+1e-7:raise ValueError('Game totals exceed the regulation minutes budget')
        withheld=sorted(p['player_id'] for p in rows if p['status'] not in ('projected','out'))
        cold=sorted(p['player_id'] for p in rows if p['player_id'] in withheld
            and p['player_id'] not in starters and p.get('projected_starter') is not True
            and set(p.get('flags',[]))=={'cold_start'}
            and p.get('components',{}).get('history',{}).get('played_games')==0
            and p.get('components',{}).get('availability',{}).get('status')=='available')
        unresolved=sorted(set(withheld)-set(cold))
        missing=max(0.,240.-minutes)
        blocking=withheld if schema_version==1 else unresolved
        status=('unavailable' if not projected else 'partial' if blocking or missing>1e-7 or len(projected)<5 else 'complete')
        teams.append(dict(team_id=tid,side=side,status=status,
            points_mean=fsum(weight(p)*p['points_mean'] for p in projected) if projected else None,
            projected_players=len(projected),out_players=sum(p['status']=='out' for p in rows),
            withheld_player_ids=withheld,projected_minutes=minutes,
            unallocated_minutes=missing,regulation_minutes=240.))
        if schema_version>=2:
            teams[-1].update(cold_start_player_ids=cold,unresolved_player_ids=unresolved)
        if schema_version>=3:
            teams[-1].update(expected_players=fsum(weight(p) for p in projected),
                             questionable_players=sum('availability_questionable' in p.get('flags',[]) for p in projected))
    available=all(t['points_mean'] is not None for t in teams)
    return dict(schema_version=schema_version,method='regulation_player_sum',game_id=game['game_id'],teams=teams,
        points_mean=fsum(t['points_mean'] for t in teams) if available else None,
        status='unavailable' if not available else 'partial' if any(t['status']!='complete' for t in teams) else 'complete',
        basis=('Sum of supported player means weighted by participation probability; the projected regulation rotation. Withheld players are excluded.'
               if schema_version>=3 else 'Sum of supported player means before rounding; conditional on participation and the projected regulation rotation. Withheld players are excluded.'))
