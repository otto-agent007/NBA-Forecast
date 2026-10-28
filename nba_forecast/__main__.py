"""Small reproducible CLI. Run `python -m nba_forecast --help`."""

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import gzip
import json
from pathlib import Path
import sqlite3
import sys

from .validation import validate_inputs


def _reject_constant(value):
    raise ValueError(f'non-finite JSON number: {value}')


def read_json(path):
    raw=Path(path).read_bytes()
    if str(path).endswith('.gz'):
        raw=gzip.decompress(raw)
    return json.loads(raw.decode('utf-8'), parse_constant=_reject_constant)


def _emit(value, output=None):
    text = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n'
    if output:
        with Path(output).open('x', encoding='utf-8') as stream:
            stream.write(text)
    else:
        print(text, end='')


def _rows(paths):
    rows = []
    for path in paths or []:
        data = read_json(path)
        if not isinstance(data, list):
            raise ValueError(f'{path} must contain a normalized list of observations')
        rows.extend(data)
    return rows


def _parser():
    parser = argparse.ArgumentParser(description='Auditable regular-season player scoring forecasts')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('validate', 'forecast'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--snapshot', required=True)
        cmd.add_argument('--history', nargs='*', default=[])
        cmd.add_argument('--scope', choices=('league', 'subset'), default='league')
        cmd.add_argument('--output')
        if name == 'forecast':
            cmd.add_argument('--stage', choices=('preliminary','final'), default='preliminary')
            cmd.add_argument('--injury-check')
            cmd.add_argument('--cache-dir',default='data/raw',help='Archived injury PDFs for the live preflight cross-check')
            cmd.add_argument('--db', required=True)
            cmd.add_argument('--mode', choices=('live', 'replay', 'demo'), default='live')
    evaluate = sub.add_parser('evaluate')
    evaluate.add_argument('--db', required=True)
    evaluate.add_argument('--run-id', required=True)
    evaluate.add_argument('--actuals', nargs='+', required=True)
    evaluate.add_argument('--output')
    report = sub.add_parser('report')
    report.add_argument('--db', required=True)
    report.add_argument('--output')
    normalize = sub.add_parser('normalize-boxscore')
    normalize.add_argument('--input', required=True)
    normalize.add_argument('--provenance', required=True)
    normalize.add_argument('--output', required=True)
    fetch = sub.add_parser('fetch-boxscore')
    fetch.add_argument('--game-id', required=True)
    fetch.add_argument('--cache-dir', default='data/raw')
    fetch.add_argument('--output', required=True)
    slate = sub.add_parser('fetch-slate')
    window=slate.add_mutually_exclusive_group()
    window.add_argument('--until', help='ISO timestamp; default next 24 hours')
    window.add_argument('--minutes-ahead', type=int, help='Use 30 for final-window games')
    slate.add_argument('--cache-dir', default='data/raw')
    slate.add_argument('--output', required=True)
    backfill = sub.add_parser('backfill')
    backfill.add_argument('--seasons', nargs='+', required=True)
    backfill.add_argument('--directory', required=True)
    backfill.add_argument('--workers', type=int, default=4)
    backfill.add_argument('--output')
    injury = sub.add_parser('injury-check')
    injury.add_argument('--snapshot', required=True)
    injury.add_argument('--cache-dir', default='data/raw')
    injury.add_argument('--output', required=True)
    calendar = sub.add_parser('fetch-schedule')
    calendar.add_argument('--directory', default='data/operations/schedule')
    calendar.add_argument('--output')
    profiles = sub.add_parser('profiles')
    profiles.add_argument('--snapshot', required=True)
    profiles.add_argument('--history', nargs='*', default=[])
    profiles.add_argument('--output')
    extract = sub.add_parser('apply-injuries')
    extract.add_argument('--snapshot', required=True)
    extract.add_argument('--schedule', required=True)
    extract.add_argument('--injury-check', required=True)
    extract.add_argument('--cache-dir', default='data/raw')
    extract.add_argument('--output', required=True)
    tick = sub.add_parser('tick')
    tick.add_argument('--directory', required=True)
    tick.add_argument('--history', nargs='*', default=[])
    tick.add_argument('--output')
    benchmarks = sub.add_parser('benchmarks')
    benchmarks.add_argument('--db', required=True)
    benchmarks.add_argument('--output')
    dedupe = sub.add_parser('dedupe-histories', help='Rebuild a ledger so each distinct input history is stored once')
    dedupe.add_argument('--directory', required=True, help='Operations directory holding forecast.sqlite')
    dedupe.add_argument('--output')
    backtest = sub.add_parser('backtest', help='Replay a season with the current model code and score it (docs/backtest.md)')
    backtest.add_argument('--history', required=True, nargs='+', help='Normalized box-score history (.json or .json.gz), one per season')
    backtest.add_argument('--team-games', required=True, nargs='+', help='Reconciled team games (datasets/model-upgrade-*/team-games.json.gz), one per season')
    backtest.add_argument('--output', required=True, help='New directory for scoreboard.json and rows.json.gz')
    backtest.add_argument('--reference', help='Directory of an earlier backtest to compare against')
    backtest.add_argument('--warmup-fraction', type=float, default=.3, help='Share of game dates used only as history')
    backtest.add_argument('--limit', type=int, help='Only the last N evaluation games (smoke runs)')
    backtest.add_argument('--market', nargs='+', help='Closing lines for the replayed games (datasets/market-closing-*/closing-lines.json.gz), one per season')
    backtest.add_argument('--injuries', nargs='+', help='Anchor injury reports for the replayed games (datasets/injury-reports-*/statuses.json.gz), one per season')
    backtest.add_argument('--previous-history', nargs='+', help="Each season's previous season of box scores, seeding the history (one per season)")
    backtest.add_argument('--previous-team-games', nargs='+', help="Each season's previous season of team games, seeding the ratings (one per season)")
    backtest.add_argument('--production-path', metavar='ARTIFACT', help="Replay production's path with this model artifact's parameters and context model (datasets/model-upgrade-2026-10-23/artifact.json.gz)")
    backtest.add_argument('--json', action='store_true', help='Print the scoreboard JSON instead of the table')
    dashboard = sub.add_parser('dashboard')
    dashboard.add_argument('--directory', required=True)
    dashboard.add_argument('--assets', default='dashboard/dist')
    dashboard.add_argument('--port', type=int, default=8765)
    dashboard.add_argument('--output', help='Export a static capture instead of starting the local server')
    post=sub.add_parser('postgame')
    post.add_argument('--directory',required=True)
    post.add_argument('--output')
    daily=sub.add_parser('daily-review')
    daily.add_argument('--directory',required=True)
    daily.add_argument('--date',help='Pacific date YYYY-MM-DD; defaults to automatic today/yesterday reports')
    daily.add_argument('--output')
    enrich=sub.add_parser('enrich-history')
    enrich.add_argument('--archive',required=True)
    enrich.add_argument('--output',required=True)
    sync=sub.add_parser('sync-players')
    sync.add_argument('--directory',required=True)
    sync.add_argument('--history',nargs='*',default=[])
    sync.add_argument('--max-shot-games',type=int,default=16)
    sync.add_argument('--output')
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    exit_code = 0
    try:
        # Refuse overwrite before any ledger or network side effect.
        if args.output and Path(args.output).exists():
            raise ValueError('output exists; use a new path to preserve the previous artifact')
        if args.command in ('validate', 'forecast'):
            snapshot, history = read_json(args.snapshot), _rows(args.history)
            if args.command == 'forecast':
                snapshot['forecast_stage']=args.stage
                if args.injury_check:
                    snapshot['injury_check']=read_json(args.injury_check)
                if args.stage == 'final' and 'injury_check' not in snapshot:
                    raise ValueError('final forecast requires --injury-check evidence')
            validate_inputs(snapshot, history, scope=args.scope)
            if args.command == 'validate':
                value = dict(valid=True, scope=args.scope, roster_players=len(snapshot['roster']['players']),
                             history_rows=len(history), games=len(snapshot['games']))
            else:
                from .model import forecast
                from .store import save_run
                if args.mode == 'live':
                    if args.scope != 'league':
                        raise ValueError('live runs require league scope')
                    from .preflight import run_preflight
                    from .profiles import build_profiles
                    check=run_preflight(snapshot,history,build_profiles(snapshot,history),Path(args.db).parent,cache_dir=args.cache_dir)
                    if check['status']!='passed':
                        raise ValueError('preflight blocked: '+ '; '.join(i['detail'] for i in check['issues']))
                    snapshot['preflight']=check
                result = forecast(snapshot, history)
                run_id = save_run(args.db, snapshot, history, result, mode=args.mode, scope=args.scope)
                value = dict(run_id=run_id, mode=args.mode, scope=args.scope, forecast=result)
        elif args.command == 'postgame':
            from .postgame import build_postgames
            value=build_postgames(args.directory)
        elif args.command == 'daily-review':
            from .daily_review import build_daily_reviews,review_day
            now=datetime.now(timezone.utc).isoformat()
            value=review_day(args.directory,args.date,now) if args.date else build_daily_reviews(args.directory,now)
        elif args.command == 'enrich-history':
            from .player_sync import enrich_archive
            value=enrich_archive(args.archive)
        elif args.command == 'sync-players':
            from .player_sync import sync_player_data
            value=sync_player_data(args.directory,_rows(args.history),max_shot_games=args.max_shot_games)
        elif args.command == 'dashboard':
            from .dashboard import dashboard_data,serve_dashboard
            if args.output:
                value=dashboard_data(args.directory,compact=True)
                value['source_mode']='static_capture'
            else:
                if not 1<=args.port<=65535:
                    raise ValueError('invalid port')
                serve_dashboard(args.directory,args.assets,args.port)
                return 0
        elif args.command == 'fetch-schedule':
            from .schedule import fetch_schedule
            value=fetch_schedule(args.directory)
        elif args.command == 'profiles':
            from .profiles import build_profiles
            value=build_profiles(read_json(args.snapshot),_rows(args.history))
        elif args.command == 'apply-injuries':
            from .injury_parser import pdf_text,parse_report,apply_report
            snapshot=read_json(args.snapshot)
            schedule=read_json(args.schedule)
            check=read_json(args.injury_check)
            snapshot['as_of']=datetime.now(timezone.utc).isoformat()
            snapshot['injury_check']=check
            for item in check['games']:
                evidence=item['anchor_report']
                if evidence:
                    text=pdf_text(Path(args.cache_dir)/(evidence['sha256']+'.pdf'),evidence['sha256'])
                    snapshot=apply_report(snapshot,parse_report(text,schedule,snapshot['roster'],evidence),[item['game_id']])
            validate_inputs(snapshot,[])
            value=snapshot
        elif args.command == 'tick':
            from .operations import run_tick
            value=run_tick(args.directory,_rows(args.history))
            exit_code=0 if value['status']=='complete' and not value['missed_final_games'] else 2
        elif args.command == 'benchmarks':
            from .benchmarks import benchmark_report
            value=benchmark_report(args.db)
        elif args.command == 'backtest':
            from .backtest import run_seasons, read_json as read_source, sha256_of, format_scoreboard
            from .market_history import read_lines
            from .injury_history import read_statuses
            count = len(args.history)
            if (args.previous_history is None)!=(args.previous_team_games is None):
                raise ValueError('--previous-history and --previous-team-games go together')
            for name in ('team_games', 'market', 'injuries', 'previous_history', 'previous_team_games'):
                if getattr(args, name) is not None and len(getattr(args, name)) != count:
                    raise ValueError(f'--{name.replace("_", "-")} needs one file per --history season')
            seasons = []
            for i in range(count):
                paths = dict(history=args.history[i], team_games=args.team_games[i],
                             market=args.market[i] if args.market else None,
                             injuries=args.injuries[i] if args.injuries else None,
                             previous_history=args.previous_history[i] if args.previous_history else None,
                             previous_team_games=args.previous_team_games[i] if args.previous_team_games else None)
                seasons.append(dict(history=read_source(paths['history']), records=read_source(paths['team_games']),
                                    market=read_lines(paths['market']) if paths['market'] else None,
                                    injuries=read_statuses(paths['injuries']) if paths['injuries'] else None,
                                    previous_history=read_source(paths['previous_history']) if paths['previous_history'] else None,
                                    previous_records=read_source(paths['previous_team_games']) if paths['previous_team_games'] else None,
                                    sources={k: dict(path=str(v), sha256=sha256_of(v)) for k, v in paths.items() if v}))
            artifact = read_source(args.production_path) if args.production_path else None
            board = run_seasons(seasons, args.output, reference=args.reference, warmup_fraction=args.warmup_fraction, artifact=artifact,
                                limit=args.limit, progress=lambda item: print(json.dumps(item), file=sys.stderr, flush=True))
            print(json.dumps(board, indent=2, sort_keys=True) if args.json else format_scoreboard(board))
            return 3 if board.get('comparison', {}).get('verdict') == 'worse' else 0
        elif args.command == 'dedupe-histories':
            import fcntl
            from .store import dedupe_histories
            folder=Path(args.directory)
            with (folder/'tick.lock').open('a') as lock:
                try:
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise ValueError('a tick is running; retry when it finishes') from exc
                value=dedupe_histories(folder/'forecast.sqlite')
        elif args.command == 'backfill':
            from .backfill import backfill_seasons
            result=backfill_seasons(args.seasons,args.directory,args.workers)
            value={k:v for k,v in result.items() if k!='games'}
            value['manifest']=str(Path(args.directory)/'manifest.json')
            exit_code=0 if result['status']=='complete' else 2
        elif args.command == 'injury-check':
            from .injuries import fetch_injury_check
            value=fetch_injury_check(read_json(args.snapshot),args.cache_dir)
        elif args.command == 'evaluate':
            from .store import evaluate_run
            value = evaluate_run(args.db, args.run_id, _rows(args.actuals))
        elif args.command == 'report':
            from .store import performance_report
            value = performance_report(args.db)
        elif args.command == 'normalize-boxscore':
            from .data import normalize_boxscore
            raw = Path(args.input).read_bytes()
            metadata = read_json(args.provenance)
            if hashlib.sha256(raw).hexdigest() != metadata['sha256']:
                raise ValueError('raw source SHA256 does not match provenance')
            value = normalize_boxscore(json.loads(raw, parse_constant=_reject_constant),
                                       metadata['source_url'], metadata['retrieved_at'])
        elif args.command == 'fetch-boxscore':
            from .data import boxscore_url, fetch_json, normalize_boxscore
            source = boxscore_url(args.game_id)
            payload = fetch_json(source, cache_dir=args.cache_dir)
            value = normalize_boxscore(payload, source, datetime.now(timezone.utc).isoformat())
        elif args.command == 'fetch-slate':
            from .data import SCHEDULE_URL, build_snapshot, fetch_json, fetch_roster
            if args.minutes_ahead is not None and not 1 <= args.minutes_ahead <= 1440:
                raise ValueError('minutes-ahead must be between 1 and 1440')
            schedule = fetch_json(SCHEDULE_URL, cache_dir=args.cache_dir)
            roster = fetch_roster(schedule['leagueSchedule']['seasonYear'], cache_dir=args.cache_dir)
            cutoff=datetime.now(timezone.utc)
            until=(cutoff+timedelta(minutes=args.minutes_ahead)).isoformat() if args.minutes_ahead else args.until
            value = build_snapshot(roster, schedule, cutoff.isoformat(), until)
        _emit(value, args.output)
        return exit_code
    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
