"""Game-specific, explicit no-injury reports; missing fields never mean healthy."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from .schedule import atomic_json,canonical
from .validation import parse_time

SUMMARY_PATTERN=r'https://site\.api\.espn\.com/apis/site/v2/sports/basketball/nba/summary\?event=(\d{6,12})'
BASIS='not_listed_verified_empty_report'
ESPN_BASIS='espn_injury_list'
# ESPN injury statuses; anything unrecognized is treated as questionable (no numeric forecast).
ESPN_STATUS={'out':'out','suspension':'out','day-to-day':'questionable','questionable':'questionable',
             'doubtful':'questionable','probable':'available'}


def _summary_team_lists(payload,game,season,evidence):
    """Each side's injury list from an identity-checked ESPN game summary: {side: list or None}."""
    from .reference import code
    match=re.fullmatch(SUMMARY_PATTERN,evidence['source_url'])
    if not match or not re.fullmatch('[a-f0-9]{64}',evidence['sha256']):raise ValueError('Unverified game-summary source')
    parse_time(evidence['retrieved_at'])
    header=payload['header'];competitions=header['competitions']
    if str(header['id'])!=match[1] or header['season']['year']!=int(season[:4])+1 or header['season']['type']!=2:
        raise ValueError('Injury summary event or regular-season identity differs')
    if len(competitions)!=1 or str(competitions[0]['id'])!=match[1] or parse_time(competitions[0]['date'])!=parse_time(game['start_time']):
        raise ValueError('Injury summary matchup date or tipoff differs')
    competitors=competitions[0]['competitors'];sides={c['homeAway']:c['team'] for c in competitors}
    if len(competitors)!=2 or set(sides)!={'home','away'} or any(code(sides[s]['abbreviation'])!=game[s+'_tricode'] for s in sides):
        raise ValueError('Injury summary home/away team identity differs')
    entries=payload.get('injuries');lists={}
    for side,team in sides.items():
        selected=[entry for entry in entries if str(entry.get('team',{}).get('id'))==str(team['id']) and code(entry.get('team',{}).get('abbreviation'))==game[side+'_tricode']] if isinstance(entries,list) else []
        lists[side]=selected[0].get('injuries') if len(selected)==1 and isinstance(selected[0].get('injuries'),list) else None
    return match[1],lists


def summary_no_injuries(payload,game,season,evidence):
    """An explicit empty list for an identity-checked game is an affirmative report."""
    event,lists=_summary_team_lists(payload,game,season,evidence)
    return [dict(game_id=game['game_id'],start_time=game['start_time'],team_id=game[side+'_team_id'],
                 provider_event_id=event,status='no_injuries_reported',**evidence)
            for side,items in lists.items() if items==[]]


def summary_injuries(payload,game,season,evidence):
    """ESPN's injury list for each team of an identity-checked game, including empty lists."""
    event,lists=_summary_team_lists(payload,game,season,evidence);reports=[]
    for side,items in lists.items():
        if items is None:continue
        players=[]
        for item in items:
            athlete=item.get('athlete') if isinstance(item,dict) else None
            name=(athlete or {}).get('fullName') or (athlete or {}).get('displayName')
            if not isinstance(name,str) or not name.strip():break  # An unreadable entry makes this team's list unusable.
            players.append(dict(name=name,espn_id=str(athlete.get('id','')),status=str(item.get('status') or '')))
        else:reports.append(dict(game_id=game['game_id'],start_time=game['start_time'],team_id=game[side+'_team_id'],
                            provider_event_id=event,status='espn_injury_list',players=players,**evidence))
    return reports


def official_coverage(snapshot,game,team):
    """A complete official report extraction covers this team for this matchup and Eastern date."""
    day=parse_time(game['start_time']).astimezone(ZoneInfo('America/New_York')).date().isoformat()
    matchup=str(game.get('away_tricode'))+'@'+str(game.get('home_tricode'))
    return any(e.get('pages_complete') and any(c['team_id']==team and c['date']==day and c['matchup']==matchup
               for c in e.get('complete_teams',[])) for e in snapshot.get('injury_extractions',[]))


def espn_list_current(report,snapshot,game,team):
    try:
        return (report['status']=='espn_injury_list' and isinstance(report['players'],list) and report['game_id']==game['game_id'] and
            report['team_id']==team and parse_time(report['start_time'])==parse_time(game['start_time']) and
            0<=(parse_time(snapshot['as_of'])-parse_time(report['retrieved_at'])).total_seconds()<=300 and
            parse_time(report['retrieved_at'])<parse_time(game['start_time']) and
            bool(re.fullmatch(SUMMARY_PATTERN,report['source_url'])) and bool(re.fullmatch('[a-f0-9]{64}',report['sha256'])))
    except (ValueError,KeyError,TypeError):return False


def apply_espn_injuries(snapshot,reports):
    """Until the official report covers a team, use ESPN's injury list for that game.

    Listed players take ESPN's status (unrecognized ones count as questionable);
    unlisted players are available, conditional on participation. A player name
    joins only when it identifies exactly one teammate. The official report
    replaces this as soon as it covers the team.
    """
    from .injury_parser import person_key
    out=deepcopy(snapshot);used=[]
    for game in out['games']:
        for team in (game['home_team_id'],game['away_team_id']):
            members=[p for p in out['roster']['players'] if p['team_id']==team]
            matching=[r for r in reports if espn_list_current(r,out,game,team)]
            if official_coverage(out,game,team) or len(matching)!=1 or not any(p['status']=='unknown' for p in members):continue
            report=matching[0];by_key={}
            for p in members:by_key.setdefault(person_key(p['name']),[]).append(p)
            listed={};unmatched=[]
            for entry in report['players']:
                found=by_key.get(person_key(entry['name']),[])
                if len(found)==1:listed[found[0]['player_id']]=entry
                else:unmatched.append(entry['name'])
            for p in members:
                if p['status']!='unknown' or p.get('status_basis')=='sourced_review':continue
                entry=listed.get(p['player_id'])
                status=ESPN_STATUS.get(entry['status'].strip().casefold(),'questionable') if entry else 'available'
                p.update(status=status,reported_status=entry['status'] if entry else 'Not listed',status_basis=ESPN_BASIS,
                         status_source_url=report['source_url'],status_observed_at=report['retrieved_at'],status_game_id=game['game_id'])
                p.pop('status_published_at',None)  # No provider publication time; keep actual retrieval only.
            used.append(dict(game_id=game['game_id'],team_id=team,source_url=report['source_url'],
                             retrieved_at=report['retrieved_at'],unmatched_players=unmatched))
    out['espn_injury_fallbacks']=used
    return out


def needs_empty_report(snapshot,game,team):
    """Only a clean absent team block can be supplemented, never parsing failures."""
    day=parse_time(game['start_time']).astimezone(ZoneInfo('America/New_York')).date().isoformat()
    matchup=game['away_tricode']+'@'+game['home_tricode']
    extractions=snapshot.get('injury_extractions',[])
    if not extractions:return False
    latest=max(extractions,key=lambda e:parse_time(e['evidence']['published_at']))
    coverage=latest.get('team_coverage',[])+latest.get('complete_teams',[])
    if not latest.get('pages_complete') or not latest.get('heading_matches') or not any(c['date']==day for c in coverage):return False
    if any(c['team_id']==team and c['date']==day and c['matchup']==matchup for c in coverage):return False
    if any(r['game_id']==game['game_id'] and r['team_id']==team for r in latest.get('rows',[])):return False
    for issue in latest.get('issues',[]):
        if (issue.get('team_id') in (None,team) and issue.get('date',day)==day and issue.get('matchup',matchup)==matchup
            and issue.get('game_id',game['game_id'])==game['game_id'] and issue['kind']!='missing_team_coverage'):
            return False
    return True


def report_current(report,snapshot,game,team):
    try:
        return (report['status']=='no_injuries_reported' and report['game_id']==game['game_id'] and report['team_id']==team and
            parse_time(report['start_time'])==parse_time(game['start_time']) and
            0<=(parse_time(snapshot['as_of'])-parse_time(report['retrieved_at'])).total_seconds()<=300 and
            parse_time(report['retrieved_at'])<parse_time(game['start_time']) and
            bool(re.fullmatch(SUMMARY_PATTERN,report['source_url'])) and bool(re.fullmatch('[a-f0-9]{64}',report['sha256'])))
    except (ValueError,KeyError,TypeError):return False


def apply_no_injuries(snapshot,reports):
    out=deepcopy(snapshot);accepted=[]
    for game in out['games']:
        for team in (game['home_team_id'],game['away_team_id']):
            matching=[r for r in reports if report_current(r,out,game,team)]
            if len(matching)!=1 or not needs_empty_report(out,game,team):continue
            report=matching[0];accepted.append(report)
            for player in out['roster']['players']:
                if player['team_id']!=team or player['status']!='unknown' or player.get('status_basis')=='sourced_review':continue
                player.update(status='available',reported_status='Not listed',status_basis=BASIS,
                    status_source_url=report['source_url'],status_observed_at=report['retrieved_at'],status_game_id=game['game_id'])
                player.pop('status_published_at',None)  # No provider publication time; keep actual retrieval only.
    out['availability_supplements']=accepted
    return out


def record_availability_update(directory,snapshot,reports,sources):
    for game in snapshot['games']:
        root=Path(directory)/'availability'/game['game_id']
        items=[r for r in reports if r['game_id']==game['game_id'] and r['team_id'] in (game['home_team_id'],game['away_team_id'])
               and parse_time(r['start_time'])==parse_time(game['start_time']) and needs_empty_report(snapshot,game,r['team_id'])]
        if not items and not (root/'latest.json').exists():continue
        captured=max((r['retrieved_at'] for r in items),key=parse_time) if items else datetime.now(timezone.utc).isoformat()
        payload=dict(schema_version=1,game_id=game['game_id'],start_time=game['start_time'],captured_at=captured,
            status='verified_empty' if items else 'superseded',
            captured_after_tipoff=parse_time(captured)>=parse_time(game['start_time']),teams=items,sources=sources,
            policy='Explicit game-specific no-injury listing; participation and starting lineup remain unconfirmed. Saved forecasts retain original inputs.')
        digest=hashlib.sha256(canonical(payload)).hexdigest()
        archive=root/'records'/(digest+'.json')
        if not archive.exists():atomic_json(archive,payload)
        atomic_json(root/'latest.json',payload)
