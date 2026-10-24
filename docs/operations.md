# The forecasting lab loop

## Before games

1. Collect the current official roster and unstarted regular-season slate.
   Collect complete final boxscores from the current and previous regular
   seasons; preserve raw bytes, source URL and actual retrieval timestamp.
2. Verify availability. Do not infer healthy/available from roster membership or
   absence from an incomplete injury list. Unknown and questionable statuses
   deliberately abstain. Status evidence expires after 24h; rosters after 48h.
3. Run validation and the deterministic baseline. Every player is represented;
   inspect the numeric, withheld, out and off-slate coverage counts.
4. Route routine source/status gaps to inexpensive extraction or a human data
   check. Route trades, changed rotations, coaching decisions, lineup roles and
   matchup hypotheses to a capable reasoning reviewer. No paid call occurs in
   this repository. The queue is a review handoff, not an autonomous LLM agent.
5. Record supported game-specific adjustments, including a reviewer, reason,
   evidence URL and observation time. Maximum minutes delta ±15 and opportunity
   multiplier 0.5–1.5. These bounds prevent accidents, not scientific validation.
6. Use the official report available at each game’s T−30 checkpoint for the final
   run (`--stage final --injury-check FILE`). Earlier runs remain preliminary.
   Capture later reports separately and review late changes. See
   [checkpoint commands](backfill-and-injuries.md). Save a live run before all included tipoffs. Keep the complete inputs and
   output. Later information produces a new run, never an edit to a prior run.

## A useful reasoning packet

Include player/game/team IDs, current status and source, prior/current minutes,
attempt rates and shooting denominators, recent lineup/coach quotes, opponent
proxy limitations, and the baseline audit. Ask: *Did opportunity change, or did
shots simply miss? Is there evidence for a minutes restriction? Who actually
inherits possessions, and does the rotation still fit 240 minutes?*

Expected output is evidence and one bounded proposal, or “insufficient evidence.”
Do not ask the expensive model to calculate rates, copy boxscores, or forecast
hundreds of players independently. Do not promote an unverified extraction to
availability or a numeric adjustment. Game-specific adjustments expire when
that game ends; they are not permanent player labels.

## After games

1. Ingest only final regular-season boxscores; missing rows remain pending.
   Explicit DNPs are zero outcomes. An out player who played counts as a miss.
2. Evaluate each run. Corrections append an evaluation revision and preserve
   prior results. Report errors for numeric forecasts and explicit exclusions
   for unresolved players, so abstention cannot hide in a low error number.
3. Run the aggregate report. The deterministic policy selects the latest live
   saved pregame row per season/model version/game/player **before** inspecting
   results. It does not substitute a favorable older or evaluated rerun. Demo,
   replay and subset runs are excluded. Model versions are evaluated separately.
4. Inspect minutes error, point bias, residual rate error, uncertainty coverage,
   and sample size. Track status/role changes alongside errors. The exact
   decomposition is forecast-rate × (forecast-minutes − actual-minutes), plus
   actual-minutes × forecast-rate − actual-points. The second component combines
   opportunities and shooting; it cannot identify those mechanisms separately.
5. Change one hypothesis at a time, add a regression test, and bump the model
   version whenever formulas/constants change. Judge new forecasts prospectively;
   do not tune and claim success on the same games. Preserve a simple baseline
  for future challenger comparisons. Point-model parameter selection remains offline.
  The versioned upgrade can refresh empirical intervals from matched prospective
  residuals after its minimum sample requirements. See [model upgrade](model-upgrade.md).

## Remaining research

- Detailed shot-location and defender matchup effects.
- Empirical minutes distributions and prospective injury-allocation evaluation.
- Rookie priors based on separately validated regular-season evidence.
- Formal proper scoring rules and rolling-origin challenger comparisons once
  sufficient point-in-time data are available.
- Joint outcome distributions, overtime and blowout scenarios.

The persistent runner and dashboard use the source validation and immutable
forecast ledger described in [season operations](season-operations.md).
