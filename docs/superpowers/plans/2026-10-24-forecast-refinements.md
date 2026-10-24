# Forecast Refinements Implementation Plan

> **For agentic workers:** Use focused independent module tasks and a shared integration pass. Track completed checks below; preserve all existing forecasts.

**Goal:** Add sourced injury-aware minutes, location-adjusted shooting, and auditable winning-margin probabilities to future forecasts and the dashboard.

**Architecture:** Three independent evidence/math modules feed the existing model snapshot and fail-closed preflight. The controller owns integration, model version/artifact changes, dashboard delivery and local runner activation.

**Tech Stack:** Python 3.11+ standard library, SQLite, React/Vite, unittest, Node tests and Playwright.

**Spec:** `docs/superpowers/specs/2026-10-24-forecast-refinements-design.md`

## Global Constraints

- No rewrite/backdating of saved forecasts or source retrieval timestamps.
- Model version `regular-season-baseline-0.6.0`; preserve 0.5.0 replay and reset mismatched calibration.
- Exact identity, cutoff, source-hash and finite-number validation.
- 240 regulation minutes, five projected starters, and existing daily/final checkpoint times.
- Margin probabilities are labeled estimates; partial inputs withhold them. Full-game settlement includes overtime; threshold comparison is strict greater-than.
- No market trades, price/edge recommendations, joint ticket probabilities or remote publication.

## Review Focus

- An old injury article, a pregame ailment or a different player's injury must not erase normal-role evidence.
- Injury exclusion must not erase actual attempts or per-minute scoring evidence, and cannot manufacture an unsupported new-team role.
- Shot-pool exclusion, incomplete locations and recency alignment must avoid double-counting hot shooting.
- Margin probabilities must invert correctly for the away team, handle exact equality and never silently use regulation outcomes for a full-game question.
- A new model cannot inherit empirical calibration from 0.5.0; old dashboards cannot display invented probability forecasts.

## Tasks and progress

- [x] Task 1: `role_context.py` plus regression tests: prepare/verify source-backed injury-exit context; supply per-player excluded role game IDs. Integrate into opportunities/profiles/rotation evidence without changing actuals.
- [x] Task 2: `shot_quality.py` plus regression tests: prepare/verify per-game locations and pooled references; adjust shooting probabilities with bounded replacement and exact fallback. Integrate before point/box-score calculation.
- [x] Task 3: `margins.py` plus regression tests: source-bound distribution inputs, discrete final-score margin estimates, strict YES/NO line calculations, uncertainty/coverage labels, and append-only outcome scoring.
- [x] Task 4: model/artifact/preflight/store integration: preserve previous versions, add new inputs and deterministic validation, verify accounting and source replay.
- [x] Task 5: dashboard line explorer and player context explanations; finite input, mobile, stage-selection and missing/legacy coverage tests.
- [x] Task 6: full tests/build, independent review, real-data diagnostic run, new artifact and pinned local release, activation receipt and final verification. Retain 9 AM scheduled restart.

Module work is isolated by file ownership; no concurrent edits to integration files. Use failing behavior tests before implementation. The integration owner records module interfaces and any necessary design refinements here.

## Review and implementation record

- Injury context uses immutable official recap retrievals, parser `nba-recap-injury-exit-2`, and excludes only confirmed same-game exits. Archived 28-game replay identifies Flagg and Missi; unsupported wording remains unconfirmed.
- Location policy is `court-location-1.0.0`, leaving player priors intact with a maximum two-percentage-point adjustment. Original descriptive shot-profile execution is unchanged.
- Margin policy uses paired historical final-score outcome dispersion, explicitly uncalibrated, minimum 100/maximum 1,230 source games; no zero-margin final outcomes; support endpoints retain tail mass and cannot be queried as exact points.
- Final real-source diagnostic checked all 8 upcoming games against the activated model key. All source and numeric contracts passed; 4 complete games produced distributions, and 4 with unresolved player coverage withheld them. It assumes everyone available and is not a live forecast.
- Independent whole-change review identified three Important issues. Each was reproduced before correction: injury rows in minute-cap quantiles, partial final caches entering margin feedback, and ambiguous injury subject/time/return language. Regression tests now cover each. No Critical issue or deferred review finding remains.
- Final Python full suite passed 523 tests; all dashboard unit files and the production build passed. Desktop/mobile browser suite passed 44 tests, with all 4 margin tests rerun after the final percentage-formatting change.
- Browser plugin is unavailable; local Playwright uses the already installed Chromium executable. No new browser dependency was installed.
- Activated model 0.6.0 and pinned both local Python services to package `2fe69663eaf168999af3b4dc6ffe2715ff5d5e1c5a59cc4ed3882c27ee62832b`. The verification tick completed without errors and created zero forecasts; all 300 saved runs remain unchanged. The 9 AM Pacific resume timer is preserved. NBA's schedule source still returns HTTP 403, so the tick used the existing calendar cache with its original observation time.
- Review, test logs, diagnostic artifacts, live activation receipt and source hashes are retained in [the release evidence](../../../datasets/forecast-refinements-2026-10-24/README.md). Work remains on the existing local branch.
