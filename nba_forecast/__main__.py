"""Small reproducible CLI. Run `python -m nba_forecast --help`."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from .validation import validate_inputs


def _reject_constant(value):
    raise ValueError(f'non-finite JSON number: {value}')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), parse_constant=_reject_constant)


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
    slate.add_argument('--until', help='ISO timestamp; default next 24 hours')
    slate.add_argument('--cache-dir', default='data/raw')
    slate.add_argument('--output', required=True)
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        # Refuse overwrite before any ledger or network side effect.
        if args.output and Path(args.output).exists():
            raise ValueError('output exists; use a new path to preserve the previous artifact')
        if args.command in ('validate', 'forecast'):
            snapshot, history = read_json(args.snapshot), _rows(args.history)
            validate_inputs(snapshot, history, scope=args.scope)
            if args.command == 'validate':
                value = dict(valid=True, scope=args.scope, roster_players=len(snapshot['roster']['players']),
                             history_rows=len(history), games=len(snapshot['games']))
            else:
                from .model import forecast
                from .store import save_run
                result = forecast(snapshot, history)
                run_id = save_run(args.db, snapshot, history, result, mode=args.mode, scope=args.scope)
                value = dict(run_id=run_id, mode=args.mode, scope=args.scope, forecast=result)
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
            schedule = fetch_json(SCHEDULE_URL, cache_dir=args.cache_dir)
            roster = fetch_roster(schedule['leagueSchedule']['seasonYear'], cache_dir=args.cache_dir)
            value = build_snapshot(roster, schedule, datetime.now(timezone.utc).isoformat(), args.until)
        _emit(value, args.output)
        return 0
    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
