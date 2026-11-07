# NBA-Forecast

Player points and minutes and game margins for the NBA regular season, from
official box scores and injury reports, with every forecast saved before tipoff
and scored after. Python 3.11+ standard library only (Poppler's `pdftotext` for
the injury PDFs); the dashboard is React with Vite. Not a betting product: the
closing line is the benchmark, and the model trails it.

## How it runs

A user systemd timer runs `python -m nba_forecast tick` every minute from a
pinned copy of the package (`docs/season-operations.md`). Each tick:

- captures the official injury report at 9 AM Pacific and again at each game's
  T−30 checkpoint, and cross-checks matchup, tipoff and rosters against ESPN;
- forecasts every pregame game whenever its evidence changes (report edition,
  ESPN injury list, roster), saving the whole input and output in an
  append-only SQLite ledger;
- captures sportsbook lines from ESPN and Kalshi at the forecast cadence, every
  minute in the last five before tip;
- after finals, scores the saved forecasts.

A second one-minute timer runs `python -m nba_forecast derived`, which rebuilds
the performance, calibration, market, postgame and daily-review reports the
dashboard reads whenever their inputs changed. It runs beside the tick, so a
rebuild that takes minutes never delays a checkpoint forecast or a line capture.

Deploy a merged change with `python3 -m nba_forecast.deploy --directory
data/operations/production` (`--dry-run` first); it records the commit in
`production/deployed.json`.

## The model

- **Players** (`nba_forecast/model.py`, version 0.4.8): minutes from recent role
  evidence, a participation probability from the team's last ten games and the
  reported status, scoring opportunities and shrunk shooting percentages,
  bounded opponent and home-court factors, a 240-minute budget and a team
  possession budget. Projections are conditional on playing; team totals weight
  them by the participation probability.
- **Production path** (version 0.6.0, `candidate.py`): the baseline plus
  rotation redistribution for absences, pace and defense factors from team box
  scores, rest and travel context, and 80% intervals calibrated on the ledger's
  own prospective residuals.
- **Margins** (`margins.py`): a discrete normal whose location blends the
  player-sum difference (0.35) with a team rating (0.65; season-to-date margin
  padded with last season's, less 0.1 points per minute of reported-out
  regulars) and whose scale is the dispersion of paired finals.

## The rule

Every change to how a forecast is computed is judged first on a three-season
replay with the official injury report each game's forecast would have read,
paired on the same player-games against a committed reference, with clustered
standard errors and a materiality floor. `worse` is not merged; `improved` is
the bar for a new feature. The command, the verdicts and every reference are in
[`docs/backtest.md`](docs/backtest.md); the rules are in [`AGENTS.md`](AGENTS.md).

Where the model stands (reference `datasets/backtest-2026-11-07-market-strength`, three
seasons, 2,604 games): margin MAE 11.16 against the closing line's 10.90,
winners 69.0%, team points MAE 9.28, player points MAE 4.48, minutes MAE 4.84,
80% interval coverage 80.2%. [`docs/research-lessons-2026-10.md`](docs/research-lessons-2026-10.md)
has what the field does, where the remaining gap is and what was tried.

## Start

```bash
python3 -m unittest discover -s tests          # 700 offline tests, about a minute
python3 -m nba_forecast --help                 # every command
npm ci --prefix dashboard && npm test --prefix dashboard && npm run build --prefix dashboard
```

The backtest command is in `AGENTS.md` (about eleven minutes); CI runs the
tests on Python 3.11 to 3.13, a 400-game smoke replay against the reference,
and the dashboard's unit, build and end-to-end checks.

## Where things are

| Path | Holds |
|---|---|
| `nba_forecast/` | The package: `model`, `candidate`, `margins`, `totals` (the forecast); `operations`, `injuries`, `injury_capture`, `preflight`, `schedule`, `market_lines`, `kalshi_lines` (the tick and the derived service); `store` (the ledger); `backtest`, `injury_history`, `espn_season`, `market_history` (the replay and its datasets); `dashboard`; `deploy` |
| `tests/` | `unittest` suites, offline, one per module |
| `datasets/` | Committed replay inputs (box-score histories, team games, injury reports, closing lines) and every backtest reference scoreboard |
| `data/` | Gitignored: production operations under `data/operations/production`, raw archives, the ledger; back it up |
| `dashboard/` | The React page; `dist/` is served by the dashboard service |
| `scripts/research/` | The analysis scripts behind the research-lessons numbers |
| `docs/` | Guides; start at [`docs/README.md`](docs/README.md) |
| `deploy/systemd/` | Unit templates (tick, derived reports, dashboard); `scripts/install_services.py` writes them for a host from `nba_forecast/units.py` |

Rules of the data: production data is never edited by hand, every ingested
source is archived with its hash and retrieval time, and nothing is read from
after a forecast's cutoff.
