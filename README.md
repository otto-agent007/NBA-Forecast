# NBA-Forecast

Reproducible regular-season player scoring forecasts with an auditable pregame
ledger and postgame feedback. Python 3.11+; no model API calls or paid dependencies. Injury extraction uses Poppler.

## Start

```bash
python -m unittest discover -s tests -v
python -m nba_forecast --help
python -m nba_forecast backfill --seasons 2025-26 2026-27 --directory data/history
```

Backfill resumes per game, archives official NBA sources, and writes
`data/history/history.json.gz` only after every indexed final passes validation.
Its `manifest.json` records coverage, failures, retrieval times and hashes.

A [validated historical snapshot](datasets/regular-season-2026-10-21/README.md) is included.

## Daily loop

1. `fetch-slate`: get the full current roster and upcoming games.
2. `injury-check`: capture the official report available at each game's **T−30**
   checkpoint; newer reports are tracked separately. Run this command at the
   checkpoint—it does not install a background scheduler.
3. `apply-injuries`: extract explicit statuses; review unmatched players and roles.
4. `forecast`: save a preliminary run, or use `--stage final --injury-check FILE`
   within 30 minutes of each included tipoff. Final is a timing/evidence label,
   not a guarantee of complete availability or superior accuracy.
5. `evaluate` final boxscores, then `report` season/model performance.

Use `--history data/history/history.json.gz` for the backfill. Every roster player
gets a projected, out, needs-review, or no-game row. Unknown availability and
unsupported production are withheld. Prior shooting evidence is preserved;
minutes and opportunities adapt faster.

## Guides

- [Season schedule, automated ticks and review inputs](docs/season-operations.md)
- [Commands, demo and input formats](docs/usage.md)
- [Backfill and T−30 report workflow](docs/backfill-and-injuries.md)
- [Daily research and review loop](docs/operations.md)
- [Model design](docs/design.md) · [Verification](docs/validation-results.md)
- [Backtest scoreboard and the model-change rule](docs/backtest.md) · [Rules for agents](AGENTS.md)

This is an initial scoring baseline. Interval widths use a measured heuristic
default until the pooled prospective calibration is ready. Any change to the
model is judged on the season replay before it is merged (`backtest`, see
`AGENTS.md`).
Injury extraction is conservative: omission never means available.
`tick` runs the loop once; persistent Linux timer templates are in `deploy/systemd/`.
The automatic loop captures a daily injury report at 9 AM Pacific and each
game's final checkpoint at T−30. Preliminary forecasts remain inspectable
alongside finals, with player outlooks grouped by team and five projected starters.
Automated and live CLI runs cross-check the matchup, tipoff and rosters against
ESPN and apply official injury information for both teams before running the
model. Only evidence that would make the forecast wrong blocks it (a started,
postponed or moved game, invalid inputs, or contradicted availability); provider
differences and pending reports are notes on the dashboard. Until a team's
official injury report is published, ESPN's game injury list supplies its
availability. Projected starters remain unconfirmed.
When the official PDF omits a healthy team's block, an explicit empty injury list
in the verified game-specific ESPN report can support conditional projections.
Missing fields, unsubmitted reports and roster Active labels cannot do this.
Back up `data/forecast.sqlite` and `data/`: they are ignored by Git. GitHub CI tests
Python 3.11–3.13; PRs are reviewed before merging.

## Dashboard

Game-day matchups, team logos, player projections and performance: see [setup and usage](docs/dashboard/README.md).

Player profiles, shot charts, expanded stats and continuous operation: [guide](docs/player-intelligence.md).

Postgame recap and model-feedback workflow: [guide](docs/postgame.md).
