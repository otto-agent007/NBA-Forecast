"""Closing-line value: does a night-before forecast know something the opening line has not priced?

    python3 scripts/research/clv.py --rows <night-before replay>/rows.json.gz --season 2024-25

The replay is the backtest run with datasets/injury-reports-<season>-night-before
(the official report by 8:30 PM ET the day before, about when the openers post).
Opening and closing home spreads come from the archived ESPN core odds responses
the closing-lines dataset points to (data/market-history/<season>/references/raw/,
gitignored), for the closing line's provider. The line's move (close minus open)
is regressed on the forecast's distance from the opener with date-clustered
errors; the placebo is the walk-forward point-differential rating, which knows
only the standings and should score about zero if the opener prices public
information. CLV per bet is the mean move in the forecast's direction; the cover
rate is how often the actual margin beat the opener on the forecast's side.
"""
import argparse
import gzip
import json
from pathlib import Path
from statistics import fmean
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from nba_forecast.backtest import GAME_FIELDS, _day, _point_differential_margins  # noqa: E402
from nba_forecast.market_history import _american  # noqa: E402
from gap_breakdown import SEASONS, read  # noqa: E402
from stats_lite import slope  # noqa: E402


def opener_and_close(payload, provider):
    """Home spread at open and close for the named provider, or None when either is missing."""
    for item in payload.get('items') or []:
        if (item.get('provider') or {}).get('name') != provider:
            continue
        home = item.get('homeTeamOdds') or {}
        opened, closed = _american(home, 'open', 'pointSpread', 'american'), _american(home, 'close', 'pointSpread', 'american')
        if opened is not None and closed is not None:
            return opened, closed
    return None


def games_with_lines(datasets, market_history, season, rows):
    forecasts = {g['game_id']: g for g in (dict(zip(GAME_FIELDS, r)) for r in rows['games'])}
    records = read(Path(datasets) / SEASONS[season] / 'team-games.json.gz')
    lines = read(Path(datasets) / f'market-closing-{season}' / 'closing-lines.json.gz')['lines']
    naive = _point_differential_margins(records, set(forecasts))
    day_of = {r['game_id']: _day(r['game_time']).isoformat() for r in records}
    games, missing = [], 0
    for line in lines:
        f = forecasts.get(line['game_id'])
        if f is None:
            continue
        raw = Path(market_history) / season / 'references' / 'raw' / (line['source_sha256'] + '.json')
        if not raw.exists():
            missing += 1
            continue
        spreads = opener_and_close(json.loads(raw.read_bytes()), line['provider'])
        if spreads is None:
            missing += 1
            continue
        games.append(dict(game_id=line['game_id'], day=day_of[line['game_id']], open=-spreads[0], close=-spreads[1],
                          forecast=f['projected_margin'], actual=f['actual_margin'], naive=naive.get(line['game_id'])))
    return games, missing


def clv(games, key):
    rows = [g for g in games if g[key] is not None]
    x = [g[key] - g['open'] for g in rows]
    y = [g['close'] - g['open'] for g in rows]
    b, se = slope(x, y, [g['day'] for g in rows])
    leaning = [g for g, v in zip(rows, x) if v != 0]
    per_bet = fmean((1 if g[key] > g['open'] else -1) * (g['close'] - g['open']) for g in leaning)
    decided = [g for g in leaning if g['actual'] != g['open']]
    covered = fmean((g[key] > g['open']) == (g['actual'] > g['open']) for g in decided)
    return dict(n=len(rows), slope=b, se=se, clv_per_bet=per_bet, cover_rate=covered,
                cover_se=(covered * (1 - covered) / len(decided)) ** .5, bets=len(decided))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--rows', required=True, help='rows.json.gz of a night-before replay')
    parser.add_argument('--season', required=True, choices=sorted(SEASONS))
    parser.add_argument('--datasets', default=str(ROOT / 'datasets'))
    parser.add_argument('--market-history', default=str(ROOT / 'data/market-history'))
    args = parser.parse_args()
    rows = json.loads(gzip.decompress(Path(args.rows).read_bytes()))
    games, missing = games_with_lines(args.datasets, args.market_history, args.season, rows)
    print(f'{args.season}: {len(games)} games with an opener, a close and a night-before forecast; {missing} lines without an archived opener')
    if len(games) < 50:
        print('too few openers to test (2023-24 closes are Betfair, which posts none)')
        return
    print(f"{'':12s} {'slope of (close-open) on (x-open)':>36s} {'CLV/bet':>8s} {'cover vs open':>16s} {'bets':>5s}")
    for label, key in (('placebo', 'naive'), ('night-before', 'forecast')):
        r = clv(games, key)
        print(f"{label:12s} {r['slope']:+.3f} ± {r['se']:.3f} (n {r['n']})".ljust(50) +
              f"{r['clv_per_bet']:+8.2f} {r['cover_rate']:7.1%} ± {r['cover_se']:.1%} {r['bets']:5d}")
    print('one standard error shown; break-even against -110 is 52.4%')


if __name__ == '__main__':
    main()
