"""Serve one frozen operations directory from main and from the dashboard branch, and compare.

    python3 scripts/research/dashboard_compare_servers.py --directory <frozen-copy> \
        --main <main checkout> --branch <this checkout> [--report report.json]

The page build on a full season's copy can take many minutes on a busy disk; requests wait up to an hour.

Use a frozen copy (never the live production directory: its files change
between requests). Both servers open the ledger read-only (URI mode=ro).

Checks (exit 1 on any mismatch):
- every game's calendar fields (everything but the detail fields) on the branch
  page equal main's;
- for every game day, /api/forecasts?date=D on the branch returns exactly main's
  detail fields (forecast_stage, forecasts, injury_capture, preflight,
  availability_update) for that day's games, and no other games;
- the branch page's default day carries the same details inline;
- every other top-level key equals main's (except generated_at, daily_reviews
  and the branch's new keys detail_days, default_day, daily_review_dates);
- daily_review_dates equals main's review dates, and /api/daily-reviews/D equals
  main's daily_reviews[D] for every date.
Timings: main's page twice (it rebuilds per request); the branch's page cold,
warm, warm with gzip, and a 304; every day endpoint cold and warm.
"""
import argparse
import gzip
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

DETAIL_FIELDS = ('forecast', 'forecast_stage', 'forecasts', 'injury_capture', 'preflight', 'availability_update')
NEW_KEYS = {'detail_days', 'default_day', 'daily_review_dates'}


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def start(checkout, directory, assets):
    port = free_port()
    process = subprocess.Popen(['nice', '-n', '10', sys.executable, '-m', 'nba_forecast', 'dashboard', '--directory', str(directory),
                                '--assets', str(assets), '--port', str(port)], cwd=checkout,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(300):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                return process, f'http://127.0.0.1:{port}'
        except OSError:
            if process.poll() is not None:
                raise RuntimeError(process.stderr.read().decode())
            time.sleep(0.1)
    raise RuntimeError('server did not start: ' + checkout)


def get(url, **headers):
    request = urllib.request.Request(url, headers=headers)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=3600) as response:
            body = response.read()
            status, headers = response.status, dict(response.headers)
    except urllib.error.HTTPError as error:
        body, status, headers = error.read(), error.code, dict(error.headers)
    return dict(status=status, seconds=round(time.perf_counter() - started, 4), bytes=len(body), headers=headers, body=body)


def decode(response):
    body = response['body']
    if response['headers'].get('Content-Encoding') == 'gzip':
        body = gzip.decompress(body)
    return json.loads(body)


def day_of(game):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if game.get('start_time'):
        return datetime.fromisoformat(game['start_time'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat()
    if game.get('source_start_time'):
        return datetime.fromisoformat(game['source_start_time'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/New_York')).date().isoformat()
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--main', required=True)
    parser.add_argument('--branch', required=True)
    parser.add_argument('--report')
    args = parser.parse_args()
    args.directory = str(Path(args.directory).resolve())
    args.main, args.branch = str(Path(args.main).resolve()), str(Path(args.branch).resolve())
    assets = tempfile.TemporaryDirectory()
    Path(assets.name, 'index.html').write_text('<!doctype html>')
    report = dict(directory=args.directory, mismatches=[], timings={})
    mismatches = report['mismatches']
    servers = []
    try:
        main_process, main_url = start(args.main, args.directory, assets.name)
        servers.append(main_process)
        branch_process, branch_url = start(args.branch, args.directory, assets.name)
        servers.append(branch_process)
        t = report['timings']
        first = get(main_url + '/api/dashboard')
        second = get(main_url + '/api/dashboard', **{'If-None-Match': first['headers'].get('ETag', '')})
        t['main_page'] = dict(cold_s=first['seconds'], revalidate_s=second['seconds'], revalidate_status=second['status'], bytes=first['bytes'])
        full = decode(first)
        cold = get(branch_url + '/api/dashboard')
        warm = get(branch_url + '/api/dashboard')
        zipped = get(branch_url + '/api/dashboard', **{'Accept-Encoding': 'gzip'})
        not_modified = get(branch_url + '/api/dashboard', **{'If-None-Match': zipped['headers']['ETag'], 'Accept-Encoding': 'gzip'})
        t['branch_page'] = dict(cold_s=cold['seconds'], warm_s=warm['seconds'], gzip_s=zipped['seconds'], not_modified_s=not_modified['seconds'],
                                not_modified_status=not_modified['status'], bytes=cold['bytes'], gzip_bytes=zipped['bytes'])
        page = decode(cold)
        if decode(zipped) != page:
            mismatches.append('gzip body differs from identity body')
        report['default_day'] = page.get('default_day')
        if not full['games']:
            mismatches.append('main served no games: wrong --directory?')
        light = {g['game_id']: g for g in page['games']}
        if set(light) != {g['game_id'] for g in full['games']}:
            mismatches.append('page game IDs differ')
        days = {}
        for g in full['games']:
            days.setdefault(day_of(g), []).append(g)
            mine = light.get(g['game_id'], {})
            calendar = lambda x: {k: v for k, v in x.items() if k not in DETAIL_FIELDS}
            if calendar(g) != calendar(mine):
                mismatches.append(f"calendar fields differ for {g['game_id']}")
            if day_of(g) == page.get('default_day'):
                if {k: mine.get(k) for k in DETAIL_FIELDS if k in g} != {k: g[k] for k in DETAIL_FIELDS if k in g}:
                    mismatches.append(f"inline details differ for {g['game_id']} on the default day")
            elif set(DETAIL_FIELDS) & set(mine):
                mismatches.append(f"{g['game_id']} carries details off the default day")
        day_cold, day_warm, day_bytes = [], [], []
        for day, games in sorted((d, g) for d, g in days.items() if d):
            response = get(branch_url + '/api/forecasts?date=' + day)
            again = get(branch_url + '/api/forecasts?date=' + day)
            day_cold.append(response['seconds'])
            day_warm.append(again['seconds'])
            day_bytes.append((day, response['bytes']))
            if response['status'] != 200:
                mismatches.append(f'/api/forecasts?date={day} returned {response["status"]}')
                continue
            served = decode(response)['games']
            expected = {g['game_id']: {k: g[k] for k in DETAIL_FIELDS if k in g} for g in games}
            if served != expected:
                mismatches.append(f'details differ on {day}: {sorted(set(served) ^ set(expected)) or "same games, different fields"}')
        t['days'] = dict(count=len(day_cold), cold_total_s=round(sum(day_cold), 2), cold_max_s=max(day_cold or [0]),
                         warm_max_s=max(day_warm or [0]), largest=sorted(day_bytes, key=lambda x: -x[1])[:5])
        for key in set(full) | set(page):
            if key in {'generated_at', 'daily_reviews', 'games'} | NEW_KEYS:
                continue
            if full.get(key) != page.get(key):
                mismatches.append(f'top-level {key} differs')
        reviews = full.get('daily_reviews') or {}
        if page.get('daily_review_dates') != sorted(reviews):
            mismatches.append('daily_review_dates differ from main review dates')
        review_times = []
        for day, value in sorted(reviews.items()):
            response = get(branch_url + f'/api/daily-reviews/{day}', **{'Accept-Encoding': 'gzip'})
            review_times.append(response['seconds'])
            if response['status'] != 200 or decode(response) != value:
                mismatches.append(f'daily review {day} differs')
        t['reviews'] = dict(count=len(review_times), max_s=max(review_times or [0]))
        report['sizes_mb'] = dict(main_page=round(first['bytes'] / 1e6, 2), branch_page=round(cold['bytes'] / 1e6, 2),
                                  branch_page_gzip=round(zipped['bytes'] / 1e6, 2),
                                  main_forecasts=round(sum(len(json.dumps(g.get('forecasts'))) for g in full['games']) / 1e6, 2),
                                  main_daily_reviews=round(len(json.dumps(reviews)) / 1e6, 2))
    finally:
        for process in servers:
            process.terminate()
            process.wait(timeout=30)
        assets.cleanup()
    text = json.dumps(report, indent=1)
    print(text)
    if args.report:
        Path(args.report).write_text(text)
    print('RESULT:', 'MATCH' if not mismatches else f'{len(mismatches)} MISMATCHES')
    return 1 if mismatches else 0


if __name__ == '__main__':
    sys.exit(main())
