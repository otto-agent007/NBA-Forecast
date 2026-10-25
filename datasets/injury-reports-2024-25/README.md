# Official injury reports, 2024-25 replay games

`statuses.json.gz`: for each team-game record in
`datasets/espn-season-2024-25/team-games.json.gz`, the newest official NBA
injury report published by the game's T-minus-30 checkpoint (the report a live
final-stage forecast uses), parsed with the production parser
(`injury_parser.parse_report`, exact name/team/game joins against the season's
box-score rosters). Built with:

```bash
python -m nba_forecast.injury_history --fetch --season 2024-25 \
  --team-games datasets/espn-season-2024-25/team-games.json.gz \
  --history datasets/espn-season-2024-25/history.json.gz \
  --calendar datasets/season-schedule-2026-10-21/calendar.json.gz \
  --directory data/injury-history/2024-25 \
  --output datasets/injury-reports-2024-25/statuses.json.gz
```

Per game: the report's URL, publication time, retrieval time and SHA256; its age
at the checkpoint; the team IDs it covers and the ones it fully reports (complete
pages, matching heading, every row resolved); each listed player's
`[status, reported status, team ID]`; and the parser issues touching the game.
Players a report omits are not stored: the replay treats them as available,
marked not listed when the team is fully reported (`backtest._roster`).

Reports were hourly until 2025-12-21, named by the hour (`..._07PM.pdf`) and
published at half past; from 2025-12-22 they are every 15 minutes
(`..._07_15PM.pdf`). When a game's anchor slot is absent the next earlier slot is
used, up to three hours back. The PDFs (about 90 KB each) are under
`data/injury-history/2024-25/raw/`, not committed; `manifest.json` there maps
each slot URL to its hash or records it absent. Counts are in `manifest.json`
here.

Coverage: every game has a report (574 PDFs; one absent slot fell back to the
report before it; mean age at the checkpoint 10 minutes, at most 90); 10,673
players are listed out (two of them played) and 382 probable, questionable or
doubtful. Only 1,881 of
2,458 team sides count as fully reported, against 91% in 2025-26: the ESPN box
scores omit players who were out all season (Saddiq Bey, DaRon Holmes II,
Nikola Topić and about twenty others), so their report rows cannot be joined and
their teams are marked incomplete. That changes a flag, not a number: players a
report omits are available either way. A few rows are genuine spelling
differences ("Carlton Carrington" for ESPN's "Bub Carrington"), about 25 of 13,430.

The PDFs were retrieved in October 2026, long after the games, but they are the
files the league published at the stated times; the replay uses each one only
for games whose checkpoint follows its publication time.
