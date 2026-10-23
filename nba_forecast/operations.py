"""One restartable operations tick. Run on a persistent host with the supplied timer."""
from copy import deepcopy
from contextlib import closing
from datetime import datetime,timezone
import json
import hashlib
from pathlib import Path
import sqlite3

from .data import fetch_roster,fetch_json,boxscore_url,normalize_boxscore
from .schedule import fetch_schedule,due_jobs,game_context,atomic_json,canonical
from .injuries import fetch_injury_check
from .injury_parser import pdf_text,parse_report,apply_report,PARSER_VERSION
from .profiles import build_profiles,eligible_history
from .model import forecast, MODEL_VERSION
from .store import save_run,evaluate_run,performance_report
from .benchmarks import benchmark_report
from .injury_capture import capture_daily,record_check
from .preflight import run_preflight,PREFLIGHT_VERSION
from .validation import parse_time,validate_history,validate_inputs,_source


def _now():return datetime.now(timezone.utc).isoformat()


def ledger_runs(db):
    """Yield bounded batches of run metadata, releasing readers before writes.

    Histories are large immutable inputs. Only artifact recovery needs them;
    routine ticks must not load one copy for every saved forecast.
    """
    if not Path(db).exists():return
    after=0;upper=None
    while True:
        with closing(sqlite3.connect(db)) as conn:
            conn.row_factory=sqlite3.Row
            if upper is None:
                upper=conn.execute('SELECT COALESCE(MAX(rowid),0) FROM runs').fetchone()[0]
            rows=conn.execute(
                'SELECT rowid AS insertion_order,run_id,snapshot_json,forecast_json,mode FROM runs '
                'WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT 32',(after,upper)).fetchall()
        if not rows:return
        after=rows[-1]['insertion_order']
        for row in rows:yield dict(row)


def _saved_history(db,run_id):
    with closing(sqlite3.connect(db)) as conn:
        row=conn.execute('SELECT history_json FROM runs WHERE run_id=?',(run_id,)).fetchone()
    return json.loads(row[0])


def apply_review(snapshot,review,*,skip_superseded=False):
    out=deepcopy(snapshot)
    if len(out['games'])!=1 or review['game_id']!=out['games'][0]['game_id']:
        raise ValueError('review targets a different game')
    game=out['games'][0];players={p['player_id']:p for p in out['roster']['players']};seen=set()
    for update in review.get('availability',[]):
        pid=update['player_id'];p=players.get(pid)
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
        for key in ('status','status_observed_at','status_source_url'):p[key]=update[key]
        p['status_published_at']=update.get('status_published_at',update['status_observed_at'])
        p['status_game_id']=review['game_id']
        p['status_basis']='sourced_review'
        p['reported_status']=update['status'].capitalize()
    out['role_adjustments']=deepcopy(review.get('role_adjustments',[]))
    out['review_input']=deepcopy(review)
    validate_inputs(out,[])
    return out


def run_tick(directory,history):
    folder=Path(directory);folder.mkdir(parents=True,exist_ok=True);lock=folder/'tick.lock'
    import fcntl
    handle=lock.open('a')
    try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close();raise ValueError('tick already running') from exc
    report=dict(started_at=_now(),status='running',created_runs=0,recovered_runs=0,evaluated_runs=0,errors=[],warnings=[],missed_final_games=[])
    try:
        _tick(folder,history,report)
        report['status']='partial' if report['errors'] or report['missed_final_games'] else 'complete'
    except Exception as exc:
        report['errors'].append(dict(task='tick',error=f'{type(exc).__name__}: {exc}'));report['status']='error'
    finally:
        report['finished_at']=_now()
        try:
            atomic_json(folder/'last-tick.json',report)
            with (folder/'ticks.jsonl').open('a') as f:f.write(json.dumps(report,sort_keys=True)+'\n')
        finally:
            fcntl.flock(handle,fcntl.LOCK_UN);handle.close()
    return report


def _tick(folder,history,report):
    validate_history(history)
    # Daily evidence must still be collected when the calendar endpoint fails.
    now=parse_time(_now());year=now.year if now.month>=7 else now.year-1
    season=f'{year}-{(year+1)%100:02d}'
    try:report['daily_injuries']=capture_daily(folder,season,_now())
    except (ValueError,OSError) as exc:
        report['errors'].append(dict(task='daily_injuries',error=str(exc)))
    try:schedule=fetch_schedule(folder/'schedule')
    except (ValueError,OSError) as exc:
        saved=folder/'schedule/latest.json'
        if not saved.exists():raise
        schedule=json.loads(saved.read_text())
        if schedule['season']!=season:raise ValueError('cached calendar belongs to another season') from exc
        report['warnings'].append(dict(task='schedule',error=str(exc),action='Using saved calendar; observation time remains unchanged.'))
    report['schedule_version']=schedule['version']
    statepath=folder/'state.json';state=json.loads(statepath.read_text()) if statepath.exists() else {'jobs':{}}
    db=folder/'forecast.sqlite';raw=folder/'raw';cache=folder/'finals';cache.mkdir(exist_ok=True)
    runs=ledger_runs(db)
    for run in runs:
        snapshot=json.loads(run['snapshot_json']);key=snapshot.get('operation_key')
        if key:
            output=folder/'runs'/run['run_id']
            if not (output/'profiles.json').exists():atomic_json(output/'profiles.json',build_profiles(snapshot,_saved_history(db,run['run_id'])))
            if not (output/'forecast.json').exists():atomic_json(output/'forecast.json',json.loads(run['forecast_json']))
            if not (output/'snapshot.json').exists():atomic_json(output/'snapshot.json',snapshot)
        if key and key not in state['jobs']:
            state['jobs'][key]=run['run_id'];report['recovered_runs']+=1
    atomic_json(statepath,state)
    from .player_sync import enrich_final_cache
    enrich_final_cache(folder)
    combined={(r['game_id'],r['player_id']):r for r in history}
    for p in sorted(cache.glob('*.json')):
        rows=json.loads(p.read_text());validate_history(rows)
        for row in rows:
            key=(row['game_id'],row['player_id']);old=combined.get(key)
            if old is None or parse_time(old['known_at'])<=parse_time(row['known_at']):combined[key]=row
    history=list(combined.values());validate_history(history)
    jobs=due_jobs(schedule,_now());by_id={g['game_id']:g for g in schedule['games']}
    reviews={}
    for job in jobs:
        # Refresh evidence throughout the day, then every five minutes near tipoff.
        seconds=300 if job['stage']=='final' else 900
        slot=int(parse_time(_now()).timestamp())//seconds
        job['key']+=f':model:{MODEL_VERSION}:parser:{PARSER_VERSION}:validator:{PREFLIGHT_VERSION}:slot:{slot}'
        roster_review=folder/'roster-reviews'/(job['game_id']+'.json')
        if roster_review.exists():job['key']+=':roster-review:'+hashlib.sha256(roster_review.read_bytes()).hexdigest()
        path=folder/'reviews'/(job['game_id']+'.json')
        if path.exists():
            review=json.loads(path.read_text());reviews[job['game_id']]=review
            job['key']+=':review:'+hashlib.sha256(canonical(review)).hexdigest()
    needed=[j for j in jobs if j['key'] not in state['jobs']]
    roster=None
    if needed:
        try:roster=fetch_roster(schedule['season'],cache_dir=raw)
        except (ValueError,OSError) as exc:
            saved=folder/'roster.json'
            candidate=json.loads(saved.read_text()) if saved.exists() else None
            if candidate and 0<=(parse_time(_now())-parse_time(candidate['observed_at'])).total_seconds()<=48*3600:
                roster=candidate
            report['warnings'].append(dict(task='roster',error=str(exc),action='Using recent saved roster.' if roster else 'Forecast withheld; injury capture still runs.'))
    if roster:atomic_json(folder/'roster.json',roster)
    capture_cache={}
    for job in needed:
        try:
            game=by_id[job['game_id']]
            snapshot=dict(schema_version=1,season=schedule['season'],as_of=_now(),roster=deepcopy(roster),games=[game],
                          forecast_stage=job['stage'],operation_key=job['key'],schedule_version=schedule['version'],
                          schedule_context=game_context(schedule,game['game_id']))
            try:
                check=fetch_injury_check(snapshot,raw,capture_cache=capture_cache);snapshot['as_of']=_now()
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
                if job['stage']=='final':raise
                report['warnings'].append(dict(game_id=job['game_id'],error=str(exc),action='Preliminary availability remains unresolved.'))
            if roster is None:raise ValueError('No roster observed within 48 hours; injury checkpoint retained, forecast withheld')
            snapshot['as_of']=_now()
            if job['game_id'] in reviews:
                snapshot=apply_review(snapshot,reviews[job['game_id']],skip_superseded=True)
                report['warnings'].extend(dict(task='review',**w) for w in snapshot.get('review_warnings',[]))
            usable=eligible_history(snapshot,history)
            profiles=build_profiles(snapshot,usable)
            verification=run_preflight(snapshot,usable,profiles,folder)
            if verification['status']!='passed':
                report['errors'].append(dict(task='preflight',game_id=job['game_id'],issues=verification['issues']))
                continue
            snapshot['preflight']=verification
            profiles=build_profiles(snapshot,usable)
            prediction=forecast(snapshot,usable)
            run_id=save_run(db,snapshot,usable,prediction)
            # The immutable operation_key in the run is authoritative if interrupted here.
            state['jobs'][job['key']]=run_id;atomic_json(statepath,state)
            output=folder/'runs'/run_id
            atomic_json(output/'profiles.json',profiles)
            atomic_json(output/'forecast.json',prediction)
            atomic_json(output/'snapshot.json',snapshot)
            report['created_runs']+=1
        except Exception as exc:
            report['errors'].append(dict(task='forecast',game_id=job['game_id'],stage=job['stage'],error=f'{type(exc).__name__}: {exc}'))
    # Prioritize pregame deadlines over downloads and postgame reporting.
    for game in schedule['games']:
        if game['status']!='final':continue
        path=cache/(game['game_id']+'.json')
        if path.exists():continue
        try:
            url=boxscore_url(game['game_id']);payload=fetch_json(url,cache_dir=raw)
            rows=normalize_boxscore(payload,url,_now());atomic_json(path,rows)
        except Exception as exc:
            report['errors'].append(dict(task='final_boxscore',game_id=game['game_id'],error=f'{type(exc).__name__}: {exc}'))
    runs=ledger_runs(db)
    for run in runs:
        if run['mode']!='live':continue
        snapshot=json.loads(run['snapshot_json']);prediction=json.loads(run['forecast_json'])
        keys={(p['game_id'],p['player_id'],p['team_id']) for p in prediction['predictions']}
        actuals=[]
        for game in snapshot['games']:
            path=cache/(game['game_id']+'.json')
            if path.exists():
                rows=json.loads(path.read_text())
                actuals.extend(r for r in rows if (r['game_id'],r['player_id'],r['team_id']) in keys)
        if actuals:
            try:evaluate_run(db,run['run_id'],actuals);report['evaluated_runs']+=1
            except Exception as exc:report['errors'].append(dict(task='evaluation',run_id=run['run_id'],error=f'{type(exc).__name__}: {exc}'))
    # Only flag games occurring after operations were first started, not an entire
    # historical season as missed. Unknown/postponed start times cannot be due.
    state.setdefault('tracking_started_at',report['started_at'])
    for game in schedule['games']:
        start=game['start_time']
        if start and game['status']!='postponed' and parse_time(state['tracking_started_at'])<parse_time(start)<=parse_time(_now()):
            key=f"{game['game_id']}:{start}:final"
            if not any(k==key or k.startswith(key+':') for k in state['jobs']):report['missed_final_games'].append(game['game_id'])
    atomic_json(statepath,state)
    atomic_json(folder/'performance.json',performance_report(db))
    atomic_json(folder/'benchmarks.json',benchmark_report(db))
    if (folder/'roster.json').exists():
        try:
            from .player_sync import sync_player_data
            report['player_sync']=sync_player_data(folder,history,roster=roster,max_shot_games=2)
            report['warnings'].extend(dict(task='shot_data',**e) for e in report['player_sync']['errors'])
            from .postgame import build_postgames
            report['postgame']=build_postgames(folder)
            report['warnings'].extend(dict(task='postgame',**e) for e in report['postgame']['errors'])
        except Exception as exc:
            report['errors'].append(dict(task='player_sync',error=f'{type(exc).__name__}: {exc}'))
