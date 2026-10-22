"""Verified observed field-goal attempts from NBA LiveData (never predictions)."""
import hashlib
import json
import math
from .data import _fetch, _timestamp, boxscore_url


def playbyplay_url(game_id):
    boxscore_url(game_id)
    return f'https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{game_id}.json'


def normalize_shots(payload, boxscore, known_at, source_url=None):
    """Reject incomplete attempt counts; retain attempts lacking coordinates.

    xLegacy/yLegacy are retained without inferred positions. Percent/arena x/y
    coordinates are deliberately not substituted for NBA legacy coordinates.
    """
    try:
        game = boxscore['game']; gid = game['gameId']; url = playbyplay_url(gid)
        if source_url is not None and source_url != url:
            raise ValueError('Play-by-play source URL does not match game ID')
        if game['gameStatus'] != 3:
            raise ValueError('Shots require a FINAL paired boxscore')
        if payload['game']['gameId'] != gid:
            raise ValueError('Play-by-play and boxscore game IDs disagree')
        if _timestamp(game['gameTimeUTC']) >= _timestamp(known_at):
            raise ValueError('Shot evidence cannot be known before game starts')
        expected = {}
        for side in ('homeTeam','awayTeam'):
            team = game[side]
            for p in team['players']:
                key = (str(team['teamId']), str(p['personId']))
                if key in expected: raise ValueError('Duplicate boxscore player')
                s = p['statistics']
                counts = [s['fieldGoalsAttempted'],s['fieldGoalsMade'],s['threePointersAttempted'],s['threePointersMade']]
                if any(not isinstance(v,int) or isinstance(v,bool) or v < 0 for v in counts):
                    raise ValueError('Invalid boxscore attempt counts')
                expected[key] = counts
        counts = {key: [0,0,0,0] for key in expected}; shots = []; seen = set()
        for action in payload['game']['actions']:
            if action.get('actionType') not in ('2pt','3pt'): continue
            number = action['actionNumber']
            if number in seen: raise ValueError('Duplicate shot action number')
            seen.add(number)
            key = (str(action['teamId']), str(action['personId']))
            if key not in expected: raise ValueError('Shot player/team absent from paired boxscore')
            if action['shotResult'] not in ('Made','Missed'): raise ValueError('Unknown shot result')
            made = action['shotResult'] == 'Made'; three = action['actionType'] == '3pt'
            counts[key][0] += 1; counts[key][1] += int(made)
            counts[key][2] += int(three); counts[key][3] += int(three and made)
            x,y = action.get('xLegacy'),action.get('yLegacy')
            valid = all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in (x,y))
            shots.append({'game_id':gid,'action_number':number,'player_id':key[1],'team_id':key[0],'period':action.get('period'),'clock':action.get('clock'),'points':3 if three else 2,'made':made,'x':x if valid else None,'y':y if valid else None,'coordinate_missing_reason':None if valid else 'NBA action has no valid xLegacy/yLegacy pair','source_url':url,'known_at':known_at})
        if counts != expected:
            mismatches = [f'{key[1]}: observed={counts[key]} boxscore={expected[key]}' for key in expected if counts[key] != expected[key]]
            raise ValueError('Shot/boxscore reconciliation failed: ' + '; '.join(mismatches))
        year = 2000 + int(gid[3:5])
        return {'game_id':gid,'season':f'{year}-{str(year+1)[-2:]}','game_time':game['gameTimeUTC'],'known_at':known_at,'source_url':url,'shots':shots,'coverage':{'attempts':len(shots),'located_attempts':sum(s['x'] is not None for s in shots),'reconciled':True},'missing_reason':None}
    except (KeyError, TypeError) as exc:
        raise ValueError(f'Incomplete NBA shot evidence: {exc}') from exc


def fetch_game_shots(game_id, rawdir):
    """Fetch and archive both sources; caller persists returned normalized cache.

    Source failures raise their precise reason, allowing callers to preserve it
    instead of silently turning unavailable data into a zero-attempt chart.
    """
    url = playbyplay_url(game_id)
    raw, observed = _fetch(url, rawdir)
    boxraw, boxobserved = _fetch(boxscore_url(game_id), rawdir)
    known_at = max((observed,boxobserved), key=_timestamp)
    result = normalize_shots(json.loads(raw), json.loads(boxraw), known_at, url)
    result['provenance'] = [{'source_url':u,'sha256':hashlib.sha256(b).hexdigest(),'retrieved_at':t} for u,b,t in ((url,raw,observed),(boxscore_url(game_id),boxraw,boxobserved))]
    return result
