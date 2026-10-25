"""A replay season of box scores and team games from ESPN game summaries.

For a season whose official NBA box scores cannot be retrieved in bulk (the NBA
edge stalls for minutes per request from this host), ESPN's summary carries the
same counting statistics, starters and did-not-play flags. Differences from the
official history, all recorded in each record's provenance:

- IDs are ESPN's (``espn-<event>`` games, ``espn-<athlete>`` players); teams are
  the NBA team IDs, matched by tricode.
- Minutes are whole minutes, as ESPN publishes them; team minutes are the
  regulation-plus-overtime length of the game.
- ESPN lists some inactive players and omits others; the replay adds every
  player the official injury report lists (``backtest._roster``).

Only regular-season games count: the NBA Cup championship, which ESPN files as a
regular-season game but the league does not count, is excluded. Each response is
archived by hash with its retrieval time (``reference.fetch_reference``).
"""
import argparse
from datetime import date, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import time

from .espn_actuals import _count, _counts, _minutes
from .injury_history import team_names
from .reference import BASE, board_events, code, fetch_reference
from .schedule import CITY_ZONES, STATE_ZONES, atomic_json, canonical
from .validation import parse_time, validate_history

VERSION = 'espn-season-1'
ARCHIVE_AGE = 10 ** 10  # finished games do not change
EXCLUDED_NOTES = ('NBA Cup Championship',)


def _iso(value):
    return parse_time(value).strftime('%Y-%m-%dT%H:%M:%SZ')


def season_events(start, end, directory, teams, *, pause=.25):
    """ESPN event IDs of final two-NBA-team games on each date from ``start`` to ``end`` inclusive."""
    codes = {code(t) for _, t in teams.values()}
    events, day = [], date.fromisoformat(start)
    while day <= date.fromisoformat(end):
        url = BASE + 'scoreboard?dates=' + day.strftime('%Y%m%d')
        cached = (Path(directory) / (hashlib.sha256(url.encode()).hexdigest() + '.json')).exists()
        board, meta = fetch_reference(url, directory, max_age=ARCHIVE_AGE)
        for event in board.get('events', []):
            if (event.get('season') or {}).get('type') not in (None, 2):
                continue
            for _, item in board_events(dict(events=[event])):
                if {item['home'], item['away']} <= codes and item['status_name'] in ('STATUS_FINAL', 'STATUS_FINAL_OVERTIME'):
                    events.append(dict(event_id=str(item['provider_event_id']), scoreboard=meta))
        if pause and not cached:
            time.sleep(pause)
        day += timedelta(days=1)
    return events


def normalize(summary, meta, teams, season):
    """(history rows, team-game record) for one final regular-season summary, or raise ValueError."""
    header = summary['header']
    event = str(header['id'])
    if meta['source_url'] != BASE + 'summary?event=' + event:
        raise ValueError('summary identity mismatch')
    if header['season']['year'] != int(season[:4]) + 1 or header['season']['type'] != 2:
        raise ValueError('summary season mismatch')
    if header.get('gameNote') in EXCLUDED_NOTES:
        raise ValueError(f"excluded: {header['gameNote']}")
    comp = header['competitions'][0]
    if len(header['competitions']) != 1 or not comp['status']['type'].get('completed'):
        raise ValueError('summary is not one final competition')
    by_code = {code(tricode): tid for tid, (_, tricode) in teams.items()}
    sides = {c['homeAway']: c for c in comp['competitors']}
    if set(sides) != {'home', 'away'}:
        raise ValueError('summary team sides not unique')
    ids = {side: by_code[code(c['team']['abbreviation'])] for side, c in sides.items()}
    periods = {len(c.get('linescores') or []) for c in sides.values()}
    if len(periods) != 1 or min(periods) < 4:
        raise ValueError('summary periods incomplete')
    length = 240. + 25. * (periods.pop() - 4)
    game_id, game_time, known = 'espn-' + event, _iso(comp['date']), meta['retrieved_at']
    rows, stats, notes = [], {}, []
    for side, competitor in sides.items():
        tid, opponent = ids[side], ids['away' if side == 'home' else 'home']
        espn_team = str(competitor['team']['id'])
        box = [b for b in summary['boxscore']['players'] if str(b['team']['id']) == espn_team]
        team_box = [b for b in summary['boxscore']['teams'] if str(b['team']['id']) == espn_team]
        if len(box) != 1 or len(team_box) != 1 or len(box[0]['statistics']) != 1:
            raise ValueError('summary box score not unique')
        group = box[0]['statistics'][0]
        keys = group['keys']
        for entry in group['athletes']:
            athlete = entry['athlete']
            row = dict(game_id=game_id, season=season, season_type='Regular Season', game_time=game_time,
                       known_at=known, source_url=meta['source_url'], player_id='espn-' + str(athlete['id']),
                       name=athlete['displayName'], team_id=tid, opponent_id=opponent, starter=bool(entry.get('starter')))
            values = dict(zip(keys, entry.get('stats') or []))
            # ESPN shows '--' minutes, with every count zero, for some players it does not flag as DNP.
            unrecorded = values.get('minutes') == '--' and all(
                v in ('0', '0-0', '--') for k, v in values.items() if k not in ('minutes', 'plusMinus'))
            if entry.get('didNotPlay') or not values or unrecorded:
                row.update(status='dnp', minutes=0., pts=0, fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0, tov=0,
                           reb=0, ast=0, stl=0, blk=0, starter=False)
            else:
                minutes, _ = _minutes(values['minutes'])
                row.update(status='played', minutes=float(minutes), pts=_count(values['points']), **_counts(values),
                           reb=_count(values['rebounds']), ast=_count(values['assists']),
                           stl=_count(values['steals']), blk=_count(values['blocks']))
            rows.append(row)
        team = {s['name']: s['displayValue'] for s in team_box[0]['statistics']}
        counts = dict(pts=_count(competitor['score']), fga=int(team['fieldGoalsMade-fieldGoalsAttempted'].split('-')[1]),
                      fta=int(team['freeThrowsMade-freeThrowsAttempted'].split('-')[1]),
                      oreb=_count(team['offensiveRebounds']), player_tov=_count(team['turnovers']))
        try:
            counts.update(team_turnovers=_count(team['teamTurnovers']), total_turnovers=_count(team['totalTurnovers']))
            counts['tov'] = counts['total_turnovers']
        except (KeyError, ValueError):
            # A few summaries carry a negative team-turnover count; keep the players' total and say so.
            counts.update(team_turnovers=None, total_turnovers=None, tov=counts['player_tov'])
            notes.append(f'{tid}: ESPN team turnovers invalid; tov is the player total')
        mine = [r for r in rows if r['team_id'] == tid]
        for field, total in (('pts', 'pts'), ('fga', 'fga'), ('fta', 'fta'), ('tov', 'player_tov')):
            if sum(r[field] for r in mine) != counts[total]:
                raise ValueError(f'player {field} do not sum to the team total for {tid}')
        stats[tid] = counts
    validate_history(rows)
    venue = summary.get('gameInfo', {}).get('venue') or {}
    address = venue.get('address') or {}
    record = dict(game_id=game_id, game_time=game_time, known_at=known, season=season,
                  home_team_id=ids['home'], away_team_id=ids['away'], team_stats=stats,
                  team_minutes={ids['home']: length, ids['away']: length}, provider_event_id=event,
                  venue=venue, venue_timezone=CITY_ZONES.get(address.get('city')) or STATE_ZONES.get(address.get('state')),
                  provenance=dict(summary={k: meta[k] for k in ('source_url', 'retrieved_at', 'sha256')},
                                  source='espn_summary', minutes_precision='whole_minutes',
                                  team_minutes='regulation plus 25 per overtime period',
                                  turnover_definition='total turnovers; ESPN totalTurnovers = turnovers + teamTurnovers',
                                  **(dict(notes=notes) if notes else {})))
    return rows, record


def build(season, start, end, directory, teams, *, pause=.25):
    folder = Path(directory)
    history, records, failures = [], [], {}
    for item in season_events(start, end, folder / 'references', teams, pause=pause):
        url = BASE + 'summary?event=' + item['event_id']
        cached = (folder / 'references' / (hashlib.sha256(url.encode()).hexdigest() + '.json')).exists()
        try:
            summary, meta = fetch_reference(url, folder / 'references', max_age=ARCHIVE_AGE)
            rows, record = normalize(summary, meta, teams, season)
            record['provenance']['scoreboard'] = item['scoreboard']
            history.extend(rows)
            records.append(record)
        except (KeyError, IndexError, TypeError, ValueError, OSError) as exc:
            failures[item['event_id']] = f'{type(exc).__name__}: {exc}'
        if pause and not cached:
            time.sleep(pause)
    validate_history(history)
    return history, records, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season', required=True, help='e.g. 2024-25')
    parser.add_argument('--start', required=True, help='First regular-season date (YYYY-MM-DD, Eastern)')
    parser.add_argument('--end', required=True, help='Last regular-season date')
    parser.add_argument('--calendar', required=True, help='Schedule calendar naming the 30 teams and tricodes')
    parser.add_argument('--directory', required=True, help='Raw archive (resumable)')
    parser.add_argument('--output', required=True, help='Folder for history.json.gz, team-games.json.gz, manifest.json')
    args = parser.parse_args()
    raw = Path(args.calendar).read_bytes()
    teams = team_names(json.loads(gzip.decompress(raw) if args.calendar.endswith('.gz') else raw))
    history, records, failures = build(args.season, args.start, args.end, args.directory, teams)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, value in (('history.json.gz', history), ('team-games.json.gz', records)):
        (out / name).write_bytes(gzip.compress(canonical(value), mtime=0))
        files[name] = hashlib.sha256((out / name).read_bytes()).hexdigest()
    counts = {}
    for r in records:
        for tid in (r['home_team_id'], r['away_team_id']):
            counts[tid] = counts.get(tid, 0) + 1
    manifest = dict(version=VERSION, season=args.season, start=args.start, end=args.end, games=len(records),
                    rows=len(history), players=len({r['player_id'] for r in history}),
                    games_per_team=sorted(set(counts.values())), failures=failures, files=files)
    atomic_json(out / 'manifest.json', manifest)
    print(json.dumps({k: v for k, v in manifest.items() if k != 'files'}, indent=2))


if __name__ == '__main__':
    main()
