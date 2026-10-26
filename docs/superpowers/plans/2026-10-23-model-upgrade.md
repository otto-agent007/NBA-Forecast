# Forecast model upgrade implementation plan

**Goal:** Correct pace, injury-allocation, rest/travel and interval limitations.
**Spec:** ../specs/2026-10-23-model-upgrade-design.md
**Architecture:** Audited additions to the existing player model, validated as a
candidate before a new pinned production release. Python standard library and
existing React/Playwright tooling; no paid model calls or dependency additions.

- [x] Acquire and validate missing team possessions evidence.
- [x] Implement and test pace/defense environment, excluding future-known data.
- [x] Implement and test constrained out-player minutes/attempt redistribution.
- [x] Fit/test numerical context effects with chronological separation.
- [x] Implement/test model-bound empirical interval calibration and sparse fallback.
- [x] Integrate candidate forecasting, profiles, provenance and artifact validation.
- [x] Run chronological train/calibration/holdout comparisons with explicit scope.
- [x] Verify real dashboard, full tests and current automatic checkpoint health.
- [x] Review measured results and activate verified improvements in a pinned release.
- [x] Save code, source artifacts, reports and the recovery path locally.

## Implementation evidence

Activated at 2026-10-24T02:15:27.365166+00:00 with real timestamps.
Artifact e25bec66bd5a9258eccb5a5de0e47afa0b84b3da1d698c25b8672910eee624b2.
Pinned package fa12bb0f379caeddc725a1dc0efd7e9bf9402d8f24f9fb48ad1433036858a0e0.
Baseline recovery release 95f69ec510ee959cb1a551a6077d9d01700d7ce836658eb1b335bafd6223deaf.
Tonight's original finals remain immutable under 0.4.2. The new model applies
only to upcoming checkpoints; historical replays were not inserted as live runs.

Team sources: 1,217 reconciled of 1,244 games; all 27 exclusions documented.
Full calendar retains excluded statistical games for rest calculations.
Current artifact/report/normalized data are in datasets/model-upgrade-2026-10-23.
Its manifest binds the 61 MB same-disk original-request archive by SHA256.
Earlier pilot folders remain development evidence, never activation candidates.

Model key 383c0fe35b920832c7cea742450c8c70ba4a44f0fdcf688ec2770f05ae3ec8be.
Parameters: pace .25, defense 1, context .5, rotation and context features enabled.
Chronological evaluation: player MAE 5.085528 -> 5.083717 (5,664 outcomes),
team MAE 10.359519 -> 10.143472 (532 team outcomes); nominal 80% intervals
covered 77.8661% of 5,661 eligible outcomes. Historical reconstruction, including
iterative development inspection, has explicit scope limits. Game-history
priors and reconstructed boxscore rosters do not validate live three-season
aggregate priors or sourced injury redistribution. Prospective assessment remains.

Independent review found an extra-minute attempt-budget edge case. Regression
and fix now constrain both minute grants and additional scoring rates, preserving
manual restrictions. Audits retain the initial proposal and final constrained
allocation. No additional cutoff/prospective-update blockers were found.

Verification: 354 Python tests, 32 desktop/mobile browser tests, JS helper tests
and production build passed. Real dashboard desktop and mobile checks confirmed
0.5.0 validation and zero prospective games, with no page errors or overflow.
Three recent frozen final snapshots passed candidate/source/forecast validation
as posthoc replays; six teams retained 240 projected minutes. Partial totals
remain partial for unresolved availability. Receipts are in production/
model-candidate-verification.json and model-upgrade-release-prepared.json.

Seven earlier final boxscores and contemporary environment records imported.
NYK/BOS final source correction reports team FGA 80 vs player sum 81. Fresh ESPN
still disagrees, NBA CDN is 403 and Stats timed out. This remains explicitly
pending with source hashes/counts; one uncached retry is bounded. No older source
is silently substituted and no inconsistent final is saved. Pending details are
shown on the Performance model panel, separately from operational failure.

First upgraded automatic tick completed at 2026-10-24T02:18:03.142730+00:00:
status complete, zero operational errors, 97 saved runs evaluated, zero new
forecasts after tipoff, prospective calibration correctly pending at 0/20 games.
Source-conflicted final remains explicitly pending; the live Performance panel
shows this separately. Automatic timer and dashboard services remain active.
Versioned receipts: datasets/model-upgrade-2026-10-23/model-upgrade-verification.json
and model-candidate-verification.json. No remote publication was performed.
