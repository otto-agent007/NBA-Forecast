# Close-teacher diagnostics implementation plan

Goal: implement PR #31's measurements in the market scoreboard and dashboard, without changing forecasts or accuracy gates.
Spec: docs/beating-the-close-2026-11-06.md, restricted by the owner's diagnostic-only instruction (no sourced-prior gate exception).

1. Add a standard-library diagnostic module with the study's fixed features, three residual targets, G/(G-1) date-clustered standard errors, disagreement buckets and trailing team-season split. Preserve missing/singular/short samples as explicit unavailable results. Test sign conventions, uncertainty, bucket boundaries, push exclusions, chronological history and reference parity.
2. Attach diagnostics to existing market_metrics (including pooled seasons) and live market reports. Keep legacy encompassing uncertainty and accuracy comparisons unchanged. Add a reproducible reference-report command with source hashes, and expose its saved result separately from live data in the dashboard.
3. Add dashboard tables with sample sizes, coefficients ± one date-clustered SE, and diagnostic interpretation. Test historical/live separation, unavailable states and mobile rendering.
4. Run unit and dashboard suites; reproduce November 6 historical numbers from committed sources, independently compare to the research script, and review the full diff. Commit and open a focused PR, with no merge or deployment.

Review focus: insufficient dates/rank-deficient designs; absent historical files; mixed providers; changing future games must not alter earlier standing views; old dashboard payload compatibility. The fixed 12-column historical design is intentionally unavailable for one-season samples rather than silently changing the specification.
