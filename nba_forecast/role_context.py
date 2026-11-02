"""Replayable same-game injury exits for conditional healthy role minutes.

Only explicit causal NBA recap statements are accepted. Absence of a match is
unknown evidence, never an assertion that a player completed a healthy game.
This module neither changes actuals nor supplies pregame availability.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from .data import MAX_BYTES, _NextDataParser
from .full_boxscore_sources import decode_official_game_page, full_boxscore_url, normalize_official_page
from . import replay_cache
from .schedule import canonical
from .validation import COUNTS, parse_time, validate_history


VERSION = 1
PARSER_VERSION = 'nba-recap-injury-exit-2'
METHOD = 'Explicit same-game injury exits; exclude healthy role minutes only, retaining actual counts and exposure.'
_HASH = re.compile(r'[0-9a-f]{64}')
_URL = re.compile(r'https://www\.nba\.com/game/(002\d{7})/box-score')
_IDENTITY = ('game_id', 'player_id', 'team_id', 'opponent_id', 'season', 'season_type')


def _eligible(row, snapshot):
    cutoff = parse_time(snapshot['as_of'])
    year = int(snapshot['season'][:4])
    seasons = {snapshot['season'], f'{year-1}-{str(year)[-2:]}'}
    excluded = set(snapshot.get('excluded_history_game_ids', []))
    excluded.update(snapshot.get('model_enhancements', {}).get('excluded_history_game_ids', []))
    excluded.update(g['game_id'] for g in snapshot.get('games', []))
    return (row['season_type']=='Regular Season' and row['season'] in seasons
            and row['game_id'] not in excluded
            and parse_time(row['game_time']) < parse_time(row['known_at']) <= cutoff)


def _history(snapshot, history):
    """Each eligible game's latest rows, validated, keyed by game.

    The map over the whole history is built once per history object and
    filter (``replay_cache``); the slate's own games are left out of each
    caller's copy, and an ambiguity or validation failure among the games
    left in is raised exactly as building the map from those games would.
    """
    cutoff = parse_time(snapshot['as_of'])
    year = int(snapshot['season'][:4])
    seasons = {snapshot['season'], f'{year-1}-{str(year)[-2:]}'}
    excluded = set(snapshot.get('excluded_history_game_ids', []))
    excluded.update(snapshot.get('model_enhancements', {}).get('excluded_history_game_ids', []))
    future = {g['game_id'] for g in snapshot.get('games', [])}
    key = ('role_context', tuple(sorted(seasons)), tuple(sorted(excluded)), replay_cache.cutoff_key(history, cutoff))
    latest, ambiguous, valid = replay_cache.league(history, key, lambda: _latest_rows(history, cutoff, seasons, excluded))
    if ambiguous - future:
        raise ValueError('Ambiguous same-time history for injury role context')
    games = {gid: rows for gid, rows in latest.items() if gid not in future}
    if not valid:
        # The whole map fails; the games left in are validated on their own.
        validate_history([row for rows in games.values() for row in rows])
    return games


def _latest_rows(history, cutoff, seasons, excluded):
    latest = {}
    ambiguous = set()
    for row in history:
        if (row['season_type'] != 'Regular Season' or row['season'] not in seasons
                or row['game_id'] in excluded
                or not parse_time(row['game_time']) < parse_time(row['known_at']) <= cutoff):
            continue
        key = row['game_id'], row['player_id']
        old = latest.get(key)
        if old is not None and parse_time(old['known_at']) == parse_time(row['known_at']):
            fields = (*_IDENTITY, 'game_time', 'status', 'minutes', *COUNTS)
            if any(old.get(k) != row.get(k) for k in fields):
                ambiguous.add(row['game_id'])
        if old is None or parse_time(old['known_at']) < parse_time(row['known_at']):
            latest[key] = row
    try:
        validate_history(list(latest.values()))
        valid = True
    except ValueError:
        valid = False
    games = defaultdict(list)
    for row in latest.values():
        games[row['game_id']].append(row)
    return dict(games), ambiguous, valid


def _prepared_snapshot(snapshot, supplied):
    if (not isinstance(supplied, dict) or type(supplied.get('schema_version')) is not int
            or supplied['schema_version'] != VERSION or supplied.get('parser_version') != PARSER_VERSION):
        raise ValueError('Unsupported injury role-context schema or parser version')
    age = (parse_time(snapshot['as_of']) - parse_time(supplied['prepared_at'])).total_seconds()
    if not 0 <= age <= 300:
        raise ValueError('Prepared injury role context is stale or future-known')
    if supplied['season'] != snapshot['season']:
        raise ValueError('Injury role-context season mismatch')
    return {**snapshot, 'as_of': supplied['prepared_at']}


def _metadata(value):
    if not isinstance(value, dict):
        raise ValueError('Injury source metadata must be an object')
    result = {key: value[key] for key in ('source_url', 'sha256', 'retrieved_at', 'bytes')}
    if not isinstance(result['sha256'], str) or not _HASH.fullmatch(result['sha256']):
        raise ValueError('Injury raw source hash is invalid')
    if not isinstance(result['source_url'], str) or not _URL.fullmatch(result['source_url']):
        raise ValueError('Injury source must be an exact official NBA game URL')
    parse_time(result['retrieved_at'])
    if type(result['bytes']) is not int or not 0 < result['bytes'] <= MAX_BYTES:
        raise ValueError('Injury source byte count is invalid')
    return result


def _read_raw(directory, metadata):
    path = Path(directory)/'raw'/(metadata['sha256']+'.html')
    raw = path.read_bytes()
    if (len(raw) != metadata['bytes'] or len(raw) > MAX_BYTES
            or hashlib.sha256(raw).hexdigest() != metadata['sha256']):
        raise ValueError('Injury raw archive hash or byte count mismatch')
    archived = _metadata(json.loads(path.with_suffix('.provenance.json').read_bytes()))
    if archived != metadata:
        raise ValueError('Injury raw archive provenance differs from immutable evidence')
    return raw


def _source_game(raw, metadata, rows):
    parser = _NextDataParser()
    parser.feed(raw.decode('utf-8'))
    source = json.loads(''.join(parser.parts))['props']['pageProps']['game']
    if any(parse_time(r['game_time']) != parse_time(source['gameTimeUTC']) for r in rows):
        raise ValueError('Injury source and history game times disagree')
    game = dict(game_id=rows[0]['game_id'], start_time=source['gameTimeUTC'],
                season_type='Regular Season', home_team_id=str(source['homeTeam']['teamId']),
                away_team_id=str(source['awayTeam']['teamId']))
    teams = {game['home_team_id'], game['away_team_id']}
    if len(teams) != 2 or any({r['team_id'], r['opponent_id']} != teams for r in rows):
        raise ValueError('Injury source and history team identities disagree')
    if metadata['source_url'] != full_boxscore_url(game['game_id']):
        raise ValueError('Injury source and history game identities disagree')
    return game


def _normalize(text):
    text = text.replace('’', "'").replace('‘', "'")
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', text)
                          if not unicodedata.combining(c)).casefold().split())


def _name_key(text):
    return ''.join(c for c in _normalize(text) if c.isalnum())


def _subjects(page):
    people = []
    aliases = defaultdict(set)
    for side in ('homeTeam', 'awayTeam'):
        team = page['game'][side]
        prefixes = {_normalize(team.get(k, '')) for k in ('teamCity', 'teamName', 'teamTricode')}
        prefixes.add(_normalize(team.get('teamCity', '')+' '+team.get('teamName', '')))
        prefixes.discard('')
        prefix = '(?:(?:'+ '|'.join(re.escape(v) for v in sorted(prefixes, key=len, reverse=True))+r')\s+)?' if prefixes else ''
        for person in team['players']:
            pid = str(person['personId'])
            names = {_normalize(person['firstName']+' '+person['familyName']), _normalize(person['familyName'])}
            for name in names:
                aliases[name].add(pid)
            people.append(dict(player_id=pid, names=names, prefix=prefix,
                               name=person['firstName']+' '+person['familyName']))
    for person in people:
        names = [name for name in person['names'] if name and len(aliases[name]) == 1]
        person['aliases'] = names
        if not names:
            person['pattern'] = None
            continue
        role = r'(?:(?:starting|backup|reserve|star)\s+)?(?:(?:point guard|shooting guard|small forward|power forward|center|guard|forward|swingman|rookie|veteran)\s+)?'
        person['pattern'] = re.compile(r'^(?:the\s+)?'+person['prefix']+role+
            '(?:'+'|'.join(re.escape(n) for n in sorted(names, key=len, reverse=True))+r')\b')
    return people


def _subject_tail(text, person):
    if person['pattern'] is None:
        return None
    match = person['pattern'].match(_normalize(text))
    if match is None:
        return None
    tail = _normalize(text)[match.end():].lstrip()
    # The Flagg recap includes a non-causal award appositive before its predicate.
    tail = re.sub(r'^,\s*[^,.!?]{1,140},\s*', '', tail)
    return tail


def _exit_kind(tail, paragraph, person, people):
    if tail is None or re.search(r'\b(?:if|might|could|questionable|last|previous|prior|earlier|yesterday|pregame|warmups)\b', tail):
        return None
    # Recaps also discuss earlier games. A named date/day in the injury
    # predicate is ambiguous without independently resolved local chronology.
    # Later sentences about tomorrow's re-evaluation are not the predicate.
    first_sentence=re.split(r'[.!?]',tail,maxsplit=1)[0]
    if re.search(r'\b(?:season (?:opener|debut)|monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|may|june|july|august|september|october|november|december)\b|\b\d{1,2}/\d{1,2}\b',first_sentence):
        return None
    side=r'(?:(?:left|right|lower|upper)\s+)?'
    body=r'(?:shoulder|knee|ankle|foot|toe|leg|hamstring|calf|thigh|groin|hip|back|neck|head|wrist|hand|finger|elbow|arm|chest|rib|abdominal|achilles(?: tendon)?)'
    if re.match(r"(?:did not|didn't) (?:play in|return for) the second half after he (?:injured|hurt) his "+side+body+r'\b', tail):
        return 'missed_second_half', 2
    # A pronoun is accepted only after a named injury subject and no other
    # roster person in the first sentence; this covers the archived Missi case.
    injury = re.match(r'injured his '+side+body+r' in the (first|second|third|fourth) quarter\b[^.?!]*\.\s+he (?:missed|sat out) the (?:rest|remainder) of the game\b', tail)
    if injury:
        first = _normalize(paragraph).split('.', 1)[0]
        for other in people:
            if other['player_id'] != person['player_id'] and any(
                    re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)', first) for name in other['aliases']):
                return None
        return 'did_not_return', ('first', 'second', 'third', 'fourth').index(injury[1])+1
    anatomy=side+'(?:'+body+r'\s+)?'
    if re.match(r"(?:left|exited) the game (?:with|due to|because of) (?:a|an|his) "+anatomy+r"(?:injury|sprain|strain|concussion|fracture) and (?:did not|didn't) return(?: to the (?:game|court))?(?:[.!?]|$)", tail):
        return 'did_not_return', None
    return None


def _extract(page, rows, metadata):
    story = page.get('story')
    if not isinstance(story, dict) or story.get('storyType') != 'recap':
        return [], 'recap_unavailable'
    paragraphs = story.get('content')
    if not isinstance(paragraphs, list) or not paragraphs or any(not isinstance(p, str) for p in paragraphs):
        return [], 'recap_unparseable'
    people = _subjects(page)
    eligible = {r['player_id']: r for r in rows if r['status']=='played' and r['minutes'] > 0}
    events = []
    contradictory = False
    for person in people:
        row = eligible.get(person['player_id'])
        if row is None:
            continue
        matches = []
        returned = False
        for index, paragraph in enumerate(paragraphs):
            tail = _subject_tail(paragraph, person)
            returned_phrase = r'(?:returned|did return|came back|re-entered|checked back in|was able to return)\b'
            if tail and (re.match(returned_phrase, tail)
                         or re.search(r'(?:\b(?:but|later|then) (?:he )?|[.!?]\s*(?:(?:however|nevertheless|nonetheless|but|then|later),?\s+)?he (?:(?:later|then|eventually) )?)'+returned_phrase, tail)):
                returned = True
            kind = _exit_kind(tail, paragraph, person, people)
            if kind:
                matches.append((index, paragraph, kind))
        if not matches:
            continue
        pbp = page.get('playByPlay')
        claimed_periods = [k[2][1] for k in matches if k[2][1] is not None]
        if isinstance(pbp, dict) and pbp.get('gameId') == row['game_id']:
            actions = pbp.get('actions', [])
            if isinstance(actions, list) and claimed_periods:
                returned |= any(isinstance(a, dict) and str(a.get('personId')) == row['player_id']
                                and type(a.get('period')) is int and a['period'] > min(claimed_periods)
                                for a in actions)
        if returned or len({kind for _, _, kind in matches}) != 1:
            contradictory = True
            continue
        index, paragraph, (status, _) = matches[0]
        events.append(dict(**{k: row[k] for k in _IDENTITY}, game_time=page['game']['gameTimeUTC'],
            player_name=person['name'], classification='injury_shortened', return_status=status,
            known_at=metadata['retrieved_at'], source_url=metadata['source_url'], source_sha256=metadata['sha256'],
            source_locator=f'props.pageProps.story.content[{index}]', source_text=paragraph,
            reason='Explicit same-game injury caused the player to miss the remainder or second half.',
            identity_basis='Unique NBA game-roster subject; exact game/team/person IDs and played history reconciled'))
    return events, 'confirmed_exit' if events else 'conflicting_evidence' if contradictory else 'no_confirmed_exit'


def _replay(directory, envelope, rows, snapshot):
    """The recap's exit events for one archived source against one set of rows, replayed once per process (``replay_cache``)."""
    if not rows:
        raise ValueError('Injury source lacks eligible history')
    metadata = _metadata(envelope['metadata'])
    if parse_time(metadata['retrieved_at']) > parse_time(snapshot['as_of']):
        raise ValueError('Injury source is future-known')
    key = ('recap', str(Path(directory).resolve()), replay_cache.digest(metadata), replay_cache.digest(envelope['game']),
           replay_cache.digest(rows), replay_cache.stamp(_raw_paths(directory, metadata)))
    return replay_cache.replay(key, lambda: _replay_source(directory, envelope, metadata, rows))


def _raw_paths(directory, metadata):
    """The raw page and its provenance a source's replay reads; ``_read_raw`` checks both."""
    path = Path(directory)/'raw'/(metadata['sha256']+'.html')
    return (path, path.with_suffix('.provenance.json'))


def _source_envelope_game(directory, metadata, rows):
    """The game identity a raw page states against one set of rows, read once per process (``replay_cache``)."""
    key = ('recap-game', str(Path(directory).resolve()), replay_cache.digest(metadata), replay_cache.digest(rows),
           replay_cache.stamp(_raw_paths(directory, metadata)))
    return replay_cache.replay(key, lambda: _source_game(_read_raw(directory, metadata), metadata, rows))


def _replay_source(directory, envelope, metadata, rows):
    raw = _read_raw(directory, metadata)
    game = _source_game(raw, metadata, rows)
    if canonical(game) != canonical(envelope['game']):
        raise ValueError('Injury source game identity differs from immutable evidence')
    page = decode_official_game_page(raw, game, metadata)
    official = normalize_official_page(page, game, metadata)
    index = {(r['team_id'], r['player_id']): r for r in official}
    played = lambda values: {(r['team_id'], r['player_id']) for r in values if r['status']=='played'}
    if played(official) != played(rows):
        raise ValueError('Injury source and history played-player identities disagree')
    for row in rows:
        source = index.get((row['team_id'], row['player_id']))
        if source is None and row['status']=='dnp':
            continue
        if source is None or any(source[k] != row[k] for k in (*_IDENTITY, 'status', *COUNTS)):
            raise ValueError('Injury source and history identities or counts disagree')
        if _name_key(source['name']) != _name_key(row['name']):
            raise ValueError('Injury source player name and history person identity disagree')
        tolerance = 1 if row.get('minutes_precision')=='whole_minutes' else .1 if row.get('minutes_precision')=='decimal_minutes' else 1e-6
        if abs(source['minutes']-row['minutes']) > tolerance:
            raise ValueError('Injury source and history minutes disagree')
    return _extract(page, rows, metadata)


def _manifest(envelope, digest):
    metadata, game = envelope['metadata'], envelope['game']
    return dict(game_id=game['game_id'], game_time=game['start_time'], known_at=metadata['retrieved_at'],
                source_url=metadata['source_url'], source_sha256=metadata['sha256'], evidence_sha256=digest)


def _assemble(snapshot, games, records):
    sources, events, unconfirmed = [], [], []
    statuses = {}
    for manifest, found, status in records:
        sources.append(manifest)
        events.extend(found)
        statuses[manifest['game_id']] = status
    for gid in sorted(games):
        status = statuses.get(gid, 'source_unavailable')
        if status != 'confirmed_exit':
            unconfirmed.append(dict(game_id=gid, status=status))
    return dict(schema_version=VERSION, parser_version=PARSER_VERSION, prepared_at=snapshot['as_of'],
        season=snapshot['season'], method=METHOD, events=sorted(events, key=lambda e: (e['game_id'], e['player_id'])),
        sources=sorted(sources, key=lambda s: s['game_id']), coverage=dict(eligible_games=len(games),
            source_games=len(sources), confirmed_events=len(events), unconfirmed_games=unconfirmed,
            policy='Missing or unmatched recap evidence remains unknown; short minutes alone never establish injury.'))


def prepare_role_context(directory, snapshot, history):
    """Capture eligible archived NBA recap evidence without rewriting actuals."""
    try:
        games = _history(snapshot, history)
        candidates = {}
        for path in sorted((Path(directory)/'raw').glob('*.provenance.json')):
            value = json.loads(path.read_bytes())
            if not isinstance(value, dict):
                raise ValueError('Injury raw provenance must be an object')
            match = _URL.fullmatch(value.get('source_url', ''))
            if not match or match[1] not in games:
                continue
            metadata = _metadata(value)
            if path.name != metadata['sha256']+'.provenance.json':
                raise ValueError('Injury raw provenance filename/hash mismatch')
            if parse_time(metadata['retrieved_at']) > parse_time(snapshot['as_of']):
                continue
            gid = match[1]
            old = candidates.get(gid)
            if old is not None and parse_time(metadata['retrieved_at']) == parse_time(old['retrieved_at']) and metadata['sha256'] != old['sha256']:
                raise ValueError('Ambiguous same-time NBA injury source versions')
            if old is None or parse_time(metadata['retrieved_at']) > parse_time(old['retrieved_at']):
                candidates[gid] = metadata
        records = []
        for gid, metadata in sorted(candidates.items()):
            envelope = dict(metadata=metadata, game=_source_envelope_game(directory, metadata, games[gid]))
            events, status = _replay(directory, envelope, games[gid], snapshot)
            encoded = canonical(envelope)
            digest = hashlib.sha256(encoded).hexdigest()
            path = Path(directory)/'role-evidence'/(digest+'.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if path.read_bytes() != encoded:
                    raise ValueError('Immutable injury evidence archive hash mismatch')
            else:
                with path.open('xb') as stream:
                    stream.write(encoded)
            records.append((_manifest(envelope, digest), events, status))
        return _assemble(snapshot, games, records)
    except (KeyError, TypeError, AttributeError, UnicodeError, OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Incomplete or unreadable injury role-context source: {exc}') from exc


def verify_role_context(snapshot, history, directory):
    """Replay immutable manifests and raw NBA bytes at the preparation cutoff."""
    supplied = snapshot.get('role_context')
    if supplied is None:
        return
    try:
        prepared = _prepared_snapshot(snapshot, supplied)
        games = _history(prepared, history)
        records, seen = [], set()
        for manifest in supplied['sources']:
            gid, digest = manifest['game_id'], manifest['evidence_sha256']
            if gid in seen or not isinstance(digest, str) or not _HASH.fullmatch(digest):
                raise ValueError('Duplicate injury source game or invalid evidence hash')
            seen.add(gid)
            encoded = (Path(directory)/'role-evidence'/(digest+'.json')).read_bytes()
            if hashlib.sha256(encoded).hexdigest() != digest:
                raise ValueError('Immutable injury evidence archive hash mismatch')
            envelope = json.loads(encoded)
            events, status = _replay(directory, envelope, games.get(gid, []), prepared)
            records.append((_manifest(envelope, digest), events, status))
        if canonical(supplied) != canonical(_assemble(prepared, games, records)):
            raise ValueError('Supplied injury role context differs from archived source replay')
    except (KeyError, TypeError, AttributeError, UnicodeError, OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Incomplete or unreadable injury role-context evidence: {exc}') from exc


def role_excluded_game_ids(snapshot, player_id, rows):
    """Return eligible exact player-game exclusions; preflight owns raw replay."""
    supplied = snapshot.get('role_context')
    if supplied is None:
        return set()
    try:
        prepared = _prepared_snapshot(snapshot, supplied)
        cutoff = parse_time(prepared['as_of'])
        sources = {}
        for source in supplied['sources']:
            gid = source['game_id']
            if gid in sources or not _HASH.fullmatch(source['source_sha256']):
                raise ValueError('Duplicate or malformed injury role source')
            if source['source_url'] != full_boxscore_url(gid) or not parse_time(source['game_time']) < parse_time(source['known_at']) <= cutoff:
                raise ValueError('Injury role source identity or cutoff is invalid')
            sources[gid] = source
        result, seen = set(), set()
        for event in supplied['events']:
            key = event['game_id'], event['player_id']
            if key in seen:
                raise ValueError('Duplicate injury player-game event')
            seen.add(key)
            source = sources.get(event['game_id'])
            if (source is None or event['classification'] != 'injury_shortened'
                    or any(event[k] != source[k] for k in ('game_time', 'known_at', 'source_url', 'source_sha256'))):
                raise ValueError('Injury role event source identity or chronology mismatch')
            if event['player_id'] != player_id:
                continue
            eligible = [r for r in rows if r.get('player_id')==player_id and _eligible(r, prepared)
                        and r['game_id']==event['game_id']]
            if not eligible:
                continue
            row = max(eligible, key=lambda r: parse_time(r['known_at']))
            fields = (*_IDENTITY, 'game_time', 'status', 'minutes')
            if any(parse_time(r['known_at']) == parse_time(row['known_at'])
                   and any(r[k] != row[k] for k in fields) for r in eligible):
                raise ValueError('Ambiguous same-time player history for injury role context')
            if (row['status']=='played' and row['minutes'] > 0
                    and all(row[k]==event[k] for k in _IDENTITY)
                    and parse_time(row['game_time'])==parse_time(event['game_time'])):
                result.add(row['game_id'])
        return result
    except (KeyError, TypeError) as exc:
        raise ValueError(f'Incomplete injury role-context identity: {exc}') from exc
