"""Regulation point sums from one saved game's supported player projections."""
from math import fsum, isfinite


def game_totals(game, predictions):
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
        minutes=fsum(p['expected_minutes'] for p in projected)
        if minutes>240+1e-7:raise ValueError('Game totals exceed the regulation minutes budget')
        withheld=sorted(p['player_id'] for p in rows if p['status'] not in ('projected','out'))
        missing=max(0.,240.-minutes)
        status=('unavailable' if not projected else 'partial' if withheld or missing>1e-7 or len(projected)<5 else 'complete')
        teams.append(dict(team_id=tid,side=side,status=status,
            points_mean=fsum(p['points_mean'] for p in projected) if projected else None,
            projected_players=len(projected),out_players=sum(p['status']=='out' for p in rows),
            withheld_player_ids=withheld,projected_minutes=minutes,
            unallocated_minutes=missing,regulation_minutes=240.))
    available=all(t['points_mean'] is not None for t in teams)
    return dict(schema_version=1,method='regulation_player_sum',game_id=game['game_id'],teams=teams,
        points_mean=fsum(t['points_mean'] for t in teams) if available else None,
        status='unavailable' if not available else 'partial' if any(t['status']!='complete' for t in teams) else 'complete',
        basis='Sum of supported player means before rounding; conditional on participation and the projected regulation rotation. Withheld players are excluded.')
