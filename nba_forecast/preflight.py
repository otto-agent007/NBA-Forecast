"""A deterministic, fail-closed gate; consistency checks are not lineup confirmation."""
from datetime import datetime,timezone
from copy import deepcopy
import hashlib
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from .injury_parser import normalized,person_key,pdf_text,parse_report,PARSER_VERSION
from .injuries import report_links,REPORT_PATTERN
from .reference import collect_references
from .rotation import projected_lineups
from .schedule import atomic_json,canonical
from .validation import parse_time,validate_inputs
from .availability import (apply_no_injuries,apply_espn_injuries,espn_list_current,needs_empty_report,official_coverage,
                           report_current,record_availability_update,BASIS,ESPN_BASIS)
from .roster_review import apply_roster_reviews,resolution
from .historical import verify_priors
from .enhancements import verify_enhancements
from .shot_profiles import verify_shot_profiles
from .model_versions import uses_refinements
import json

PREFLIGHT_VERSION='independent-inputs-8'


def _same_people(nba,espn):
    """Both rosters name the same people once generational suffixes are ignored.

    Providers disagree on suffixes; that alone must not block a forecast. It
    counts only when every name stays distinct on both sides without them.
    """
    keys,seen={person_key(n) for n in nba},{person_key(n) for n in espn}
    return keys==seen and len(keys)==len(nba) and len(seen)==len(espn)


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
    """Block only when a forecast would be wrong; everything else is a visible note.

    Blocking: invalid inputs, applied availability contradicting its source, a
    game ESPN shows as started or postponed, or a changed tipoff. Provider
    disagreements, missing independent evidence and pending reports are notes.
    """
    issues=[];warnings=[];suffixes=[];notes=[]
    def issue(code,detail,team=None):issues.append(dict(code=code,detail=detail,team_id=team))
    def note(code,detail,team=None,**extra):notes.append(dict(code=code,detail=detail,team_id=team,**extra))
    try:validate_inputs(snapshot,history)
    except (ValueError,KeyError,TypeError) as exc:issue('input_contract',str(exc))
    cutoff=parse_time(snapshot['as_of'])
    extractions=[]
    for extraction in snapshot.get('injury_extractions',[]):
        try:
            validate_extraction(extraction,cutoff);extractions.append(extraction)
        except (ValueError,KeyError,TypeError) as exc:issue('injury_provenance_invalid',str(exc))  # Corrupt or forged evidence.
    for source in reference.get('sources',[]):
        age=(cutoff-parse_time(source['retrieved_at'])).total_seconds()
        max_age=300 if any(path in source.get('source_url','') for path in ('/scoreboard?','/summary?')) else 900
        if not 0<=age<=max_age:note('reference_stale','An ESPN cross-check source is older than its freshness limit: '+source.get('source_url',''))
    if not reference.get('sources'):note('reference_missing','ESPN cross-check evidence is unavailable; NBA sources are used alone.')
    for failure in reference.get('failures',[]):
        note('reference_partial','ESPN '+failure['kind'].replace('_',' ')+' unavailable: '+failure['error'],failure.get('team_id'))
    lineups=projected_lineups(snapshot['roster']['players'],profiles['players'])
    for game in snapshot['games']:
        pair=(game.get('home_tricode'),game.get('away_tricode'))
        paired=[g for g in reference.get('games',[]) if (g['home'],g['away'])==pair]
        matched=[g for g in paired if parse_time(g['start_time'])==parse_time(game['start_time'])]
        if len(matched)==1 and (matched[0].get('state')!='pre' or matched[0].get('status_name') not in (None,'STATUS_SCHEDULED')):
            issue('matchup_not_scheduled','ESPN shows this game as '+str(matched[0].get('status_name') or matched[0].get('state'))+', not an upcoming game.')
        elif not matched and len(paired)==1:
            issue('tipoff_changed','ESPN lists this matchup at '+str(paired[0]['start_time'])+'; the schedule will be rechecked.')
        elif len(matched)!=1:
            note('matchup_unconfirmed','ESPN does not independently confirm this matchup and tipoff.')
        for tid in (game['home_team_id'],game['away_team_id']):
            players=[p for p in snapshot['roster']['players'] if p['team_id']==tid]
            expected={normalized(p['name']) for p in players};names=reference.get('rosters',{}).get(tid,[])
            observed={normalized(name) for name in names}
            if names and expected!=observed and len(observed)==len(names) and _same_people([p['name'] for p in players],names):
                suffixes.append(dict(team_id=tid,nba=sorted(p['name'] for p in players if normalized(p['name']) not in observed),
                                     espn=sorted(name for name in names if normalized(name) not in expected)))
            elif names and (expected!=observed or len(observed)!=len(names)):
                # The official NBA roster is authoritative; availability comes from injury reports.
                adjudication=resolution(snapshot,profiles,reference,game,tid)
                quarantined=adjudication and all(p['status']=='unknown' and p.get('status_basis')=='roster_membership_review' for p in players if p['player_id'] in adjudication['quarantine_player_ids'])
                if quarantined:warnings.append(adjudication)
                else:
                    keys={person_key(name) for name in names};own={person_key(p['name']) for p in players}
                    missing=[p['name'] for p in players if person_key(p['name']) not in keys]
                    extra=[name for name in names if person_key(name) not in own]
                    if not missing and not extra:  # Names collide without suffixes; list exact spellings.
                        missing=[p['name'] for p in players if normalized(p['name']) not in observed]
                        extra=[name for name in names if normalized(name) not in expected]
                    detail='ESPN\'s roster differs from the official NBA roster.'
                    if missing:detail+=' Not on ESPN\'s roster: '+', '.join(missing)+'.'
                    if extra:detail+=' Not on the NBA roster (no forecast): '+', '.join(extra)+'.'
                    note('roster_difference',detail,tid,nba_only=missing,espn_only=extra)
            elif not names and reference.get('sources'):
                note('roster_unconfirmed','ESPN\'s roster for this team was unavailable; the official NBA roster is used.',tid)
            if not official_coverage({'injury_extractions':extractions},game,tid):
                empty=[r for r in reference.get('no_injuries',[]) if report_current(r,snapshot,game,tid)]
                fallback=[r for r in reference.get('espn_injuries',[]) if espn_list_current(r,snapshot,game,tid)]
                if len(empty)==1 and needs_empty_report(snapshot,game,tid):
                    report=empty[0]
                    for player in players:
                        if player.get('status_basis')!=BASIS:continue
                        if player['status']!='available' or player.get('status_source_url')!=report['source_url'] or player.get('status_observed_at')!=report['retrieved_at']:
                            issue('availability_conflict','Conditional zero-injury availability differs from its verified source.',tid)
                elif len(fallback)==1 and any(p.get('status_basis')==ESPN_BASIS for p in players):
                    report=fallback[0]
                    for player in players:
                        if player.get('status_basis')==ESPN_BASIS and (player.get('status_source_url')!=report['source_url'] or player.get('status_observed_at')!=report['retrieved_at']):
                            issue('availability_conflict',player['name']+': ESPN-based availability differs from its source.',tid)
                    note('official_report_pending','The official injury report does not cover this team yet; ESPN\'s injury list is used until it does.',tid)
                else:
                    note('injury_status_unavailable','No official or ESPN injury list covers this team yet; its players without a status get no numeric forecast.',tid)
            else:
                day=parse_time(game['start_time']).astimezone(ZoneInfo('America/New_York')).date().isoformat()
                matchup=str(game.get('away_tricode'))+'@'+str(game.get('home_tricode'))
                covering=[e for e in extractions if e.get('pages_complete') and any(c['team_id']==tid and c['date']==day and c['matchup']==matchup for c in e.get('complete_teams',[]))]
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
                # Starters only label team totals; the player forecasts do not depend on them.
                note('starting_five_unresolved','Five distinct eligible projected starters cannot be established.',tid)
    return dict(schema_version=1,version=PREFLIGHT_VERSION,checked_at=snapshot['as_of'],
                status='blocked' if issues else 'passed',issues=issues,notes=notes,warnings=warnings,name_suffix_matches=suffixes,
                lineups=lineups,sources=reference.get('sources',[]),
                policy='Inputs cross-checked; projected starters remain unconfirmed. Missing availability is never inferred '
                       'healthy without an official or ESPN injury list.')


def run_preflight(snapshot,history,profiles,directory,*,cache_dir=None):
    phase='reference_unavailable'
    reference={}
    try:
        phase='model_archive_unverified'
        verify_enhancements(snapshot,directory)
        phase='shot_archive_unverified'
        verify_shot_profiles(snapshot,history,directory)
        if uses_refinements(snapshot):
            from .role_context import verify_role_context
            from .shot_quality import verify_shot_quality
            phase='role_context_archive_unverified'
            if 'role_context' not in snapshot:raise ValueError('Refined forecasts require a prepared injury-exit context, including empty coverage.')
            verify_role_context(snapshot,history,directory)
            phase='shot_quality_archive_unverified'
            if 'shot_quality' not in snapshot:raise ValueError('Refined forecasts require prepared shot-quality evidence, including empty coverage.')
            verify_shot_quality(snapshot,history,directory)
        phase='injury_archive_unverified'
        verify_priors(snapshot,directory)
        verified=verify_extractions(snapshot,cache_dir or Path(directory)/'raw')
        checked=deepcopy(snapshot);checked['injury_extractions']=verified
        phase='availability_evidence_invalid'
        reviews=[]
        for game in checked['games']:
            path=Path(directory)/'roster-reviews'/(game['game_id']+'.json')
            if path.exists():reviews.extend(json.loads(path.read_text()))
        checked['roster_reviews']=reviews
        # ESPN is a cross-check: without it, the official NBA evidence stands alone (noted).
        try:reference=collect_references(checked,Path(directory)/'references',roster_reviews=reviews)
        except (ValueError,OSError,KeyError,TypeError) as exc:reference=dict(failures=[dict(kind='cross_check',error=str(exc))])
        snapshot['as_of']=datetime.now(timezone.utc).isoformat()
        checked['as_of']=snapshot['as_of']
        checked=apply_no_injuries(checked,reference.get('no_injuries',[]))
        checked=apply_espn_injuries(checked,reference.get('espn_injuries',[]))
        checked=apply_roster_reviews(checked,profiles,reference)
        snapshot['roster']=deepcopy(checked['roster'])
        snapshot['availability_supplements']=deepcopy(checked['availability_supplements'])
        snapshot['espn_injury_fallbacks']=deepcopy(checked['espn_injury_fallbacks'])
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
