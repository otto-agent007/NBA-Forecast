"""One restartable operations tick. Run on a persistent host with the supplied timer."""
from copy import deepcopy
from contextlib import closing
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import json
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import time
from zoneinfo import ZoneInfo

from .data import fetch_roster,fetch_json,boxscore_url,normalize_boxscore
from .schedule import fetch_schedule,fetch_verified_secondary_schedule,due_jobs,game_context,atomic_json,canonical
from .injuries import fetch_injury_check
from .injury_parser import pdf_text,parse_report,apply_report,PARSER_VERSION
from .profiles import build_profiles,eligible_history
from .model import forecast, MODEL_VERSION
from .store import save_run,evaluate_run,performance_report
from .benchmarks import benchmark_report
from .injury_capture import capture_daily,record_check,reported_unavailable
from .preflight import run_preflight,PREFLIGHT_VERSION
from .validation import COUNTS,parse_time,validate_history,validate_inputs,_source
from .enhancements import load_active,prepare
from .boxscore_model import VERSION as BOX_MODEL_VERSION
from .shot_profiles import prepare_shot_profiles
from .model_versions import uses_refinements
from .intervals import INTERVAL_FAMILY
from .market_lines import capture_game_lines,market_report,saved_capture_evidence
from .kalshi_lines import capture_kalshi_lines
from .reference import archive_daily_references


def _now():
    return datetime.now(timezone.utc).isoformat()


# The season calendar is downloaded once and checked once a day after this hour
# (Pacific), before the 9 AM forecasts, or sooner when a forecast finds a moved
# tipoff. A failed check retries from five minutes, doubling, up to three hours.
CALENDAR_CHECK_HOUR=8
CALENDAR_RETRY_SECONDS=(300,3*3600)
# Derived reports (performance, calibration, postgame, daily reviews, market) are
# rebuilt by the derived service, `python -m nba_forecast derived` on its own
# one-minute timer, when their inputs change and at least this often. The tick
# never rebuilds them: a rebuild takes minutes, and the tick's checkpoint
# forecasts and line captures run to the minute.
DERIVED_REFRESH_SECONDS=6*3600
# A saved roster this recent serves preliminary forecasts; the T-30 final checkpoint
# refreshes one older than fifteen minutes. Membership changes are an evidence change.
ROSTER_REUSE_SECONDS=3*3600
ROSTER_FINAL_REUSE_SECONDS=900
# A job that failed is retried after this long (near tipoff, sooner).
JOB_RETRY_SECONDS=(900,300)
# The market captures are optional downloads; they run after the pregame forecasts
# and share one time budget, cut further when the tick is already old, so a slow
# network (each request waiting out its timeout) cannot starve a checkpoint or push
# the tick into systemd's ten-minute kill. On 2026-11-06 the captures took 135-475 s
# a tick for two hours of failing name resolution and the 9 AM forecasts came 82
# minutes late.
CAPTURE_BUDGET_SECONDS=120
TICK_SOFT_LIMIT_SECONDS=420
# The evaluation memory outlives a deploy: its entries are scored again under the new
# code a few at a time, for at most this long a tick, instead of all in the first
# tick. On 2026-11-09 the memory was discarded with the code and the first tick after
# the deploy re-scored 3800 runs in 379 s, writing nothing, while games tipped.
EVALUATION_RECHECK_SECONDS=20
# The tick's record of a player sync keeps its counts; these per-player blocks are
# the sync's own players/index.json and made each ticks.jsonl row about 400 KB.
PLAYER_SYNC_DETAIL_KEYS=('rotation_roles','player_summaries','shot_queue')
PACIFIC=ZoneInfo('America/Los_Angeles')

_CODE_DIGEST=None


def _code_digest():
    """Fingerprint of this package's source; cached decisions expire with the code."""
    global _CODE_DIGEST
    if _CODE_DIGEST is None:
        package=Path(__file__).resolve().parent
        digest=hashlib.sha256()
        for path in sorted(package.rglob('*.py')):
            digest.update(path.relative_to(package).as_posix().encode()+b'\0'+path.read_bytes())
        _CODE_DIGEST=digest.hexdigest()
    return _CODE_DIGEST


def _read(path,default):
    return json.loads(path.read_text()) if path.exists() else default


def _contents(folder):
    """Each file's name and content digest; a rewrite with identical bytes changes nothing."""
    if not folder.exists():
        return []
    return sorted((p.name,hashlib.sha256(p.read_bytes()).hexdigest()) for p in folder.iterdir() if p.is_file())


def _game_archives(raw):
    """Archived NBA box scores and play-by-play: the raw evidence derived reports read directly.

    Provenance files are written once per content digest. Other archives (rosters,
    injury reports, official pages) reach the reports only through finals.
    """
    names=[]
    for path in sorted(raw.glob('*.provenance.json')) if raw.exists() else []:
        try:
            url=json.loads(path.read_text()).get('source_url','')
        except (ValueError,AttributeError):
            url='/liveData/boxscore/'  # Unreadable: count it, conservatively.
        if re.search(r'/liveData/(boxscore|playbyplay)/',str(url)):
            names.append(path.name)
    return names


def _ledger_state(db):
    """Append-only tables: row counts and newest rowids identify their contents."""
    if not Path(db).exists():
        return None
    with closing(sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        return [list(conn.execute(f'SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM {table}').fetchone())
                for table in ('runs','evaluations')]


def _roster(folder,season,raw,jobs,report):
    """The roster for this tick's jobs: a recent saved copy, else a fresh capture, else a recent fallback."""
    saved=_read(folder/'roster.json',None)
    now=parse_time(_now())
    age=(now-parse_time(saved['observed_at'])).total_seconds() if saved else None
    reuse=ROSTER_FINAL_REUSE_SECONDS if any(j['stage']=='final' for j in jobs) else ROSTER_REUSE_SECONDS
    if saved and age is not None and 0<=age<=reuse:
        report['roster_source']='saved'
        return saved
    try:
        roster=fetch_roster(season,cache_dir=raw)
    except (ValueError,OSError) as exc:
        if saved and age is not None and 0<=age<=48*3600:
            report['roster_source']='saved_after_failure'
            report['warnings'].append(dict(task='roster',error=str(exc),action='Using recent saved roster.'))
            return saved
        report['roster_source']='unavailable'
        report['warnings'].append(dict(task='roster',error=str(exc),action='Forecast withheld; injury capture still runs.'))
        return None
    atomic_json(folder/'roster.json',roster)
    report['roster_source']='fresh'
    return roster


def _evidence_key(check,espn_injuries,roster,stage):
    """What a forecast would be built from; a new forecast is made only when this changes.

    Without a usable injury check the key falls back to a time slot, so the
    attempt is repeated periodically instead of never.
    """
    if isinstance(check,Exception) or check is None:
        seconds=JOB_RETRY_SECONDS[1] if stage=='final' else JOB_RETRY_SECONDS[0]
        reports=['unavailable',int(parse_time(_now()).timestamp())//seconds]
    else:
        reports=[[name,(item.get(name) or {}).get('sha256')] for item in check['games'] for name in ('anchor_report','latest_report')]
    members=sorted((p['player_id'],p['team_id'],p.get('status','unknown')) for p in roster['players']) if roster else None
    return hashlib.sha256(canonical(dict(reports=reports,espn_injuries=espn_injuries,roster=members))).hexdigest()


def _calendar_due(state,now):
    if state.get('refresh_requested_at'):
        return True
    try:
        checked=parse_time(state['checked_at'])
    except (KeyError,TypeError,ValueError):
        return True
    local=now.astimezone(PACIFIC)
    check=local.replace(hour=CALENDAR_CHECK_HOUR,minute=0,second=0,microsecond=0)
    if local<check:
        check-=timedelta(days=1)
    return checked<check


def request_calendar_check(folder,reason):
    """Have the next tick recheck the calendar, e.g. after a forecast saw a moved tipoff."""
    path=Path(folder)/'schedule/fetch-state.json'
    atomic_json(path,dict(_read(path,{}),refresh_requested_at=_now(),refresh_reason=reason))


def _calendar(folder,season,report):
    """The saved season calendar, checked once a day.

    The check downloads NBA's calendar; if that fails, ESPN's slate for today and
    tomorrow verifies near-term games against the saved one instead. A failed
    check never withholds forecasts: each forecast's preflight still confirms
    its own matchup and tipoff against ESPN's scoreboard.
    """
    path=folder/'schedule/fetch-state.json'
    state=_read(path,{})
    now=parse_time(_now())
    saved=_read(folder/'schedule/latest.json',None)
    deferred=bool(state.get('next_attempt_at')) and now<parse_time(state['next_attempt_at'])
    if saved is not None and (deferred or not _calendar_due(state,now)):
        schedule,source=saved_calendar(folder)
        if source=='saved_nba' and state.get('consecutive_failures'):
            source='unverified_cached_nba'
        report['schedule_source']=source
        return schedule
    # The daily check is also when the day's ESPN injury list and depth charts are archived.
    try:
        daily=archive_daily_references(folder/'references',now.astimezone(PACIFIC).date().isoformat())
        report['daily_references']=dict(sources=len(daily['sources']),failures=len(daily['failures']))
    except Exception as exc:
        report['warnings'].append(dict(task='daily_references',error=f'{type(exc).__name__}: {exc}'))
    try:
        schedule=fetch_schedule(folder/'schedule')
        source='nba'
        report['schedule_source']='fresh_nba'
    except (ValueError,OSError) as exc:
        if saved is None:
            raise
        if saved['season']!=season:
            raise ValueError('saved calendar belongs to another season') from exc
        try:
            schedule=fetch_verified_secondary_schedule(saved,folder/'schedule',_now())
            source='espn'
            report['schedule_source']='espn_verified_cached_nba'
            report['schedule_recovery']=dict(nba_error=str(exc),crosscheck=schedule['crosscheck'])
        except (ValueError,OSError) as secondary_error:
            failures=state.get('consecutive_failures',0)+1
            delay=min(CALENDAR_RETRY_SECONDS[0]*2**min(failures-1,16),CALENDAR_RETRY_SECONDS[1])
            retry=(now+timedelta(seconds=delay)).isoformat()
            atomic_json(path,dict(state,consecutive_failures=failures,last_failure_at=now.isoformat(),
                                  last_error=f'NBA: {exc}; ESPN: {secondary_error}',next_attempt_at=retry))
            report['schedule_source']='unverified_cached_nba'
            report['warnings'].append(dict(task='schedule',nba_error=str(exc),secondary_error=str(secondary_error),
                action='Using the saved calendar; each forecast still confirms its matchup and tipoff. Next check at '+retry+'.'))
            return saved
    atomic_json(path,dict(checked_at=now.isoformat(),source=source,consecutive_failures=0))
    return schedule


def saved_calendar(folder):
    """The calendar the ticks are using, from disk alone: the day's ESPN-verified copy while it is in use, else the saved NBA calendar.

    Returns ``(schedule, source)``; the schedule is None until the first tick saves one.
    """
    saved=_read(folder/'schedule/latest.json',None)
    if saved is None:
        return None,None
    state=_read(folder/'schedule/fetch-state.json',{})
    verified=_read(folder/'schedule/secondary/verified-latest.json',None)
    if state.get('source')=='espn' and verified and verified.get('crosscheck',{}).get('nba_base_version')==saved.get('version'):
        return verified,'espn_verified_cached_nba'
    return saved,'saved_nba'


def ledger_runs(db):
    """Yield bounded batches of run metadata, releasing readers before writes.

    Histories are large immutable inputs. Only artifact recovery needs them;
    routine ticks must not load one copy for every saved forecast.
    """
    if not Path(db).exists():
        return
    after=0
    upper=None
    while True:
        with closing(sqlite3.connect(db)) as conn:
            conn.row_factory=sqlite3.Row
            if upper is None:
                upper=conn.execute('SELECT COALESCE(MAX(rowid),0) FROM runs').fetchone()[0]
            rows=conn.execute(
                'SELECT rowid AS insertion_order,run_id,snapshot_json,forecast_json,mode FROM runs '
                'WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT 32',(after,upper)).fetchall()
        if not rows:
            return
        after=rows[-1]['insertion_order']
        for row in rows:
            yield dict(row)


def _run_ids(db):
    """(run_id, mode) in insertion order, without opening any JSON."""
    if not Path(db).exists():
        return []
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute('SELECT run_id,mode FROM runs ORDER BY rowid').fetchall()


def _run_row(db,run_id,columns):
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory=sqlite3.Row
        return conn.execute(f'SELECT {columns} FROM runs WHERE run_id=?',(run_id,)).fetchone()


def _saved_history(db,run_id):
    with closing(sqlite3.connect(db)) as conn:
        row=conn.execute('SELECT history_json FROM runs WHERE run_id=?',(run_id,)).fetchone()
    return json.loads(row[0])


def apply_review(snapshot,review,*,skip_superseded=False):
    out=deepcopy(snapshot)
    if len(out['games'])!=1 or review['game_id']!=out['games'][0]['game_id']:
        raise ValueError('review targets a different game')
    game=out['games'][0]
    players={p['player_id']:p for p in out['roster']['players']}
    seen=set()
    for update in review.get('availability',[]):
        pid=update['player_id']
        p=players.get(pid)
        if not p or pid in seen or p['team_id'] not in (game['home_team_id'],game['away_team_id']):
            raise ValueError('duplicate or wrong-team review player')
        seen.add(pid)
        observed=parse_time(update['status_observed_at'])
        published=parse_time(update.get('status_published_at',update['status_observed_at']))
        if not published<=observed<=parse_time(out['as_of']):
            raise ValueError('review availability evidence chronology is invalid')
        if update['status'] not in ('available','out','questionable','unknown'):
            raise ValueError('invalid review availability status')
        _source(update['status_source_url'])
        if p.get('status_observed_at') and published<parse_time(p.get('status_published_at',p['status_observed_at'])):
            if skip_superseded:
                out.setdefault('review_warnings',[]).append(dict(kind='superseded_review_availability',
                    game_id=review['game_id'],player_id=pid,
                    review_published_at=published.isoformat(),
                    retained_published_at=p.get('status_published_at',p['status_observed_at'])))
                continue
            raise ValueError('review cannot supersede newer availability evidence')
        for key in ('status','status_observed_at','status_source_url'):
            p[key]=update[key]
        p['status_published_at']=update.get('status_published_at',update['status_observed_at'])
        p['status_game_id']=review['game_id']
        p['status_basis']='sourced_review'
        p['reported_status']=update['status'].capitalize()
    out['role_adjustments']=deepcopy(review.get('role_adjustments',[]))
    out['review_input']=deepcopy(review)
    validate_inputs(out,[])
    return out


def run_tick(directory,history):
    folder=Path(directory)
    folder.mkdir(parents=True,exist_ok=True)
    lock=folder/'tick.lock'
    import fcntl
    handle=lock.open('a')
    try:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise ValueError('tick already running') from exc
    report=dict(started_at=_now(),status='running',created_runs=0,recovered_runs=0,evaluated_runs=0,errors=[],warnings=[],missed_final_games=[],waiting_jobs=[])
    try:
        _tick(folder,history,report)
        report['status']='partial' if report['errors'] or report['missed_final_games'] else 'complete'
    except Exception as exc:
        report['errors'].append(dict(task='tick',error=f'{type(exc).__name__}: {exc}'))
        report['status']='error'
    finally:
        report['finished_at']=_now()
        try:
            atomic_json(folder/'last-tick.json',report)
            with (folder/'ticks.jsonl').open('a') as f:
                f.write(json.dumps(report,sort_keys=True)+'\n')
        finally:
            fcntl.flock(handle,fcntl.LOCK_UN)
            handle.close()
    return report


def _phases(report):
    """Seconds each phase of a tick took, under ``report['timings']``: call the returned function as each phase ends."""
    import time
    timings=report['timings']={}
    last=[time.monotonic()]
    def mark(name):
        now=time.monotonic()
        timings[name]=round(now-last[0],3)
        last[0]=now
    return mark


def _tick(folder,history,report):
    validate_history(history)
    phase=_phases(report)
    tick_started=time.monotonic()
    # Daily evidence must still be collected when the calendar endpoint fails.
    now=parse_time(_now())
    year=now.year if now.month>=7 else now.year-1
    season=f'{year}-{(year+1)%100:02d}'
    try:
        report['daily_injuries']=capture_daily(folder,season,_now())
    except (ValueError,OSError) as exc:
        report['errors'].append(dict(task='daily_injuries',error=str(exc)))
    phase('daily_injuries')
    schedule=_calendar(folder,season,report)
    report['schedule_version']=schedule['version']
    phase('calendar')
    statepath=folder/'state.json'
    state=json.loads(statepath.read_text()) if statepath.exists() else {'jobs':{}}
    db=folder/'forecast.sqlite'
    raw=folder/'raw'
    cache=folder/'finals'
    cache.mkdir(exist_ok=True)
    _recover_runs(folder,db,state,report)
    atomic_json(statepath,state)
    if report.get('recovered_artifacts'):
        request_derived(folder,'recovered_artifacts')
    phase('recover_runs')
    # Enrichment only fills optional fields (rebounds, assists, steals, blocks, starter)
    # into captured finals; a failure is reported and the tick goes on to forecast.
    try:
        from .player_sync import enrich_final_cache
        report['warnings'].extend(dict(task='enrich_finals',**e) for e in enrich_final_cache(folder,skipped=[]))
    except Exception as exc:
        report['errors'].append(dict(task='enrich_finals',error=f'{type(exc).__name__}: {exc}'))
    history=_merge_finals(history,cache,errors=report['errors'])
    prior_path=folder/'historical-priors.json'
    if prior_path.exists():
        book=json.loads(prior_path.read_text())
        if book.get('boxscore_fields_version')!=1:
            from .historical import expand_prior_fields
            atomic_json(prior_path,expand_prior_fields(book,folder))
    phase('history')
    jobs=due_jobs(schedule,_now(),include_waiting=True)
    by_id={g['game_id']:g for g in schedule['games']}
    report['waiting_jobs']=[dict(game_id=j['game_id'],ready_at=j['ready_at'],reason='Waiting for the 9 AM Pacific initial forecast checkpoint.') for j in jobs if j.get('waiting')]
    upcoming=[by_id[j['game_id']] for j in jobs]
    jobs=[j for j in jobs if not j.get('waiting')]
    # The day's cross-check is informational; each forecast's preflight confirms its own game.
    crosscheck=schedule.get('crosscheck',{})
    report['schedule_unverified_games']=sorted(j['game_id'] for j in jobs if j['game_id'] in set(crosscheck.get('blocked_game_ids',[])))
    report['warnings'].extend(dict(task='schedule_crosscheck',espn_id=event['espn_id'],
        error='An ESPN event has no unique NBA game ID in the saved calendar.') for event in crosscheck.get('unmatched_secondary_events',[]))
    active_model,model_digest=load_active(folder,_now())
    # The ESPN game summary's injury lists are forecast evidence. The digests the last
    # capture saved key the jobs; the capture itself runs after the forecasts, so a
    # change ESPN makes rebuilds the forecast one tick later.
    capture=dict(evidence=saved_capture_evidence(folder,upcoming))
    # A forecast is rebuilt when its evidence changes: the official report edition
    # for the game, ESPN's injury list and the roster. These checks are cheap.
    roster=_roster(folder,season,raw,jobs,report) if jobs else None
    capture_cache={}
    checks={}
    for job in jobs:
        try:
            checks[job['game_id']]=fetch_injury_check(dict(season=season,games=[by_id[job['game_id']]],as_of=_now(),forecast_stage=job['stage']),raw,capture_cache=capture_cache)
        except (ValueError,OSError) as exc:
            checks[job['game_id']]=exc
    reviews=_job_keys(folder,jobs,checks,capture,roster,active_model,model_digest)
    phase('evidence_checks')
    # A job that failed is retried after a pause instead of every minute.
    failed=state.setdefault('failed_jobs',{})
    for key in [k for k,v in failed.items() if (parse_time(_now())-parse_time(v['at'])).total_seconds()>86400]:
        del failed[key]
    def deferred(job):
        item=failed.get(job['key'])
        pause=JOB_RETRY_SECONDS[1] if job['stage']=='final' else JOB_RETRY_SECONDS[0]
        return bool(item) and (parse_time(_now())-parse_time(item['at'])).total_seconds()<pause
    report['deferred_jobs']=[dict(game_id=j['game_id'],stage=j['stage'],failed_at=failed[j['key']]['at'])
                             for j in jobs if j['key'] not in state['jobs'] and deferred(j)]
    needed=[j for j in jobs if j['key'] not in state['jobs'] and not deferred(j)]
    tick=SimpleNamespace(schedule=schedule,by_id=by_id,roster=roster,raw=raw,db=db,state=state,statepath=statepath,report=report,
                         failed=failed,checks=checks,reviews=reviews,history=history,parsed_reports={},active_model=active_model,
                         model_digest=model_digest)
    for job in needed:
        try:
            _forecast_job(folder,job,tick)
        except Exception as exc:
            report['errors'].append(dict(task='forecast',game_id=job['game_id'],stage=job['stage'],error=f'{type(exc).__name__}: {exc}'))
            failed[job['key']]=dict(at=_now(),reason=type(exc).__name__)
    atomic_json(statepath,state)
    phase('forecasts')
    # Market lines for upcoming games, at the forecast cadence, after the pregame
    # deadlines and within the capture budget; never a reason to fail the tick.
    deadline=time.monotonic()+max(0.,min(CAPTURE_BUDGET_SECONDS,TICK_SOFT_LIMIT_SECONDS-(time.monotonic()-tick_started)))
    try:
        report['market_lines']=capture_game_lines(folder,upcoming,season,_now(),deadline=deadline)
    except Exception as exc:
        report['warnings'].append(dict(task='market_lines',error=f'{type(exc).__name__}: {exc}'))
    try:
        report['kalshi_lines']=capture_kalshi_lines(folder,upcoming,_now(),deadline=deadline)
    except Exception as exc:
        report['warnings'].append(dict(task='kalshi_lines',error=f'{type(exc).__name__}: {exc}'))
    phase('market_lines')
    # Prioritize pregame deadlines over downloads and postgame reporting.
    if active_model:
        # Per-game failures are caught inside; anything that escapes them is reported here,
        # so finals collection, player data and evaluation still run.
        try:
            from .outcome_sync import sync_finished_games
            synced=sync_finished_games(folder,schedule,history,_now())
            report['final_sync']=synced
            report['errors'].extend(dict(task='final_actuals',**e) for e in synced['errors'])
            report['warnings'].extend(dict(task='environment_update',**e) for e in synced.get('environment_warnings',[]))
        except Exception as exc:
            report['errors'].append(dict(task='final_sync',error=f'{type(exc).__name__}: {exc}'))
    phase('final_sync')
    _collect_finals(schedule,cache,raw,report)
    phase('collect_finals')
    # Complete/revise player boxes before evaluating the frozen forecasts.
    if (folder/'roster.json').exists():
        try:
            from .player_sync import sync_player_data
            synced=sync_player_data(folder,history,roster=roster,max_shot_games=16,schedule=schedule)
            report['player_sync']=player_sync_summary(synced)
            report['warnings'].extend(dict(task='shot_data',**e) for e in synced['errors'])
        except Exception as exc:
            report['errors'].append(dict(task='player_sync',error=f'{type(exc).__name__}: {exc}'))
    phase('player_sync')
    # LiveData play-by-play for the postgame recaps and the daily review's exit and blowout labels.
    try:
        from .postgame import collect_sources
        report['postgame_sources']=collect_sources(folder,schedule,_now())
        report['warnings'].extend(dict(task='postgame_sources',**e) for e in report['postgame_sources']['errors'])
    except Exception as exc:
        report['errors'].append(dict(task='postgame_sources',error=f'{type(exc).__name__}: {exc}'))
    phase('postgame_sources')
    try:
        _evaluate_runs(folder,db,report,deadline=tick_started+TICK_SOFT_LIMIT_SECONDS)
    except Exception as exc:
        report['errors'].append(dict(task='evaluate_runs',error=f'{type(exc).__name__}: {exc}'))
    _missed_finals(state,schedule,report,folder)
    atomic_json(statepath,state)
    phase('evaluate_runs')
    _carry_derived(folder,active_model,report)
    phase('derived')


def player_sync_summary(synced):
    """A player sync's counts and errors, without its per-player blocks (``PLAYER_SYNC_DETAIL_KEYS``),
    which the sync writes to ``players/index.json`` and the dashboard reads from there."""
    out={k:v for k,v in synced.items() if k not in PLAYER_SYNC_DETAIL_KEYS}
    queue=synced.get('shot_queue')
    if isinstance(queue,list):
        out['shot_queue_games']=len(queue)
        out['shot_queue_pending']=sum(1 for item in queue if item.get('status')!='complete')
    out['detail']='players/index.json'
    return out


def compact_ticks(directory):
    """Rewrite ``ticks.jsonl`` with each row's player sync reduced to ``player_sync_summary``.

    Rows written before 2026-11-06 carried the whole sync, about 400 KB each. The
    file is rewritten beside itself and renamed into place under the tick lock, so
    no tick appends meanwhile; a line that is not JSON (a write a power loss cut
    short) is kept as it is.
    """
    folder=Path(directory)
    path=folder/'ticks.jsonl'
    result=dict(rows=0,kept_lines=0,bytes_before=0,bytes_after=0)
    if not path.exists():
        return result
    import fcntl
    handle=(folder/'tick.lock').open('a')
    try:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise ValueError('tick already running') from exc
    try:
        result['bytes_before']=path.stat().st_size
        tmp=path.with_name(path.name+'.compact')
        with path.open('rb') as src,tmp.open('wb') as out:
            for line in src:
                if not line.strip():
                    continue
                try:
                    row=json.loads(line)
                    if isinstance(row,dict) and isinstance(row.get('player_sync'),dict):
                        row['player_sync']=player_sync_summary(row['player_sync'])
                    out.write((json.dumps(row,sort_keys=True)+'\n').encode())
                    result['rows']+=1
                except ValueError:
                    out.write(line if line.endswith(b'\n') else line+b'\n')
                    result['kept_lines']+=1
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp,path)
        result['bytes_after']=path.stat().st_size
        return result
    finally:
        fcntl.flock(handle,fcntl.LOCK_UN)
        handle.close()


def _recover_runs(folder,db,state,report):
    """Runs saved before a crash wrote their artifacts or their job key get both back."""
    # A run already recorded under its operation key with all three artifacts
    # needs nothing; only the others are opened.
    recorded=set(state['jobs'].values())
    artifacts=('profiles.json','forecast.json','snapshot.json')
    for run_id,_ in _run_ids(db):
        output=folder/'runs'/run_id
        if run_id in recorded and all((output/name).exists() for name in artifacts):
            continue
        run=_run_row(db,run_id,'snapshot_json,forecast_json')
        snapshot=json.loads(run['snapshot_json'])
        key=snapshot.get('operation_key')
        if key:
            missing=[name for name in artifacts if not (output/name).exists()]
            if 'profiles.json' in missing:
                atomic_json(output/'profiles.json',build_profiles(snapshot,_saved_history(db,run_id)))
            if 'forecast.json' in missing:
                atomic_json(output/'forecast.json',json.loads(run['forecast_json']))
            if 'snapshot.json' in missing:
                atomic_json(output/'snapshot.json',snapshot)
            if missing:
                report['recovered_artifacts']=report.get('recovered_artifacts',0)+len(missing)
        if key and key not in state['jobs']:
            state['jobs'][key]=run_id
            report['recovered_runs']+=1


def _game_identity(row):
    return (row['season'],parse_time(row['game_time']),tuple(sorted((row['team_id'],row['opponent_id']))))


def _merge_finals(history,cache,errors=None):
    """The input history plus every captured final, the newest known_at winning per player-game.

    A captured final lists the game's players; an input row for a player it leaves out
    (a stale-roster DNP the official box contradicted) is dropped.

    Without ``errors`` a bad captured final raises. With a list, one that cannot be read or
    validated is left out (its game keeps the input rows) and one whose game identity differs
    from the input history's replaces that game's input rows; both are recorded there, and the
    tick goes on.
    """
    combined={(r['game_id'],r['player_id']):r for r in history}
    first={}
    for r in history:
        first.setdefault(r['game_id'],r)
    for p in sorted(cache.glob('*.json')):
        try:
            rows=json.loads(p.read_text())
            validate_history(rows)
        except (ValueError,KeyError,TypeError,AttributeError,OSError) as exc:
            if errors is None:
                raise
            errors.append(dict(task='finals_cache',game_id=p.stem,error=f'{type(exc).__name__}: {exc}'))
            continue
        players={r['player_id'] for r in rows}
        mismatched=set()
        if errors is not None:
            mismatched={r['game_id'] for r in rows
                        if r['game_id'] in first and _game_identity(first[r['game_id']])!=_game_identity(r)}
            if mismatched:
                errors.append(dict(task='finals_cache',game_id=p.stem,
                    error='Captured final and input history disagree on game identity; the captured final is used'))
        for key in [k for k in combined if (k[0]==p.stem and k[1] not in players) or k[0] in mismatched]:
            del combined[key]
        for row in rows:
            key=(row['game_id'],row['player_id'])
            old=combined.get(key)
            if old is None or parse_time(old['known_at'])<=parse_time(row['known_at']):
                combined[key]=row
    history=list(combined.values())
    validate_history(history)
    return history


def _job_keys(folder,jobs,checks,capture,roster,active_model,model_digest):
    """Extend each job key with everything a forecast is built from; return the games' reviews."""
    reviews={}
    for job in jobs:
        version=active_model['model_version'] if active_model else MODEL_VERSION
        evidence=_evidence_key(checks.get(job['game_id']),(capture.get('evidence') or {}).get(job['game_id']),roster,job['stage'])
        job['key']+=f':model:{version}:parser:{PARSER_VERSION}:validator:{PREFLIGHT_VERSION}:evidence:{evidence}'
        job['key']+=':boxscore:'+BOX_MODEL_VERSION
        if active_model:
            job['key']+=':configuration:'+active_model['model_key']+':artifact:'+model_digest
        prior_book=folder/'historical-priors.json'
        if prior_book.exists():
            job['key']+=':history-prior:'+hashlib.sha256(prior_book.read_bytes()).hexdigest()
        roster_review=folder/'roster-reviews'/(job['game_id']+'.json')
        if roster_review.exists():
            job['key']+=':roster-review:'+hashlib.sha256(roster_review.read_bytes()).hexdigest()
        path=folder/'reviews'/(job['game_id']+'.json')
        if path.exists():
            review=json.loads(path.read_text())
            reviews[job['game_id']]=review
            job['key']+=':review:'+hashlib.sha256(canonical(review)).hexdigest()
    return reviews


def _forecast_job(folder,job,tick):
    """Build, verify, forecast and save one job; ``tick`` carries this tick's shared state."""
    schedule,by_id,roster,raw,report=tick.schedule,tick.by_id,tick.roster,tick.raw,tick.report
    checks,reviews,history,parsed_reports=tick.checks,tick.reviews,tick.history,tick.parsed_reports
    active_model,model_digest,db,state,statepath,failed=tick.active_model,tick.model_digest,tick.db,tick.state,tick.statepath,tick.failed
    game=by_id[job['game_id']]
    snapshot=dict(schema_version=1,season=schedule['season'],as_of=_now(),roster=deepcopy(roster),games=[game],
                  forecast_stage=job['stage'],operation_key=job['key'],schedule_version=schedule['version'],
                  schedule_context=game_context(schedule,game['game_id']))
    try:
        check=checks[job['game_id']]
        if isinstance(check,Exception):
            raise check
        snapshot['as_of']=_now()
        snapshot['injury_check']=check
        record_check(folder,check,job['stage'])
        for item in check['games']:
            evidence=item['latest_report'] or item.get('anchor_report')
            if evidence and roster is not None:
                text=pdf_text(raw/(evidence['sha256']+'.pdf'),evidence['sha256'])
                parsed=parse_report(text,schedule,roster,evidence)
                snapshot=apply_report(snapshot,parsed,allow_not_listed=True)
                report['warnings'].extend(dict(forecast_game_id=job['game_id'],**i) for i in parsed['issues'])
    except (ValueError,OSError) as exc:
        if job['stage']=='final':
            raise
        report['warnings'].append(dict(game_id=job['game_id'],error=str(exc),action='Preliminary availability remains unresolved.'))
    if roster is None:
        raise ValueError('No roster observed within 48 hours; injury checkpoint retained, forecast withheld')
    snapshot['as_of']=_now()
    if job['game_id'] in reviews:
        snapshot=apply_review(snapshot,reviews[job['game_id']],skip_superseded=True)
        report['warnings'].extend(dict(task='review',**w) for w in snapshot.get('review_warnings',[]))
    usable=eligible_history(snapshot,history)
    snapshot['reported_unavailable']=reported_unavailable(folder,schedule,roster,usable,game,parsed_reports)
    prior_path=folder/'historical-priors.json'
    if prior_path.exists():
        book=json.loads(prior_path.read_text())
        teams={game['home_team_id'],game['away_team_id']}
        player_ids={p['player_id'] for p in roster['players'] if p['team_id'] in teams}
        snapshot['historical_priors']={'players':[p for p in book['players'] if p['player_id'] in player_ids], 'aggregation':book.get('aggregation')}
    if active_model:
        snapshot['model_enhancements']=prepare(active_model,model_digest,game,snapshot['as_of'],directory=folder)
    # One game's unusable source costs that game's evidence, not every forecast: it is left
    # out exactly as if it had not been collected, and reported once per tick.
    rejected=[]
    snapshot['shot_profiles']=prepare_shot_profiles(folder,snapshot,usable,rejected=rejected)
    if uses_refinements(snapshot):
        from .role_context import prepare_role_context
        from .shot_quality import prepare_shot_quality
        snapshot['role_context']=prepare_role_context(folder,snapshot,usable,rejected=rejected)
        snapshot['shot_quality']=prepare_shot_quality(folder,snapshot,usable,rejected=rejected)
    for item in rejected:
        if item not in report['warnings']:
            report['warnings'].append(item)
    profiles=build_profiles(snapshot,usable)
    verification=run_preflight(snapshot,usable,profiles,folder)
    if verification['status']!='passed':
        report['errors'].append(dict(task='preflight',game_id=job['game_id'],issues=verification['issues']))
        if any(i['code']=='tipoff_changed' for i in verification['issues']):
            request_calendar_check(folder,'tipoff_changed:'+job['game_id'])
        failed[job['key']]=dict(at=_now(),reason='preflight')
        return
    snapshot['preflight']=verification
    profiles=build_profiles(snapshot,usable)
    prediction=forecast(snapshot,usable)
    run_id=save_run(db,snapshot,usable,prediction)
    # The immutable operation_key in the run is authoritative if interrupted here.
    state['jobs'][job['key']]=run_id
    atomic_json(statepath,state)
    output=folder/'runs'/run_id
    atomic_json(output/'profiles.json',profiles)
    atomic_json(output/'forecast.json',prediction)
    atomic_json(output/'snapshot.json',snapshot)
    report['created_runs']+=1


def _collect_finals(schedule,cache,raw,report):
    """Capture the official box score of every final game not yet cached."""
    for game in schedule['games']:
        if game['status']!='final':
            continue
        path=cache/(game['game_id']+'.json')
        if path.exists():
            continue
        try:
            url=boxscore_url(game['game_id'])
            payload=fetch_json(url,cache_dir=raw)
            rows=normalize_boxscore(payload,url,_now())
            atomic_json(path,rows)
        except Exception as exc:
            report['errors'].append(dict(task='final_boxscore',game_id=game['game_id'],error=f'{type(exc).__name__}: {exc}'))


MISSED_FINAL_WINDOW_HOURS=48


def _missed_finals(state,schedule,report,folder=None):
    """Games that tipped since operations began without a final-stage run are missed, not an old season.

    A miss is recorded once, under ``state['missed_finals']`` and mirrored to
    ``missed-finals.json`` (game, tipoff, the tick that first saw it), and is
    never backdated into a success. It marks the tick ``partial`` for
    ``MISSED_FINAL_WINDOW_HOURS`` after the tipoff, or until the derived service
    has recovered the game's report edition and closes (``recovered-evidence.json``);
    older and recovered misses stay on the record (``report['missed_final_record']``)
    without marking every later tick.
    A miss whose final run turns up after all (a recovered run) leaves the record.
    """
    from .recover_evidence import settled_games
    # Only flag games occurring after operations were first started, not an entire
    # historical season as missed. Unknown/postponed start times cannot be due.
    state.setdefault('tracking_started_at',report['started_at'])
    record=state.setdefault('missed_finals',{})
    now=parse_time(_now())
    settled=settled_games(folder) if folder is not None else set()
    recovered=[]
    changed=False
    for game in schedule['games']:
        start=game['start_time']
        if not (start and game['status']!='postponed' and parse_time(state['tracking_started_at'])<parse_time(start)<=now):
            continue
        key=f"{game['game_id']}:{start}:final"
        if any(k==key or k.startswith(key+':') for k in state['jobs']):
            if game['game_id'] in record:
                del record[game['game_id']]
                changed=True
            continue
        item=record.get(game['game_id'])
        if item is None or item['start_time']!=start:
            record[game['game_id']]=dict(start_time=start,first_seen_at=report['started_at'])
            changed=True
        if now-parse_time(start)<=timedelta(hours=MISSED_FINAL_WINDOW_HOURS):
            (recovered if game['game_id'] in settled else report['missed_final_games']).append(game['game_id'])
    report['missed_final_record']=dict(total=len(record),recent=len(report['missed_final_games']),recovered=sorted(recovered),
                                       older=sorted(g for g in record if g not in report['missed_final_games'] and g not in recovered))
    if folder is not None and (changed or not (Path(folder)/'missed-finals.json').exists()):
        atomic_json(Path(folder)/'missed-finals.json',dict(schema_version=1,tracking_started_at=state['tracking_started_at'],
            window_hours=MISSED_FINAL_WINDOW_HOURS,games=[dict(game_id=g,**v) for g,v in sorted(record.items())],
            policy='A tipped game without its T-30 final run is recorded once and never backdated; '
                   'it marks the tick partial for the window after tipoff, or until its report edition and closes '
                   'are recovered (recovered-evidence.json), and stays on the record after.'))


def _absent_from_final(rows,predictions):
    """Forecast players a saved final leaves out, as explicit did-not-play rows.

    A saved final is the game's complete box score (every player who played, with
    reconciled team scores), so a forecast player it does not list did not play:
    on assignment, waived, traded, or inactive without a listing. Scored like any
    other DNP instead of staying pending forever; ``absent_from_final`` marks them.
    """
    listed={r['player_id'] for r in rows}
    known=max(r['known_at'] for r in rows)
    absent=[]
    for p in predictions:
        team=[r for r in rows if r['team_id']==p['team_id']]
        if p['player_id'] in listed or p['status'] in ('no_game','needs_review') or not team:
            continue
        like=team[0]
        absent.append(dict(game_id=like['game_id'],season=like['season'],season_type=like['season_type'],
            game_time=like['game_time'],known_at=known,source_url=like['source_url'],player_id=p['player_id'],
            name=p['name'],team_id=p['team_id'],opponent_id=like['opponent_id'],status='dnp',minutes=0,
            **{field:0 for field in COUNTS},participation_reason='Not in the final box score',absent_from_final=True))
    return absent


def _evaluate_runs(folder,db,report,*,recheck_seconds=EVALUATION_RECHECK_SECONDS,deadline=None):
    """Score live runs whose final files or evaluation revisions changed since they were last scored.

    Runs are immutable, so each run's game IDs are remembered; an unchanged run
    costs one file hash per game instead of decoding its forecast again.

    The memory (``evaluation-state.json``) outlives a code change. New code can
    change which final rows a run is scored against (``_absent_from_final``, the
    validation in ``evaluate_run``), so an entry remembered under older code is
    scored again under this code before it counts as unchanged; ``stale`` in
    the file lists the entries still waiting for that. A stale run whose finals
    or revisions changed is scored at once, like a new run. The others are
    re-checked newest run first (the games being read and reviewed now), for
    ``recheck_seconds`` a tick, so a deploy costs the next ticks seconds each
    instead of costing the first one minutes. A re-check that finds the same
    final rows writes nothing: ``evaluate_run`` appends a revision only when
    they differ.

    Past ``deadline`` (a ``time.monotonic()`` value, the tick's soft limit)
    nothing more is scored; those runs wait for the next tick and the ones
    already scored are remembered. ``report['evaluation_backlog']`` counts the
    stale entries left, the ones re-checked now, how many of those gained a
    revision, and the changed runs deferred by the deadline.
    """
    path=folder/'evaluation-state.json'
    saved=_read(path,{})
    known=saved.get('runs',{})
    # Entries this code has not scored yet: every one of them after a code change.
    stale=set(known) if saved.get('code')!=_code_digest() else set(saved.get('stale',[]))&set(known)
    if not Path(db).exists():
        return
    with closing(sqlite3.connect(db)) as conn:
        latest=dict(conn.execute('SELECT run_id,MAX(revision) FROM evaluations GROUP BY run_id').fetchall())
    finals={}
    def final(gid):
        if gid not in finals:
            target=folder/'finals'/(gid+'.json')
            raw=target.read_bytes() if target.exists() else None
            finals[gid]=(hashlib.sha256(raw).hexdigest() if raw is not None else None,raw)
        return finals[gid]
    def signature(games,revision):
        return hashlib.sha256(canonical(dict(finals=[[gid,final(gid)[0]] for gid in games],revision=revision))).hexdigest()
    def score(run_id):
        """Score one run against its saved finals and remember it as scored by this code.

        Returns None when the evaluation failed (reported; the run stays as it was and is
        tried again next tick), else whether it had finals to score and whether that wrote a revision.
        """
        run=_run_row(db,run_id,'snapshot_json,forecast_json')
        snapshot=json.loads(run['snapshot_json'])
        prediction=json.loads(run['forecast_json'])
        keys={(p['game_id'],p['player_id'],p['team_id']) for p in prediction['predictions']}
        games=[g['game_id'] for g in snapshot['games']]
        actuals=[]
        revision=latest.get(run_id)
        for gid in games:
            raw=final(gid)[1]
            if raw is not None:
                rows=json.loads(raw)
                actuals.extend(r for r in rows if (r['game_id'],r['player_id'],r['team_id']) in keys)
                actuals.extend(_absent_from_final(rows,[p for p in prediction['predictions'] if p['game_id']==gid]))
        if actuals:
            try:
                revision=evaluate_run(db,run_id,actuals)['revision']
            except Exception as exc:
                report['errors'].append(dict(task='evaluation',run_id=run_id,error=f'{type(exc).__name__}: {exc}'))
                return None
        known[run_id]=dict(games=games,signature=signature(games,revision))
        stale.discard(run_id)
        return dict(evaluated=bool(actuals),revised=revision!=latest.get(run_id))
    def late():
        return deadline is not None and time.monotonic()>=deadline
    unchanged=deferred=0
    waiting=[]
    live=set()
    for run_id,mode in _run_ids(db):
        if mode!='live':
            continue
        live.add(run_id)
        entry=known.get(run_id)
        if entry and entry['signature']==signature(entry['games'],latest.get(run_id)):
            if run_id in stale:
                waiting.append(run_id)
            else:
                unchanged+=1
            continue
        if late():
            deferred+=1
            continue
        result=score(run_id)
        if result and result['evaluated']:
            report['evaluated_runs']+=1
    stale&=live  # an entry for a run this ledger does not hold has nothing to re-check
    rechecked=revised=0
    stop=time.monotonic()+recheck_seconds
    for run_id in reversed(waiting):
        if time.monotonic()>=stop or late():
            break
        result=score(run_id)
        if result:
            rechecked+=1
            revised+=result['revised']
    report['unchanged_evaluation_runs']=unchanged
    report['evaluation_backlog']=dict(stale=len(stale),rechecked=rechecked,revised=revised,deferred=deferred)
    atomic_json(path,dict(code=_code_digest(),runs=known,stale=sorted(stale)))


def _derived_signature(folder,db,schedule,active_model,model_digest):
    """Fingerprint of everything the derived reports read, without reading their contents."""
    # Per-observation corroboration metadata changes every cross-check; the games do not.
    games=[{k:v for k,v in g.items() if k!='schedule_corroboration'} for g in schedule['games']]
    return hashlib.sha256(canonical(dict(code=_code_digest(),ledger=_ledger_state(db),
        pacific_date=parse_time(_now()).astimezone(PACIFIC).date().isoformat(),
        schedule=dict(season=schedule.get('season'),games=games),
        model=[active_model['model_key'],model_digest] if active_model else None,
        finals=_contents(folder/'finals'),game_archives=_game_archives(folder/'raw'),
        roster=(folder/'roster.json').exists(),request=_read(folder/'derived-request.json',None)))).hexdigest()


def request_derived(folder,reason):
    """Ask the derived service for a rebuild its inputs alone would not show, such as rewritten run artifacts."""
    atomic_json(folder/'derived-request.json',dict(requested_at=_now(),reason=reason))


def _derived_due(folder,db,schedule,active_model,model_digest):
    """Whether the derived reports need rebuilding: an input changed, an output is missing, or they are DERIVED_REFRESH_SECONDS old.

    Returns ``(due, signature, saved)``, ``saved`` being the summary of the last rebuild.
    """
    saved=_read(folder/'derived-reports.json',{})
    signature=_derived_signature(folder,db,schedule,active_model,model_digest)
    outputs=[folder/'performance.json',folder/'benchmarks.json',folder/'daily-reviews/index.json',folder/'market/report.json']
    if active_model:
        outputs.append(folder/'model-status.json')
    if (folder/'roster.json').exists():
        outputs.append(folder/'postgame/index.json')
    try:
        age=(parse_time(_now())-parse_time(saved['computed_at'])).total_seconds()
    except (KeyError,TypeError,ValueError):
        age=None
    current=(saved.get('signature')==signature and age is not None and 0<=age<DERIVED_REFRESH_SECONDS
             and all(p.exists() for p in outputs))
    return not current,signature,saved


def run_derived(directory):
    """One run of the derived service: rebuild the derived reports when their inputs changed, otherwise do nothing.

    It runs from its own timer beside the tick, so a rebuild that takes minutes
    never delays a checkpoint forecast or a line capture. It does not take the
    tick's lock: the two share the ledger through SQLite's write-ahead log, and a
    deploy holds both locks so neither interleaves with it. A run that finds the
    lock held (another run, or a deploy) reports ``busy`` and does nothing.
    """
    folder=Path(directory)
    folder.mkdir(parents=True,exist_ok=True)
    import fcntl
    handle=(folder/'derived.lock').open('a')
    report=dict(started_at=_now(),status='running',errors=[],warnings=[])
    try:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        report.update(status='busy',finished_at=_now())
        return report
    try:
        _derived(folder,report)
    except Exception as exc:
        report['errors'].append(dict(task='derived',error=f'{type(exc).__name__}: {exc}'))
        report['status']='error'
    finally:
        report['finished_at']=_now()
        try:
            atomic_json(folder/'last-derived.json',report)
            with (folder/'derived.jsonl').open('a') as f:
                f.write(json.dumps(report,sort_keys=True)+'\n')
        finally:
            fcntl.flock(handle,fcntl.LOCK_UN)
            handle.close()
    return report


def _derived(folder,report):
    schedule,source=saved_calendar(folder)
    if schedule is None:
        report.update(status='waiting',reason='No saved calendar yet; the first tick saves one.')
        return
    report['schedule_source']=source
    _recover_missed(folder,schedule,report)
    db=folder/'forecast.sqlite'
    active_model,model_digest=load_active(folder,_now())
    due,signature,saved=_derived_due(folder,db,schedule,active_model,model_digest)
    if not due:
        report.update(status='unchanged',computed_at=saved['computed_at'])
        return
    # The model status carries the tick's outcome discovery summary.
    report['final_sync']=_read(folder/'last-tick.json',{}).get('final_sync')
    _derived_reports(folder,db,schedule,active_model,model_digest,report)
    computed=_now()
    atomic_json(folder/'derived-reports.json',dict(signature=signature,computed_at=computed,
        report={k:report[k] for k in ('prospective_calibration','prospective_margin_calibration','postgame','daily_review','market') if k in report},
        errors=list(report['errors']),warnings=list(report['warnings'])))
    report.update(status='rebuilt',computed_at=computed)


def _recover_missed(folder,schedule,report):
    """Fetch what a missed game's checkpoint and closing captures would have saved, once the game is over.

    A recorded capture asks for a rebuild, so the market report picks up the
    recovered closes; a source given up after its retries is a warning.
    """
    from .recover_evidence import auto_recover
    try:
        summary=auto_recover(folder,schedule,now=_now())
    except Exception as exc:
        report['errors'].append(dict(task='recover_evidence',error=f'{type(exc).__name__}: {exc}'))
        return
    if not summary:
        return
    report['recovered_evidence']=summary
    if summary['recorded']:
        request_derived(folder,'recovered evidence: '+', '.join(summary['recorded']))
    report['warnings'].extend(dict(task='recover_evidence',item=label,
        action='Given up after its retries; run recover-evidence by hand to try again.') for label in summary['gave_up'])


def _carry_derived(folder,active_model,report):
    """Carry the derived service's latest summary, errors and warnings into this tick's report; the tick rebuilds nothing itself."""
    saved=_read(folder/'derived-reports.json',{})
    report.update(saved.get('report',{}))
    report['errors'].extend(saved.get('errors',[]))
    report['warnings'].extend(saved.get('warnings',[]))
    report['derived_reports']=dict(status='carried' if saved else 'none',computed_at=saved.get('computed_at'),service='nba-derived')
    # Outcome discovery runs every tick; keep its summary in the model status current.
    status=folder/'model-status.json'
    if active_model and status.exists():
        current=json.loads(status.read_text())
        if current.get('outcomes')!=report.get('final_sync'):
            current['outcomes']=report.get('final_sync')
            atomic_json(status,current)


def _activate(folder,expected,digest,report):
    """Point the activation at ``digest`` only while it still names ``expected``, the artifact this tick loaded.

    A deploy that ran during the tick already moved the pointer to an artifact
    bound to the new release; a refit of the old one must not move it back.
    """
    path=folder/'model-upgrade-active.json'
    current=_read(path,{}).get('artifact_sha256')
    if current!=expected:
        report['warnings'].append(dict(task='activation',expected=expected,found=current,
            action='The activation moved during this tick (a deploy); the refit calibration was installed but not activated.'))
        return False
    atomic_json(path,dict(artifact_sha256=digest,activated_at=_now()))
    return True


def _derived_reports(folder,db,schedule,active_model,model_digest,report):
    """Ledger summaries, calibration, postgame and daily reviews, rebuilt from saved evidence."""
    performance=performance_report(db)
    atomic_json(folder/'performance.json',performance)
    atomic_json(folder/'benchmarks.json',benchmark_report(db,performance))
    if active_model:
        from .prospective_calibration import fit_prospective
        from .prospective_margin_calibration import fit_prospective_margin, verify_margin_calibration_sources
        from .margins import CALIBRATION_FAMILY
        from .enhancements import install_artifact
        assessed=fit_prospective(folder,active_model['model_key'],_now(),interval_family=INTERVAL_FAMILY)
        atomic_json(folder/'prospective-calibration-status.json',assessed)
        report['prospective_calibration']={k:assessed.get(k) for k in ('status','sample_count','game_count','minimum_games','reason')}
        report['errors'].extend(dict(task='prospective_calibration',detail=e) for e in assessed.get('collection_errors',[]))
        if assessed['status']=='ready' and assessed.get('residual_evidence_sha256')!=(active_model.get('calibration') or {}).get('residual_evidence_sha256'):
            updated=deepcopy(active_model)
            updated['calibration']=assessed
            updated['known_at']=_now()
            digest=install_artifact(folder,updated)
            if _activate(folder,model_digest,digest,report):
                active_model,model_digest=updated,digest
        margin_assessed=fit_prospective_margin(folder,CALIBRATION_FAMILY,_now())
        atomic_json(folder/'prospective-margin-calibration-status.json',margin_assessed)
        report['prospective_margin_calibration']={k:margin_assessed.get(k) for k in ('status','reason','sample_count','training_games','validation_games')}
        report['errors'].extend(dict(task='prospective_margin_calibration',detail=e)
                                for e in margin_assessed.get('collection_errors',[]))
        if (margin_assessed['status']=='ready' and
                margin_assessed.get('evidence_sha256')!=(active_model.get('margin_calibration') or {}).get('evidence_sha256')):
            if verify_margin_calibration_sources(folder,margin_assessed):
                updated=deepcopy(active_model)
                updated['margin_calibration']=margin_assessed
                updated['known_at']=_now()
                digest=install_artifact(folder,updated)
                if _activate(folder,model_digest,digest,report):
                    active_model,model_digest=updated,digest
            else:
                report['errors'].append(dict(task='prospective_margin_calibration',
                    detail='Ready correction did not replay against saved forecasts and original finals'))
        atomic_json(folder/'model-status.json',dict(model_version=active_model['model_version'],
            model_key=active_model['model_key'],artifact_sha256=model_digest,known_at=active_model['known_at'],
            validation=active_model.get('validation_report'),prospective=assessed,
            prospective_margin=margin_assessed,
            applied_margin_calibration=({k:active_model['margin_calibration'].get(k)
                for k in ('status','training_games','validation_games','evidence_sha256','training_cutoff','margin_family','model_keys')}
                if active_model.get('margin_calibration') else None),
            outcomes=report.get('final_sync')))
    if (folder/'roster.json').exists():
        try:
            from .postgame import build_postgames
            report['postgame']=build_postgames(folder)
            report['warnings'].extend(dict(task='postgame',**e) for e in report['postgame']['errors'])
        except Exception as exc:
            report['errors'].append(dict(task='postgame',error=f'{type(exc).__name__}: {exc}'))
    from .daily_review import build_daily_reviews
    report['daily_review']=build_daily_reviews(folder,_now(),schedule=schedule)
    report['errors'].extend(dict(task='daily_review',**e) for e in report['daily_review']['errors'])
    try:
        market=market_report(folder,schedule,_now())
        atomic_json(folder/'market/report.json',market)
        report['market']=market['summary']
    except Exception as exc:
        report['errors'].append(dict(task='market_report',error=f'{type(exc).__name__}: {exc}'))
