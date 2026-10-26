# Official injury reports the night before, 2023-24 replay games

The opening-line companion of `datasets/injury-reports-2023-24/`: for each
team-game record in `datasets/espn-season-2023-24/team-games.json.gz`, the
newest official report published by 8:30 PM Eastern the day before the game
(see `datasets/injury-reports-2024-25-night-before/` for why that time). 6,572
players are listed out (56 of them played) and 2,752 questionable, doubtful or
probable here (midday: 6,917 and 2,780); 1,552 of 2,460 team sides are fully
reported (midday 1,607). Built with:

```bash
python -m nba_forecast.injury_history --fetch --workers 2 --anchor 'D-1 20:30' --season 2023-24 \
  --team-games datasets/espn-season-2023-24/team-games.json.gz \
  --history datasets/espn-season-2023-24/history.json.gz \
  --calendar datasets/season-schedule-2026-10-21/calendar.json.gz \
  --directory data/injury-history/2023-24-night-before \
  --output datasets/injury-reports-2023-24-night-before/statuses.json.gz
```

Same format, parser and caveats as the T-minus-30 dataset (see its README);
`anchor` is `D-1 20:30`. Every game but the two of opening night uses the 8 PM
report of the day before; there was no report on 2023-10-23, so those two have
none. The archive's manifest lists 452 slots because earlier hours of the same
evenings were linked from the T-minus-30 archive; only the 8 PM ones are read.
The replay still reads box scores through the day before (see the 2024-25
README). Raw PDFs are under `data/injury-history/2023-24-night-before/raw/`, not
committed.
