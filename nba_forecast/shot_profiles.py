"""Reproducible observed court-zone shares for projected shot opportunities.

Zones are descriptive geometric bins, not official NBA chart classifications.
Only attempt allocation changes: scoring, shooting percentages, and calibration
remain those of the existing model. Unknown positions remain unknown.
"""
from collections import defaultdict
import copy
import hashlib
import json
import math
from pathlib import Path
import re

from .data import MAX_BYTES, boxscore_url
from .full_boxscore_sources import decode_official_game_page, full_boxscore_url
from .schedule import canonical
from .shots import COORDINATE_SYSTEM, normalize_page_shots, normalize_shots, playbyplay_url
from .validation import parse_time, validate_history


VERSION = 1
METHOD = 'Observed player zone shares within two-point and three-point attempts; descriptive geometry, no scoring adjustment.'
SHOT_KEYS = ('game_id','action_number','player_id','team_id','period','clock','points','made',
             'x','y','coordinate_missing_reason')
COUNT_FIELDS = ('fga','fgm','fg3a','fg3m')
LABELS = {
    'near_basket_2pt':'Near basket (within 4 feet)',
    'paint_2pt':'Paint beyond 4 feet',
    'midrange_2pt':'Other two-point locations',
    'left_corner_3pt':'Left corner three-point locations',
    'right_corner_3pt':'Right corner three-point locations',
    'above_break_3pt':'Other three-point locations',
    'long_range_3pt':'Three-point locations beyond 35 feet',
    'unlocated_2pt':'Two-point attempts with missing location',
    'unlocated_3pt':'Three-point attempts with missing location',
    'unresolved_2pt':'Two-point locations unsupported by observed attempts',
    'unresolved_3pt':'Three-point locations unsupported by observed attempts',
}


def _context(snapshot,history):
    cutoff=parse_time(snapshot['as_of'])
    year=int(snapshot['season'][:4])
    seasons={snapshot['season'],f'{year-1}-{str(year)[-2:]}'}
    excluded=set(snapshot.get('model_enhancements',{}).get('excluded_history_game_ids',[]))
    excluded.update(snapshot.get('excluded_history_game_ids',[]))
    future={g['game_id'] for g in snapshot['games']}
    teams={str(g[side+'_team_id']) for g in snapshot['games'] if g['season_type']=='Regular Season'
           for side in ('home','away')}
    roster={p['player_id']:str(p['team_id']) for p in snapshot['roster']['players'] if str(p['team_id']) in teams}
    latest={}
    for row in history:
        if (row['season_type']!='Regular Season' or row['season'] not in seasons
                or row['game_id'] in excluded or row['game_id'] in future
                or parse_time(row['game_time'])>=cutoff or parse_time(row['known_at'])>cutoff):
            continue
        key=row['game_id'],row['player_id']
        prior=latest.get(key)
        if prior and parse_time(prior['known_at'])==parse_time(row['known_at']):
            if any(prior[k]!=row[k] for k in ('team_id','opponent_id','game_time','status',*COUNT_FIELDS)):
                raise ValueError('Ambiguous same-time actuals for shot profile reconciliation')
        if prior is None or parse_time(prior['known_at'])<parse_time(row['known_at']):
            latest[key]=row
    validate_history(list(latest.values()))
    games=defaultdict(list)
    for row in latest.values():
        games[row['game_id']].append(row)
    return dict(cutoff=cutoff,seasons=seasons,excluded=excluded,future=future,roster=roster,history=games)


def _source_times(source):
    values=[source['known_at']]
    values.extend(p['retrieved_at'] for p in source.get('provenance',[]))
    values.extend(s['known_at'] for s in source.get('shots',[]))
    values.extend(s['known_at'] for s in source.get('team_attempts',[]))
    values.extend(r['known_at'] for r in source.get('paired_final_rows',[]))
    return [parse_time(value) for value in values]


def _eligible(source,context):
    gid=source['game_id']
    return (not source.get('missing_reason') and source.get('season') in context['seasons']
            and gid not in context['excluded'] and gid not in context['future']
            and parse_time(source['game_time'])<context['cutoff']
            and all(t<=context['cutoff'] for t in _source_times(source)))


def _read_raw(directory,metadata,gid):
    digest=metadata['sha256']
    if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest):
        raise ValueError('Shot raw evidence hash is invalid')
    url=metadata['source_url']
    if url==full_boxscore_url(gid):suffix='.html'
    elif url in (playbyplay_url(gid),boxscore_url(gid)):suffix='.json'
    else:raise ValueError('Shot raw source URL does not match this NBA game')
    path=Path(directory)/'raw'/(digest+suffix)
    raw=path.read_bytes()
    if len(raw)>MAX_BYTES or hashlib.sha256(raw).hexdigest()!=digest:
        raise ValueError('Shot raw archive hash mismatch')
    archived=json.loads(path.with_suffix('.provenance.json').read_bytes())
    if (archived['source_url']!=url or archived['sha256']!=digest
            or parse_time(archived['retrieved_at'])>parse_time(metadata['retrieved_at'])):
        raise ValueError('Shot raw provenance differs or observation predates the archive')
    if archived.get('bytes',len(raw))!=len(raw):
        raise ValueError('Shot raw archive byte count differs')
    manifest=dict(source_url=url,sha256=digest,retrieved_at=metadata['retrieved_at'],
                  raw_path='raw/'+path.name,first_observed_at=archived['retrieved_at'])
    return raw,manifest


def _shot_signature(shots):
    return sorted(({key:s.get(key) for key in SHOT_KEYS} for s in shots),
                  key=lambda s:(s['game_id'],s['action_number']))


def _replay(directory,source,history,context):
    """Reproduce locations and counts from byte-verified NBA raw sources."""
    gid=source['game_id']
    if not re.fullmatch(r'002\d{7}',gid) or not _eligible(source,context):
        raise ValueError('Shot profile source is outside the eligible historical cutoff')
    if not history:
        raise ValueError('Shot source has no eligible final actuals for reconciliation')
    if any(parse_time(r['game_time'])!=parse_time(source['game_time']) for r in history):
        raise ValueError('Shot source and actual game times disagree')
    metadata=source.get('provenance')
    if not isinstance(metadata,list) or not metadata:
        raise ValueError('Shot source lacks raw provenance for replay')
    raw_sources={}
    manifests=[]
    for item in metadata:
        if item['source_url'] in raw_sources:
            raise ValueError('Duplicate shot raw source')
        raw,manifest=_read_raw(directory,item,gid)
        raw_sources[item['source_url']]=(raw,item)
        manifests.append(manifest)
    if parse_time(source['known_at'])<max(parse_time(m['retrieved_at']) for m in manifests):
        raise ValueError('Shot source observation predates its raw evidence')
    page_url=full_boxscore_url(gid)
    if page_url in raw_sources:
        if len(raw_sources)!=1:
            raise ValueError('Official shot page replay requires one unambiguous raw source')
        raw,meta=raw_sources[page_url]
        # Home/away are explicitly identified by the official page. History
        # independently binds the game, tipoff, person, team, and opponent IDs.
        from .data import _NextDataParser
        parser=_NextDataParser();parser.feed(raw.decode('utf-8'))
        embedded=json.loads(''.join(parser.parts))['props']['pageProps']['game']
        game=dict(game_id=gid,start_time=history[0]['game_time'],season_type='Regular Season',
                  home_team_id=str(embedded['homeTeam']['teamId']),away_team_id=str(embedded['awayTeam']['teamId']))
        pairs={(r['team_id'],r['opponent_id']) for r in history}
        if pairs!={(game['home_team_id'],game['away_team_id']),(game['away_team_id'],game['home_team_id'])}:
            raise ValueError('Shot source and actual team/opponent identities disagree')
        page=decode_official_game_page(raw,game,meta)
        replay=normalize_page_shots(page,game,meta,final_rows=history)
    else:
        pbp_url,box_url=playbyplay_url(gid),boxscore_url(gid)
        if set(raw_sources)!={pbp_url,box_url}:
            raise ValueError('Shot replay requires paired official play-by-play and final boxscore')
        raw,pbp_meta=raw_sources[pbp_url];boxraw,box_meta=raw_sources[box_url]
        known=max((pbp_meta['retrieved_at'],box_meta['retrieved_at']),key=parse_time)
        box=json.loads(boxraw)
        home,away=(str(box['game'][side+'Team']['teamId']) for side in ('home','away'))
        if {(r['team_id'],r['opponent_id']) for r in history}!={(home,away),(away,home)}:
            raise ValueError('Shot source and actual team/opponent identities disagree')
        replay=normalize_shots(json.loads(raw),box,known,final_rows=history)
    if source['season']!=replay['season'] or any(r['season']!=replay['season'] for r in history):
        raise ValueError('Shot source season differs from raw replay or final actuals')
    if source.get('coordinate_system')!=COORDINATE_SYSTEM or source.get('source_url')!=replay['source_url']:
        raise ValueError('Shot source coordinate system or URL differs from raw replay')
    if canonical(_shot_signature(source['shots']))!=canonical(_shot_signature(replay['shots'])):
        raise ValueError('Shot locations or outcomes differ from raw source replay')
    supplied=source.get('coverage',{})
    if supplied.get('reconciled') is not True:
        raise ValueError('Shot source does not assert reconciled coverage')
    for key in ('attempts','located_attempts'):
        if supplied.get(key)!=replay['coverage'][key]:
            raise ValueError('Shot coverage counts differ from raw source replay')
    if 'players' in supplied:
        order=lambda rows:sorted(rows,key=lambda r:(r['team_id'],r['player_id']))
        if canonical(order(supplied['players']))!=canonical(order(replay['coverage']['players'])):
            raise ValueError('Shot player coverage differs from raw source replay')
    for shot in source['shots']:
        if shot['source_url']!=source['source_url'] or parse_time(shot['known_at'])>parse_time(source['known_at']):
            raise ValueError('Shot action source or observation time differs from its game')
        if shot.get('source_sha256') not in (None,*[m['sha256'] for m in manifests]):
            raise ValueError('Shot action hash differs from its raw source')
    known=max([source['known_at']]+[r['known_at'] for r in history],key=parse_time)
    return replay,sorted(manifests,key=lambda m:m['source_url']),known


def _zone(shot):
    points=shot['points'];x,y=shot['x'],shot['y']
    if x is None or y is None:return f'unlocated_{points}pt'
    distance=math.hypot(x,y)
    if points==2:
        if distance<=40:return 'near_basket_2pt'
        if abs(x)<=80 and -52.5<=y<=137.5:return 'paint_2pt'
        return 'midrange_2pt'
    if abs(x)>=220 and y<=87.5:return 'left_corner_3pt' if x<0 else 'right_corner_3pt'
    return 'long_range_3pt' if distance>350 else 'above_break_3pt'


def _assemble(snapshot,context,records):
    players={pid:dict(player_id=pid,team_id=team,observed_attempts=0,observed_makes=0,
                     games=0,game_ids=[],located_attempts=0,zones={}) for pid,team in context['roster'].items()}
    sources=[]
    for manifest,replay in records:
        sources.append(manifest)
        for covered in replay['coverage']['players']:
            pid=covered['player_id']
            if pid in players:
                players[pid]['games']+=1
                players[pid]['game_ids'].append(manifest['game_id'])
        for shot in replay['shots']:
            if shot['player_id'] not in players:continue
            player=players[shot['player_id']]
            name=_zone(shot)
            zone=player['zones'].setdefault(name,dict(zone=name,label=LABELS[name],points=shot['points'],
                observed_attempts=0,observed_makes=0,located_attempts=0))
            for target in (player,zone):
                target['observed_attempts']+=1
                target['observed_makes']+=int(shot['made'])
                target['located_attempts']+=int(shot['x'] is not None and shot['y'] is not None)
    result=[]
    for pid in sorted(players):
        player=players[pid];player['zones']=[player['zones'][k] for k in sorted(player['zones'])]
        player['game_ids'].sort()
        result.append(player)
    return dict(schema_version=VERSION,prepared_at=snapshot['as_of'],season=snapshot['season'],method=METHOD,
                coordinate_system=COORDINATE_SYSTEM,sources=sorted(sources,key=lambda s:s['game_id']),players=result,
                coverage=dict(source_games=len(sources),roster_players=len(result),
                    players_with_games=sum(p['games']>0 for p in result),
                    observed_attempts=sum(p['observed_attempts'] for p in result),
                    located_attempts=sum(p['located_attempts'] for p in result)))


def _manifest(source,digest,raw_sources,known):
    return dict(game_id=source['game_id'],sha256=digest,known_at=known,
                game_time=source['game_time'],source_known_at=source['known_at'],raw_sources=raw_sources)


def _prepared_snapshot(snapshot,supplied):
    if type(supplied.get('schema_version')) is not int or supplied['schema_version']!=VERSION:
        raise ValueError('Unsupported shot profile version')
    age=(parse_time(snapshot['as_of'])-parse_time(supplied['prepared_at'])).total_seconds()
    if not 0<=age<=300:
        raise ValueError('Prepared shot profiles are stale or future-known')
    # Preflight may advance the clock while fetching independent references.
    # Reproduce only facts eligible at capture, even inside this short window.
    return {**snapshot,'as_of':supplied['prepared_at']}


def prepare_shot_profiles(directory,snapshot,history):
    """Capture successful eligible shot collections in immutable evidence files."""
    context=_context(snapshot,history);records=[]
    for path in sorted((Path(directory)/'shots').glob('*.json')):
        raw=path.read_bytes();source=json.loads(raw)
        if source.get('missing_reason') or not _eligible(source,context):continue
        gid=source['game_id']
        if path.stem!=gid:raise ValueError('Shot collection filename and game identity disagree')
        rows=context['history'].get(gid,[])
        if not any(r['player_id'] in context['roster'] for r in rows):continue
        replay,raw_sources,known=_replay(directory,source,rows,context)
        digest=hashlib.sha256(raw).hexdigest()
        archived=Path(directory)/'shot-evidence'/(digest+'.json')
        archived.parent.mkdir(parents=True,exist_ok=True)
        if archived.exists():
            if archived.read_bytes()!=raw:raise ValueError('Immutable shot evidence archive hash differs')
        else:
            with archived.open('xb') as stream:stream.write(raw)
        records.append((_manifest(source,digest,raw_sources,known),replay))
    return _assemble(snapshot,context,records)


def verify_shot_profiles(snapshot,history,directory):
    """Rebuild captured input from immutable files and replay its raw evidence."""
    supplied=snapshot.get('shot_profiles')
    if supplied is None:return
    prepared_snapshot=_prepared_snapshot(snapshot,supplied)
    context=_context(prepared_snapshot,history);records=[];seen=set()
    for manifest in supplied['sources']:
        gid=manifest['game_id'];digest=manifest['sha256']
        if gid in seen:raise ValueError('Duplicate shot profile source game')
        seen.add(gid)
        if (not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest)
                or parse_time(manifest['known_at'])>context['cutoff']):
            raise ValueError('Shot profile source hash or cutoff is invalid')
        raw=(Path(directory)/'shot-evidence'/(digest+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Shot evidence archive hash mismatch')
        source=json.loads(raw)
        if source['game_id']!=gid:raise ValueError('Shot profile source game differs from archive')
        rows=context['history'].get(gid,[])
        if not any(r['player_id'] in context['roster'] for r in rows):
            raise ValueError('Shot profile source has no eligible slate player history')
        replay,raw_sources,known=_replay(directory,source,rows,context)
        records.append((_manifest(source,digest,raw_sources,known),replay))
    expected=_assemble(prepared_snapshot,context,records)
    if canonical(supplied)!=canonical(expected):
        raise ValueError('Supplied shot profiles differ from archived source replay')


def apply_shot_profiles(result,snapshot):
    """Allocate existing expected 2PA/3PA; leave every scoring estimate unchanged."""
    supplied=snapshot.get('shot_profiles')
    if supplied is None:return result
    _prepared_snapshot(snapshot,supplied)
    profiles={p['player_id']:p for p in supplied['players']}
    output=copy.deepcopy(result)
    for row in output['predictions']:
        row['modeled_shot_profile']=None
        if row['status'] not in ('projected','out'):continue
        observed=profiles.get(row['player_id'])
        if observed is None or observed['team_id']!=row['team_id']:
            raise ValueError('Projected player lacks a matching prepared shot profile')
        fga,three=row['expected_fga'],row['expected_fg3a']
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (fga,three)) or not 0<=three<=fga:
            raise ValueError('Projected shot opportunities are invalid')
        zones=[]
        for points,total in ((2,fga-three),(3,three)):
            observed_zones=[z for z in observed['zones'] if z['points']==points]
            attempts=sum(z['observed_attempts'] for z in observed_zones)
            if not attempts:
                key=f'unresolved_{points}pt'
                zones.append(dict(zone=key,label=LABELS[key],points=points,observed_attempts=0,
                    observed_makes=0,located_attempts=0,projected_attempts=total))
                continue
            remaining=total
            for index,zone in enumerate(observed_zones):
                projected=(remaining if index==len(observed_zones)-1
                           else total*zone['observed_attempts']/attempts)
                zones.append({**zone,'projected_attempts':projected})
                remaining-=projected
        row['modeled_shot_profile']={**copy.deepcopy(observed),'method':METHOD,'zones':zones,
            'source_games':list(observed['game_ids']),
            'scoring_changed':False,'projected_attempts':fga,'projected_three_attempts':three}
    return output
