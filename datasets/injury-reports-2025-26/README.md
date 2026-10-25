# Official injury reports, 2025-26 replay games

`statuses.json.gz`: for each team-game record in
`datasets/model-upgrade-2026-10-23/team-games.json.gz` (1,203 from 2025-26 and
the 14 week-one 2026-27 games it also holds), the newest official NBA
injury report published by the game's T-minus-30 checkpoint (the report a live
final-stage forecast uses), parsed with the production parser
(`injury_parser.parse_report`, exact name/team/game joins against the season's
box-score rosters). Built with:

```bash
python -m nba_forecast.injury_history --fetch --season 2025-26 2026-27 \
  --team-games datasets/model-upgrade-2026-10-23/team-games.json.gz \
  --history datasets/regular-season-expanded-2026-10-22/history.json.gz \
  --calendar datasets/season-schedule-2026-10-21/calendar.json.gz \
  --directory data/injury-history/2025-26 \
  --output datasets/injury-reports-2025-26/statuses.json.gz
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
`data/injury-history/2025-26/raw/`, not committed; `manifest.json` there maps
each slot URL to its hash or records it absent. Counts are in `manifest.json`
here.

Coverage: every game has a report (734 PDFs, none absent; mean age at the
checkpoint 3 minutes, at most 30); 2,212 of 2,434 team sides are fully
reported, 11,009 players are listed out (none of them played) and 110 are
probable, questionable or doubtful at the checkpoint.

The PDFs were retrieved in October 2026, long after the games, but they are the
files the league published at the stated times; the replay uses each one only
for games whose checkpoint follows its publication time.
