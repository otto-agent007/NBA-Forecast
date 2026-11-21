"""Polymarket's prices beside Kalshi's and DraftKings' for the same games, each source's last capture before tip.

    python3 scripts/research/polymarket_sample.py POLYMARKET_DIR OPERATIONS_DIR GAME_ID [GAME_ID ...]

``POLYMARKET_DIR`` holds ``market/polymarket/`` (a scratch capture, or the
operations directory itself once the capture service records Polymarket);
``OPERATIONS_DIR`` holds the calendar, ``market/kalshi/`` and
``market/game-lines/``. For each game: the home-win probability (Polymarket and
Kalshi bid-ask mids, DraftKings' moneyline without the vig), the home margin
(where the exchanges' lines cross one half; minus DraftKings' home spread) and
the total, with the minutes before tip of each source's last recorded change
(a capture is appended only when its numbers changed). Reads only; no network.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nba_forecast.kalshi_lines import closing_kalshi_line, read_kalshi_lines  # noqa: E402
from nba_forecast.market_lines import _implied, closing_lines, read_game_lines  # noqa: E402
from nba_forecast.polymarket_lines import closing_polymarket_line, read_polymarket_lines  # noqa: E402
from nba_forecast.validation import parse_time  # noqa: E402


def fmt(value, digits=1, signed=False):
    return '   n/a' if value is None else f'{value:{"+" if signed else ""}6.{digits}f}'


def main(polymarket_dir, operations_dir, game_ids):
    games = {g['game_id']: g for g in json.loads((Path(operations_dir) / 'schedule' / 'latest.json').read_text())['games']}
    print(f'{"game":<18}{"source":<12}{"home win":>9}{"bid":>6}{"ask":>6}{"margin":>8}{"total":>8}  last change before tip')
    for gid in game_ids:
        game = games[gid]
        tip = parse_time(game['start_time'])
        before = lambda at: f'{(tip - parse_time(at)).total_seconds() / 60:.1f} min'  # noqa: E731
        poly = closing_polymarket_line(read_polymarket_lines(polymarket_dir, gid), game['start_time'])
        kalshi = closing_kalshi_line(read_kalshi_lines(operations_dir, gid), game['start_time'])
        book = next((r for r in closing_lines(read_game_lines(operations_dir, gid), game['start_time']).values()
                     if str(r['provider']).replace(' ', '').lower() == 'draftkings'), None)
        label = f"{game['away_tricode']} @ {game['home_tricode']}"
        if poly:
            line = poly['moneyline']
            print(f'{label:<18}{"Polymarket":<12}{fmt(poly["home_win_probability"], 3):>9}{fmt(line["bid"], 2)}{fmt(line["ask"], 2)}'
                  f'{fmt(poly["implied_home_margin"], 2, True):>8}{fmt(poly["implied_total"]):>8}  {before(poly["captured_at"])}'
                  f'  (winner volume ${line["volume"] or 0:,.0f}, liquidity ${line["liquidity"] or 0:,.0f})')
        if kalshi:
            print(f'{label:<18}{"Kalshi":<12}{fmt(kalshi["home_win_probability"], 3):>9}{"":>12}'
                  f'{fmt(kalshi["implied_home_margin"], 2, True):>8}{fmt(kalshi["implied_total"]):>8}  {before(kalshi["captured_at"])}')
        if book:
            lines = book['lines']
            home, away = _implied(lines['moneyline']['home']['current_odds']), _implied(lines['moneyline']['away']['current_odds'])
            spread = lines['spread']['home']['current_line']
            print(f'{label:<18}{"DraftKings":<12}{fmt(home / (home + away) if home and away else None, 3):>9}{"":>12}'
                  f'{fmt(-spread if spread is not None else None, 2, True):>8}{fmt(lines["total"]["over"]["current_line"]):>8}  {before(book["captured_at"])}'
                  f'  (moneyline {lines["moneyline"]["home"]["current_odds"]} / {lines["moneyline"]["away"]["current_odds"]})')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
