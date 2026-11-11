"""What capturing Kalshi's player prop ladders costs on disk, from saved public responses.

    python3 scripts/research/props_capture_cost.py PAYLOADS_DIR [ROSTER_JSON]

PAYLOADS_DIR holds saved responses of
``/trade-api/v2/markets?event_ticker=<series>-<event>&limit=1000`` named
``markets-<series>-<event>.json`` (the four series of at least one game) and,
optionally, ``candles/<ticker>.json``: one-minute candlesticks of single markets
over a game day (``/series/<series>/markets/<ticker>/candlesticks``).

It prints, per game with all four series saved: the raw bytes, their gzip size
(what ``kalshi_props`` archives) and the size of the record ``extract_props``
writes; then the capture slots per game day at the props cadence and the share of
slots in which a sampled market's quote changed (a record is appended when any
rung of the game changed, so the share for the game is at least the highest
single-market share). The bytes per day assume a record and its responses in
every slot, an upper bound. With ROSTER_JSON (an operations ``roster.json``) it
also counts how ``match_players`` matched every saved player ladder, by rule.
"""
import glob
import gzip
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from nba_forecast import kalshi_props as P  # noqa: E402


def slots(hours):
    """Capture slots in the ``hours`` before tip at the props cadence: 15 minutes, 5 in the last half hour."""
    count, t = 0, hours * 3600
    while t > 0:
        count += 1
        t -= 300 if t <= 1800 else 900
    return count


def matches(folder, roster_path):
    """How every player ladder in the saved responses matches the roster, by rule; unmatched names listed."""
    from collections import Counter
    players = json.load(open(roster_path))['players']
    codes = {p['team_code']: p['team_id'] for p in players if p.get('team_code')}
    ladders, bases, unmatched = set(), Counter(), []
    for path in sorted(glob.glob(os.path.join(folder, 'markets-KXNBA*-*.json'))):
        for m in json.load(open(path))['markets']:
            event = m['event_ticker']
            key = m['ticker'][len(event) + 1:].rsplit('-', 1)[0]
            name = m['title'].rsplit(':', 1)[0].strip()
            ladders.add((event, key, name))
    names = sorted({(name, codes.get(key[:3])) for _, key, name in ladders})
    found = P.match_players(names, players)
    for event, key, name in sorted(ladders):
        pid, basis = found[(name, codes.get(key[:3]))]
        bases[basis if pid else 'unmatched'] += 1
        if not pid:
            unmatched.append((event, name, key[:3], basis))
    print(f'player ladders (event, series, player): {len(ladders)}; by match rule: {dict(bases)}')
    for item in unmatched:
        print('  unmatched:', item)


def main(folder):
    games = {}
    for path in sorted(glob.glob(os.path.join(folder, 'markets-KXNBA*-*.json'))):
        series, event = os.path.basename(path)[len('markets-'):-len('.json')].split('-', 1)
        games.setdefault(event, {})[series] = open(path, 'rb').read()
    full = {e: s for e, s in games.items() if set(s) == set(P.SERIES.values()) and all(json.loads(r)['markets'] for r in s.values())}
    if not full:
        print('no game with all four series and markets in', folder)
        return
    per_game = []
    for event, raw in sorted(full.items()):
        stats = {stat: json.loads(raw[series])['markets'] for stat, series in P.SERIES.items()}
        away, home = event[7:10], event[10:13]
        start = datetime.strptime('20' + event[:7], '%Y%b%d').replace(hour=23, tzinfo=timezone.utc).isoformat()
        game = dict(game_id=event, start_time=start, home_tricode=home, away_tricode=away, home_team_id='H', away_team_id='A')
        # event_ticker() rebuilds the event from the start time; keep the saved event's date.
        tickers = {m['event_ticker'] for items in stats.values() for m in items}
        original = P.event_ticker
        P.event_ticker = lambda series, g: next(t for t in tickers if t.startswith(series + '-'))
        try:
            record = P.extract_props(game, stats, [], start, [dict(source_url='x' * 100, retrieved_at=start, sha256='0' * 64,
                                                                   bytes=0, archive='raw/' + '0' * 64 + '.json.gz')] * 4)
        finally:
            P.event_ticker = original
        raw_bytes = sum(len(r) for r in raw.values())
        gz_bytes = sum(len(gzip.compress(r, mtime=0)) for r in raw.values())
        record_bytes = len(json.dumps(record, sort_keys=True)) + 1
        rungs = sum(len(s['ladder']) for p in record['players'] for s in p['stats'].values())
        per_game.append((raw_bytes, gz_bytes, record_bytes))
        print(f'{event}: players {len(record["players"])}, rungs {rungs}, raw {raw_bytes} B, gzip {gz_bytes} B, record {record_bytes} B')
    n = len(per_game)
    raw_mean, gz_mean, rec_mean = (sum(x[i] for x in per_game) / n for i in range(3))
    window = slots(24)
    print(f'mean per capture over {n} games: raw {raw_mean:.0f} B, archived gzip {gz_mean:.0f} B, record {rec_mean:.0f} B')
    print(f'capture slots in the 24 hours before tip: {window}')
    per_day = window * (gz_mean + rec_mean)
    print(f'upper bound per game per day (a record and four archived responses in every slot): {per_day / 1e6:.2f} MB '
          f'(records {window * rec_mean / 1e6:.2f} MB, archives {window * gz_mean / 1e6:.2f} MB, {4 * window} archive files)')
    print(f'10-game slate per day: {10 * per_day / 1e6:.1f} MB; 1,230 games: {1230 * per_day / 1e9:.2f} GB; '
          f'uncompressed archives would be {10 * window * raw_mean / 1e6:.0f} MB a 10-game day')
    candles = sorted(glob.glob(os.path.join(folder, 'candles', '*.json')))
    if candles:
        changed = pairs = 0
        best = 0.
        for path in candles:
            series = sorted((c['end_period_ts'], (c.get('yes_bid') or {}).get('close_dollars'), (c.get('yes_ask') or {}).get('close_dollars'))
                            for c in json.load(open(path))['candlesticks'])
            if not series:
                continue
            tip = series[-1][0]
            times, t = [], tip - 1
            while t > series[0][0]:
                times.append(t)
                t -= 300 if tip - t <= 1800 else 900
            def quote(at):
                last = None
                for end, bid, ask in series:
                    if end > at:
                        break
                    last = (bid, ask)
                return last
            samples = [quote(x) for x in reversed(times)]
            c = sum(1 for a, b in zip(samples, samples[1:]) if a and b and a != b)
            p = sum(1 for a, b in zip(samples, samples[1:]) if a and b)
            changed, pairs = changed + c, pairs + p
            best = max(best, c / p if p else 0.)
            print(f'  {os.path.basename(path)[:-5]}: quote changed in {c}/{p} consecutive slots')
        print(f'sampled markets: quote changed in {changed}/{pairs} = {changed / pairs:.2f} of slots; highest single market {best:.2f}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '.')
    if len(sys.argv) > 2:
        matches(sys.argv[1], sys.argv[2])
