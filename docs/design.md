# NBA-Forecast v0.1 design (October 2026, historical)

The design the first version was built to. The contracts below still hold; the
model has since grown participation probabilities, team budgets, a team-rating
margin blend, injury-report replays and a refined production path, which
`README.md`, `docs/backtest.md` and `docs/season-operations.md` describe.

Build a reproducible player scoring baseline, not a claim of betting edge. The
owner wants every active player covered, regular-season evidence only, explicit
minutes/opportunities/roles, resistance to single-game shooting noise, and a
feedback loop.

## Architecture and boundaries

Python 3.11+ standard library. JSON input/output, SQLite immutable forecast
ledger, unittest tests. No API keys, paid data subscriptions, or LLM calls.
Official NBA LiveData boxscores are the initial ingest format. Network retrieval
is optional; cached raw files are equally usable. Fail closed on missing or
invalid data. A separately supplied, sourced current full roster is mandatory;
historical boxscores cannot establish today's active pool.

All timestamps are timezone-aware ISO 8601. IDs are strings. `source_url` must
be HTTPS; provenance is recorded, not magically authenticated by a URL.

## Shared contracts

`history: list[dict]`: each row contains `game_id`, `season` (YYYY-YY),
`season_type` = `Regular Season`, `game_time`, `known_at`, `source_url`,
`player_id`, `name`, `team_id`, `opponent_id`, `minutes`, `fgm`, `fga`,
`fg3m`, `fg3a`, `ftm`, `fta`, `tov`, `pts`, `status` (`played` or `dnp`).
Counts nonnegative integers; minutes nonnegative finite; score identity exact;
made <= attempted; no duplicated (game_id, player_id). Unknown is not zero.

`snapshot: dict`: `schema_version: 1`, `season`, `as_of`, `roster`, `games`,
optional `role_adjustments` list.
`roster`: `observed_at`, `source_url`, `complete: true`, `team_ids` (30 unique
team IDs for league scope; any explicit subset for demo), `players` list.
Players: `player_id`, `name`, `team_id`, `status` (`available`, `out`,
`questionable`, `unknown`). All roster members get a pool row even off slate.
Any nonunknown status also requires `status_source_url` and
`status_observed_at` within 24 hours before cutoff. Roster membership alone is
not availability evidence.
`games`: `game_id`, `start_time`, `season_type: Regular Season`,
`home_team_id`, `away_team_id`; one game per team per snapshot.
`role_adjustments`: `player_id`, `game_id`, `observed_at`, `source_url`,
`reason`, `reviewer`, `minutes_delta` in [-15,15],
`opportunity_multiplier` in [0.5,1.5]. Game-specific and audited. These can
encode coaching/lineup evidence without letting prose directly change a model.

`model.forecast(snapshot, history) -> dict`: pure function after input validation.
Output contains `model_version`, `as_of`, `season`, `predictions`,
`review_queue`, `coverage`. Each prediction contains `player_id`, `name`,
`team_id`, `game_id` (null off slate), `start_time` (null off slate),
`status` (`projected`, `out`, `needs_review`, `no_game`), `reason`,
`expected_minutes`, `expected_fga`, `expected_fta`, `expected_tov`,
`usage_proxy`, `points_mean`, `points_sd`, `points_lower80`,
`points_upper80` (null when unsupported), `history_games`, `flags`,
and `components` with model audit details. Out players have zero projections.
`store.stored_forecast(snapshot, forecast)` is what a tick saves: the same
forecast without the `no_game` rows of teams outside the snapshot's games,
which `coverage` (`roster_players`, `pool_rows`, `by_status`) still counts.

`store.save_run(db_path, snapshot, history, forecast, mode='live', scope='league')
-> str`: transactionally save full canonical inputs and output with SHA256,
actual wall-clock created_at and unique run_id. Live must be saved before all
tipoffs, with snapshot.as_of <= now; replay/demo can use past dates but are
explicitly excluded from live metrics. League scope requires 30 roster teams.
The predictions are every roster player once, or every roster player of the
slate's teams once with the others counted in `coverage`.
No mutation of existing predictions. No silent replacement on rerun.
`store.evaluate_run(db_path, run_id, actuals) -> dict`: matching final normalized
rows only. Repeated actuals identical are idempotent; corrections append an
evaluation revision. Unmatched predictions remain pending, never zero-filled.
Metrics: MAE, RMSE, mean signed error, minutes MAE, 80% interval coverage and
width; sample counts and status groups; actionable diagnostic flags. An out
forecast for a player who plays is included as an error. Off-slate and
unsupported rows excluded explicitly. Non-live reports clearly labeled.

`store.performance_report(db_path) -> dict`: choose latest actually saved live
pregame row per (season, model_version, game_id, player_id) before examining
results; equal save times break by insertion order. Use the chosen run's latest
evaluation revision. Never fall back to an older favorable/evaluated run. Keep
versions/seasons separate; exclude demo/replay/subset and off-slate rows.

## Statistical scope

Use previous-season regular-season baselines plus current regular-season data.
Minutes and attempt rates adapt faster than shooting efficiency. Shrink shooting
percentages to established attempts so a bad shooting night alone cannot erase
the baseline. No unobserved rookie production and no automatic teammate-usage
redistribution. Each projected player carries a participation probability:
his rate of appearing with positive minutes over the team's last ten games
since he joined, leaving out games his official injury report listed him out
of, shrunk toward a 2025-26 role prior, and scaled by a reported
questionable-type status. Player projections stay conditional on playing;
the 240-minute budget and team totals weight them by that probability; minutes
the rotation leaves unallocated enter the team total at a replacement scoring rate.
Each team's expected possessions used are held to its own usage level (league
112.6 per 240 minutes), so rates learned in other roles cannot add up past it.
Questionable players are projected that way; unknown players are withheld. Roster changes and major minutes/shot
changes enter a reasoning queue. Opponent adjustment is a bounded, shrunk
points-allowed proxy derived only from complete pre-cutoff team boxscores; it
is not a measured possession-adjusted defensive rating. A symmetric home-court
factor (home up, away down by the same ratio) comes from complete pre-cutoff
box scores that record the side, shrunk toward a 2025-26 prior of 1.45% and
bounded. Intervals are initial heuristics until measured on a sufficient
prospective sample.

## Evidence and acceptance

Reject preseason and future/late-known inputs, invalid counts, stale roster
snapshots (>48h), unknown adjustment targets, incomplete league manifests,
and duplicate facts. A current 519-player/30-team roster has been verified,
but availability remains unknown; do not manufacture live numeric projections.
Commit a conspicuously synthetic
offline demo plus a primary-source historical ingestion check if accessible.
Tests cover full pool, cold starts, trades, shooting shrinkage, opponent effects,
time leakage, timestamp immutability, idempotency, DNP vs missing, calibration,
and one end-to-end CLI run. Independent final code review before PR.
