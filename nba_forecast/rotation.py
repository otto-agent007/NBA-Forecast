"""Descriptive recent role evidence, never a confirmed upcoming lineup."""
from statistics import fmean
from collections import defaultdict
from .validation import parse_time


def rotation_role(player,rows,*,excluded_game_ids=None):
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
    return result


def projected_lineups(players,profiles):
    """Choose a projected five as a team, without changing numeric forecasts."""
    by_id={p['player_id']:p for p in profiles};teams=defaultdict(list)
    for p in players:teams[p['team_id']].append(p)
    result={}
    for team,members in teams.items():
        eligible=[]
        for player in members:
            profile=by_id.get(player['player_id'],{});role=profile.get('rotation_role') or {}
            minutes=profile.get('expected_minutes_baseline')
            if minutes is None:minutes=role.get('recent_minutes')
            if player.get('status')=='out' or not (role.get('observations') or (minutes or 0)>0):continue
            eligible.append((player,role,minutes or 0))
        latest=max((parse_time(r['last_game_time']) for _,r,_ in eligible if r.get('last_game_time')),default=None)
        def rank(item):
            player,role,minutes=item
            latest_starter=bool(latest and role.get('last_game_time') and parse_time(role['last_game_time'])==latest and role.get('last_game_started') is True)
            return (-int(latest_starter),-int(role.get('label')=='starter'),-minutes,player.get('name',''),player['player_id'])
        selected=[p['player_id'] for p,_,_ in sorted(eligible,key=rank)[:5]]
        result[team]=dict(player_ids=selected,unfilled_slots=5-len(selected),confirmed=False,
                          basis='Projected five from the last captured starters and supported role minutes; lineup unconfirmed.')
    return result
