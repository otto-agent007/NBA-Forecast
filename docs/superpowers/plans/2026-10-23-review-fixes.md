# Repository review fixes

> Execute inline with the executing-plans and test-driven-development skills.

**Goal:** Fix the five findings in the accepted repository review without changing forecasting formulas or existing immutable ledger records.

**Architecture:** Stream ledger metadata in bounded batches and fetch history only for recovery or a selected benchmark run. Merge observations by evidence time. Preserve newer official availability when manual overrides have been superseded. Make dashboard refresh invalidate postgame data and filter reviews by saved queue membership.

**Tech stack:** Python 3.11+ standard library, SQLite, React, Node tests and Playwright browser regressions.

**Spec:** The five findings and remedies in the repository review accepted by the owner's “we need to fix everything” instruction.

## Constraints and review focus

- Preserve existing database schema, stored bytes, hashes, outcome-blind forecast selection and historical evaluation revisions.
- Retain the current forecasting formulas and model version.
- Bound reader memory independently of the number of saved history copies; do not hold a SQLite reader open while a tick writes evaluations.
- Recovery must still reconstruct missing artifacts from the original saved history.
- Newer evidence wins; equal evidence times retain deterministic cache precedence, consistent with player synchronization.
- Superseded overrides must remain auditable. Invalid identities, future timestamps and other malformed reviews must still fail validation.
- Refresh must reload unchanged game IDs; changing games must cancel prior requests.
- The review queue must include projected role-review players without flagging every projected player.

## Task 1: Bounded ledger readers

**Files:** `nba_forecast/operations.py`, `nba_forecast/store.py`, `nba_forecast/benchmarks.py`, `nba_forecast/dashboard.py`, `tests/test_ledger_readers.py`.

- [x] Add memory-budget regressions with several independently selected runs carrying large valid histories. Verify operations and aggregate readers omit unused histories, benchmarks retain only one history, and writes work between iterator yields.
- [x] Run `python3 -m unittest tests.test_ledger_readers -v`; expect memory regressions to fail before implementation.
- [x] Implement bounded metadata iteration, on-demand recovery history, streaming report/dashboard reads and per-run benchmark processing.
- [x] Run the reader tests and the Python suite; expect all tests to pass.

## Task 2: Evidence precedence and superseded reviews

**Files:** `nba_forecast/operations.py`, `tests/test_operations.py`, `docs/season-operations.md`.

- [x] Add tick regressions for a newer imported correction, a newer cached observation, and official availability superseding a review while other reviewed adjustments survive.
- [x] Run `python3 -m unittest tests.test_operations -v`; expect the reported cases to fail before implementation.
- [x] Compare `known_at` while merging history. Add an explicit tick policy for skipping superseded availability, recording a warning and preserving the submitted review and other valid adjustments; keep direct review validation strict.
- [x] Run operations tests and the Python suite; expect all tests to pass.

## Task 3: Dashboard refresh and review membership

**Files:** `dashboard/src/App.jsx`, `dashboard/src/Postgame.jsx`, `dashboard/src/data.js`, `dashboard/tests/data.test.js`, browser test/config, `dashboard/package.json`, lockfile and CI.

- [x] Add a unit regression for projected players in the saved review queue, and browser regressions for queue navigation and refreshing a newly available recap for the same game.
- [x] Run unit/browser regressions; expect the reported interactions to fail before implementation.
- [x] Propagate queue membership into display rows and use it in the review filter. Pass an explicit successful-refresh revision to postgame fetching.
- [x] Add the browser regressions to CI and document how to run them.
- [x] Run frontend unit tests, production build and desktop/mobile browser regressions; expect all to pass.

## Task 4: Completion review

- [x] Review the complete diff, including ledger compatibility, evidence chronology, abort handling and browser fixtures.
- [x] Run full Python and frontend checks, compilation and whitespace validation; repeat targeted original reproductions with the updated code.
- [x] Record exact results and any remaining operational limitations. Leave the work local on `fix/review-findings`.

## Execution evidence

- Branch: `fix/review-findings`, base `72e8547`. Existing clone was clean before changes.
- Reader tests: three memory failures observed before implementation; all reader and existing ledger/recovery tests pass afterward. Reader histories remain immutable and on-demand.
- Evidence tests: imported-correction and superseded-review tick failures observed before implementation; 160 Python tests now pass, including invalid/future evidence and input immutability checks.
- UI unit regression: projected role-review player was absent before implementation. Desktop/mobile browser tests reproduced both reported UI failures before changes; game-switch cancellation already passed.
- Local execution uses the existing clone on a dedicated fix branch. Implementation choices and the five remedies were authorized by the accepted review; no additional design approval was needed.
- Full histories are still stored per run for ledger compatibility. This change bounds reader memory; it does not migrate or deduplicate immutable historical storage.

## Final validation

- Python: `python3 -m unittest discover -s tests` — 160 tests pass.
- JavaScript: `node --test --test-isolation=none dashboard/tests/data.test.js` — 8 tests pass; `npm test --prefix dashboard` passes too.
- Dashboard: production build passes; `npm ci --offline --ignore-scripts` verifies the lockfile can reproduce the dependency tree.
- Browser: 6 desktop/mobile Playwright tests pass, covering queue membership, new/corrected postgame refresh and cancellation. Both reported interactions failed before implementation. Chromium was supplied through `PLAYWRIGHT_CHROMIUM_EXECUTABLE`; Browser plugin was unavailable.
- Original production-build browser reproduction passes with no JavaScript errors and no mobile page overflow. Updated captures are under `/tmp/nba-fixes-*.png`.
- Original 43,650-row dataset reproduction: three runs retain their existing 61.7 MiB on disk; routine reader peak is 0.05 MiB versus 61.62 MiB before. The newer imported 30-minute observation remains 30 minutes, and a superseded review produces a successful run with the official status retained.
- Six large-history independent-run test: operations peak 0.098 MiB, aggregate peak 0.091 MiB, benchmark peak 4.103 MiB.
- Independent final review: no remaining findings. Strengthened the evaluation-between-yields regression to append a real correction, assert revision 2 and verify two persisted revisions using a separate fixture copy.
- Python compilation and `git diff --check` pass. No remote publication, hosted CI or persistent service execution was performed.
