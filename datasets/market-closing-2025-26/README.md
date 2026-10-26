# Closing game lines, 2025-26 replay games

`closing-lines.json.gz`: one closing spread, total and moneyline pair per game for
the 1,217 team-game records in `datasets/model-upgrade-2026-10-23/team-games.json.gz`
(1,216 lines; one game had no pregame provider with a close). Built 2026-10-24
with:

```bash
python -m nba_forecast.market_history \
  --team-games datasets/model-upgrade-2026-10-23/team-games.json.gz \
  --directory data/market-history/2025-26
```

Source: ESPN's core odds endpoint by event ID
(`sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/<id>/competitions/<id>/odds`),
the first pregame provider with a close: DraftKings for 966 games, ESPN BET for
250. Both are retail books, so a sharp close would likely be a little better.
Each row keeps its source URL, the SHA256 of the archived response and its
retrieval time; the raw responses are under `data/market-history/2025-26/`
(not committed). `home_spread` is the home team's closing spread (negative when
favoured); moneylines are American odds.

Used by `backtest --market` (see `docs/backtest.md`). This records what the
market said; it is not a quote, a recommendation or a bet.
