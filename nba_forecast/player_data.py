"""Observed player pages; every denominator is explicit and cutoff bounded."""
import math
from .data import _timestamp
from .rotation import rotation_role

FIELDS = ('minutes','pts','reb','ast','stl','blk','tov','fgm','fga','fg3m','fg3a','ftm','fta','oreb','dreb','pf','plus_minus')


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _summary(rows, season):
    rows = [r for r in rows if r['season'] == season]
    played = [r for r in rows if r['status'] == 'played']
    coverage = {f: {'available_games': sum(_number(r.get(f)) for r in played), 'total_games': len(played)} for f in FIELDS}
    averages = {f: (sum(r[f] for r in played if _number(r.get(f))) / coverage[f]['available_games'] if coverage[f]['available_games'] else None) for f in FIELDS}
    percentages = {}
    for label, made, attempted in [('fg_pct','fgm','fga'),('fg3_pct','fg3m','fg3a'),('ft_pct','ftm','fta')]:
        pairs = [r for r in played if _number(r.get(made)) and _number(r.get(attempted))]
        attempts = sum(r[attempted] for r in pairs)
        percentages[label] = 100 * sum(r[made] for r in pairs) / attempts if attempts else None
        coverage[label] = {'available_games': len(pairs), 'total_games': len(played)}
    precision='seconds' if played and all(r.get('minutes_precision')=='seconds' for r in played) else 'mixed_or_rounded'
    return {'season': season, 'gp': len(played), 'dnp': sum(r['status'] == 'dnp' for r in rows), 'averages': averages, 'coverage': coverage, 'percentages': percentages,'minutes_precision':precision}


def build_player_pages(roster, history, season, as_of, shots=None):
    cutoff = _timestamp(as_of)
    year = int(season[:4])
    prior = f'{year-1}-{str(year)[-2:]}'
    grouped, seen = {}, set()
    for row in history:
        if _timestamp(row['known_at']) > cutoff or _timestamp(row['game_time']) >= cutoff:
            continue
        if row.get('season_type') != 'Regular Season' or row['season'] not in (season, prior):
            continue
        key = (str(row['player_id']), row['game_id'])
        if key in seen:
            raise ValueError('Duplicate player/game history evidence')
        seen.add(key)
        grouped.setdefault(key[0], []).append(row)
    shot_games = list(shots.values()) if isinstance(shots, dict) else list(shots or [])
    players = {}
    people={str(p['player_id']):p for p in (roster.get('players', []) if isinstance(roster, dict) else roster)}
    for pid,rows in grouped.items():
        current=[r for r in rows if r['season']==season and r['status']=='played']
        if pid not in people and current:
            latest=max(current,key=lambda r:(_timestamp(r['game_time']),r['game_id']))
            people[pid]={'player_id':pid,'name':latest.get('name',pid),'team_id':latest.get('team_id'),
                'roster_membership':'historical_appearance','identity_basis':'Verified final box score; not on the current roster.'}
    for person in people.values():
        pid = str(person['player_id'])
        rows = grouped.get(pid, [])
        chart_rows, covered_games, missing_reasons, zero_games = [], set(), [], 0
        played_games={r['game_id']:r for r in rows if r['season']==season and r['status']=='played'}
        for game in shot_games:
            if game.get('game_id') not in played_games:
                continue
            if not game.get('known_at') or _timestamp(game['known_at']) > cutoff:
                continue
            if game.get('season') != season or (game.get('game_time') and _timestamp(game['game_time']) >= cutoff):
                continue
            if game.get('missing_reason'):
                missing_reasons.append(game['missing_reason'])
                continue
            relevant = [{**s, 'provenance': game.get('provenance', [])} for s in game.get('shots', []) if str(s['player_id']) == pid]
            observed=played_games[game['game_id']]
            coverage=next((p for p in game.get('coverage',{}).get('players',[]) if str(p['player_id'])==pid),None)
            reconciled=game.get('coverage',{}).get('reconciled') is True
            # A zero-attempt appearance is covered only by an explicit player
            # reconciliation; an empty feed is never treated as a clean game.
            if not reconciled or (not relevant and not (coverage and coverage.get('reconciled'))):
                continue
            checks={'fga':len(relevant),'fgm':sum(s['made'] for s in relevant),
                'fg3a':sum(s['points']==3 for s in relevant),'fg3m':sum(s['made'] and s['points']==3 for s in relevant)}
            if any(observed.get(k)!=v for k,v in checks.items()):
                missing_reasons.append('Shot totals differ from the latest final box score')
                continue
            covered_games.add(game['game_id'])
            chart_rows.extend(relevant)
            if not relevant:
                zero_games+=1
        located = sum(s.get('x') is not None and s.get('y') is not None for s in chart_rows)
        missing=set(played_games)-covered_games
        if missing:
            missing_reasons.append(f'{len(missing)} played game(s) awaiting verified shot data')
        chart = {'shots': chart_rows, 'coverage': {'attempts': len(chart_rows), 'located_attempts': located, 'games': len(covered_games),
            'played_games':len(played_games),'missing_games':len(missing),'missing_game_ids':sorted(missing),
            'zero_attempt_games':zero_games,'complete':not missing and bool(played_games)},
            'missing_reason': '; '.join(sorted(set(missing_reasons))) if missing_reasons else ('No captured appearances this season' if not played_games else None),
            'coordinate_system': 'NBA legacy coordinates; 10 units per foot, hoop origin', 'observed_only': True}
        recent = sorted((r for r in rows if r['season'] == season), key=lambda r: (r['game_time'], r['game_id']), reverse=True)[:10]
        players[pid] = {**person, 'player_id': pid, 'rotation_role': rotation_role(person,rows), 'current_season': _summary(rows, season), 'prior_season': _summary(rows, prior), 'recent_games': [{**r, **{f: r.get(f) if r['status'] == 'played' else None for f in FIELDS}} for r in recent], 'shot_chart': chart}
    return {'season': season, 'as_of': as_of, 'players': players}
