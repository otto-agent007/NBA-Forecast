"""How long the ticks ran on one Pacific date and when each game's last pre-tip line capture happened.

    python3 scripts/research/capture_gaps.py OPERATIONS_DIR YYYY-MM-DD

Reads only: the tail of ``ticks.jsonl`` (the last 120 MB), the saved calendar and
the capture states ``market/capture-state.json`` (ESPN) and
``market/kalshi/capture-state.json`` (Kalshi), whose cadence slots keep each
capture's time for 48 hours. Prints the tick count, median and longest duration,
the ticks over 60 and 180 seconds, gaps of more than three minutes between tick
starts, and for each game tipping that date the minutes between its last ESPN
and Kalshi captures and its tipoff. Run it within two days of the date.
"""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo('America/Los_Angeles')


def main(folder, day):
    path = os.path.join(folder, 'ticks.jsonl')
    size = os.path.getsize(path)
    rows = []
    with open(path, 'rb') as stream:
        stream.seek(max(0, size - 120_000_000))
        stream.readline()
        for line in stream:
            try:
                row = json.loads(line)
                start = datetime.fromisoformat(row['started_at'])
                seconds = (datetime.fromisoformat(row['finished_at']) - start).total_seconds()
            except (ValueError, KeyError, TypeError):
                continue
            if start.astimezone(PACIFIC).date().isoformat() == day:
                rows.append((start, seconds))
    durations = sorted(s for _, s in rows)
    if durations:
        print(f'{day}: ticks {len(durations)}, median {durations[len(durations) // 2]:.1f} s, over 60 s {sum(d > 60 for d in durations)}, '
              f'over 180 s {sum(d > 180 for d in durations)}, longest {durations[-1]:.0f} s')
    starts = sorted(t for t, _ in rows)
    for a, b in zip(starts, starts[1:]):
        if (b - a).total_seconds() > 180:
            print(f'  no tick started from {a.astimezone(PACIFIC):%H:%M:%S} to {b.astimezone(PACIFIC):%H:%M:%S}')
    schedule = json.load(open(os.path.join(folder, 'schedule', 'latest.json')))
    states = {name: json.load(open(os.path.join(folder, 'market', *rel))).get('captured', {})
              for name, rel in (('espn', ('capture-state.json',)), ('kalshi', ('kalshi', 'capture-state.json')))
              if os.path.exists(os.path.join(folder, 'market', *rel))}
    for game in schedule['games']:
        if not game.get('start_time'):
            continue
        tip = datetime.fromisoformat(game['start_time'])
        if tip.astimezone(PACIFIC).date().isoformat() != day:
            continue
        lasts = []
        for name, captured in states.items():
            before = [v for k, v in captured.items() if k.startswith(game['game_id'] + ':') and v < tip.timestamp()]
            lasts.append(f'{name} {(tip.timestamp() - max(before)) / 60:.1f} min before tip' if before else f'{name} none on record')
        print(f'  {game["game_id"]} {game["away_tricode"]}@{game["home_tricode"]} {tip.astimezone(PACIFIC):%H:%M}: ' + ', '.join(lasts))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
