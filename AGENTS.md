# Working in this repository

The goal is forecasts that are measurably more accurate: player points and
minutes (player props) and game margins. Auditability supports that goal; it
does not replace it.

## The model-change rule

Any change to how a forecast is computed (model code, constants, allocation,
intervals, enhancements, priors) must be judged on the backtest before it is
merged or activated:

```bash
python -m nba_forecast backtest \
  --history datasets/espn-season-2024-25/history.json.gz datasets/regular-season-expanded-2026-10-22/history.json.gz \
  --team-games datasets/espn-season-2024-25/team-games.json.gz datasets/model-upgrade-2026-10-23/team-games.json.gz \
  --injuries datasets/injury-reports-2024-25/statuses.json.gz datasets/injury-reports-2025-26/statuses.json.gz \
  --market datasets/market-closing-2024-25/closing-lines.json.gz datasets/market-closing-2025-26/closing-lines.json.gz \
  --output /tmp/backtest-$(date +%Y%m%d-%H%M) \
  --reference datasets/backtest-2026-10-25-two-seasons
```

It replays the 2024-25 and 2025-26 seasons, each with the official injury
report every game's final forecast would have read, in about seven minutes,
and pairs the same player-games against the reference. The verdict pools both
seasons; each season's own verdict is shown beside it. `worse` on points, minutes, team points or margin
error, the 80% interval score, participation or winner Brier, or on points,
team-point or margin bias, means the change is not merged. `within_noise` is acceptable only for a change whose
purpose is not accuracy (a simplification, a bug fix, an operational fix); say
so. `improved` is the bar for a new model feature. A change that improves the
backtest is then also expected to show up in the live `performance.json`
benchmarks after enough games; both are reported, neither is hidden.

When a change is merged, commit its scoreboard as the new reference
(`datasets/backtest-<date>/`, both files) and point this file at it. See
`docs/backtest.md` for what the replay can and cannot measure.

## Before changing the model

Read `docs/field-notes-2026-10-24.md`: what the replay cannot see (week-one
rosters, early participation), the market comparison to run instead,
the measured constants behind the budgets, and the traps.

## Other rules

- Do not change the model version or model key for changes that do not alter
  forecasts. Calibration pools across versions by interval family, but
  forecasts, ledgers and dashboards are labeled by version.
- A finding that would block a forecast must mean the forecast would be
  wrong. Provider disagreements, pending reports and missing cross-checks are
  notes (see `docs/season-operations.md`, "Preforecast cross-check").
- Prefer measured accuracy over new verification layers, documents or
  reviews. If a feature cannot be measured on the backtest or in the live
  benchmarks, say that before building it.
- `python -m unittest discover -s tests` must pass. Commit your work; do not
  leave multi-day changes uncommitted in the shared working tree.
- Production data lives under `data/operations/production` and is never
  edited by hand. Use copies for experiments.
