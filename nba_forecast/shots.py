"""Reconciled observed field-goal attempts from archived official NBA sources."""
from datetime import datetime, timezone
import hashlib
import json
import math
import re

from .data import _fetch, _timestamp, boxscore_url
from .full_boxscore_sources import fetch_official_game_page, full_boxscore_url, normalize_official_page
from .validation import validate_history


SHOT_SOURCE_VERSION = 'nba-liveness-and-official-page-v2'
COORDINATE_SYSTEM = 'NBA legacy coordinates; 10 units per foot, hoop origin'
_FIELDS = ('fga', 'fgm', 'fg3a', 'fg3m')
_SOURCE_FIELDS = ('fieldGoalsAttempted', 'fieldGoalsMade', 'threePointersAttempted', 'threePointersMade')


def playbyplay_url(game_id):
    boxscore_url(game_id)
    return f'https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{game_id}.json'


def _counts(values):
    if any(type(v) is not int or v < 0 for v in values):
        raise ValueError('Invalid boxscore attempt counts')
    fga, fgm, tpa, tpm = values
    if fgm > fga or tpa > fga or tpm > fgm or tpm > tpa or fgm-tpm > fga-tpa:
        raise ValueError('Inconsistent boxscore attempt counts')
    return values


def _box_players(game, validated_rows=None):
    if validated_rows is not None:
        return ({(r['team_id'],r['player_id']):_counts([r[k] for k in _FIELDS]) for r in validated_rows},
                {(r['team_id'],r['player_id']) for r in validated_rows if r['status']=='played'})
    expected, played, people = {}, set(), set()
    for side in ('homeTeam', 'awayTeam'):
        team = game[side]
        for person in team['players']:
            key = str(team['teamId']), str(person['personId'])
            if key in expected or key[1] in people:
                raise ValueError('Duplicate boxscore player')
            people.add(key[1])
            stats = person['statistics']
            expected[key] = _counts([stats[k] for k in _SOURCE_FIELDS])
            participation = person.get('played')
            if participation is not None:
                if participation not in ('0', '1'):
                    raise ValueError('Invalid boxscore participation')
                did_play = participation == '1'
            elif re.match(r'^(DNP|DND|Inactive)(?:\s|$)', person.get('comment') or '', re.I):
                did_play = False
            else:
                # Official pages list a minute clock even for a participant
                # credited with zero elapsed minutes and zero attempts.
                minute_clock = stats.get('minutes')
                did_play = bool(isinstance(minute_clock, str) and re.fullmatch(r'\d+:[0-5]\d', minute_clock))
                did_play = did_play or bool(expected[key][0])
            if did_play:
                played.add(key)
            elif any(expected[key]):
                raise ValueError('DNP boxscore player has field-goal attempts')
    return expected, played


def _check_final_rows(final_rows, game, expected, played):
    if final_rows is None:
        return None
    validate_history(final_rows)
    if not final_rows:
        raise ValueError('Paired normalized final is empty')
    seen_played = set()
    latest = None
    for row in final_rows:
        if row['game_id'] != game['gameId'] or _timestamp(row['game_time']) != _timestamp(game['gameTimeUTC']):
            raise ValueError('Shot and normalized final game identities disagree')
        key = row['team_id'], row['player_id']
        if row['status'] == 'played':
            seen_played.add(key)
            if key not in expected or [row[k] for k in _FIELDS] != expected[key]:
                raise ValueError(f'Shot/normalized final reconciliation failed for {row["player_id"]}')
        elif key in played or (key in expected and any(expected[key])):
            raise ValueError('Shot and normalized final participation disagree')
        latest = row['known_at'] if latest is None else max((latest, row['known_at']), key=_timestamp)
    if seen_played != played:
        raise ValueError('Shot and normalized final played player identities disagree')
    return latest


def _location(action):
    x, y = action.get('xLegacy'), action.get('yLegacy')
    valid = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (x, y))
    # Preserve observed full-court attempts; reject provider sentinel values.
    valid = valid and -250 <= x <= 250 and -52.5 <= y <= 887.5
    return (x, y, None) if valid else (None, None, 'NBA action has no valid xLegacy/yLegacy pair')


def _normalize(payload, boxscore, known_at, url, *, final_rows=None, page_schema=False, paired_rows=None):
    game = boxscore['game']
    gid = game['gameId']
    playbyplay_url(gid)
    if game['gameStatus'] != 3:
        raise ValueError('Shots require a FINAL paired boxscore')
    if payload['game']['gameId'] != gid:
        raise ValueError('Play-by-play and boxscore game IDs disagree')
    if _timestamp(game['gameTimeUTC']) >= _timestamp(known_at):
        raise ValueError('Shot evidence cannot be known before game starts')
    if _timestamp(known_at) > datetime.now(timezone.utc):
        raise ValueError('Shot evidence is future-known')
    expected, played = _box_players(game, paired_rows)
    final_known = _check_final_rows(final_rows, game, expected, played)
    if final_known:
        known_at = max((known_at, final_known), key=_timestamp)
        if _timestamp(known_at) > datetime.now(timezone.utc):
            raise ValueError('Paired final evidence is future-known')
    counts = {key: [0, 0, 0, 0] for key in expected}
    shots, team_attempts, seen, periods, ended = [], [], set(), set(), set()
    actions = payload['game']['actions']
    if not isinstance(actions, list) or not actions:
        raise ValueError('NBA play-by-play actions are missing')
    for action in actions:
        number = action['actionNumber']
        if type(number) is not int or number < 0:
            raise ValueError('Invalid shot action number')
        kind = action.get('actionType')
        period = action.get('period')
        if page_schema:
            if type(period) is not int or period < 1:
                raise ValueError('Official play-by-play period is missing')
            periods.add(period)
            if kind in ('period', 'game') and action.get('subType') == 'end' and action.get('clock') in ('PT00M00.00S', 'PT00M00.0S', 'PT00M00S'):
                ended.add(period)
        if isinstance(kind, str) and kind.lower() == 'heave':
            if (page_schema or 'isFieldGoal' in action) and action.get('isFieldGoal') not in (0, False):
                raise ValueError('Team heave contradicts field-goal flag')
            team_attempts.append(dict(game_id=gid, action_number=number, team_id=str(action['teamId']),
                period=period, clock=action.get('clock'), source_url=url, known_at=known_at,
                reason='Official NBA team heave; not an individual field-goal attempt', source_action=dict(action)))
            continue
        if kind in ('2pt', '3pt'):
            points = 3 if kind == '3pt' else 2
            if page_schema and action.get('isFieldGoal') != 1:
                raise ValueError('Field-goal action contradicts official field-goal flag')
            if action.get('shotValue', points) != points:
                raise ValueError('Contradictory official shot value')
        elif page_schema and (kind in ('Made Shot', 'Missed Shot') or action.get('isFieldGoal') == 1):
            if action.get('isFieldGoal') != 1 or type(action.get('shotValue')) is not int or action['shotValue'] not in (2, 3):
                raise ValueError('Official field-goal shot value is missing or invalid')
            points = action['shotValue']
        else:
            continue
        # Traditional NBA pages reuse an event number for the companion
        # block/steal. Only two actual attempts with the same ID are duplicates.
        if number in seen:
            raise ValueError('Duplicate shot action number')
        seen.add(number)
        key = str(action['teamId']), str(action['personId'])
        if key not in expected or key not in played:
            raise ValueError('Shot player/team absent from paired played boxscore')
        if action['shotResult'] not in ('Made', 'Missed'):
            raise ValueError('Unknown shot result')
        made, three = action['shotResult'] == 'Made', points == 3
        if kind in ('Made Shot', 'Missed Shot') and made != (kind == 'Made Shot'):
            raise ValueError('Contradictory official shot result')
        for i, value in enumerate((1, int(made), int(three), int(three and made))):
            counts[key][i] += value
        x, y, missing = _location(action)
        shots.append(dict(game_id=gid, action_number=number, player_id=key[1], team_id=key[0],
                          period=period, clock=action.get('clock'), points=points, made=made,
                          x=x, y=y, coordinate_missing_reason=missing, source_url=url, known_at=known_at))
    if counts != expected:
        mismatches = [f'{key[1]}: observed={counts[key]} boxscore={expected[key]}' for key in expected if counts[key] != expected[key]]
        raise ValueError('Shot/boxscore reconciliation failed: ' + '; '.join(mismatches))
    if page_schema:
        final_period = game['period']
        if type(final_period) is not int or final_period < 4 or periods != set(range(1, final_period+1)) or ended != periods:
            raise ValueError('Official play-by-play does not cover every completed period')
    year = 2000 + int(gid[3:5])
    coverage = dict(attempts=len(shots), located_attempts=sum(s['x'] is not None for s in shots), reconciled=True)
    if page_schema or final_rows is not None:
        players = []
        for key in sorted(played, key=lambda key: (key[0], key[1])):
            fga, fgm, tpa, tpm = expected[key]
            players.append(dict(player_id=key[1], team_id=key[0], status='played', attempts=fga, made=fgm,
                three_attempts=tpa, three_made=tpm, located_attempts=sum(s['player_id']==key[1] and s['x'] is not None for s in shots), reconciled=True))
        coverage.update(played_players=len(played), covered_players=len(players), players=players)
    return dict(game_id=gid, season=f'{year}-{str(year+1)[-2:]}', game_time=game['gameTimeUTC'],
                known_at=known_at, source_url=url, shots=shots, team_attempts=team_attempts,
                coverage=coverage, missing_reason=None, coordinate_system=COORDINATE_SYSTEM,
                source_version=SHOT_SOURCE_VERSION)


def normalize_shots(payload, boxscore, known_at, source_url=None, *, final_rows=None):
    """Reject incomplete attempt counts; never infer missing NBA coordinates."""
    try:
        url = playbyplay_url(boxscore['game']['gameId'])
        if source_url is not None and source_url != url:
            raise ValueError('Play-by-play source URL does not match game ID')
        return _normalize(payload, boxscore, known_at, url, final_rows=final_rows)
    except (KeyError, TypeError) as exc:
        raise ValueError(f'Incomplete NBA shot evidence: {exc}') from exc


def normalize_page_shots(page, game, metadata, *, final_rows=None):
    """Normalize official embedded PBP using unchanged NBA legacy coordinates.

    The fetch helper validates the archived HTML hash and exact matchup first.
    Supplied final rows must agree for every played NBA person, including those
    with zero attempts. No provider coordinate conversions are performed.
    """
    try:
        source = page['game']
        url = full_boxscore_url(game['game_id'])
        if metadata['source_url'] != url or source['gameId'] != game['game_id'] or game['season_type'] != 'Regular Season':
            raise ValueError('Official shot page source/game mismatch')
        if _timestamp(source['gameTimeUTC']) != _timestamp(game['start_time']):
            raise ValueError('Official shot page game time mismatch')
        for side in ('home', 'away'):
            if str(source[side+'Team']['teamId']) != str(game[side+'_team_id']):
                raise ValueError('Official shot page team identity mismatch')
        if not re.fullmatch(r'[0-9a-f]{64}', metadata['sha256']):
            raise ValueError('Official shot page evidence hash missing or invalid')
        paired_rows = normalize_official_page(page, game, metadata)
        result = _normalize(dict(game=page['playByPlay']), dict(game=source), metadata['retrieved_at'],
                            url, final_rows=final_rows, page_schema=True, paired_rows=paired_rows)
        result['provenance'] = [dict(metadata)]
        result['paired_final_rows'] = paired_rows
        for shot in result['shots']:
            shot['source_sha256'] = metadata['sha256']
        return result
    except (KeyError, TypeError) as exc:
        raise ValueError(f'Incomplete official NBA shot evidence: {exc}') from exc


def fetch_game_shots(game_id, rawdir, *, final_rows=None, game=None, references=None):
    """Archive official sources and require full-game per-player reconciliation.

    The official game page avoids LiveData access failures. ``references`` is
    reserved for compatible provider fallbacks and is not used by this adapter.
    """
    url = playbyplay_url(game_id)
    failures = []
    try:
        raw, observed = _fetch(url, rawdir)
        boxraw, boxobserved = _fetch(boxscore_url(game_id), rawdir)
        known_at = max((observed, boxobserved), key=_timestamp)
        result = normalize_shots(json.loads(raw), json.loads(boxraw), known_at, url, final_rows=final_rows)
        result['provenance'] = [dict(source_url=u, sha256=hashlib.sha256(b).hexdigest(), retrieved_at=t)
            for u,b,t in ((url,raw,observed),(boxscore_url(game_id),boxraw,boxobserved))]
        for shot in result['shots']:
            shot['source_sha256'] = result['provenance'][0]['sha256']
        return result
    except (ValueError, OSError) as exc:
        failures.append(dict(source_url=url, reason=str(exc)))
        if game is None:
            raise
    if game.get('game_id') != game_id:
        raise ValueError('Shot request and supplied game identities disagree')
    try:
        page, metadata = fetch_official_game_page(game, cache_dir=rawdir)
        result = normalize_page_shots(page, game, metadata, final_rows=final_rows)
        result['source_failures'] = failures
        return result
    except (ValueError, OSError) as exc:
        failures.append(dict(source_url=full_boxscore_url(game_id), reason=str(exc)))
        raise ValueError('All official shot sources unavailable: ' + '; '.join(f['reason'] for f in failures)) from exc
