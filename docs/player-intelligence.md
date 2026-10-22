# Player profiles, shot charts and expanded forecasting

## Run it

From the repository root (Python 3.11+, Node 22+, Poppler `pdftotext`):

```sh
npm ci --prefix dashboard
npm run build --prefix dashboard
python -m nba_forecast tick --directory data/operations/production --history datasets/regular-season-expanded-2026-10-22/history.json.gz
python -m nba_forecast sync-players --directory data/operations/production --history datasets/regular-season-expanded-2026-10-22/history.json.gz --max-shot-games 12
python -m nba_forecast dashboard --directory data/operations/production
```

Open http://127.0.0.1:8765. Click any player for overview, current/prior season
averages, last 10 captured games, actual shot chart and pregame forecast history.
The player pool covers the full active roster. Profile means use captured games
played, exclude DNPs, and show a denominator for every category. Percentages use
total makes / attempts. Missing data never becomes zero. Roster bio fields come
from NBA.com/players; the profile and game-log update times remain visible.

## Forecast behavior

Model 0.2.1 adds REB, AST, 3PM, STL and BLK to PTS, FGA, FTA and TO. Other-category
rates shrink against explicit fixed modeling priors, not asserted current league
averages. Minutes and shooting retain separate smoothing rates. Optional stats
require positive-minute observed evidence; unavailable fields stay null. Review
flags expose roster/role changes and unallocated team minutes. Sourced reviewed
`role_adjustments` can include `minutes_cap` (0–48) as well as minutes delta and
opportunity multiplier; cap applies before team scaling. No automatic recipient
of vacated minutes is asserted. Coaching and matchup evaluations remain expert
review tasks with game-specific source, timestamp, rationale and reviewer.

Availability keeps explicit Available, Out, uncertain and unknown distinct. The
new conditional policy permits a `Not listed` assumption only when the entire
PDF page set is present, report headings match its publication timestamp, the
exact game/date/team is covered, and that team's rows have no parsing/identity/
submission issues. Unsupported or missing-status player rows prevent inference.
This is **not confirmation of participation**. Conditional estimates carry the
flag and wording in the UI and ledger. Unknown/Questionable/Doubtful/Probable
continue to require review. Missing or incomplete reports do not become healthy.

NBA guidance says reports update continually, not that T−30 is the last update:
https://official.nba.com/nba-injury-report-2026-27-season/
The runner keeps the T−30 anchor for auditing and applies the latest captured
report for availability. Pregame refresh slots are 15 minutes, then five minutes
inside T−30. A reviewed input or model/parser revision also gets a distinct job.
Every successful run is immutable; nothing overwrites the original prediction.

## Shot evidence

NBA LiveData play-by-play is paired with a FINAL boxscore. Every player's 2/3-point
makes and attempts must reconcile before publishing. Only `xLegacy/yLegacy`
coordinates are plotted (10 units/foot, hoop origin); no invented locations or
predicted heatmaps. Half-court clipping and missing coordinates have explicit
counts. The filter selects made/missed/all attempts. Each shot retains its source
URL, retrieval time, event number and game ID; raw payload hashes are archived.

A 403/500 or schema/reconciliation failure becomes a visible missing-data reason.
Failure retries are deferred six hours; successful shot files are cached. Each
runner tick fetches at most two missing shot games after pregame work. An explicit
sync can fetch up to 50. Coverage is captured games, not a claim of full-season
completeness. NBA interfaces are undocumented and access varies by game/endpoint.

## Feedback and routine costs

Evaluations add per-stat sample counts, MAE, bias and minutes/rate error
components. Aggregate reporting remains outcome-blind: the latest eligible
pregame forecast is chosen before looking for its result. No-result/low-sample
states remain explicit. Player history displays saved predictions beside actual
final outcomes when matched. Fixed priors and heuristic intervals are not claims
of accuracy or calibration; review feedback before making a versioned adjustment.

All collection, arithmetic, validation, caching and routine reporting run without
LLMs. Only unresolved role/coaching/matchup questions enter the expert review
queue. This release adds no paid model calls or automatic model tuning.

## Continuous host setup

A persistent Linux host is required for an actual always-on service. The current
Work session is not that host; no 24/7 deployment is claimed. On your chosen host:

```sh
python scripts/install_services.py --directory data/operations/production --history datasets/regular-season-expanded-2026-10-22/history.json.gz
systemctl --user daemon-reload
systemctl --user enable --now nba-forecast.timer nba-dashboard.service
systemctl --user status nba-forecast.timer nba-dashboard.service
journalctl --user -u nba-forecast.service
```

The installer resolves real paths and refuses to replace existing units. Enable
user lingering if your host requires it. The dashboard remains on localhost;
use SSH port forwarding or an authenticated reverse proxy for remote access.
Do not put the SQLite ledger on ephemeral serverless storage. Persist/back up the
operations directory. The heartbeat, error log and coverage report show missed
checkpoints, failures and stale data. Timer execution is not a timing guarantee;
long source timeouts can delay ticks and are reported.

For an offline static capture, export dashboard.json as before and copy the
operations `players/` directory into `dashboard/public/players/` before building.
Only serve a capture with `?snapshot=1`; it must not be labeled live.
