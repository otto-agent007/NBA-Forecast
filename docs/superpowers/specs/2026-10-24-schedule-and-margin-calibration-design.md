# Schedule access and margin calibration

The NBA CDN returns HTTP 403 for the season schedule and an unrelated live scoreboard request from this host, with both minimal and browser-style headers. The NBA schedule webpage and ESPN daily scoreboard respond. This supports host-level CDN denial; it does not establish a bad schedule path. The original NBA schedule remains the game-ID authority.

## Calendar recovery

When the NBA season feed fails, fetch ESPN's current and following Eastern-calendar dates with bounded HTTPS requests and archive exact bytes, retrieval times and hashes. Match ESPN games to cached NBA IDs only by an unambiguous pair of home/away teams and a bounded time window. Validate season, regular-season type, status and timestamp. Update only verified start/status fields in a separately versioned overlay; retain the NBA original and expose both source timestamps. An unmatched near-term game is withheld from automatic forecasting and surfaced for review. No ESPN event may invent an NBA game ID. The dashboard reads the verified overlay only while it is current and bound to the saved NBA base version.

The fallback is meant to keep a future 9 AM and T-minus-30 schedule correct when one CDN host denies access. If both sources are unavailable, the runner keeps the original cache visibly marked as unverified and withholds due jobs. A fresh NBA season response restores direct operation. The owner has deliberately paused the October 24 morning run during forecaster work; verification must not restart it.

## Margin probability calibration

Model 0.6.0 has no saved forecast/outcome pairs as of this design. Earlier model runs and all-available diagnostics cannot fit its probabilities. Keep those estimates explicitly uncalibrated. Continue the existing player-points prospective interval workflow.

Collect one latest genuinely pregame margin distribution per game and exact model key from the immutable ledger before reading finals. Require a complete final reconciled to the archived official NBA page, its hash, team identities and scores, with original known-at time. Fit a bounded location/scale correction in fixed, nonoverlapping 112-game windows. Select the earliest Eastern game-day boundary with at least 64 training games across eight dates and at least 32 later validation games, then test once using proper scores. Freeze the first attempt per window; a failed window cannot pass later by adding games. Reselect the saved runs and original finals before promotion, and recompute the fit and scores from embedded sample evidence at artifact load. Record source hashes, exclusions, actual scores, training cutoff, validation metrics and artifact binding. Never backdate outcomes, rewrite saved probabilities, combine results across incompatible model keys, or claim calibration before eligible evidence exists. Apply a validated correction only to subsequent saved forecasts, retaining the raw baseline distribution and hash for comparison. The dashboard shows the active correction separately from the latest attempted window and shows whether each saved probability used that correction.

## Considered approaches

- Changing the NBA CDN URL or spoofing more headers is unsupported: two unrelated CDN paths return the same 403. Keep the canonical URL and add a cross-checked secondary source.
- Fitting on last season's results or the all-available diagnostic would give a quick number but mix models or future-known features. Use exact-model prospective results and state pending until enough arrive.
- A correction based on the raw mean and scale is reproducible and auditable. A larger black-box margin model would need far more same-model games and is deferred.

## Verification

Regression cases cover host denial, wrong season/teams, duplicate matches, time changes, postponed games, missing games, source freshness, and direct-source recovery. Calibration tests cover latest-run selection before finals, complete finals, cutoffs, model-key isolation, chronological train/validation separation, proper scores, insufficient samples and immutable old forecasts. Prepare a pinned release and use isolated replay; keep the owner-paused live runner stopped unless the owner resumes it.
