"""Official injury reports for a replayed season, as a live run would have read them.

For each team-game record the anchor is the newest official report published by
the game's T-minus-30 checkpoint, the one a live final-stage forecast uses
(``injuries.checkpoints``). Reports were hourly until 2025-12-21 (published at
half past) and every 15 minutes from 2025-12-22. When the anchor slot is absent
the next earlier one is tried, up to ``MAX_AGE`` before the checkpoint; the
report's age is kept. Every PDF is archived by hash with its retrieval time.

The parse is production's (``injury_parser.parse_report``): exact name, team and
game joins against the season's box-score rosters, and a team counts as fully
reported only when the report is complete and every row for it resolves. Players
it lists keep their reported status; players it omits are recorded nowhere, and
the replay treats them as available, as live runs do for a complete team.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import threading
import time

from .data import _fetch
from .injuries import EASTERN, LEGACY_LAST_DAY, report_time
from .injury_parser import PARSER_VERSION, parse_report, pdf_text
from .schedule import atomic_json, canonical
from .validation import parse_time

VERSION = 'injury-history-1'
CHECKPOINT = timedelta(minutes=30)
MAX_AGE = timedelta(hours=3)
BASE = 'https://ak-static.cms.nba.com/referee/injury/Injury-Report_'
# The report CDN serves some files slowly and blocks bursts.
TIMEOUT = 20
PAUSE = 4.
RETRY_PAUSE = 30.


def slot_url(local):
    """The official URL of the report published at an Eastern time, in that date's naming."""
    day = local.date().isoformat()
    if day <= LEGACY_LAST_DAY:
        return BASE + day + '_' + local.strftime('%I%p') + '.pdf'
    return BASE + day + '_' + local.strftime('%I_%M%p') + '.pdf'


def candidates(game_time):
    """Report URLs a game's checkpoint could anchor on, newest first, within MAX_AGE."""
    checkpoint = parse_time(game_time) - CHECKPOINT
    local = checkpoint.astimezone(EASTERN).replace(second=0, microsecond=0)
    found = []
    # Step back a quarter hour at a time; each date's naming keeps the slots it publishes.
    slot = local.replace(minute=local.minute - local.minute % 15)
    while checkpoint - slot.astimezone(timezone.utc) <= MAX_AGE:
        url = slot_url(slot)
        published = report_time(url)
        if published is not None and published <= checkpoint and url not in found:
            if published == slot.astimezone(timezone.utc):
                found.append(url)
        slot -= timedelta(minutes=15)
    return found


def _http_status(exc):
    text = str(exc)
    for code in ('403', '404'):
        if f'HTTP {code}' in text:
            return int(code)
    return None


def _slot(url, raw, fetch, sleep, pause, retry_pause):
    """(manifest entry, None) for a fetched or absent slot, or (None, failure) after a network error."""
    for attempt in range(2):
        try:
            body, retrieved = fetch(url, raw, timeout=TIMEOUT)
            if not body.startswith(b'%PDF-'):
                raise ValueError('official injury report response is not a PDF')
            sleep(pause)
            return dict(status='ok', sha256=hashlib.sha256(body).hexdigest(), retrieved_at=retrieved), None
        except ValueError as exc:
            code = _http_status(exc)
            if code is None:
                sleep(pause)
                return None, f'{url}: {exc}'
            if attempt == 0:
                sleep(retry_pause)
    sleep(pause)
    return dict(status='absent', http_status=code, checked_at=datetime.now(timezone.utc).isoformat()), None


def _recover(slots, raw):
    """Add reports an interrupted run archived but did not record: each PDF's provenance names its URL."""
    for meta_path in sorted(Path(raw).glob('*.provenance.json')):
        meta = json.loads(meta_path.read_text())
        url = meta.get('source_url', '')
        if url not in slots and report_time(url) and (Path(raw) / (meta['sha256'] + '.pdf')).exists():
            slots[url] = dict(status='ok', sha256=meta['sha256'], retrieved_at=meta['retrieved_at'])


def fetch_reports(records, directory, *, pause=PAUSE, retry_pause=RETRY_PAUSE, fetch=_fetch, sleep=time.sleep,
                  workers=1):
    """Archive each game's anchor report; resumable through ``manifest.json``.

    A 403 or 404 slot is retried once after ``retry_pause`` (the CDN answers a
    throttled request the same way as an absent file) and then recorded absent.
    A network failure stops that game, to be retried on the next run. With
    ``workers`` above one, every game's first-choice slot is fetched in parallel
    first (the CDN is slow per request, not per client); fallbacks follow in order.
    """
    folder = Path(directory)
    raw = folder / 'raw'
    path = folder / 'manifest.json'
    manifest = json.loads(path.read_text()) if path.exists() else dict(version=VERSION, slots={}, anchors={})
    slots, anchors = manifest['slots'], manifest['anchors']
    _recover(slots, raw)
    ordered = sorted(records, key=lambda r: (r['game_time'], r['game_id']))
    if workers > 1:
        first = sorted({candidates(r['game_time'])[0] for r in ordered if r['game_id'] not in anchors} - set(slots))
        lock = threading.Lock()

        def work(url):
            entry, _ = _slot(url, raw, fetch, sleep, pause, retry_pause)
            if entry is not None:
                with lock:  # each worker records its own result
                    slots[url] = entry
                    atomic_json(path, manifest)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(work, first))
    failures = {}
    for record in ordered:
        if record['game_id'] in anchors:
            continue
        anchor = None
        for url in candidates(record['game_time']):
            entry = slots.get(url)
            if entry is None:
                entry, failure = _slot(url, raw, fetch, sleep, pause, retry_pause)
                if entry is None:
                    failures[record['game_id']] = failure
                    break
                slots[url] = entry
                atomic_json(path, manifest)
            if entry['status'] == 'ok':
                anchor = url
                break
        if record['game_id'] in failures:
            continue
        anchors[record['game_id']] = anchor
        atomic_json(path, manifest)
    manifest['failures'] = failures
    atomic_json(path, manifest)
    return manifest


def team_names(calendar):
    """Team ID -> (full name, tricode) from a schedule calendar's games."""
    teams = {}
    for g in calendar['games']:
        for side in ('home', 'away'):
            if g.get(side + '_team_id') not in (None, '0'):
                teams[g[side + '_team_id']] = (g[side + '_name'], g[side + '_tricode'])
    return teams


def extract(records, history, manifest, directory, teams):
    """Each record's anchor report, parsed against the season's box-score rosters.

    Returns per game: the evidence, the report's age at the checkpoint, the team
    IDs it fully reports, each listed player's [status, reported status, team
    ID], and the parser issues touching the game's teams.
    """
    raw = Path(directory) / 'raw'
    schedule = dict(games=[])
    for r in records:
        (home_name, home_code), (away_name, away_code) = teams[r['home_team_id']], teams[r['away_team_id']]
        schedule['games'].append(dict(game_id=r['game_id'], start_time=r['game_time'],
                                      home_team_id=r['home_team_id'], away_team_id=r['away_team_id'],
                                      home_name=home_name, away_name=away_name,
                                      home_tricode=home_code, away_tricode=away_code))
    players = {}
    for row in history:
        players[(row['team_id'], row['player_id'])] = dict(player_id=row['player_id'], name=row['name'],
                                                            team_id=row['team_id'])
    roster = dict(players=list(players.values()))
    parsed = {}
    games = {}
    for record in records:
        gid = record['game_id']
        url = manifest['anchors'].get(gid)
        if url is None:
            games[gid] = dict(report=None)
            continue
        slot = manifest['slots'][url]
        if url not in parsed:
            published = report_time(url).isoformat()
            evidence = dict(source_url=url, published_at=published, retrieved_at=slot['retrieved_at'],
                            sha256=slot['sha256'])
            parsed[url] = parse_report(pdf_text(raw / (slot['sha256'] + '.pdf'), slot['sha256']), schedule, roster,
                                       evidence)
        report = parsed[url]
        sides = (record['home_team_id'], record['away_team_id'])
        day = parse_time(record['game_time']).astimezone(EASTERN).date().isoformat()
        checkpoint = parse_time(record['game_time']) - CHECKPOINT
        games[gid] = dict(
            report=report['evidence'],
            age_minutes=(checkpoint - parse_time(report['evidence']['published_at'])).total_seconds() / 60,
            covered_team_ids=sorted({c['team_id'] for c in report['team_coverage']
                                     if c['date'] == day and c['team_id'] in sides}),
            complete_team_ids=sorted({c['team_id'] for c in report['complete_teams']
                                      if c['date'] == day and c['team_id'] in sides}),
            statuses={row['player_id']: [row['status'], row['reported_status'], row['team_id']]
                      for row in report['rows'] if row['game_id'] == gid},
            issues=[i for i in report['issues'] if i.get('game_id') == gid
                    or (i.get('team_id') in sides and i.get('date') in (None, day))])
    return games


def summary(games):
    reported = [g for g in games.values() if g['report']]
    sides = 2 * len(games)
    return dict(games=len(games), with_report=len(reported),
                teams_covered=sum(len(g['covered_team_ids']) for g in reported),
                teams_complete=sum(len(g['complete_team_ids']) for g in reported), team_sides=sides,
                listed_players=sum(len(g['statuses']) for g in reported),
                listed_out=sum(s[0] == 'out' for g in reported for s in g['statuses'].values()),
                listed_questionable=sum(s[0] == 'questionable' for g in reported for s in g['statuses'].values()),
                mean_age_minutes=(sum(g['age_minutes'] for g in reported) / len(reported)) if reported else None,
                max_age_minutes=max((g['age_minutes'] for g in reported), default=None),
                issues=sum(len(g['issues']) for g in reported))


def read_statuses(path):
    raw = Path(path).read_bytes()
    data = json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)
    return data['games']


def _read(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--team-games', required=True, help='Team-game records (json or json.gz)')
    parser.add_argument('--history', required=True, help='Box-score history whose rows give each team roster')
    parser.add_argument('--calendar', required=True, help='Schedule calendar naming the 30 teams and tricodes')
    parser.add_argument('--season', required=True, nargs='+', help='Seasons to keep from the records, e.g. 2025-26')
    parser.add_argument('--directory', required=True, help='Raw archive and manifest (resumable)')
    parser.add_argument('--output', help='Write statuses.json.gz here once every game is resolved')
    parser.add_argument('--fetch', action='store_true', help='Fetch missing reports first')
    args = parser.parse_args()
    records = [r for r in _read(args.team_games) if r['season'] in args.season]
    if args.fetch:
        fetch_reports(records, args.directory)
    manifest = json.loads((Path(args.directory) / 'manifest.json').read_text())
    pending = [r['game_id'] for r in records if r['game_id'] not in manifest['anchors']]
    result = dict(records=len(records), pending=len(pending), failures=len(manifest.get('failures') or {}),
                  slots=len(manifest['slots']), absent=sum(s['status'] == 'absent' for s in manifest['slots'].values()))
    if args.output and not pending:
        history = [r for r in _read(args.history) if r['season'] in args.season]
        games = extract(records, history, manifest, args.directory, team_names(_read(args.calendar)))
        data = dict(version=VERSION, parser_version=PARSER_VERSION, seasons=args.season, summary=summary(games),
                    games=games)
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(gzip.compress(canonical(data), mtime=0))
        result.update(summary=data['summary'], output=str(out),
                      sha256=hashlib.sha256(out.read_bytes()).hexdigest())
        atomic_json(out.parent / 'manifest.json', dict(
            version=VERSION, parser_version=PARSER_VERSION, seasons=args.season, team_games=args.team_games,
            history=args.history, files={out.name: result['sha256']}, summary=data['summary'],
            slots=result['slots'], absent_slots=result['absent']))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
