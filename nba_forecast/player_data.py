"""Observed player pages; every denominator is explicit and cutoff bounded."""
import math
from .data import _timestamp
from .rotation import rotation_role

FIELDS = ('minutes','pts','reb','ast','stl','blk','tov','fgm','fga','fg3m','fg3a','ftm','fta')


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
    return {'season': season, 'gp': len(played), 'dnp': sum(r['status'] == 'dnp' for r in rows), 'averages': averages, 'coverage': coverage, 'percentages': percentages}


def build_player_pages(roster, history, season, as_of, shots=None):
    cutoff = _timestamp(as_of)
    year = int(season[:4]); prior = f'{year-1}-{str(year)[-2:]}'
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
    for person in roster.get('players', []) if isinstance(roster, dict) else roster:
        pid = str(person['player_id']); rows = grouped.get(pid, [])
        chart_rows, eligible_games, missing_reasons = [], 0, []
        played_games={r['game_id'] for r in rows if r['season']==season and r['status']=='played'}
        for game in shot_games:
            if game.get('game_id') not in played_games:continue
            if not game.get('known_at') or _timestamp(game['known_at']) > cutoff:
                continue
            if game.get('season') != season or (game.get('game_time') and _timestamp(game['game_time']) >= cutoff):
                continue
            if game.get('missing_reason'):
                missing_reasons.append(game['missing_reason']); continue
            relevant = [{**s, 'provenance': game.get('provenance', [])} for s in game.get('shots', []) if str(s['player_id']) == pid]
            if relevant:
                eligible_games += 1; chart_rows.extend(relevant)
        located = sum(s.get('x') is not None and s.get('y') is not None for s in chart_rows)
        chart = {'shots': chart_rows, 'coverage': {'attempts': len(chart_rows), 'located_attempts': located, 'games': eligible_games}, 'missing_reason': '; '.join(sorted(set(missing_reasons))) if missing_reasons else ('No verified shot data available' if not chart_rows else None), 'coordinate_system': 'NBA legacy coordinates; 10 units per foot, hoop origin', 'observed_only': True}
        recent = sorted((r for r in rows if r['season'] == season), key=lambda r: (r['game_time'], r['game_id']), reverse=True)[:10]
        players[pid] = {**person, 'player_id': pid, 'rotation_role': rotation_role(person,rows), 'current_season': _summary(rows, season), 'prior_season': _summary(rows, prior), 'recent_games': [{**r, **{f: r.get(f) if r['status'] == 'played' else None for f in FIELDS}} for r in recent], 'shot_chart': chart}
    return {'season': season, 'as_of': as_of, 'players': players}
