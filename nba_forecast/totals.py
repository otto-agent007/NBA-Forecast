"""Regulation point sums from one saved game's supported player projections."""
from math import fsum, isfinite

# Minutes the projected rotation leaves unallocated are still played by someone.
# Schema 4 scores them at the 2025-26 rate of players averaging under 20
# minutes a game (0.377 points per minute, minutes-weighted): the players who
# absorb them. Player projections are never raised for it; the residual is a
# separate, disclosed term of the team total.
REPLACEMENT_POINTS_PER_MINUTE = .38
# Below this many projected players the roster, not the rotation, is in doubt,
# so missing minutes stay a disclosed deficit instead of replacement production.
RESIDUAL_MIN_PROJECTED = 8
# Withheld players whose only reasons are missing evidence are covered by the
# residual: whatever minutes they get are among the unallocated ones.
RESIDUAL_COVERED_FLAGS = {'cold_start', 'no_scoring_opportunity_evidence', 'team_minutes_unresolved'}
# Schema 5: a reported tag (probable through doubtful) only lowers the chance that
# such a bench player takes any of those minutes, so it no longer blocks the total.
TAGGED_COVERED_FLAGS = RESIDUAL_COVERED_FLAGS | {'availability_questionable'}


def game_totals(game, predictions, *, schema_version=5, lineups=None):
    """Team and game point sums. Schema 3 weights each projection by its participation
    probability; schema 4 also scores unallocated minutes at the replacement rate;
    schema 5 also covers tagged bench players without evidence."""
    if type(schema_version) is not int or schema_version not in (1,2,3,4,5):
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
        def bench(p):
            return (p['player_id'] not in starters and p.get('projected_starter') is not True
                    and p.get('components',{}).get('availability',{}).get('status')
                    in (('available','questionable') if schema_version>=5 else ('available',)))
        if schema_version>=4:
            covered=TAGGED_COVERED_FLAGS if schema_version>=5 else RESIDUAL_COVERED_FLAGS
            cold=sorted(p['player_id'] for p in rows if p['player_id'] in withheld and bench(p)
                and set(p.get('flags',[]))-{'availability_questionable'} and set(p['flags'])<=covered)
        else:
            cold=sorted(p['player_id'] for p in rows if p['player_id'] in withheld and bench(p)
                and set(p.get('flags',[]))=={'cold_start'}
                and p.get('components',{}).get('history',{}).get('played_games')==0)
        unresolved=sorted(set(withheld)-set(cold))
        missing=max(0.,240.-minutes)
        blocking=withheld if schema_version==1 else unresolved
        filled=schema_version>=4 and len(projected)>=RESIDUAL_MIN_PROJECTED
        residual=missing if filled else 0.
        status=('unavailable' if not projected else 'partial' if blocking or (missing>1e-7 and not filled) or len(projected)<5 else 'complete')
        teams.append(dict(team_id=tid,side=side,status=status,
            points_mean=fsum(weight(p)*p['points_mean'] for p in projected)+residual*REPLACEMENT_POINTS_PER_MINUTE if projected else None,
            projected_players=len(projected),out_players=sum(p['status']=='out' for p in rows),
            withheld_player_ids=withheld,projected_minutes=minutes,
            unallocated_minutes=missing,regulation_minutes=240.))
        if schema_version>=2:
            teams[-1].update(cold_start_player_ids=cold,unresolved_player_ids=unresolved)
        if schema_version>=3:
            teams[-1].update(expected_players=fsum(weight(p) for p in projected),
                             questionable_players=sum('availability_questionable' in p.get('flags',[]) for p in projected))
        if schema_version>=4:
            teams[-1].update(points_mean_projected=fsum(weight(p)*p['points_mean'] for p in projected) if projected else None,
                             residual_minutes=residual,residual_points=residual*REPLACEMENT_POINTS_PER_MINUTE,
                             replacement_points_per_minute=REPLACEMENT_POINTS_PER_MINUTE)
    available=all(t['points_mean'] is not None for t in teams)
    return dict(schema_version=schema_version,method='regulation_player_sum',game_id=game['game_id'],teams=teams,
        points_mean=fsum(t['points_mean'] for t in teams) if available else None,
        status='unavailable' if not available else 'partial' if any(t['status']!='complete' for t in teams) else 'complete',
        basis=('Sum of supported player means weighted by participation probability, plus unallocated regulation minutes at the replacement scoring rate. Withheld players without evidence are covered by that residual.'
               if schema_version>=4 else
               'Sum of supported player means weighted by participation probability; the projected regulation rotation. Withheld players are excluded.'
               if schema_version>=3 else 'Sum of supported player means before rounding; conditional on participation and the projected regulation rotation. Withheld players are excluded.'))
