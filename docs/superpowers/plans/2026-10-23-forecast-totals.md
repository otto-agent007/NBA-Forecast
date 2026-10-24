# Forecast point totals implementation plan

> Implementation and final verification are performed inline in this session.

**Goal:** Team and combined regulation point projections in both forecast stages.
**Architecture:** Shared aggregation of saved player means, stored for new runs
and derived read-only for older runs. React displays coverage with each total.
**Tech stack:** Python standard library, React, existing Playwright suite.
**Spec:** ../specs/2026-10-23-forecast-totals-design.md

## Constraints and review focus

Preserve immutable forecasts, existing player numbers and validation gates.
Never infer zero for unknown players, mix games or stages, sum rounded values,
change totals with player filters or present partial coverage as complete.

## Tasks

- [x] Add failing aggregation, saved-total integrity and dashboard compatibility tests.
- [x] Implement `nba_forecast/totals.py`, integrate model/store/dashboard.
- [x] Add failing browser coverage for team/game totals, stage selection and filters.
- [x] Add responsive totals component with partial/unavailable coverage.
- [x] Run Python/JS/browser suites and production build; inspect live dashboard.
- [x] Review changes and save locally.

## Verification

224 Python tests passed; JS helper suite passed; 26 desktop/mobile browser tests
passed. Production build and whitespace checks passed. Live checks at
http://127.0.0.1:8765/ verified original Detroit/Miami final totals (121.9/120.5,
combined 242.4), partial coverage, both forecast stages, no runtime errors or
horizontal overflow at 1440 and 390 pixels. Automatic operations saved six
new 0.4.1 runs with persisted game totals before 5 PM Pacific.

Saved totals use canonical JSON comparison against recomputed aggregates, including
JSON types, to reject forged numbers, counts or coverage metadata. The corresponding
regression failed before the correction and passes in the full Python suite.

Implementation was self-reviewed against the spec and live results. The separate
reviewer did not complete independent file inspection before being stopped; no
independent review approval is claimed. No remote publication was performed.
