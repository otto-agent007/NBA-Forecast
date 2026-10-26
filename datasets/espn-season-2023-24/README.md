# 2023-24 regular season from ESPN game summaries

The third replay season, and the first under the Player Participation Policy and
the 65-game minimum for awards, the resting rules 2024-25 and 2025-26 also play
under. `history.json.gz` holds one row per player per game in the normalized
history schema; `team-games.json.gz` one record per game in the team-game schema
the backtest reads. All 1,230 games, 82 per team. Built with:

```bash
python -m nba_forecast.espn_season --season 2023-24 --start 2023-10-24 --end 2024-04-14 \
  --calendar datasets/season-schedule-2026-10-21/calendar.json.gz \
  --directory data/espn-history/2023-24 --output datasets/espn-season-2023-24
```

Same source, schema and differences from the official history as
`datasets/espn-season-2024-25/` (see its README): ESPN IDs, whole minutes,
some inactive players omitted, team minutes from the game length. The
In-Season Tournament championship (2023-12-09, Lakers-Pacers), which ESPN files
as a regular-season game under the note "NBA In-Season Tournament Championship"
but the league does not count, is excluded; the tournament's semifinals count.
Every scoreboard and summary response is archived by SHA256 with its retrieval
time under `data/espn-history/2023-24/` (not committed). Counts and file hashes
are in `manifest.json`.
