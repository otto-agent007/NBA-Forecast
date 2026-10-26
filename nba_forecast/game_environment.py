"""Pure cutoff-filtered possession and opponent-defense estimates.

Pace is an estimate using FGA + .44 FTA - OREB + total TOV. It is not an
exact possession count. No rest/travel coefficients are assumed here.
"""
from collections import Counter, defaultdict
import math
from statistics import mean

from .validation import parse_time


def estimated_possessions(record):
    """Average both teams and normalize complete game minutes to 48 minutes."""
    ids=[str(record['home_team_id']),str(record['away_team_id'])]
    if len(set(ids))!=2:raise ValueError('distinct opposing teams required')
    possessions=[]
    for tid in ids:
        stats=record['team_stats'][tid]
        for key in ('pts','fga','fta','oreb','tov'):
            if key not in stats or isinstance(stats[key],bool) or not isinstance(stats[key],(int,float)) or not math.isfinite(stats[key]) or stats[key]<0:
                raise ValueError('missing or invalid '+key)
        possessions.append(stats['fga']+.44*stats['fta']-stats['oreb']+stats['tov'])
    if 'team_minutes' in record:
        totals=[record['team_minutes'][t] for t in ids]
        overtime=[round((m-240)/25) for m in totals]
        if any(not math.isfinite(m) or ot<0 or abs(m-(240+25*ot))>.15 for m,ot in zip(totals,overtime)) or len(set(overtime))!=1:
            raise ValueError('incomplete or inconsistent team minutes')
        duration=48+5*overtime[0]
    elif 'game_minutes' in record:
        duration=record['game_minutes']
        if not math.isfinite(duration) or duration<48 or (duration-48)%5:
            raise ValueError('invalid game duration')
    else:raise ValueError('complete minutes required for overtime normalization')
    result=mean(possessions)*48/duration
    if result<=0:raise ValueError('nonpositive estimated possessions')
    return result


def build_environment(team_games, game, as_of, context=None, parameters=None):
    """Return ``teams`` keyed by NBA ID and a shared eligibility ``audit``.

    Live mode requires both game_time and known_at before cutoff. Explicit
    ``context={'evidence_kind':'historical_reconstruction'}`` permits later
    observed final statistics from earlier games for chronological research.
    That mode is labelled and must never be used for prospective claims.
    Pace factor is matchup pace / own historical pace, avoiding a second
    adjustment for tempo already represented in player history.
    """
    context=context or {};parameters=parameters or {}
    minimum=int(parameters.get('min_games',5));shrink=float(parameters.get('shrinkage_games',10))
    maximum=int(parameters.get('max_team_games',40));decay=float(parameters.get('decay',.95))
    if minimum<1 or maximum<minimum or shrink<0 or not math.isfinite(shrink) or not 0<decay<=1:
        raise ValueError('invalid environment parameters')
    kind=context.get('evidence_kind','live')
    if kind not in ('live','historical_reconstruction'):raise ValueError('unknown evidence kind')
    cutoff=min(parse_time(as_of),parse_time(game.get('start_time') or game['game_time']))
    ids=[str(game['home_team_id']),str(game['away_team_id'])]
    if len(set(ids))!=2:raise ValueError('distinct target teams required')
    eligible=[];rejected=[];counts=Counter(str(r.get('game_id')) for r in team_games)
    for r in team_games:
        gid=str(r.get('game_id'))
        try:
            if not r.get('game_id'):raise ValueError('missing game ID')
            if counts[gid]!=1:raise ValueError('duplicate game ID')
            if gid==str(game.get('game_id')):continue
            if parse_time(r['game_time'])>=cutoff:continue
            # Parse known_at even during reconstruction, so missing provenance
            # can never quietly become acceptable research evidence.
            known=parse_time(r['known_at'])
            if kind=='live' and known>cutoff:continue
            pace=estimated_possessions(r)
            eligible.append((r,pace))
        except (KeyError,TypeError,ValueError,OverflowError) as exc:rejected.append(dict(game_id=gid,reason=str(exc)))
    eligible.sort(key=lambda item:(parse_time(item[0]['game_time']),str(item[0]['game_id'])))
    league_pace=mean(p for _,p in eligible) if eligible else None
    # Per-100 defense uses raw possessions, keeping OT from inflating ratings.
    samples=defaultdict(list);league_points=league_possessions=0
    for r,pace in eligible:
        home,away=str(r['home_team_id']),str(r['away_team_id'])
        duration=48 if 'team_minutes' not in r else 48+5*round((r['team_minutes'][home]-240)/25)
        if 'game_minutes' in r and 'team_minutes' not in r:duration=r['game_minutes']
        raw_possessions=pace*duration/48
        for tid,opp in ((home,away),(away,home)):
            conceded=r['team_stats'][opp]['pts']
            samples[tid].append(dict(game_id=r['game_id'],pace=pace,points_allowed=conceded,
                                     points_scored=r['team_stats'][tid]['pts'],possessions=raw_possessions,
                                     fga=r['team_stats'][tid]['fga'],fta=r['team_stats'][tid]['fta']))
            league_points+=conceded;league_possessions+=raw_possessions
    league_defense=100*league_points/league_possessions if league_possessions else None
    for tid in samples:
        samples[tid]=samples[tid][-maximum:]
        for index,sample in enumerate(samples[tid]):sample['weight']=decay**(len(samples[tid])-1-index)
    def weighted(tid,key):
        values=samples[tid]
        return sum(s[key]*s['weight'] for s in values)/sum(s['weight'] for s in values)
    team_pace={t:weighted(t,'pace') for t in ids if samples[t]}
    supported=all(len(samples[t])>=minimum for t in ids)
    if supported:
        smoothed={t:(len(samples[t])*team_pace[t]+shrink*league_pace)/(len(samples[t])+shrink) for t in ids}
        expected=mean(smoothed.values())
    else:expected=league_pace
    result={}
    for tid,opp in ((ids[0],ids[1]),(ids[1],ids[0])):
        own=samples[tid];opponent=samples[opp]
        defense_supported=len(opponent)>=minimum and league_defense is not None and league_defense>0
        opponent_defense=100*weighted(opp,'points_allowed')/weighted(opp,'possessions') if opponent else None
        own_offense=100*weighted(tid,'points_scored')/weighted(tid,'possessions') if own else None
        defense=(len(opponent)*opponent_defense+shrink*league_defense)/(len(opponent)+shrink)/league_defense if defense_supported else 1.0
        fallback=[]
        if not supported:fallback.append('insufficient team games for pace')
        if not defense_supported:fallback.append('insufficient opponent games for defense')
        result[tid]=dict(pace_factor=expected/team_pace[tid] if supported else 1.0,defense_factor=defense,
                         expected_possessions=expected,reference_pace=team_pace.get(tid),
                         expected_fga=weighted(tid,'fga')/weighted(tid,'possessions')*expected if own and expected is not None else None,
                         expected_fta=weighted(tid,'fta')/weighted(tid,'possessions')*expected if own and expected is not None else None,
                         audit=dict(team_games=len(own),opponent_games=len(opponent),fallback=fallback,
                                    game_ids=[s['game_id'] for s in own],opponent_game_ids=[s['game_id'] for s in opponent],
                                    observation_weights=[s['weight'] for s in own],
                                    own_offense_per100=own_offense,
                                    opponent_defense_per100=opponent_defense,league_defense_per100=league_defense))
    return dict(teams=result,audit=dict(as_of=as_of,cutoff=cutoff.isoformat(),evidence_kind=kind,
                 eligible_games=len(eligible),rejected_games=rejected,league_pace=league_pace,
                 possession_method='mean(FGA + .44*FTA - OREB + total TOV), normalized to 48 minutes',
                 min_games=minimum,shrinkage_games=shrink,max_team_games=maximum,decay=decay))
