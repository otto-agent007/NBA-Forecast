# Closing game lines, 2023-24 replay games

`closing-lines.json.gz`: one closing spread, total and moneyline pair for each of
the 1,230 games in `datasets/espn-season-2023-24/team-games.json.gz` (none
missing). Built 2026-10-26 with:

```bash
python -m nba_forecast.market_history \
  --team-games datasets/espn-season-2023-24/team-games.json.gz \
  --directory data/market-history/2023-24
```

Source: ESPN's core odds endpoint by event ID, the first pregame provider with a
close: Betfair Sportsbook for 1,229 games and Caesars (Colorado) for one; the
responses also carry DraftKings, ESPN BET, MGM and Caesars, which the rule does
not pick. Checked against the finals: closing-spread margin MAE 10.53 (2024-25:
10.52), favourite won 69.1%, total MAE 14.22, no-vig moneyline Brier 0.1989
(0.1992). Its moneylines carry a 6.0% overround, against about 4.5% at the
other retail books, which the no-vig conversion removes. Half the spreads are
whole numbers (2024-25: none), so some games land on the line; a scorer
against the spread must count those as pushes. Each row keeps its source URL,
the SHA256 of the archived response and its retrieval time; the raw responses
are under `data/market-history/2023-24/` (not committed). `home_spread` is the
home team's closing spread (negative when favoured); moneylines are American odds.

Used by `backtest --market` (see `docs/backtest.md`). This records what the
market said; it is not a quote, a recommendation or a bet.
