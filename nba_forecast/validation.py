"""Fail-closed validation at the boundary of the forecasting system."""

import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

COUNTS = ('fgm', 'fga', 'fg3m', 'fg3a', 'ftm', 'fta', 'tov', 'pts')


def parse_time(value):
    """Parse an explicit offset; never interpret a local/naive timestamp."""
    if not isinstance(value, str):
        raise ValueError('timestamps must be ISO 8601 strings')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError(f'invalid timestamp: {value}') from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('timestamps require a timezone')
    return result.astimezone(timezone.utc)


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a nonempty string')
    return value


def _source(value):
    _text(value, 'source_url')
    parsed = urlparse(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('source_url must be an HTTPS evidence URL')


def _number(value, field, low=0, high=math.inf):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field} must be numeric')
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{field} outside [{low}, {high}]')


def _season(value):
    if not isinstance(value, str) or not re.fullmatch(r'20\d{2}-\d{2}', value):
        raise ValueError('season must be YYYY-YY')
    year = int(value[:4])
    if int(value[5:]) != (year + 1) % 100:
        raise ValueError('season years must be consecutive')
    return year


def validate_history(history):
    """Validate normalized FINAL regular-season observations, including DNPs.

    The ingestion adapter verifies final-game status. Manually normalized imports
    assert the same contract and must retain a verifiable original source.
    """
    if not isinstance(history, list):
        raise ValueError('history must be a list')
    seen = set()
    games = {}
    try:
        for row in history:
            for field in ('game_id', 'player_id', 'name', 'team_id', 'opponent_id'):
                _text(row[field], field)
            if row['team_id'] == row['opponent_id']:
                raise ValueError('team cannot oppose itself')
            _season(row['season'])
            if row['season_type'] != 'Regular Season':
                raise ValueError('only regular-season observations are allowed')
            start, known = parse_time(row['game_time']), parse_time(row['known_at'])
            if known <= start:
                raise ValueError('a final observation must be known after game start')
            _source(row['source_url'])
            key = (row['game_id'], row['player_id'])
            if key in seen:
                raise ValueError(f'duplicate observation: {key}')
            seen.add(key)
            game = (row['season'], start, tuple(sorted((row['team_id'], row['opponent_id']))))
            if row['game_id'] in games and games[row['game_id']] != game:
                raise ValueError('inconsistent game identity')
            games[row['game_id']] = game
            _number(row['minutes'], 'minutes', high=100)
            for field in COUNTS:
                if isinstance(row[field], bool) or not isinstance(row[field], int) or row[field] < 0:
                    raise ValueError(f'{field} must be a nonnegative integer')
            if any(row[m] > row[a] for m, a in (('fgm', 'fga'), ('fg3m', 'fg3a'), ('ftm', 'fta'))):
                raise ValueError('made shots exceed attempts')
            if (row['fg3m'] > row['fgm'] or row['fg3a'] > row['fga'] or
                    row['fgm'] - row['fg3m'] > row['fga'] - row['fg3a']):
                raise ValueError('inconsistent two/three-point counts')
            if row['pts'] != 2 * row['fgm'] + row['fg3m'] + row['ftm']:
                raise ValueError('points do not match made shots/free throws')
            if row['status'] not in ('played', 'dnp'):
                raise ValueError('observation status must be played or dnp')
            if row['status'] == 'dnp' and (row['minutes'] != 0 or any(row[k] for k in COUNTS)):
                raise ValueError('DNP observations must have zero minutes and counts')
    except (KeyError, TypeError) as exc:
        raise ValueError(f'incomplete normalized history: {exc}') from exc


def validate_inputs(snapshot, history, scope='league'):
    """Validate a point-in-time roster/slate and all evidence used by the model."""
    validate_history(history)
    if scope not in ('league', 'subset'):
        raise ValueError('scope must be league or subset')
    try:
        if snapshot['schema_version'] != 1 or isinstance(snapshot['schema_version'], bool):
            raise ValueError('unsupported schema_version')
        year = _season(snapshot['season'])
        cutoff = parse_time(snapshot['as_of'])
        roster = snapshot['roster']
        observed = parse_time(roster['observed_at'])
        if not cutoff - timedelta(hours=48) <= observed <= cutoff:
            raise ValueError('roster must be observed within 48 hours before cutoff')
        _source(roster['source_url'])
        if roster['complete'] is not True:
            raise ValueError('roster completeness must be explicitly asserted')
        teams = roster['team_ids']
        if not isinstance(teams, list) or not teams:
            raise ValueError('roster team_ids must be a nonempty list')
        for team in teams:
            _text(team, 'team_id')
        if len(set(teams)) != len(teams) or (scope == 'league' and len(teams) != 30):
            raise ValueError('league scope requires exactly 30 distinct teams')
        players = {}
        if not isinstance(roster['players'], list):
            raise ValueError('roster players must be a list')
        for player in roster['players']:
            for field in ('player_id', 'name', 'team_id'):
                _text(player[field], field)
            if player['player_id'] in players:
                raise ValueError('duplicate roster player')
            if player['team_id'] not in teams:
                raise ValueError('player team absent from roster team_ids')
            if player['status'] not in ('available', 'out', 'questionable', 'unknown'):
                raise ValueError('invalid availability status')
            if player['status'] != 'unknown':
                _source(player['status_source_url'])
                if not cutoff - timedelta(hours=24) <= parse_time(player['status_observed_at']) <= cutoff:
                    raise ValueError('availability evidence must be within 24 hours before cutoff')
            players[player['player_id']] = player
        if set(p['team_id'] for p in players.values()) != set(teams):
            raise ValueError('each declared team must have roster entries')
        games, playing_teams = {}, set()
        if not isinstance(snapshot['games'], list):
            raise ValueError('games must be a list')
        for game in snapshot['games']:
            _text(game['game_id'], 'game_id')
            if game['game_id'] in games:
                raise ValueError('duplicate scheduled game')
            if game['season_type'] != 'Regular Season':
                raise ValueError('only regular-season games may be forecast')
            if parse_time(game['start_time']) <= cutoff:
                raise ValueError('forecast cutoff must precede tipoff')
            pair = (game['home_team_id'], game['away_team_id'])
            if pair[0] == pair[1] or any(t not in teams or t in playing_teams for t in pair):
                raise ValueError('invalid teams or multiple games per team in slate')
            playing_teams.update(pair)
            games[game['game_id']] = game
        seen_adjustments = set()
        for adjustment in snapshot.get('role_adjustments', []):
            key = (adjustment['player_id'], adjustment['game_id'])
            if key in seen_adjustments or key[0] not in players or key[1] not in games:
                raise ValueError('duplicate or unknown role adjustment target')
            seen_adjustments.add(key)
            game = games[key[1]]
            if players[key[0]]['team_id'] not in (game['home_team_id'], game['away_team_id']):
                raise ValueError('role adjustment targets wrong game')
            if not cutoff - timedelta(hours=48) <= parse_time(adjustment['observed_at']) <= cutoff:
                raise ValueError('role evidence must be recent and precede cutoff')
            _source(adjustment['source_url'])
            _text(adjustment['reason'], 'reason')
            _text(adjustment['reviewer'], 'reviewer')
            _number(adjustment['minutes_delta'], 'minutes_delta', -15, 15)
            _number(adjustment['opportunity_multiplier'], 'opportunity_multiplier', 0.5, 1.5)
        for row in history:
            if _season(row['season']) > year:
                raise ValueError('history includes a future season')
            if parse_time(row['known_at']) > cutoff or parse_time(row['game_time']) >= cutoff:
                raise ValueError('history includes future or late-known facts')
    except (KeyError, TypeError) as exc:
        raise ValueError(f'incomplete snapshot: {exc}') from exc
