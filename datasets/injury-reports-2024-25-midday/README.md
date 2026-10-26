# Official injury reports at midday, 2024-25 replay games

The preliminary-forecast companion of `datasets/injury-reports-2024-25/`: for each
team-game record in `datasets/espn-season-2024-25/team-games.json.gz`, the newest official report published by 12:30 PM
Eastern on game day (or by the T-minus-30 checkpoint for a game that tips
before 1:00 PM). Most questionable, doubtful and probable tags are still open
then: 2852 players carry one here, against a few hundred at T-minus-30; 7523 are
listed out. Built with:

```bash
python -m nba_forecast.injury_history --fetch --workers 2 --anchor 12:30 --season 2024-25 \
  --team-games datasets/espn-season-2024-25/team-games.json.gz \
  --history datasets/espn-season-2024-25/history.json.gz \
  --calendar datasets/season-schedule-2026-10-21/calendar.json.gz \
  --directory data/injury-history/2024-25-midday \
  --output datasets/injury-reports-2024-25-midday/statuses.json.gz
```

Same format, parser and caveats as the T-minus-30 dataset (see its README);
`anchor` is `12:30`. Used by the preliminary replay (`docs/backtest.md`,
"Preliminary replay") and to fit `model.STATUS_PARTICIPATION`. Raw PDFs are under
`data/injury-history/2024-25-midday/raw/`, not committed.
