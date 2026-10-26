# Closing game lines, 2024-25 replay games

`closing-lines.json.gz`: one closing spread, total and moneyline pair for each of
the 1,229 games in `datasets/espn-season-2024-25/team-games.json.gz` (none
missing). Built 2026-10-25 with:

```bash
python -m nba_forecast.market_history \
  --team-games datasets/espn-season-2024-25/team-games.json.gz \
  --directory data/market-history/2024-25
```

Source: ESPN's core odds endpoint by event ID, the first pregame provider with a
close: ESPN BET for every game (2025-26 is mostly DraftKings). Both are retail
books, so a sharp close would likely be a little better. Each row keeps its
source URL, the SHA256 of the archived response and its retrieval time; the raw
responses are under `data/market-history/2024-25/` (not committed).
`home_spread` is the home team's closing spread (negative when favoured);
moneylines are American odds.

Used by `backtest --market` (see `docs/backtest.md`). This records what the
market said; it is not a quote, a recommendation or a bet.
