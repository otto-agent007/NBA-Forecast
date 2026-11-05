"""Descriptive recent role evidence, never a confirmed upcoming lineup."""
import re
from statistics import fmean
from collections import defaultdict
from .validation import parse_time

# A starter keeps his place while he is away. A team game he missed counts as an
# absence when the official injury report listed him out of it, or its box score
# lists him as not dressed (Inactive, DND, NWT), never for a coach's decision.
_NOT_DRESSED=re.compile(r'(Inactive|DND|NWT)\b',re.I)


def team_games(rows):
    """Each team's games in ``rows``, as sorted (tipoff, game ID) pairs."""
    games=defaultdict(set)
    for r in rows:
        if r.get('team_id') and r.get('game_time'):
            games[r['team_id']].add((parse_time(r['game_time']),r['game_id']))
    return {team:sorted(values) for team,values in games.items()}


def _absent(row):
    reason=row.get('participation_reason') or ''
    return row['status']=='dnp' and bool(_NOT_DRESSED.match(reason)) and not re.search(r'coach',reason,re.I)


def rotation_role(player,rows,*,excluded_game_ids=None,team_games=None,reported_out=None):
    recent=sorted((r for r in rows if player.get('team_id') and r.get('team_id')==player['team_id'] and r['status']=='played'),
                  key=lambda r:(parse_time(r['game_time']),r['game_id']))[-5:]
    observed=[r for r in recent if type(r.get('starter')) is bool]
    starts=sum(r['starter'] for r in observed)
    normal=[r for r in recent if r['game_id'] not in set(excluded_game_ids or ())]
    result=dict(label=('starter' if starts>len(observed)/2 else 'bench') if observed else 'unknown',
                observations=len(observed),starts=starts,game_ids=[r['game_id'] for r in observed],
                recent_minutes=fmean(r['minutes'] for r in normal) if normal else None,
                last_game_time=recent[-1]['game_time'] if recent else None,
                last_game_started=recent[-1].get('starter') if recent else None,
                confirmed_lineup=False,basis='Last five captured appearances on the current team; upcoming lineup unconfirmed.')
    if excluded_game_ids is not None:
        result['injury_shortened_game_ids']=sorted({r['game_id'] for r in recent if r['game_id'] in excluded_game_ids})
    if team_games is not None:
        # The team's games since his last appearance, and whether he was away from every one.
        last=parse_time(recent[-1]['game_time']) if recent else None
        missed=[gid for when,gid in team_games if last is not None and when>last]
        own={r['game_id']:r for r in rows if r.get('team_id')==player.get('team_id')}
        reported=set(reported_out or ())
        result['missed_game_ids']=missed
        result['absent_since_last_game']=bool(missed) and all(g in reported or (g in own and _absent(own[g])) for g in missed)
    return result


def projected_lineups(players,profiles):
    """Choose a projected five as a team, without changing numeric forecasts."""
    by_id={p['player_id']:p for p in profiles}
    teams=defaultdict(list)
    for p in players:
        teams[p['team_id']].append(p)
    result={}
    for team,members in teams.items():
        eligible=[]
        for player in members:
            profile=by_id.get(player['player_id'],{})
            role=profile.get('rotation_role') or {}
            minutes=profile.get('expected_minutes_baseline')
            if minutes is None:
                minutes=role.get('recent_minutes')
            if player.get('status')=='out' or not (role.get('observations') or (minutes or 0)>0):
                continue
            eligible.append((player,role,minutes or 0))
        latest=max((parse_time(r['last_game_time']) for _,r,_ in eligible if r.get('last_game_time')),default=None)
        def rank(item):
            player,role,minutes=item
            # Started the team's latest game, or started his own last appearance and was
            # away from every team game since: a returning starter outranks his fill-in.
            latest_starter=bool(latest and role.get('last_game_time') and role.get('last_game_started') is True
                                and (parse_time(role['last_game_time'])==latest or role.get('absent_since_last_game') is True))
            return (-int(latest_starter),-int(role.get('label')=='starter'),-minutes,player.get('name',''),player['player_id'])
        selected=[p['player_id'] for p,_,_ in sorted(eligible,key=rank)[:5]]
        result[team]=dict(player_ids=selected,unfilled_slots=5-len(selected),confirmed=False,
                          basis='Projected five from the last captured starters (a starter away since his last start keeps his place) and supported role minutes; lineup unconfirmed.')
    return result
