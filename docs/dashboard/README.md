# Dashboard

Run from the repository root with Python 3.11+ and Node 22:

```sh
npm ci --prefix dashboard
npm run build --prefix dashboard
python -m nba_forecast dashboard --directory data/operations/live
```

Open http://127.0.0.1:8765. Point `--directory` at your existing operational
output directory (schedule/latest.json, forecast.sqlite and reports). An empty
directory shows an empty state; opening the dashboard does not create forecasts.
Run the existing operational pipeline separately to update evidence.

- **Games:** choose a date and matchup; review availability, source times and the
  T−30 checkpoint. Refresh reloads saved files, not NBA feeds or paid models.
  Outlooks are separated by team, with **Starters**, **Bench**, and **Role
  unverified** groups. These describe the last five captured appearances on the
  player's current team; they do not confirm the upcoming starting lineup.
  Players are ordered by projected/recent minutes, with surname tie-breaks.
  The preliminary/final controls retain the earlier forecast after a final is
  saved. Daily report status shows the 9 AM Pacific capture and its source.
- **Projections:** compare all players on the selected slate, filter by status,
  search players or team codes, sort by projected points and open player details.
  Minutes, FGA, NBA points and method-labeled 80% ranges come from saved forecasts.
  A dash means unsupported/missing, not zero. Out players can have explicit zeros.
- **Players:** browse the full saved roster, including players not on this slate.
- **Performance:** see prospective error, interval coverage and paired baselines
  after outcomes are recorded. No evaluated results means no accuracy score.
  The 0.5.0 historical validation panel is separately labeled reconstruction,
  with its sample counts, scope and prospective calibration progress.

Expanded stat forecasts and NBA-style profiles are now available: see
[the player intelligence guide](../player-intelligence.md). Fantasy totals still
require a selected scoring rule; they are not implied by the table layout.

The T−30 checkpoint is our operational target, not a guarantee that the NBA's
last or most accurate report is published then. Later reports and lineup changes
can supersede it; inspect timestamps and the later-report warning.

The server is read-only, binds to localhost and serves only built assets plus
`/api/dashboard`. The local persistent runner is configured separately; there is
no hosted deployment or login here.
Live pregame ledger runs must match the current game identity and tipoff; replay
runs, changed tipoffs and postponed games cannot supply a displayed forecast.
Current roster membership is not historical availability evidence. Conditional
Not listed projections use a fully parsed team report and remain visibly conditional.

Optional portable snapshot: `python -m nba_forecast dashboard --directory
<operations-dir> --output dashboard/public/dashboard.json` (one shell line),
then rebuild and open `/?snapshot=1`. Snapshot mode is explicitly labeled; the
capture file is ignored by git. Developer server: `npm run dev --prefix dashboard`
with the Python server running on port 8765.

Team SVGs are retrieved from the NBA CDN; URLs, retrieval timestamps and hashes
are in `dashboard/public/logos/sources.json`. Team marks belong to their respective
owners and are not covered by this project's software license. The dashboard is
independent of the NBA. Fonts are bundled through Fontsource under their included
licenses. All routine dashboard calculations are deterministic and use no LLMs.

Validation: `python -m unittest discover -s tests -v`, `npm test --prefix dashboard`,
and `npm run build --prefix dashboard`. CI runs both Python and frontend checks.

Browser regressions run on desktop and mobile viewports using synthetic saved
evidence without contacting NBA sources:

```sh
npm exec --prefix dashboard -- playwright install chromium
npm run test:e2e --prefix dashboard
```

CI installs Chromium and runs these checks too. An existing compatible Chromium
can be selected with `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/absolute/path/to/chrome`.
The tests cover saved role-review queue membership, refreshing new or corrected
postgame evidence for an unchanged game, and cancellation when switching games.
Refresh reloads postgame evidence as well as the main dashboard capture. The
review filter includes projected players who appear in the saved role queue.

Player outlooks select at most five supported projected starters per team before
filtering or sorting. Unfilled starting slots remain explicit; no official lineup
is implied. Use the team buttons to inspect either roster. Season PPG remains
visible when game projections are withheld, with captured season/sample details
in the tooltip. The cross-check panel distinguishes the latest input-validation
attempt from the validation attached to the selected saved forecast. Legacy runs
remain visible and are labeled unverified.
