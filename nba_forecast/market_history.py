"""Closing game lines for a replayed season, from ESPN's core odds endpoint.

ESPN's game summary drops its pickcenter once a game is final, but the core odds
endpoint keeps each pregame provider's opening and closing spread, total and
moneyline by event ID, which the team-game records already carry. Every
response is archived with its hash and retrieval time (``fetch_reference``);
the backtest scores the close on exactly the games it replays. This records what
the market said; it is not a quote, a recommendation or a bet.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import time

from .market_lines import _number
from .reference import fetch_reference
from .schedule import atomic_json, canonical

VERSION = 'market-history-1'
ODDS_URL = 'https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{0}/competitions/{0}/odds'
# Finished games' lines do not change; an archived response is reused indefinitely.
ARCHIVE_AGE = 10 ** 10


def _american(block, *keys):
    for key in keys:
        block = (block or {}).get(key)
    return _number(block) if block is not None else None


def closing_line(payload):
    """The first pregame provider with a close: home spread, total and both moneylines, or None."""
    for item in payload.get('items') or []:
        home, away = item.get('homeTeamOdds') or {}, item.get('awayTeamOdds') or {}
        if not home.get('close'):
            continue  # live-odds providers carry no close
        line = dict(provider=(item.get('provider') or {}).get('name'),
                    home_spread=_american(home, 'close', 'pointSpread', 'american'),
                    total=_american(item, 'close', 'total', 'american'),
                    home_moneyline=_american(home, 'close', 'moneyLine', 'american'),
                    away_moneyline=_american(away, 'close', 'moneyLine', 'american'))
        if line['home_spread'] is not None or line['total'] is not None:
            return line
    return None


def backfill(records, directory, *, pause=0.25):
    """One closing-line row per team-game record with an ESPN event ID; misses are listed, not guessed."""
    folder = Path(directory) / 'references'
    rows, missing = [], []
    for record in sorted(records, key=lambda r: r['game_time']):
        event = record.get('provider_event_id')
        if not event:
            missing.append(dict(game_id=record['game_id'], reason='no ESPN event ID'))
            continue
        url = ODDS_URL.format(event)
        archived = (folder / (hashlib.sha256(url.encode()).hexdigest() + '.json')).exists()
        try:
            payload, meta = fetch_reference(url, folder, max_age=ARCHIVE_AGE)
        except (OSError, ValueError) as exc:
            missing.append(dict(game_id=record['game_id'], reason=f'{type(exc).__name__}: {exc}'))
            continue
        line = closing_line(payload)
        if line is None:
            missing.append(dict(game_id=record['game_id'], reason='no pregame provider with a close'))
        else:
            rows.append(dict(game_id=record['game_id'], provider_event_id=str(event), source_url=url,
                             source_sha256=meta['sha256'], retrieved_at=meta['retrieved_at'], **line))
        if pause and not archived:
            time.sleep(pause)
    return dict(version=VERSION, lines=rows, missing=missing)


def read_lines(path):
    raw = Path(path).read_bytes()
    data = json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)
    return {row['game_id']: row for row in data['lines']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--team-games', required=True, help='Team-game records with provider_event_id (json or json.gz)')
    parser.add_argument('--directory', required=True, help='Output folder: closing-lines.json.gz plus the raw archive')
    args = parser.parse_args()
    raw = Path(args.team_games).read_bytes()
    records = json.loads(gzip.decompress(raw) if args.team_games.endswith('.gz') else raw)
    result = backfill(records, args.directory)
    out = Path(args.directory)
    (out / 'closing-lines.json.gz').write_bytes(gzip.compress(canonical(result), mtime=0))
    atomic_json(out / 'manifest.json', dict(version=VERSION, team_games=args.team_games, lines=len(result['lines']),
                                            missing=len(result['missing'])))
    print(json.dumps(dict(lines=len(result['lines']), missing=len(result['missing']))))


if __name__ == '__main__':
    main()
