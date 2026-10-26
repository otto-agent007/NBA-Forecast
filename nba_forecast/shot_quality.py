"""Source-bound location expectations for a bounded part of current shooting.

Historical ability, current exposure and free throws remain the existing
model's inputs. This replaces part of current observed makes; it never adds a
second empirical shooting bonus. Court geometry does not measure a defender's
distance, a contest, or player motivation. Constants are uncalibrated modeling
choices, not fitted performance claims. Legacy shot-profile schema is untouched.
"""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

from .data import MAX_BYTES, boxscore_url
from .full_boxscore_sources import full_boxscore_url
from .schedule import canonical
from .shot_profiles import _context, _eligible, _manifest, _replay, _zone
from .shots import COORDINATE_SYSTEM, playbyplay_url
from .validation import parse_time


VERSION = 'court-location-1.0.0'
SCHEMA_VERSION = 1
METHOD = ('Replace a bounded share of current-season makes with source-verified '
          'court-zone expectations excluding the player; retain historical ability and exposure.')
PARAMETERS = dict(current_decay_per_game=.985, maximum_replacement_strength=.35,
    replacement_pseudo_attempts=50., maximum_probability_change=.02,
    minimum_located_attempts=10, minimum_source_games=2, minimum_current_coverage=.8,
    reference_pseudo_attempts=100., minimum_reference_attempts=50, minimum_reference_players=10)
FAMILIES = {'two_point': 2, 'three_point': 3}
ZONE_POINTS = dict(near_basket_2pt=2, paint_2pt=2, midrange_2pt=2,
    left_corner_3pt=3, right_corner_3pt=3, above_break_3pt=3, long_range_3pt=3,
    unlocated_2pt=2, unlocated_3pt=3)


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('Shot quality evidence hash is invalid')
    return value


def _json(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('Shot quality evidence exceeds archive size limit')
    value = json.loads(raw)
    canonical(value)  # Reject NaN/infinity throughout supplied evidence.
    return value


def _counts(item):
    n, m = item['attempts'], item['makes']
    if type(n) is not int or type(m) is not int or not 0 <= m <= n:
        raise ValueError('Shot quality counts are invalid')
    return n, m


def _number(value, low=0., high=math.inf):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Shot quality numeric input is invalid')
    return value


def _family_counts(row, points):
    return dict(attempts=row['fg3a'] if points == 3 else row['fga']-row['fg3a'],
                makes=row['fg3m'] if points == 3 else row['fgm']-row['fg3m'])


def _assemble(snapshot, context, records):
    by_game = {}
    pool = defaultdict(lambda: defaultdict(lambda: dict(attempts=0, makes=0)))
    located = 0
    for manifest, replay in records:
        groups = defaultdict(lambda: defaultdict(lambda: dict(attempts=0, makes=0)))
        for shot in replay['shots']:
            zone = _zone(shot)
            item = groups[shot['player_id']][zone]
            item['attempts'] += 1
            item['makes'] += int(shot['made'])
            if not zone.startswith('unlocated_'):
                located += 1
                item = pool[zone][shot['player_id']]
                item['attempts'] += 1
                item['makes'] += int(shot['made'])
        by_game[manifest['game_id']] = (manifest, groups)
    current = defaultdict(list)
    for rows in context['history'].values():
        for row in rows:
            if row['season'] == snapshot['season'] and row['player_id'] in context['roster']:
                current[row['player_id']].append(row)
    players = []
    for pid, team in sorted(context['roster'].items()):
        rows = sorted(current[pid], key=lambda r: (parse_time(r['game_time']), r['game_id']))
        games = []
        for index, row in enumerate(rows):
            captured = by_game.get(row['game_id'])
            zones = captured[1].get(pid, {}) if captured else {}
            games.append(dict(game_id=row['game_id'], season=row['season'], team_id=row['team_id'],
                game_time=row['game_time'], known_at=row['known_at'], status=row['status'],
                weight=PARAMETERS['current_decay_per_game'] ** (len(rows)-index-1),
                source_sha256=captured[0]['sha256'] if captured else None,
                zones=[dict(zone=z, points=ZONE_POINTS[z], **zones[z]) for z in sorted(zones)],
                **{name: _family_counts(row, points) for name, points in FAMILIES.items()}))
        players.append(dict(player_id=pid, team_id=team, current_games=games))
    reference = []
    for zone, contributions in sorted(pool.items()):
        reference.append(dict(zone=zone, points=ZONE_POINTS[zone],
            attempts=sum(p['attempts'] for p in contributions.values()),
            makes=sum(p['makes'] for p in contributions.values()),
            players=[dict(player_id=pid, **p) for pid, p in sorted(contributions.items())]))
    return dict(schema_version=SCHEMA_VERSION, version=VERSION, method=METHOD,
        prepared_at=snapshot['as_of'], season=snapshot['season'], coordinate_system=COORDINATE_SYSTEM,
        parameters=deepcopy(PARAMETERS), sources=sorted((m for m, _ in records), key=lambda m: m['game_id']),
        players=players, pool=reference,
        coverage=dict(source_games=len(records), source_attempts=sum(len(r['shots']) for _, r in records),
            located_attempts=located, roster_players=len(players)))


def prepare_shot_quality(directory, snapshot, history):
    """Capture all eligible current source games, including off-slate peers."""
    context = _context(snapshot, history)
    records = []
    for path in sorted((Path(directory)/'shots').glob('*.json')):
        raw = path.read_bytes()
        source = _json(raw)
        if source.get('season') != snapshot['season'] or not _eligible(source, context):
            continue
        gid = source['game_id']
        if path.stem != gid:
            raise ValueError('Shot quality collection filename and game identity disagree')
        actuals = context['history'].get(gid, [])
        if not actuals:
            continue  # No independent eligible actuals: no usable reference evidence.
        replay, raw_sources, known = _replay(directory, source, actuals, context)
        digest = hashlib.sha256(raw).hexdigest()
        archived = Path(directory)/'shot-quality-evidence'/(digest+'.json')
        archived.parent.mkdir(parents=True, exist_ok=True)
        try:
            with archived.open('xb') as stream:
                stream.write(raw)
        except FileExistsError:
            if archived.read_bytes() != raw:
                raise ValueError('Immutable shot quality archive hash differs')
        records.append((_manifest(source, digest, raw_sources, known), replay))
    return _assemble(snapshot, context, records)


def _prepared(snapshot, supplied):
    canonical(supplied)
    if (type(supplied.get('schema_version')) is not int or supplied['schema_version'] != SCHEMA_VERSION
            or supplied.get('version') != VERSION or supplied.get('method') != METHOD
            or supplied.get('season') != snapshot['season'] or supplied.get('coordinate_system') != COORDINATE_SYSTEM
            or canonical(supplied.get('parameters')) != canonical(PARAMETERS)):
        raise ValueError('Unsupported shot quality schema or method')
    cutoff = parse_time(supplied['prepared_at'])
    if not 0 <= (parse_time(snapshot['as_of'])-cutoff).total_seconds() <= 300:
        raise ValueError('Prepared shot quality is stale or future-known')
    return {**snapshot, 'as_of': supplied['prepared_at']}


def verify_shot_quality(snapshot, history, directory):
    """Replay immutable normalized and raw archives at the captured cutoff."""
    supplied = snapshot.get('shot_quality')
    if supplied is None:
        return
    prepared = _prepared(snapshot, supplied)
    context = _context(prepared, history)
    records, seen = [], set()
    for manifest in supplied['sources']:
        gid, digest = manifest['game_id'], _hash(manifest['sha256'])
        if gid in seen or parse_time(manifest['known_at']) > context['cutoff']:
            raise ValueError('Duplicate or future-known shot quality source')
        seen.add(gid)
        raw = (Path(directory)/'shot-quality-evidence'/(digest+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Shot quality archive hash mismatch')
        source = _json(raw)
        if source['game_id'] != gid or source['season'] != prepared['season']:
            raise ValueError('Shot quality source game or season differs from archive')
        replay, raw_sources, known = _replay(directory, source, context['history'].get(gid, []), context)
        records.append((_manifest(source, digest, raw_sources, known), replay))
    if canonical(supplied) != canonical(_assemble(prepared, context, records)):
        raise ValueError('Supplied shot quality differs from archived source replay')


def _checked_inputs(snapshot, supplied):
    """Validate arithmetic/identity in memory; raw authentication is preflight's job."""
    prepared = _prepared(snapshot, supplied)
    cutoff = parse_time(prepared['as_of'])
    excluded = set(snapshot.get('excluded_history_game_ids', []))
    excluded.update(snapshot.get('model_enhancements', {}).get('excluded_history_game_ids', []))
    excluded.update(g['game_id'] for g in snapshot['games'])
    manifests = {}
    for source in supplied['sources']:
        gid = source['game_id']
        if not isinstance(gid, str) or gid in manifests or gid in excluded or not re.fullmatch(r'002\d{7}', gid):
            raise ValueError('Invalid shot quality source identity')
        _hash(source['sha256'])
        start, known = parse_time(source['game_time']), parse_time(source['known_at'])
        source_known = parse_time(source['source_known_at'])
        if not start < source_known <= known <= cutoff:
            raise ValueError('Future-known shot quality source')
        if not source['raw_sources']:
            raise ValueError('Shot quality source lacks raw provenance')
        urls = set()
        for raw in source['raw_sources']:
            digest = _hash(raw['sha256'])
            url = raw['source_url']
            if url in urls or url not in (full_boxscore_url(gid), playbyplay_url(gid), boxscore_url(gid)):
                raise ValueError('Shot quality raw source identity differs')
            urls.add(url)
            suffix = '.html' if url == full_boxscore_url(gid) else '.json'
            if raw['raw_path'] != 'raw/'+digest+suffix:
                raise ValueError('Shot quality raw archive path differs')
            if not start < parse_time(raw['first_observed_at']) <= parse_time(raw['retrieved_at']) <= source_known:
                raise ValueError('Shot quality raw evidence is future-known or backdated')
        manifests[gid] = source
    roster = {p['player_id']: p['team_id'] for p in snapshot['roster']['players']}
    players = {}
    for player in supplied['players']:
        pid = player['player_id']
        if pid in players or pid not in roster or player['team_id'] != roster[pid]:
            raise ValueError('Shot quality player/team identity differs')
        players[pid] = player
        games = player['current_games']
        seen, chronology = set(), []
        for index, game in enumerate(games):
            gid = game['game_id']
            if (gid in seen or gid in excluded or game['season'] != supplied['season']
                    or not parse_time(game['game_time']) < parse_time(game['known_at']) <= cutoff):
                raise ValueError('Shot quality current game identity or cutoff differs')
            seen.add(gid)
            chronology.append((parse_time(game['game_time']), gid))
            expected_weight = PARAMETERS['current_decay_per_game'] ** (len(games)-index-1)
            if _number(game['weight'], high=1.) != expected_weight:
                raise ValueError('Shot quality recency weight differs')
            for family in FAMILIES:
                _counts(game[family])
            source = manifests.get(gid)
            if game['source_sha256'] != (source['sha256'] if source else None):
                raise ValueError('Shot quality game does not match its captured source')
            zones = {}
            for zone in game['zones']:
                name = zone['zone']
                if name in zones or name not in ZONE_POINTS or ZONE_POINTS[name] != zone['points']:
                    raise ValueError('Shot quality player zone differs')
                zones[name] = zone
                _counts(zone)
            if source:
                for family, points in FAMILIES.items():
                    for field in ('attempts', 'makes'):
                        if sum(z[field] for z in zones.values() if z['points'] == points) != game[family][field]:
                            raise ValueError('Shot quality attempts do not reconcile with actuals')
            elif zones:
                raise ValueError('Shot quality locations have no source')
        if chronology != sorted(chronology):
            raise ValueError('Shot quality games are not chronological')
    pool = {}
    for zone in supplied['pool']:
        name = zone['zone']
        if name in pool or name not in ZONE_POINTS or name.startswith('unlocated_') or ZONE_POINTS[name] != zone['points']:
            raise ValueError('Shot quality reference zone differs')
        _counts(zone)
        members = {}
        for player in zone['players']:
            pid = player['player_id']
            if not isinstance(pid, str) or not pid or pid in members:
                raise ValueError('Shot quality reference player identity differs')
            if _counts(player)[0] <= 0:
                raise ValueError('Shot quality reference player has no attempts')
            members[pid] = player
        if any(sum(p[k] for p in members.values()) != zone[k] for k in ('attempts', 'makes')):
            raise ValueError('Shot quality reference counts do not reconcile')
        pool[name] = zone
    coverage = supplied['coverage']
    for count in coverage.values():
        if type(count) is not int or count < 0:
            raise ValueError('Shot quality coverage count is invalid')
    if (coverage['source_games'] != len(manifests) or coverage['roster_players'] != len(players)
            or coverage['located_attempts'] != sum(z['attempts'] for z in pool.values())
            or coverage['located_attempts'] > coverage['source_attempts']
            or (not manifests and coverage['source_attempts'])):
        raise ValueError('Shot quality coverage does not reconcile with captured sources')
    return players, pool


def _shooting_denominator(evidence):
    for name in ('percentage', 'default_percentage'):
        _number(evidence[name], high=1.)
    for name in ('prior_made', 'prior_attempts', 'prior_effective_attempts', 'current_weighted_made',
                 'current_weighted_attempts', 'default_pseudo_attempts'):
        _number(evidence[name])
    if (evidence['prior_made'] > evidence['prior_attempts']
            or evidence['prior_effective_attempts'] > evidence['prior_attempts']
            or evidence['current_weighted_made'] > evidence['current_weighted_attempts']):
        raise ValueError('Shot quality shooting counts do not reconcile')
    denominator = evidence['prior_effective_attempts']+evidence['current_weighted_attempts']+evidence['default_pseudo_attempts']
    scale = evidence['prior_effective_attempts']/evidence['prior_attempts'] if evidence['prior_attempts'] else 0.
    numerator = evidence['prior_made']*scale+evidence['current_weighted_made']+evidence['default_percentage']*evidence['default_pseudo_attempts']
    if denominator <= 0 or not math.isclose(numerator/denominator, evidence['percentage'], rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError('Shot quality requires the unchanged baseline shooting estimate')
    return denominator


def adjust_shooting(shooting, snapshot, player_id):
    """Return a copy and audit; inadequate evidence leaves all numbers exact."""
    output = deepcopy(shooting)
    audit = dict(version=VERSION, method=METHOD, status='fallback', reason='locations_unavailable',
                 parameters=deepcopy(PARAMETERS), families={}, scoring_changed=False,
                 limitation='Court location only; no defender-distance or contest data. Uncalibrated adjustment.')
    supplied = snapshot.get('shot_quality')
    if supplied is None:
        return output, audit
    players, pool = _checked_inputs(snapshot, supplied)
    audit.update(prepared_at=supplied['prepared_at'], source_games=[s['game_id'] for s in supplied['sources']])
    player = players.get(player_id)
    if player is None:
        audit['reason'] = 'player_locations_unavailable'
        return output, audit
    for family, points in FAMILIES.items():
        evidence = shooting[family]
        denominator = _shooting_denominator(evidence)
        current = player['current_games']
        attempts = sum(g['weight']*g[family]['attempts'] for g in current)
        made = sum(g['weight']*g[family]['makes'] for g in current)
        item = dict(status='fallback', reason='insufficient_locations', baseline_percentage=evidence['percentage'],
                    adjusted_percentage=evidence['percentage'], probability_change=0.)
        audit['families'][family] = item
        if (evidence.get('current_decay_per_game') != PARAMETERS['current_decay_per_game']
                or not math.isclose(attempts, evidence['current_weighted_attempts'], rel_tol=1e-12, abs_tol=1e-12)
                or not math.isclose(made, evidence['current_weighted_made'], rel_tol=1e-12, abs_tol=1e-12)):
            item['reason'] = 'current_shooting_evidence_differs'
            continue
        peers = {}
        for name, zone in pool.items():
            if zone['points'] != points:
                continue
            members = [p for p in zone['players'] if p['player_id'] != player_id]
            peers[name] = dict(attempts=sum(p['attempts'] for p in members),
                              makes=sum(p['makes'] for p in members), players=len(members))
        total = sum(p['attempts'] for p in peers.values())
        family_rate = sum(p['makes'] for p in peers.values())/total if total else None
        expected, located_made, located_attempts, raw_attempts = 0., 0., 0., 0
        games, references = set(), {}
        for game in current:
            for zone in game['zones']:
                reference = peers.get(zone['zone'])
                if (zone['points'] != points or reference is None
                        or reference['attempts'] < PARAMETERS['minimum_reference_attempts']
                        or reference['players'] < PARAMETERS['minimum_reference_players']):
                    continue
                n = PARAMETERS['reference_pseudo_attempts']
                probability = (reference['makes']+n*family_rate)/(reference['attempts']+n)
                references[zone['zone']] = dict(**reference, percentage=probability)
                weight = game['weight']
                located_attempts += weight*zone['attempts']
                located_made += weight*zone['makes']
                expected += weight*zone['attempts']*probability
                raw_attempts += zone['attempts']
                if zone['attempts']:
                    games.add(game['game_id'])
        coverage = located_attempts/attempts if attempts else 0.
        item.update(located_attempts=located_attempts, located_makes=located_made,
            expected_located_makes=expected, current_attempts=attempts, located_raw_attempts=raw_attempts,
            coverage=coverage, source_games=sorted(games), references=references,
            peer_family_percentage=family_rate, historical_effective_attempts=evidence['prior_effective_attempts'],
            shooting_denominator=denominator)
        if (raw_attempts < PARAMETERS['minimum_located_attempts'] or len(games) < PARAMETERS['minimum_source_games']
                or coverage < PARAMETERS['minimum_current_coverage']):
            continue
        strength = PARAMETERS['maximum_replacement_strength']*located_attempts/(located_attempts+PARAMETERS['replacement_pseudo_attempts'])
        unbounded = strength*(expected-located_made)/denominator
        limit = PARAMETERS['maximum_probability_change']
        delta = min(limit, max(-limit, unbounded))
        percentage = min(1., max(0., evidence['percentage']+delta))
        output[family]['percentage'] = percentage
        item.update(status='applied', reason='supported_location_replacement', replacement_strength=strength,
            unbounded_probability_change=unbounded, probability_change=percentage-evidence['percentage'],
            adjusted_percentage=percentage)
    applied = any(v['status'] == 'applied' for v in audit['families'].values())
    audit.update(status='applied' if applied else 'fallback',
        reason='supported_location_replacement' if applied else 'insufficient_locations',
        scoring_changed=any(v['probability_change'] != 0. for v in audit['families'].values()))
    return output, audit
