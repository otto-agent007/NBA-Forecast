"""A deterministic, fail-closed gate; consistency checks are not lineup confirmation."""
from datetime import datetime,timezone
from copy import deepcopy
import hashlib
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from .injury_parser import normalized,pdf_text,parse_report,PARSER_VERSION
from .injuries import report_links,REPORT_PATTERN
from .reference import collect_references
from .rotation import projected_lineups
from .schedule import atomic_json,canonical
from .validation import parse_time,validate_inputs
from .availability import apply_no_injuries,needs_empty_report,report_current,record_availability_update,BASIS
from .roster_review import apply_roster_reviews,resolution
from .historical import verify_priors
from .enhancements import verify_enhancements
from .shot_profiles import verify_shot_profiles
import json

PREFLIGHT_VERSION='independent-inputs-6'


def validate_extraction(extraction,cutoff):
    evidence=extraction['evidence'];url=evidence['source_url']
    if extraction.get('parser_version')!=PARSER_VERSION:raise ValueError('Injury extraction parser version is unverified')
    if not re.fullmatch(REPORT_PATTERN,url) or not re.fullmatch('[a-f0-9]{64}',evidence['sha256']):
        raise ValueError('Injury extraction requires an official source URL and SHA256 hash')
    published=parse_time(evidence['published_at']);retrieved=parse_time(evidence['retrieved_at'])
    if not published<=retrieved<=cutoff or (cutoff-published).total_seconds()>86400:
        raise ValueError('Injury extraction is stale or future-known')
    links=report_links(url,cutoff.isoformat())
    if len(links)!=1 or parse_time(links[0]['published_at'])!=published:
        raise ValueError('Injury publication time disagrees with its official URL')


def verify_extractions(snapshot,cache_dir):
    """Reproduce coverage/status joins from archived bytes, not supplied labels."""
    verified=[];texts={};ids={g['game_id'] for g in snapshot['games']}
    targets={(parse_time(g['start_time']).astimezone(ZoneInfo('America/New_York')).date().isoformat(),
              str(g.get('away_tricode'))+'@'+str(g.get('home_tricode'))) for g in snapshot['games']}
    def relevant(extraction):
        return dict(pages_complete=extraction.get('pages_complete'),
            complete_teams=sorted([c for c in extraction.get('complete_teams',[]) if (c['date'],c['matchup']) in targets],key=canonical),
            rows=sorted([r for r in extraction.get('rows',[]) if r['game_id'] in ids],key=canonical))
    for extraction in snapshot.get('injury_extractions',[]):
        validate_extraction(extraction,datetime.now(timezone.utc))
        evidence=extraction['evidence'];sha=evidence['sha256']
        if sha not in texts:texts[sha]=pdf_text(Path(cache_dir)/(sha+'.pdf'),sha)
        rebuilt=parse_report(texts[sha],{'games':snapshot['games']},snapshot['roster'],evidence)
        if relevant(rebuilt)!=relevant(extraction):raise ValueError('Supplied injury coverage or statuses differ from the archived PDF')
        verified.append(rebuilt)
    return verified


def check_inputs(snapshot,history,profiles,reference):
    issues=[];warnings=[]
    def issue(code,detail,team=None):issues.append(dict(code=code,detail=detail,team_id=team))
    try:validate_inputs(snapshot,history)
    except (ValueError,KeyError,TypeError) as exc:issue('input_contract',str(exc))
    cutoff=parse_time(snapshot['as_of'])
    extractions=[]
    for extraction in snapshot.get('injury_extractions',[]):
        try:
            validate_extraction(extraction,cutoff);extractions.append(extraction)
        except (ValueError,KeyError,TypeError) as exc:issue('injury_provenance_invalid',str(exc))
    for source in reference.get('sources',[]):
        age=(cutoff-parse_time(source['retrieved_at'])).total_seconds()
        max_age=300 if any(path in source.get('source_url','') for path in ('/scoreboard?','/summary?')) else 900
        if not 0<=age<=max_age:issue('reference_time_invalid','Independent source is future-known or exceeds its freshness limit.')
    if not reference.get('sources'):issue('reference_missing','Independent source evidence is unavailable.')
    lineups=projected_lineups(snapshot['roster']['players'],profiles['players'])
    for game in snapshot['games']:
        pair=(game.get('home_tricode'),game.get('away_tricode'))
        matched=[g for g in reference.get('games',[]) if (g['home'],g['away'])==pair and parse_time(g['start_time'])==parse_time(game['start_time'])]
        if len(matched)!=1 or matched[0].get('state')!='pre' or matched[0].get('status_name') not in (None,'STATUS_SCHEDULED'):
            issue('matchup_not_confirmed','Independent scoreboard does not confirm this unstarted matchup and tipoff.')
        day=parse_time(game['start_time']).astimezone(ZoneInfo('America/New_York')).date().isoformat()
        matchup=str(game.get('away_tricode'))+'@'+str(game.get('home_tricode'))
        for tid in (game['home_team_id'],game['away_team_id']):
            players=[p for p in snapshot['roster']['players'] if p['team_id']==tid]
            expected={normalized(p['name']) for p in players};names=reference.get('rosters',{}).get(tid,[])
            observed={normalized(name) for name in names}
            if not names or expected!=observed or len(observed)!=len(names):
                adjudication=resolution(snapshot,profiles,reference,game,tid)
                quarantined=adjudication and all(p['status']=='unknown' and p.get('status_basis')=='roster_membership_review' for p in players if p['player_id'] in adjudication['quarantine_player_ids'])
                missing=[p['name'] for p in players if normalized(p['name']) not in observed]
                extra=[name for name in names if normalized(name) not in expected]
                detail='Independent roster membership differs or is unavailable.'
                if missing:detail+=' NBA only: '+', '.join(missing[:5])+'.'
                if extra:detail+=' ESPN only: '+', '.join(extra[:5])+'.'
                if quarantined:warnings.append(adjudication)
                else:issue('roster_not_confirmed',detail,tid)
            covering=[e for e in extractions if e.get('pages_complete') and any(c['team_id']==tid and c['date']==day and c['matchup']==matchup for c in e.get('complete_teams',[]))]
            if not covering:
                empty=[r for r in reference.get('no_injuries',[]) if report_current(r,snapshot,game,tid)]
                if len(empty)!=1 or not needs_empty_report(snapshot,game,tid):
                    issue('injury_coverage_missing','No complete official report or verified game-specific zero-injury report covers this team/date.',tid)
                else:
                    report=empty[0]
                    for player in players:
                        if player.get('status_basis')!=BASIS:continue
                        if player['status']!='available' or player.get('status_source_url')!=report['source_url'] or player.get('status_observed_at')!=report['retrieved_at']:
                            issue('availability_conflict','Conditional zero-injury availability differs from its verified source.',tid)
            else:
                latest=max(covering,key=lambda e:parse_time(e['evidence']['published_at']))
                statuses={r['player_id']:r['status'] for r in latest.get('rows',[]) if r['game_id']==game['game_id']}
                for player in players:
                    if player['status']=='unknown' or player['status']==statuses.get(player['player_id'],'available'):continue
                    review=snapshot.get('review_input',{})
                    reviewed=player.get('status_basis')=='sourced_review' and review.get('game_id')==game['game_id'] and any(
                        u['player_id']==player['player_id'] and all(u.get(k)==player.get(k) for k in ('status','status_source_url','status_observed_at')) and
                        u.get('status_published_at',u['status_observed_at'])==player.get('status_published_at',player['status_observed_at']) and
                        parse_time(u.get('status_published_at',u['status_observed_at']))>=parse_time(latest['evidence']['published_at'])
                        for u in review.get('availability',[]))
                    if not reviewed:issue('availability_conflict',player['name']+': applied availability conflicts with the official report and has no matching newer review.',tid)
            ids=lineups[tid]['player_ids'];members={p['player_id'] for p in players if p['status']!='out'}
            if len(ids)!=5 or len(set(ids))!=5 or not set(ids)<=members:
                issue('starting_five_unresolved','Five distinct eligible projected starters cannot be established.',tid)
    return dict(schema_version=1,version=PREFLIGHT_VERSION,checked_at=snapshot['as_of'],
                status='blocked' if issues else 'passed',issues=issues,warnings=warnings,lineups=lineups,sources=reference.get('sources',[]),
                policy='Inputs cross-checked; projected starters remain unconfirmed. No missing availability is inferred healthy.')


def run_preflight(snapshot,history,profiles,directory,*,cache_dir=None):
    phase='reference_unavailable'
    reference={}
    try:
        phase='model_archive_unverified'
        verify_enhancements(snapshot,directory)
        phase='shot_archive_unverified'
        verify_shot_profiles(snapshot,history,directory)
        phase='injury_archive_unverified'
        verify_priors(snapshot,directory)
        verified=verify_extractions(snapshot,cache_dir or Path(directory)/'raw')
        checked=deepcopy(snapshot);checked['injury_extractions']=verified
        phase='reference_unavailable'
        reviews=[]
        for game in checked['games']:
            path=Path(directory)/'roster-reviews'/(game['game_id']+'.json')
            if path.exists():reviews.extend(json.loads(path.read_text()))
        checked['roster_reviews']=reviews
        reference=collect_references(checked,Path(directory)/'references',roster_reviews=reviews)
        snapshot['as_of']=datetime.now(timezone.utc).isoformat()
        checked['as_of']=snapshot['as_of']
        checked=apply_no_injuries(checked,reference.get('no_injuries',[]))
        checked=apply_roster_reviews(checked,profiles,reference)
        snapshot['roster']=deepcopy(checked['roster'])
        snapshot['availability_supplements']=deepcopy(checked['availability_supplements'])
        snapshot['roster_reviews']=deepcopy(reviews);snapshot['roster_adjudications']=deepcopy(checked['roster_adjudications'])
        record_availability_update(directory,checked,reference.get('no_injuries',[]),reference.get('sources',[]))
        result=check_inputs(checked,history,profiles,reference)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        result=dict(schema_version=1,version=PREFLIGHT_VERSION,status='blocked',checked_at=datetime.now(timezone.utc).isoformat(),
                    issues=[dict(code=phase,detail=str(exc),team_id=None)],lineups={},sources=reference.get('sources',[]))
        record_availability_update(directory,snapshot,[],reference.get('sources',[]))
    digest=hashlib.sha256(canonical(result)).hexdigest()
    for game in snapshot['games']:
        root=Path(directory)/'preflight'/game['game_id']
        payload={**result,'game_id':game['game_id'],'start_time':game['start_time']}
        archive=root/'records'/(digest+'.json')
        if not archive.exists():atomic_json(archive,payload)
        atomic_json(root/'latest.json',payload)
    return result
