# Official injury reports, 2023-24 replay games

`statuses.json.gz`: for each team-game record in
`datasets/espn-season-2023-24/team-games.json.gz`, the newest official NBA
injury report published by the game's T-minus-30 checkpoint, parsed with the
production parser. Same format, parser and caveats as
`datasets/injury-reports-2024-25/` (see its README). Built with:

```bash
python -m nba_forecast.injury_history --fetch --workers 2 --season 2023-24 \
  --team-games datasets/espn-season-2023-24/team-games.json.gz \
  --history datasets/espn-season-2023-24/history.json.gz \
  --calendar datasets/season-schedule-2026-10-21/calendar.json.gz \
  --directory data/injury-history/2023-24 \
  --output datasets/injury-reports-2023-24/statuses.json.gz
```

Reports were hourly all season, named by the hour (`..._07PM.pdf`) and
published at half past. Coverage: every game has a report (564 PDFs, no absent
slot; mean age at the checkpoint 9.5 minutes, at most 43); 9,723 players are
listed out (three of them played) and 426 probable, questionable or doubtful.
1,896 of 2,460 team sides count as fully reported (2024-25: 1,881 of 2,458).

32 team sides are missing from their report altogether, most in the first
weeks (BOS@NYK is not on the 6:30 PM report of 2023-10-25); they carry no
statuses, so their players are available. Of the 538 parser issues on covered
teams, 516 are rows that do not join a box-score player of that team: players
who did not play all season (Vlatko Čančar, Jaylen Clark, Damion Lee, Christian
Koloko, Ricky Rubio, Steven Adams, Nathan Knight, Justin Lewis) or never played
for the listing team (James Harden for Philadelphia, Kyle Lowry for Charlotte,
Marcus Morris Sr. for San Antonio and the Clippers), and two spelling
differences ("Nathan Mensah" for ESPN's "Nate Mensah", 7 rows; "Kenyon Martin
Jr." for "KJ Martin", 1). The other 22 are reason text wrapped onto a status
line. Each only marks the team incomplete. Raw PDFs are under
`data/injury-history/2023-24/raw/`, not committed.

The PDFs were retrieved in October 2026, long after the games, but they are the
files the league published at the stated times; the replay uses each one only
for games whose checkpoint follows its publication time.
