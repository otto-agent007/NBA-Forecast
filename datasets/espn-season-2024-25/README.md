# 2024-25 regular season from ESPN game summaries

The second replay season. `history.json.gz` holds one row per player per game in
the normalized history schema; `team-games.json.gz` one record per game in the
team-game schema the backtest reads. Built with:

```bash
python -m nba_forecast.espn_season --season 2024-25 --start 2024-10-22 --end 2025-04-13 \
  --calendar datasets/season-schedule-2026-10-21/calendar.json.gz \
  --directory data/espn-history/2024-25 --output datasets/espn-season-2024-25
```

Why ESPN and not the official NBA box scores used for 2025-26: in October 2026
the NBA edge (cdn.nba.com, www.nba.com) took four to six minutes per request
from this host or timed out, which puts a season out of reach, while ESPN's
summary returns in under a second. What differs from the official history:

- **Minutes are whole minutes**, as ESPN publishes them (2025-26 has seconds).
  Paired comparisons score both versions against the same actual minutes, so the
  rounding cancels in a verdict; absolute minutes errors carry about a quarter
  minute of rounding.
- **IDs are ESPN's**: games are `espn-<event>`, players `espn-<athlete>`; teams are
  NBA team IDs matched by tricode. Players therefore do not link to 2025-26's
  NBA IDs. `history-nba-ids.json.gz` is the same rows with 2025-26's player IDs
  where the name matches exactly one 2025-26 player (28,888 of 32,554 rows), the
  seed of the early-season 2025-26 replay; it is rebuilt by
  `scripts/research/previous_ids.py` (sha256 `7ae8098b…`).
- **ESPN omits some inactive players** that the official box score lists. The
  replay adds every player the official injury report lists for the game
  (`backtest._listed_absentees`), so reported absences are scored either way.
- **Team minutes** are the game length (240 plus 25 per overtime period), not the
  sum of rounded player minutes.

Player points, field goals, threes, free throws and turnovers are checked to sum
to ESPN's team totals for every game. The NBA Cup championship (2024-12-17), which
ESPN files as a regular-season game but the league does not count, is excluded.
Every scoreboard and summary response is archived by SHA256 with its retrieval
time under `data/espn-history/2024-25/` (not committed); each record's provenance
names its summary. Counts and file hashes are in `manifest.json`.
