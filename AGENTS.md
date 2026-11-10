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
  --history datasets/espn-season-2023-24/history.json.gz datasets/espn-season-2024-25/history.json.gz datasets/regular-season-expanded-2026-10-22/history.json.gz \
  --team-games datasets/espn-season-2023-24/team-games.json.gz datasets/espn-season-2024-25/team-games.json.gz datasets/model-upgrade-2026-10-23/team-games.json.gz \
  --injuries datasets/injury-reports-2023-24/statuses.json.gz datasets/injury-reports-2024-25/statuses.json.gz datasets/injury-reports-2025-26/statuses.json.gz \
  --market datasets/market-closing-2023-24/closing-lines.json.gz datasets/market-closing-2024-25/closing-lines.json.gz datasets/market-closing-2025-26/closing-lines.json.gz \
  --output /tmp/backtest-$(date +%Y%m%d-%H%M) \
  --reference datasets/backtest-2026-11-07-market-strength
```

It replays the 2023-24, 2024-25 and 2025-26 seasons, each with the official
injury report every game's final forecast would have read, in about eleven
minutes, and pairs the same player-games against the reference. The verdict
pools the three seasons; each season's own verdict is shown beside it. A change to availability,
participation or reported statuses must also pass the preliminary replay
(`docs/backtest.md`, "Preliminary replay"). `worse` on points, minutes, team points or margin
error, the 80% interval score, participation or winner Brier, or on points,
team-point or margin bias, means the change is not merged. `within_noise` is acceptable only for a change whose
purpose is not accuracy (a simplification, a bug fix, an operational fix); say
so. `improved` is the bar for a new model feature. A verdict needs the change to
exceed two clustered standard errors and a materiality floor of 0.5% of the
reference level; CI's `--limit 400` smoke run reports the interval score without
gating it (`docs/backtest.md`). A change that improves the
backtest is then also expected to show up in the live `performance.json`
benchmarks after enough games; both are reported, neither is hidden.

That command replays the baseline path. Production forecasts go through
`candidate.forecast_candidate` as well (minutes redistribution, pace and
defense budgets, rest and travel), so every model change is also replayed on
the production path, against its own reference, in about thirty-five minutes:

```bash
python -m nba_forecast backtest \
  --history datasets/espn-season-2023-24/history.json.gz datasets/espn-season-2024-25/history.json.gz datasets/regular-season-expanded-2026-10-22/history.json.gz \
  --team-games datasets/espn-season-2023-24/team-games.json.gz datasets/espn-season-2024-25/team-games.json.gz datasets/model-upgrade-2026-10-23/team-games.json.gz \
  --injuries datasets/injury-reports-2023-24/statuses.json.gz datasets/injury-reports-2024-25/statuses.json.gz datasets/injury-reports-2025-26/statuses.json.gz \
  --market datasets/market-closing-2023-24/closing-lines.json.gz datasets/market-closing-2024-25/closing-lines.json.gz datasets/market-closing-2025-26/closing-lines.json.gz \
  --production-path datasets/production-artifact-2026-11-09/artifact.json.gz \
  --output /tmp/backtest-production-$(date +%Y%m%d-%H%M) \
  --reference datasets/backtest-2026-11-09-production-path
```

A change must not be `worse` on either path, by the rules above. A change
inside the production-only layers (`candidate.py`, `rotation_allocation.py`,
`game_environment.py`, `context_effects.py`, the artifact's parameters or
context model) leaves the baseline replay identical and is judged on the
production path. The backtest refuses a reference replayed on the other path.

When a change is merged, commit both scoreboards as the new references
(`datasets/backtest-<date>/` and `datasets/backtest-<date>-production-path/`,
both files each) and point this file at them. A live artifact with new
parameters or a new context model is exported with
`scripts/research/export_production_artifact.py` and gets a new
production-path reference. See `docs/backtest.md` for what each replay can and
cannot measure.

## Before changing the model

Read `docs/field-notes-2026-10-24.md`: what the replay cannot see (week-one
rosters, early participation), the market comparison to run instead,
the measured constants behind the budgets, and the traps. Read
`docs/research-lessons-2026-10.md` for what the field does, where the model
stands against the close, and which ideas were tried or set aside.
Read `docs/beating-the-close-2026-11-06.md` before any work aimed at the
market: what the close can and cannot be beaten on, and the close-as-teacher
measurements of what the model is missing.

## Other rules

- Do not change the model version or model key for changes that do not alter
  forecasts. Player calibration pools across versions by interval family and
  the margin correction by margin method, settlement and parameter set
  (`margins.CALIBRATION_FAMILY`), but forecasts, ledgers and dashboards are
  labeled by version.
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
