# Official injury reports at midday, 2023-24 replay games

The preliminary-forecast companion of `datasets/injury-reports-2023-24/`: for
each team-game record in `datasets/espn-season-2023-24/team-games.json.gz`, the
newest official report published by 12:30 PM Eastern on game day (or by the
T-minus-30 checkpoint for a game that tips before 1:00 PM). 2,780 players carry
a probable, questionable or doubtful tag here, against 426 at T-minus-30; 6,917
are listed out (30 of them played); 1,607 of 2,460 team sides are fully
reported. Built with:

```bash
python -m nba_forecast.injury_history --fetch --workers 2 --anchor 12:30 --season 2023-24 \
  --team-games datasets/espn-season-2023-24/team-games.json.gz \
  --history datasets/espn-season-2023-24/history.json.gz \
  --calendar datasets/season-schedule-2026-10-21/calendar.json.gz \
  --directory data/injury-history/2023-24-midday \
  --output datasets/injury-reports-2023-24-midday/statuses.json.gz
```

Same format, parser and caveats as the T-minus-30 dataset (see its README);
`anchor` is `12:30`. 167 report slots; the four absent ones are the 9 AM to
12 PM slots of opening night, 2023-10-24, so its two games (DEN-LAL, GSW-PHX)
have no midday report and every player in them is available. Raw PDFs are under
`data/injury-history/2023-24-midday/raw/`, not committed.
