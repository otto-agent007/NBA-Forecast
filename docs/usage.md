# Commands, demo and input formats

The command-line reference for the forecasting pipeline: it ingests official
regular-season boxscores, covers the entire supplied active roster, forecasts
scoring through minutes and opportunities, and keeps an immutable prediction and
evaluation ledger. For what the project is today and how production runs, start
at the repository `README.md`; this page lists the commands and their inputs.

Python 3.11+; standard library only. Run from the repository root; no installation,
API key, paid model, or network connection is needed for the offline tests/demo.

```bash
python -m unittest discover -s tests -v
python -m nba_forecast --help
```

## Try the complete flow offline

This example is deliberately synthetic, including names, observations and
availability. It tests the full workflow, not forecasting accuracy.

```bash
python examples/make_demo.py --output data/demo
python -m nba_forecast forecast --snapshot data/demo/snapshot.json \
  --history data/demo/history.json --scope subset --mode demo \
  --db data/demo/ledger.sqlite --output data/demo/forecast.json
```

Copy `run_id` from `data/demo/forecast.json` into the evaluation command:

```bash
python -m nba_forecast evaluate --db data/demo/ledger.sqlite \
  --run-id RUN_ID --actuals data/demo/actuals.json
python -m nba_forecast report --db data/demo/ledger.sqlite
```

The per-run evaluation reports error, bias, uncertainty coverage and diagnostic
flags. The aggregate report excludes this demo. Output files refuse overwrites;
use a fresh directory to repeat the demo. SQLite saves each run under a new ID.

## Verify real data and full-pool coverage

[`examples/verified/`](../examples/verified/README.md) contains unchanged official
NBA boxscores with retrieval times and SHA256 hashes, plus an observed roster
snapshot of **519 players across all 30 teams**. It includes three final 2026–27
opening games and one prior-season sample. These few games are ingestion
fixtures, not a sufficient baseline for the whole league.

```bash
mkdir -p data/verified
python -m nba_forecast normalize-boxscore \
  --input examples/verified/boxscore_0022600001.json \
  --provenance examples/verified/boxscore_0022600001.json.provenance.json \
  --output data/verified/history.json
python -m nba_forecast forecast --snapshot examples/verified/snapshot.json \
  --history data/verified/history.json --mode replay \
  --db data/verified/ledger.sqlite --output data/verified/forecast.json
```

The output contains every roster player, including players with no game and
players awaiting evidence. The source roster establishes membership, **not game
availability**; numeric projections are withheld for unknown/questionable
availability. Historical snapshots use `replay`, never a backdated `live` label.

## Collect and make a prospective forecast

```bash
mkdir -p data/today
python -m nba_forecast fetch-slate --output data/today/snapshot.json
python -m nba_forecast fetch-boxscore --game-id 0022600001 \
  --output data/today/game-0022600001.json
```

The slate defaults to the next 24 hours; `--until` accepts an explicit timestamp
to shorten it. Downloads preserve exact raw bytes and provenance in `data/raw/`.
Collect the previous season and all completed current-season games needed for
your baseline. Pass multiple normalized history files after `--history`. The
collector fetches one explicit boxscore at a time; no hidden background sweep or
scheduled job is installed. Missing or failed sources stop ingestion.

Before a numeric live forecast, add sourced availability to the snapshot:
`available`, `out`, or `questionable`, with `status_source_url` and
`status_observed_at` within the 24 hours preceding `as_of`. Keep unverified
players `unknown`. An official roster listing is not evidence of being healthy.
After collecting new evidence, update `as_of` to the actual current cutoff and
ensure all games remain unstarted. Record proposed rotation/usage changes in
the audited `role_adjustments` schema in [the design](design.md).

```bash
python -m nba_forecast validate --snapshot data/today/snapshot.json \
  --history data/today/game-0022600001.json
python -m nba_forecast forecast --snapshot data/today/snapshot.json \
  --history data/today/game-0022600001.json --db data/forecast.sqlite \
  --output data/today/forecast.json
```

Live runs require the complete 30-team manifest, a fresh roster, cutoff no later
than the actual save time, and actual saving before **every** tipoff in the
snapshot. After some games start, fetch a new remaining-game slate. A live run
with no numeric predictions is a coverage checkpoint, not evidence of skill.

After games finish, fetch their final boxscores and run `evaluate` with that
run's `run_id`. Include only games/players belonging to that run; unexpected
identities are rejected. Corrections create new evaluation revisions; previous
forecasts and evaluation revisions remain unchanged.

## What is modeled

| Component | Initial behavior |
| --- | --- |
| Minutes | Current games receive recency weight; up to six prior-season pseudo-games stabilize the start of the season. Explicit DNPs count; missing data never become zero. |
| Opportunities | Separate FGA, 3PA, FTA and turnover rates per minute adapt faster than shooting efficiency. `usage_proxy = FGA + 0.44*FTA + TOV` is an opportunity count, **not USG%**. |
| Shooting | Attempt-weighted shrinkage retains established two-point, three-point and free-throw baselines. One poor night cannot simply replace the prior. |
| Opponent | Bounded ±5% shrunk points-allowed adjustment from complete pre-cutoff boxscores, normalized for overtime duration. This is not possession-adjusted defense. |
| Roles | Team changes, minutes/opportunity changes and rotation gaps trigger review. Sourced game-specific adjustments can change minutes/rates within explicit bounds. No automatic teammate-usage transfer. |
| Uncertainty | Initial 80% intervals combine shooting and minutes variability. They are explicitly uncalibrated until prospective coverage is measured. |
| Full pool | Every roster entry becomes projected, out, needs_review, or no_game. Cold starts with no positive-minute regular-season evidence are withheld. |

Exact formulas/defaults and component audits are in
[`nba_forecast/model.py`](../nba_forecast/model.py). Aggregate projected regulation
minutes cannot exceed 240 per team; deficits are flagged rather than filled by
inventing bench production. No overtime forecast, assists/rebounds forecast,
market pricing, or automatic parameter tuning is included in v0.1.

## Improve with evidence and control cost

Routine downloads, normalization, arithmetic, validation, tests and error
measurement are deterministic: **zero model API spend**. The structured review
queue separates routine source/availability verification from harder
roster/rotation/coaching questions. A cheap extractor may draft sourced facts;
a capable reasoning model or human reviews only difficult changes and writes a
bounded, timestamped adjustment. No model can silently override the ledger or
make unsupported player statistics. See [the operating loop](operations.md).

`report --db data/forecast.sqlite` provides prospective feedback by season and
model version, with deterministic pregame run selection. It reports MAE, RMSE,
signed bias, minutes error, 80% interval coverage/width and sample counts.
Review minutes versus scoring-rate errors before changing a parameter. Compare
future model versions prospectively; never rewrite prior predictions to make a
new model appear better.

## Source and model limitations

- NBA CDN and `/players` schemas are not stable public contracts; HTTP 403,
  outages and schema changes are possible. Offline archived input is supported.
- Provenance links and hashes make observations auditable; they do not prove
  that a manually edited fact is true. Keep source artifacts and analyst notes.
- The included source examples were retrieved on October 21, 2026 (Pacific).
  They cannot establish what this pipeline knew before that retrieval time.
- Availability and coaching judgments require fresh evidence. No automated
  injury-report parser or licensed tracking/on-off dataset is bundled.
- The model uses only current/previous regular seasons. Preseason and playoff
  data are rejected; schedules exclude the non-regular-season Cup final.
- No fitted performance benchmark or betting edge is claimed. Small-sample
  calibration flags are diagnostics, not proof. Evaluation currently uses final
  points/minutes; richer opportunity and opponent-adjusted scoring is future work.

Design and implementation scope: [design](design.md),
[plan](implementation-plan.md). CI runs the offline suite on Python 3.11–3.13.
