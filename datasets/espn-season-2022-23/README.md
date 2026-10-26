# 2022-23 regular season from ESPN game summaries (prior season only)

Box scores for the season before the first replay season, 2023-24, so that
experiments seeding a replay with the previous season (team-rating priors,
roster continuity, player-impact ratings) can do so for 2023-24 as for the later
seasons. It is not replayed or scored: 2022-23 predates the Player
Participation Policy and the 65-game awards minimum, so its resting patterns
differ from the replay seasons, and no injury reports or market lines were
collected for it. All 1,230 games, 82 per team, 31,551 player rows. Built with:

```bash
python -m nba_forecast.espn_season --season 2022-23 --start 2022-10-18 --end 2023-04-09 \
  --calendar datasets/season-schedule-2026-10-21/calendar.json.gz \
  --directory data/espn-history/2022-23 --output datasets/espn-season-2022-23
```

Same source, schema and differences from the official history as
`datasets/espn-season-2024-25/` (see its README). The raw responses are under
`data/espn-history/2022-23/` (not committed); counts and file hashes are in
`manifest.json`.
