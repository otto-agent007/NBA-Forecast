# Possessions, rotation, context and uncertainty upgrade

Owner request: correct the current model's missing pace/possessions, automatic
injury redistribution, numerical rest/travel context and empirically evaluated
uncertainty. Preserve original forecasts and active final checkpoints.

## Approach

Keep the transparent player model and add independently auditable components.
An independent replacement model would discard the current source/role work;
arbitrary multipliers would add unsupported assumptions. Build a candidate with
source-backed team possessions, constrained injury allocation, fitted context
effects, and empirical residual intervals. Compare chronologically before
switching the pinned production runner. Do not interrupt active forecasts.

## Data and validation

The current source-bound history contains 1,244 games and 43,650 player rows.
It omits offensive rebounds. Collect final ESPN game summaries matched to NBA
game IDs, opposing team identities, basketball date and scores. Preserve raw
bytes/hashes and actual observation times. Never infer missing counts as zero.
Estimated possessions use FGA + 0.44*FTA - OREB + TOV, averaging both teams and
adjusting for overtime. Call this an estimate, not an exact possession count.

Past finals were collected after those games. Historical chronological research
therefore reconstructs pregame statistical features from earlier game events;
it cannot claim original point-in-time roster/injury evidence. Keep it separate
from genuine prospective ledger evaluation. Preserve all source known_at values.
Split by game dates into training, calibration/selection and untouched holdout;
do not tune coefficients or interval widths using holdout outcomes.

## Modules and boundaries

- `research_game_data.py`: bounded archived team-boxscore collection and joins.
- `game_environment.py`: shared, strictly cutoff-filtered team pace/defense model.
  Input game records have game_id, game_time, known_at, home_team_id,
  away_team_id, team_stats keyed by NBA team ID with pts/fga/fta/oreb/tov,
  optional venue_timezone and provenance. No source field is fabricated.
- `rotation_allocation.py`: supported healthy inputs only; distribute evidenced
  out-player vacancies within observed and manual minute/attempt caps. Unknown
  availability receives no invented production. Account for attempts already
  added by extra minutes before increasing scoring rates. Emit a full audit.
- `calibration.py`: cutoff/model-specific empirical standardized residual
  quantiles with explicit sample counts and evidence kind. Sparse evidence uses
  an explicit heuristic fallback. Time dependence prevents an unconditional
  coverage guarantee; show actual chronological/prospective coverage separately.
- Root integration owns core model, context fitting, operations, profiles,
  validation, dashboard and versioned release/rollback.

## Invariants

No future-known input in live forecasts; no edits/backdating in the ledger;
five supported starters and existing source checks remain mandatory. Team
minutes <=240 and player minutes <=48/manual caps. Team/game totals reconcile
with player rows; no double pace or injury adjustment. Unknown inputs remain
visible. Manual reviewed inputs retain precedence. Record neutral fallbacks
when data cannot support an effect. Keep the pinned baseline until candidate
verification and measured comparisons support activation.

## References

NBA possession/pace definitions: https://jr.nba.com/basictraditional-stats-vs-advanced-stats/
Time-dependent interval caveats: https://www.stat.berkeley.edu/~ryantibs/papers/nexcp.pdf
