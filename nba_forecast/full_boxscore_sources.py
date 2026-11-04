"""Exact NBA game-page evidence for full final boxes when LiveData is unavailable.

The official page embeds NBA person/team IDs and clock minutes in __NEXT_DATA__.
Supplementing an existing final requires identical identities and core counts;
the original source/hash remain intact alongside the later supplemental evidence.
"""
from collections import Counter
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re

from .data import (DEFAULT_CACHE, MAX_BYTES, OPTIONAL_STAT_FIELDS, STAT_FIELDS,
                   _NextDataParser, _fetch, _minutes, archive_payload)
from .validation import COUNTS, OPTIONAL_COUNTS, parse_time, shooting_percentages, validate_history


# Comments NBA box scores give a listed player who did not play: Did Not Play,
# Did Not Dress, Not With Team (e.g. "NWT - League Suspension"), Inactive.
DID_NOT_PLAY_COMMENT = re.compile(r'^(DNP|DND|NWT|Inactive)(?:\s|$)', re.I)


def full_boxscore_url(game_id):
    if not isinstance(game_id, str) or not re.fullmatch(r"002\d{7}", game_id):
        raise ValueError("Official full box requires a regular-season NBA game ID")
    return f"https://www.nba.com/game/{game_id}/box-score"


def _integer(value, field, *, signed=False):
    if type(value) is float and math.isfinite(value) and value.is_integer():
        value = int(value)
    if type(value) is not int or (not signed and value < 0):
        raise ValueError(f"Official NBA {field} must be an {'integer' if signed else 'unsigned integer'}")
    return value


def decode_official_game_page(raw, game, metadata):
    """Decode hash-bound HTML only after exact game, tipoff, and team checks."""
    try:
        if not isinstance(raw, bytes) or len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != metadata['sha256']:
            raise ValueError('Official NBA page bytes do not match their evidence hash')
        parser = _NextDataParser()
        parser.feed(raw.decode('utf-8'))
        page = json.loads(''.join(parser.parts))['props']['pageProps']
        _validate_page_identity(page,game,metadata)
        return page
    except (KeyError, TypeError, UnicodeDecodeError, AttributeError) as exc:
        raise ValueError(f'Incomplete official NBA game page: {exc}') from exc


def _validate_page_identity(page,game,metadata):
    if metadata['source_url'] != full_boxscore_url(game['game_id']) or game['season_type'] != 'Regular Season':
        raise ValueError('Official NBA page source/game mismatch')
    if not re.fullmatch(r'[a-f0-9]{64}',metadata['sha256']):
        raise ValueError('Official NBA page evidence hash is invalid')
    observed,start = parse_time(metadata['retrieved_at']),parse_time(game['start_time'])
    if observed <= start or observed > datetime.now(timezone.utc)+timedelta(minutes=1):
        raise ValueError('Official final observation time is invalid')
    source=page['game']
    if (source['gameId'] != game['game_id'] or source['gameStatus'] != 3
            or parse_time(source['gameTimeUTC']) != start):
        raise ValueError('Official NBA final game identity/time/status mismatch')
    if _integer(source['period'],'period') < 4:
        raise ValueError('Official NBA final period is invalid')
    if game['home_team_id'] == game['away_team_id']:
        raise ValueError('Official NBA matchup cannot oppose itself')
    for side in ('home','away'):
        team=source[side+'Team']
        if str(team['teamId']) != str(game[side+'_team_id']):
            raise ValueError('Official NBA page team identity mismatch')
        if game.get(side+'_tricode') and team['teamTricode'] != game[side+'_tricode']:
            raise ValueError('Official NBA page team code mismatch')
        _integer(team['score'],'score')


def fetch_official_game_page(game, *, cache_dir=DEFAULT_CACHE):
    """Fetch/archive the exact official HTML; return (pageProps, provenance)."""
    url = full_boxscore_url(game['game_id'])
    raw, known_at = _fetch(url, cache_dir)
    metadata = archive_payload(raw, url, known_at, cache_dir)
    return decode_official_game_page(raw, game, metadata), metadata


def _rows(page, game, metadata):
    source = page['game']
    year = 2000 + int(game['game_id'][3:5])
    rows, seen = [], set()
    for side, other in (('home', 'away'), ('away', 'home')):
        team = source[side+'Team']
        players = team['players']
        if not isinstance(players, list) or not players:
            raise ValueError('Official NBA final player list is empty')
        # The page lists inactive players only once it is republished, some time after the
        # final: as players commented "Inactive - ...", or in a separate inactives list.
        listed = {str(person['personId']) for person in players}
        inactives = [dict(person, statistics={}, comment='Inactive', played='0', position='')
                     for person in team.get('inactives') or [] if str(person['personId']) not in listed]
        inactives_listed = bool(inactives) or any(
            re.match(r'^Inactive(?:\s|$)', person.get('comment') or '', re.I) for person in players)
        team_rows = []
        for person in players + inactives:
            pid = str(_integer(person['personId'], 'personId'))
            if pid in seen:
                raise ValueError('Duplicate official NBA player identity')
            seen.add(pid)
            stats = person['statistics']
            clock, comment = stats.get('minutes'), person.get('comment') or ''
            dnp_comment = isinstance(comment, str) and bool(DID_NOT_PLAY_COMMENT.match(comment))
            overflow = isinstance(clock,str) and bool(re.fullmatch(r'\d+:60',clock))
            if isinstance(clock,str) and clock.startswith('PT'):
                minutes = _minutes(clock)
            elif isinstance(clock,str) and re.fullmatch(r'\d+:(?:[0-5]\d|60)',clock):
                minute, second = map(int,clock.split(':'))
                minutes = minute + second/60
            elif clock in ('',None):
                minutes = None
            else:
                raise ValueError('Official NBA played minutes require explicit minutes:seconds')
            participation = person.get('played')
            if participation is not None and participation not in ('0','1'):
                raise ValueError('Official NBA participation flag is invalid')
            dnp = participation == '0' or (participation is None and dnp_comment and not minutes)
            if dnp:
                if minutes not in (None,0):
                    raise ValueError('Official NBA DNP contradicts source minutes')
                if any(stats.get(name) not in (None, 0, 0.0) for name in (*STAT_FIELDS.values(), *OPTIONAL_STAT_FIELDS.values())):
                    raise ValueError('Official NBA DNP contradicts source counts')
                minutes = 0
            else:
                if minutes is None:
                    raise ValueError('Official NBA played minutes require explicit minutes:seconds')
            row = dict(game_id=game['game_id'], season=f'{year}-{str(year+1)[-2:]}',
                       season_type='Regular Season', game_time=game['start_time'],
                       known_at=metadata['retrieved_at'], source_url=metadata['source_url'],
                       source_sha256=metadata['sha256'],
                       source_provenance={'nba_full_boxscore':dict(metadata)},
                       player_id=pid, name=person.get('name') or f"{person['firstName']} {person['familyName']}".strip(),
                       team_id=str(team['teamId']), opponent_id=str(source[other+'Team']['teamId']),
                       game_period=source['period'], status='dnp' if dnp else 'played',
                       minutes=minutes, minutes_precision='not_played' if dnp else 'seconds',
                       minutes_source_value=clock, optional_stat_errors={},
                       official_inactives_listed=inactives_listed)
            if overflow:
                row['minutes_normalization'] = 'source seconds value 60 carried into the next minute; original string retained'
            if dnp_comment and minutes and not dnp:
                row['participation_audit'] = dict(source_comment=comment,
                    source_minutes=clock, resolution='played: explicit positive minutes and numeric counts override stale DNP comment')
            if 'starter' in person:
                if person['starter'] not in ('0','1'):
                    raise ValueError('Official NBA starter flag is invalid')
                row['starter'] = person['starter']=='1'
            elif isinstance(person.get('position'), str):
                # The game page has no starter flag; it gives a position to the starting five only.
                row['starter'] = bool(person['position'])
            for field, key in STAT_FIELDS.items():
                row[field] = 0 if dnp else _integer(stats[key], field)
            for field, key in OPTIONAL_STAT_FIELDS.items():
                value = stats.get(key)
                row[field] = (0 if dnp else None if value is None
                              else _integer(value, field, signed=field=='plus_minus'))
                if row[field] is None:
                    row['optional_stat_errors'][field] = 'missing from official NBA player statistics'
            if dnp:
                row.update(counts_origin='structural zero from explicit NBA DNP/inactive comment',
                           participation_reason=comment)
            row.update(shooting_percentages(row))
            team_rows.append(row)
        if not any(row['status']=='played' for row in team_rows):
            raise ValueError('Official NBA final has no played rows')
        if sum(row['pts'] for row in team_rows) != team['score']:
            raise ValueError('Official NBA player points do not reconcile with final team score')
        team_stats = dict(team['statistics'])
        team_rebounds = None
        if team_stats.get('reboundsPersonal') is not None:
            # LiveData includes unattributed team rebounds in reboundsTotal;
            # traditional game-page boxes report the player sum there instead.
            personal = _integer(team_stats['reboundsPersonal'],'reboundsPersonal')
            team_rebounds = _integer(team_stats['reboundsTeam'],'reboundsTeam')
            if personal + team_rebounds != _integer(team_stats['reboundsTotal'],'reboundsTotal'):
                raise ValueError('Official NBA personal/team rebound totals disagree')
            team_stats['reboundsTotal'] = personal
        team_counts = {field: (_integer(team_stats[key],field,signed=field=='plus_minus')
                              if team_stats.get(key) is not None else None)
                       for field,key in {**STAT_FIELDS,**OPTIONAL_STAT_FIELDS}.items()}
        if team_rebounds is not None:
            team_counts['team_rebounds'] = team_rebounds
        for key,field in (('turnoversTeam','team_turnovers'),('turnoversTotal','total_turnovers')):
            if team_stats.get(key) is not None:
                team_counts[field]=_integer(team_stats[key],field)
        for field, key in {**STAT_FIELDS, **OPTIONAL_STAT_FIELDS}.items():
            if field == 'plus_minus' or team_stats.get(key) is None:
                continue
            if all(row[field] is not None for row in team_rows):
                total = _integer(team_stats[key], field)
                if sum(row[field] for row in team_rows) != total:
                    raise ValueError(f'Official NBA team/player {field} totals mismatch')
        for row in team_rows:
            row['team_boxscore'] = dict(team_counts)
            row['team_optional_stat_errors'] = {field:'missing from official NBA team statistics'
                for field in OPTIONAL_COUNTS if team_counts.get(field) is None}
        rows.extend(team_rows)
    validate_history(rows)
    return rows


def normalize_official_boxscore(raw, game, metadata):
    """Normalize source-proven exact-clock actuals without interpreting names as IDs."""
    page = decode_official_game_page(raw, game, metadata)
    try:
        return _rows(page, game, metadata)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f'Incomplete official NBA full boxscore: {exc}') from exc


def normalize_official_page(page, game, metadata):
    """Normalize a page already checked by decode_official_game_page/fetch_official_game_page."""
    try:
        _validate_page_identity(page,game,metadata)
        return _rows(page,game,metadata)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f'Incomplete official NBA full boxscore: {exc}') from exc


def fetch_official_boxscore(game, *, cache_dir=DEFAULT_CACHE):
    page, metadata = fetch_official_game_page(game, cache_dir=cache_dir)
    return normalize_official_page(page,game,metadata)


def _official_team_metadata(rows):
    """Require one source-consistent team box before attaching it to members."""
    result={}
    for team_id in {r['team_id'] for r in rows}:
        members=[r for r in rows if r['team_id']==team_id]
        boxes=[r.get('team_boxscore') for r in members]
        if not isinstance(boxes[0],dict) or any(b!=boxes[0] for b in boxes):
            raise ValueError('Official team statistics disagree across player rows')
        box=copy.deepcopy(boxes[0])
        player_tov=sum(r['tov'] for r in members)
        if box.get('tov') is not None and box['tov']!=player_tov:
            raise ValueError('Official team/player turnover totals disagree')
        if all(box.get(k) is not None for k in ('team_turnovers','total_turnovers')):
            for field in ('team_turnovers','total_turnovers'):
                _integer(box[field],field)
            if box['total_turnovers']!=player_tov+box['team_turnovers']:
                raise ValueError('Official total and component turnovers disagree')
        result[team_id]=dict(members=members,box=box)
    return result


def supplement_official_team_turnovers(page,rows,metadata,verified_shots):
    """Derive unattributed turnovers only from a fully verified official PBP.

    The caller first validates the official page, complete final, and shots.
    All individual turnover events must also match every player before zero
    team turnovers can be observed. No new observation time is manufactured.
    """
    validate_history(rows)
    try:
        source=page['game']
        gid=source['gameId']
        url=full_boxscore_url(gid)
        if not rows or {r['game_id'] for r in rows}!={gid} or metadata['source_url']!=url:
            raise ValueError('Official turnover source/game identity mismatch')
        if (verified_shots['game_id']!=gid or verified_shots['source_url']!=url
                or verified_shots['coverage'].get('reconciled') is not True):
            raise ValueError('Official turnover derivation requires verified full-game shots')
        proofs=verified_shots.get('provenance',[])
        if not any(p.get('source_url')==url and p.get('sha256')==metadata['sha256']
                   and p.get('retrieved_at')==metadata['retrieved_at'] for p in proofs):
            raise ValueError('Official turnover proof has different source provenance')
        covered={(p['team_id'],p['player_id']):(p['attempts'],p['made'],p['three_attempts'],p['three_made'])
                 for p in verified_shots['coverage']['players'] if p.get('status')=='played' and p.get('reconciled') is True}
        expected_shots={(r['team_id'],r['player_id']):tuple(r[k] for k in ('fga','fgm','fg3a','fg3m'))
                        for r in rows if r['status']=='played'}
        if covered!=expected_shots or len(covered)!=len(verified_shots['coverage']['players']):
            raise ValueError('Official turnover proof does not cover every played player')
        teams=_official_team_metadata(rows)
        sides={str(source[side+'Team']['teamId']):label for side,label in (('home','h'),('away','v'))}
        if set(teams)!=set(sides) or page['playByPlay']['gameId']!=gid or source['gameStatus']!=3:
            raise ValueError('Official turnover matchup/final identity mismatch')
        final_period=source['period']
        if type(final_period) is not int or final_period<4 or {r.get('game_period') for r in rows}!={final_period}:
            raise ValueError('Official turnover final period is invalid')
        actions=page['playByPlay']['actions']
        periods=set()
        ended=set()
        seen=set()
        individual=Counter()
        events={t:[] for t in teams}
        expected={(r['team_id'],r['player_id']):r['tov'] for r in rows}
        for action in actions:
            period=action['period']
            if type(period) is not int or not 1<=period<=final_period:
                raise ValueError('Official turnover play-by-play period is invalid')
            periods.add(period)
            if (action.get('actionType') in ('period','game') and action.get('subType')=='end'
                    and action.get('clock') in ('PT00M00.00S','PT00M00.0S','PT00M00S')):
                ended.add(period)
            if action.get('actionType') not in ('turnover','Turnover'):
                continue
            number=action['actionNumber']
            if type(number) is not int or number<0 or number in seen:
                raise ValueError('Duplicate or invalid official turnover action number')
            seen.add(number)
            if action.get('isFieldGoal') not in (0,False):
                raise ValueError('Official turnover contradicts field-goal evidence')
            team_id,person_id=str(action.get('teamId')),str(action.get('personId'))
            if (team_id,person_id) in expected:
                individual[(team_id,person_id)]+=1
                continue
            if team_id in teams and person_id=='0':
                resolved=team_id
                encoding='explicit teamId and personId zero'
            elif (team_id=='0' and person_id in teams and action.get('location')==sides[person_id]
                    and not action.get('playerName') and not action.get('playerNameI')):
                resolved=person_id
                encoding='NBA team ID in personId, zero teamId, matching home/away location'
            elif (team_id in teams and action.get('personId') is None
                    and isinstance(action.get('qualifiers'),list) and 'team' in action['qualifiers']):
                resolved=team_id
                encoding='explicit teamId and team qualifier without personId'
            else:
                raise ValueError('Official turnover has an unknown or ambiguous player/team identity')
            events[resolved].append(dict(action_number=number,identity_encoding=encoding,
                                         source_action=copy.deepcopy(action)))
        if periods!=set(range(1,final_period+1)) or ended!=periods:
            raise ValueError('Official turnover play-by-play lacks completed periods')
        if any(individual[key]!=count for key,count in expected.items()):
            raise ValueError('Official turnover events do not reconcile with every player')
        known=max([metadata['retrieved_at'],verified_shots['known_at']]+[r['known_at'] for r in rows],key=parse_time)
        audits={}
        for team_id,value in teams.items():
            player_tov=sum(r['tov'] for r in value['members'])
            observed=dict(team_turnovers=len(events[team_id]),total_turnovers=player_tov+len(events[team_id]))
            for field,count in observed.items():
                if value['box'].get(field) is not None and value['box'][field]!=count:
                    raise ValueError('Official turnover events disagree with explicit team statistics')
                value['box'][field]=count
            audits[team_id]=dict(basis='Complete official PBP: all played-player shot counts, every completed period, and every player turnover reconciled',
                source_url=url,source_sha256=metadata['sha256'],retrieved_at=metadata['retrieved_at'],
                completed_periods=sorted(ended),player_turnovers=player_tov,
                team_turnovers=observed['team_turnovers'],total_turnovers=observed['total_turnovers'],
                team_events=events[team_id],players_reconciled=[dict(player_id=r['player_id'],tov=r['tov'])
                    for r in sorted(value['members'],key=lambda r:r['player_id'])])
        result=copy.deepcopy(rows)
        for row in result:
            row['team_boxscore']=copy.deepcopy(teams[row['team_id']]['box'])
            row['team_turnover_audit']=copy.deepcopy(audits[row['team_id']])
            row['known_at']=known
        return result
    except (KeyError,TypeError,AttributeError) as exc:
        raise ValueError(f'Incomplete official turnover evidence: {exc}') from exc


def _share_official_team_metadata(enriched,original_rows,official_rows):
    teams=_official_team_metadata(official_rows)
    originals={(r['team_id'],r['player_id']):r for r in original_rows}
    for row in enriched:
        team=teams[row['team_id']]
        source=team['members'][0]
        original=originals[(row['team_id'],row['player_id'])]
        known=max((r['known_at'] for r in team['members']),key=parse_time)
        row['team_boxscore_update']=dict(previous_team_boxscore=copy.deepcopy(original.get('team_boxscore')),
            previous_known_at=original['known_at'],basis='Exact NBA game/team IDs; shared team statistics apply to every team member')
        row['team_boxscore']=copy.deepcopy(team['box'])
        row['team_optional_stat_errors']=copy.deepcopy(source.get('team_optional_stat_errors',{}))
        row['team_boxscore_provenance']=dict(game_id=source['game_id'],team_id=row['team_id'],
            source_url=source['source_url'],source_sha256=source['source_sha256'],known_at=known,
            basis='Exact NBA game/team identity; independent of player participation identity')
        if source.get('team_turnover_audit'):
            row['team_turnover_audit']=copy.deepcopy(source['team_turnover_audit'])
        else:
            row.pop('team_turnover_audit',None)
        row['game_period']=source['game_period']
        row['known_at']=max((row['known_at'],known),key=parse_time)
        row.setdefault('source_provenance',{})['nba_full_boxscore']=copy.deepcopy(source['source_provenance']['nba_full_boxscore'])
    return enriched


def validate_complete_official_final(rows):
    """Check complete-game playing time and on-court score conservation.

    Normalization can also inspect small source fragments in isolation; a
    production final must pass this gate before replacing the final cache.
    Unknown optional plus/minus remains unknown and is reported as unchecked.
    """
    validate_history(rows)
    if not rows or len({r['game_id'] for r in rows})!=1:
        raise ValueError('Complete final requires exactly one game')
    periods={r.get('game_period') for r in rows}
    if len(periods)!=1 or type(next(iter(periods))) is not int or next(iter(periods))<4:
        raise ValueError('Complete final period is missing or inconsistent')
    period=next(iter(periods))
    duration=48+5*(period-4)
    expected_minutes=duration*5
    teams={r['team_id'] for r in rows}
    if len(teams)!=2:
        raise ValueError('Complete final requires both teams')
    scores={t:sum(r['pts'] for r in rows if r['team_id']==t) for t in teams}
    audit={}
    for team in teams:
        played=[r for r in rows if r['team_id']==team and r['status']=='played']
        minutes=sum(r['minutes'] for r in played)
        if len(played)<5 or abs(minutes-expected_minutes)>2/60+1e-8:
            raise ValueError('Complete final team minutes or played-player count disagree')
        if any(r['minutes']>duration+1e-8 for r in played):
            raise ValueError('Complete final player minutes exceed game duration')
        other=next(t for t in teams if t!=team)
        pm_checked=all(r.get('plus_minus') is not None for r in played)
        if pm_checked and sum(r['plus_minus'] for r in played)!=5*(scores[team]-scores[other]):
            raise ValueError('Complete final plus/minus does not reconcile with team score')
        audit[team]=dict(played_players=len(played),minutes=minutes,expected_minutes=expected_minutes,plus_minus_reconciled=pm_checked)
    return dict(game_id=rows[0]['game_id'],periods=period,teams=audit)


def enrich_final_rows(rows, official_rows):
    """Return new rows only when exact NBA identities and all core counts agree."""
    return _reconcile_final_rows(rows,official_rows,allow_corrections=False)


def revise_final_rows(rows, official_rows):
    """Explicitly revise source counts, preserving complete prior rows and diffs.

    Game/team/player identities and final scores still must agree. This API is
    separate from enrichment so callers must deliberately opt into corrections.
    """
    return _reconcile_final_rows(rows,official_rows,allow_corrections=True)


def _reconcile_final_rows(rows,official_rows,*,allow_corrections):
    validate_history(rows)
    validate_history(official_rows)
    if not rows or not official_rows or len({r['game_id'] for r in rows+official_rows}) != 1:
        raise ValueError('Full box enrichment requires exactly one common final game')
    index = {(r['team_id'],r['player_id']):r for r in official_rows}
    played = lambda values: {(r['team_id'],r['player_id']) for r in values if r['status']=='played'}
    if played(rows) != played(official_rows):
        raise ValueError('Full box enrichment played player identities disagree')
    teams = {r['team_id'] for r in official_rows}
    if {r['team_id'] for r in rows} != teams:
        raise ValueError('Full box enrichment team identities disagree')
    for team_id in teams:
        if sum(r['pts'] for r in rows if r['team_id']==team_id) != sum(r['pts'] for r in official_rows if r['team_id']==team_id):
            raise ValueError('Full box revision final team scores disagree')
    listed = {r['team_id']:r.get('official_inactives_listed') for r in official_rows}
    enriched = []
    for original in rows:
        key = original['team_id'],original['player_id']
        if key not in index:
            if original['status'] != 'dnp':
                raise ValueError('Full box enrichment lacks an exact NBA player/team identity')
            # The official box lists every coach's-decision DNP, and once its inactive list is
            # published, everyone on the team that night. A DNP it leaves out came from another
            # source's stale roster (an ESPN DNP for a player who was on another team).
            if listed[original['team_id']] or re.search(r"coach.?s decision", str(original.get('participation_reason') or ''), re.I):
                continue
            retained=copy.deepcopy(original)
            retained['full_boxscore_enrichment']=dict(status='original_dnp_absent_from_official',
                original_source_url=original['source_url'],
                reason='Retained explicit source-backed DNP; no official player identity join was made')
            for field in (*OPTIONAL_COUNTS,'plus_minus'):
                if retained.get(field) is None:
                    retained[field]=0
            retained.update(shooting_percentages(retained))
            retained['official_inactives_listed']=listed[original['team_id']]
            enriched.append(retained)
            continue
        source = index[key]
        if (parse_time(original['game_time']) != parse_time(source['game_time'])
                or any(original[k] != source[k] for k in ('opponent_id','status'))):
            raise ValueError('Full box enrichment core counts or game identity disagree')
        changes = {field:dict(before=original[field],after=source[field]) for field in COUNTS
                   if original[field] != source[field]}
        for field in (*OPTIONAL_COUNTS,'plus_minus'):
            if original.get(field) is not None and source.get(field) is not None and original[field] != source[field]:
                changes[field]=dict(before=original[field],after=source[field])
        if changes and not allow_corrections:
            raise ValueError('Full box enrichment counts disagree with original evidence: '+', '.join(changes))
        precision = original.get('minutes_precision')
        tolerance = 1 if precision == 'whole_minutes' else .1 if precision == 'decimal_minutes' else 1e-6
        if abs(original['minutes']-source['minutes']) > tolerance:
            if not allow_corrections:
                raise ValueError('Full box enrichment minutes disagree beyond source precision')
            changes['minutes']=dict(before=original['minutes'],after=source['minutes'])
        row = copy.deepcopy(original)
        if allow_corrections:
            provenance=copy.deepcopy(original.get('source_provenance',{}))
            provenance.update(copy.deepcopy(source['source_provenance']))
            row.update(copy.deepcopy(source))
            row['source_provenance']=provenance
            row['official_final_revision']=dict(
                status='official_source_revision',changes=changes,
                previous_row=copy.deepcopy(original),
                basis='Official NBA full box after exact game, team, played-person, and final-score reconciliation')
        audit = dict(source_url=source['source_url'], source_sha256=source['source_sha256'],
                     retrieved_at=source['known_at'], identity_basis='exact NBA game/team/person IDs',
                     previous_known_at=original['known_at'], previous_minutes=original['minutes'],
                     previous_minutes_precision=precision,
                     previous_minutes_source_value=original.get('minutes_source_value'),
                     previous_optional_stat_errors=copy.deepcopy(original.get('optional_stat_errors',{})))
        for field in ('minutes','minutes_precision','minutes_source_value',*OPTIONAL_COUNTS,'plus_minus'):
            if source.get(field) is not None:
                row[field] = source[field]
                row.setdefault('optional_stat_errors',{}).pop(field,None)
            else:
                row.setdefault(field,None)
        row.update(shooting_percentages(row))
        row['known_at'] = max((original['known_at'],source['known_at']),key=parse_time)
        row.setdefault('source_provenance',{})['nba_full_boxscore'] = copy.deepcopy(source['source_provenance']['nba_full_boxscore'])
        row['full_boxscore_enrichment'] = audit
        row['minutes_source_url'] = source['source_url']
        for field in ('starter','official_inactives_listed'):
            if field in source:
                row[field] = source[field]
        enriched.append(row)
    # Players only the official box lists (inactive players, which ESPN's box omits) did not
    # play: the played identities agree above.
    originals = {(r['team_id'],r['player_id']) for r in rows}
    for source in official_rows:
        if (source['team_id'],source['player_id']) not in originals:
            row = copy.deepcopy(source)
            row['full_boxscore_enrichment'] = dict(status='official_dnp_absent_from_original',
                source_url=source['source_url'], source_sha256=source['source_sha256'], retrieved_at=source['known_at'],
                identity_basis='exact NBA game/team/person IDs')
            enriched.append(row)
    _share_official_team_metadata(enriched,rows+[r for r in official_rows if (r['team_id'],r['player_id']) not in originals],official_rows)
    validate_history(enriched)
    return enriched
