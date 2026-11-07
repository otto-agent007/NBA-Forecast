# Close-teacher diagnostics implementation plan

Goal: implement PR #31's measurements in the market scoreboard and dashboard, without changing forecasts or accuracy gates.
Spec: docs/beating-the-close-2026-11-06.md, restricted by the owner's diagnostic-only instruction (no sourced-prior gate exception).

1. Add a standard-library diagnostic module with the study's fixed features, three residual targets, G/(G-1) date-clustered standard errors, disagreement buckets and trailing team-season split. Preserve missing/singular/short samples as explicit unavailable results. Test sign conventions, uncertainty, bucket boundaries, push exclusions, chronological history and reference parity.
2. Attach diagnostics to existing market_metrics (including pooled seasons) and live market reports. Keep legacy encompassing uncertainty and accuracy comparisons unchanged. Add a reproducible reference-report command with source hashes, and expose its saved result separately from live data in the dashboard.
3. Add dashboard tables with sample sizes, coefficients ± one date-clustered SE, and diagnostic interpretation. Test historical/live separation, unavailable states and mobile rendering.
4. Run unit and dashboard suites; reproduce November 6 historical numbers from committed sources, independently compare to the research script, and review the full diff. Commit and open a focused PR, with no merge or deployment.

Review focus: insufficient dates/rank-deficient designs; absent historical files; mixed providers; changing future games must not alter earlier standing views; old dashboard payload compatibility. The fixed 12-column historical design is intentionally unavailable for one-season samples rather than silently changing the specification.

## Implementation record

Completed the module, integrations, dashboard and regression checks. Original
research and the structured report reproduce the November 6 measurements in CI.
The original loader assigns 14 source-labeled 2026–27 games to its 2025–26 input
bundle; exact reproduction retains that study grouping and discloses it in JSON,
the dashboard and documentation. Forecasts and raw records remain unchanged.
The default dashboard snapshot is copied from the verified CI artifact and
compared against regeneration in the regression test. Existing accuracy gates
and the 0.65 rating weight are untouched.

Review follow-up (2026-11-07): the snapshot moved from the reference dataset
directory into the generated module `nba_forecast/close_teacher_reference.py`,
because pinned releases ship only `.py` files and the dashboard's fallback path
did not exist in production. The season indicators are now derived from the
seasons in the sample instead of being fixed to 2024-25 and 2025-26, so
per-season fits are identifiable and the design survives a change of gate seasons.
