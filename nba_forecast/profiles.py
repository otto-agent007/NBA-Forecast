"""Full-roster role evidence. Vacated opportunities are review work, not allocation."""
from collections import defaultdict
from statistics import fmean
from .model import _previous_season, _opportunities
from .validation import parse_time, validate_history


def eligible_history(snapshot,history):
    cutoff=parse_time(snapshot['as_of']);seasons={snapshot['season'],_previous_season(snapshot['season'])}
    return sorted((r for r in history if r['season_type']=='Regular Season' and r['season'] in seasons
                   and parse_time(r['known_at'])<=cutoff and parse_time(r['game_time'])<cutoff),
                  key=lambda r:(parse_time(r['game_time']),r['game_id'],r['player_id']))


def build_profiles(snapshot,history):
    validate_history(history)
    eligible=eligible_history(snapshot,history);by_player=defaultdict(list)
    for row in eligible:by_player[row['player_id']].append(row)
    profiles=[];teams=defaultdict(list)
    for p in snapshot['roster']['players']:
        rows=by_player[p['player_id']];current=[r for r in rows if r['season']==snapshot['season']]
        previous=[r for r in rows if r['season']!=snapshot['season']];recent=current[-5:]
        supported=any(r['minutes']>0 for r in rows)
        minutes,rates,variance,components=_opportunities(previous,current)
        team_current=[r for r in recent if r['team_id']==p['team_id']]
        flags=[]
        if not supported:flags.append('cold_start')
        if not current:flags.append('current_role_unobserved')
        if rows and rows[-1]['team_id']!=p['team_id']:flags.append('team_change')
        if recent and previous and abs(fmean(r['minutes'] for r in recent)-fmean(r['minutes'] for r in previous))>=5:
            flags.append('minutes_change')
        starter=[r['starter'] for r in team_current if 'starter' in r]
        profile=dict(player_id=p['player_id'],name=p['name'],team_id=p['team_id'],availability=p['status'],
                     previous_games=len(previous),current_games=len(current),recent_game_ids=[r['game_id'] for r in recent],
                     expected_minutes_baseline=minutes if supported else None,
                     fga_per_minute=rates['fga'] if supported else None,fta_per_minute=rates['fta'] if supported else None,
                     recent_minutes=fmean(r['minutes'] for r in recent) if recent else None,
                     recent_fga=fmean(r['fga'] for r in recent) if recent else None,
                     starter_role=('starter' if sum(starter)>len(starter)/2 else 'bench') if starter else 'unknown',
                     flags=flags,review_tier='reasoning_review' if flags and supported else 'data_verification' if not supported else 'routine')
        profiles.append(profile);teams[p['team_id']].append(profile)
    summaries=[]
    for team,players in sorted(teams.items()):
        out=[p for p in players if p['availability']=='out']
        summaries.append(dict(team_id=team,out_player_ids=[p['player_id'] for p in out],
                             vacated_baseline_minutes=sum(p['expected_minutes_baseline'] or 0 for p in out),
                             vacated_baseline_fga=sum((p['expected_minutes_baseline'] or 0)*(p['fga_per_minute'] or 0) for p in out),
                             opportunity_ranking=[p['player_id'] for p in sorted(players,key=lambda p:-(p['fga_per_minute'] or 0)) if p['fga_per_minute'] is not None],
                             action='Review coaching/rotation and game-specific beneficiary evidence before reallocating.' if out else 'Monitor role changes.'))
    return dict(as_of=snapshot['as_of'],season=snapshot['season'],players=profiles,teams=summaries,
                eligible_history_rows=len(eligible),policy='Baseline minutes are conditional role estimates, not availability-confirmed forecasts.')
