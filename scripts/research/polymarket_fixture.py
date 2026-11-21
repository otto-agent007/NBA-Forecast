"""Trim one archived Polymarket capture into the offline test fixtures.

    python3 scripts/research/polymarket_fixture.py CAPTURE_DIR GAME_ID tests/fixtures/polymarket

Reads the first capture of ``CAPTURE_DIR/market/polymarket/GAME_ID.jsonl`` and the
two responses it names under ``raw/`` (the Gamma event and the CLOB order books),
and writes ``event.json`` and ``books.json``: the same JSON with the event cut to
the fields ``polymarket_lines`` reads, its winner market, the spread and total
lines within ``SPREAD`` and ``TOTAL`` points of the main line, one first-half
market and one player prop (which the capture must ignore), and each kept
market's first-outcome book cut to its best ``LEVELS`` prices a side. Values are
not edited. Reads no network.
"""
import gzip
import json
from pathlib import Path
import sys

EVENT_KEYS = ('id', 'slug', 'title', 'startTime', 'volume', 'liquidity', 'teams')
TEAM_KEYS = ('id', 'name', 'abbreviation', 'ordering')
MARKET_KEYS = ('id', 'question', 'slug', 'outcomes', 'outcomePrices', 'clobTokenIds', 'sportsMarketType', 'line', 'volumeNum',
               'liquidityNum', 'bestBid', 'bestAsk', 'active', 'closed', 'acceptingOrders', 'createdAt', 'marketMetadata')
SPREAD, TOTAL, LEVELS = 8, 8, 3


def load(folder, source):
    path = next(p for p in (folder / 'raw').glob(source['sha256'] + '.json*') if not p.name.endswith('.provenance.json'))
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == '.gz' else raw)


def main(capture, game_id, out):
    folder = Path(capture) / 'market' / 'polymarket'
    record = json.loads((folder / (game_id + '.jsonl')).read_text().splitlines()[0])
    payload, books = (load(folder, source) for source in record['sources'])
    event = next(e for e in payload['events'] if e['slug'] == record['event']['slug'])
    centre = dict(spreads=record['main_spread']['home_line'], totals=record['main_total']['line'])
    width = dict(spreads=SPREAD, totals=TOTAL)
    kept, extras = [], set()
    for m in event['markets']:
        kind = m.get('sportsMarketType')
        if kind == 'moneyline' or (kind in centre and abs(abs(m['line']) - abs(centre[kind])) <= width[kind]):
            kept.append(m)
        elif kind in ('first_half_moneyline', 'points') and kind not in extras:
            extras.add(kind)
            kept.append(m)
    trimmed = dict({k: event[k] for k in EVENT_KEYS if k in event}, teams=[{k: t[k] for k in TEAM_KEYS if k in t} for t in event['teams']],
                   markets=[{k: m[k] for k in MARKET_KEYS if k in m} for m in kept])
    tokens = {json.loads(m['clobTokenIds'])[0] for m in kept} | {t for m in kept if m.get('sportsMarketType') == 'moneyline' for t in json.loads(m['clobTokenIds'])}
    best = lambda levels, reverse: sorted(levels, key=lambda x: float(x['price']), reverse=reverse)[:LEVELS]  # noqa: E731
    cut = [dict(b, bids=best(b['bids'], True), asks=best(b['asks'], False)) for b in books if b['asset_id'] in tokens]
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'event.json').write_text(json.dumps(dict(events=[trimmed]), indent=1) + '\n')
    (out / 'books.json').write_text(json.dumps(cut, indent=1) + '\n')
    print(f"{record['event']['slug']} captured {record['captured_at']}: {len(kept)} of {len(event['markets'])} markets, {len(cut)} of {len(books)} books")


if __name__ == '__main__':
    main(*sys.argv[1:4])
