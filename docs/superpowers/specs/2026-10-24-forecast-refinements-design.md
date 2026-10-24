# Injury context, shot location, and winning-margin forecasts

The user authorized implementation of all three pending forecasting changes on October 24. Existing Python/React architecture and the configured local runner are retained. This document records implementation choices within that authorization.

## Approach

Use independently replayable source evidence and bounded statistical adjustments. A broad learned tracking-data model is not supported by the available locations; a manual-only injury list would miss future games. Preserve old model execution and immutable forecasts, introduce model version `regular-season-baseline-0.6.0`, and install a separate model artifact after validation.

1. Parse explicit same-game injury exits from archived official NBA recaps, reconcile exact game/team/player identity and participation, and use real retrieval timestamps. Never infer injury from short minutes, substitutions, a pregame injury mention, or emotional narratives. Exclude verified shortened appearances only from normal-role minutes and minute variance; retain actual scoring, attempts, exposure, and starter facts. Current availability and explicit minute restrictions remain authoritative. Missing evidence stays visible.
2. Retain historical shooting ability and replace only a bounded share of current-season shooting outcomes with a location-based expectation. Pool reference evidence separately from the player's own outcomes, preserve recency weights and exact shot reconciliation, and fall back to the existing estimate when locations or reference evidence are inadequate. Locations do not reveal defender distance or motivation. All source bytes, model constants, adjustment effects and cutoffs are auditable.
3. Persist a discrete distribution of final home-minus-away margins with every eligible forecast. Do not calculate probabilities from partial rotations, or sum independent player intervals. Model paired team outcomes and make overtime scope explicit. Display the probability of a selected team's margin being strictly greater than an entered line, and its complementary NO probability. Equality belongs to NO for this threshold question. Do not imply these are calibrated market recommendations, ingest prices, execute trades, or multiply legs into a parlay.

## Constraints

- Source evidence must be known by the forecast cutoff; never rewrite or backdate saved forecasts.
- Existing model version 0.5.0 and snapshots without the new inputs remain replayable.
- New model keys cannot inherit old calibration labels or residual fits.
- Full box-score arithmetic, five projected starters, 240 regulation minutes, 9 AM Pacific and T-minus-30 scheduling remain enforced.
- Dashboard explains what inputs changed an estimate and withholds unsupported probabilities.
- Protect the existing scheduled 9 AM restart while editing. Pin complete code before changing the runner's configured release.

## Validation

Use regression cases for Flagg's verified injury exit, pregame injuries and unrelated 'did not return' prose, future-known/tampered archives, missing locations, small samples, team/player swaps, threshold equality, mirrored away probabilities, incomplete totals, overtime/no-tie final outcomes, and old snapshots. Verify current NBA raw source cases without presenting retrospective replays as pregame forecasts. Run Python, dashboard and browser checks and a fresh independent code review before local activation. Record the new artifact/release and limitations; do not claim improved forecast accuracy until measured prospectively.
