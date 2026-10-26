# Official injury reports the night before, 2024-25 replay games

The opening-line companion of `datasets/injury-reports-2024-25/`: for each
team-game record in `datasets/espn-season-2024-25/team-games.json.gz`, the newest official report published by
8:30 PM Eastern the day before the game. Every team has filed its next-day
statuses by then (5 PM local), and live captures first see the opening lines at
about the same time (7:45-11:30 PM ET for the 2026-10-25 and 10-26 slates), so
a replay on these statuses forecasts with roughly what the opener knew.
7,162 players are listed out and 2,785 questionable here (midday: 7,523 and
2,852); 1,527 of 2,458 team sides are fully reported (midday 1,583).
Built with:

```bash
python -m nba_forecast.injury_history --fetch --workers 2 --anchor 'D-1 20:30' --season 2024-25 \
  --team-games datasets/espn-season-2024-25/team-games.json.gz \
  --history datasets/espn-season-2024-25/history.json.gz \
  --calendar datasets/season-schedule-2026-10-21/calendar.json.gz \
  --directory data/injury-history/2024-25-night-before \
  --output datasets/injury-reports-2024-25-night-before/statuses.json.gz
```

Same format, parser and caveats as the T-minus-30 dataset (see its README);
`anchor` is `D-1 20:30`. 163 report slots, none absent (84 were already in the
T-minus-30 or midday archives and were linked, not fetched again). The replay
still reads box scores through the day before, so a team on the second night of
a back-to-back is forecast with a result the evening report predates. Raw PDFs are
under `data/injury-history/2024-25-night-before/raw/`, not committed.
