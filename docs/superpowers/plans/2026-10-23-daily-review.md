# Daily Forecast Review Implementation Plan

> Execute with the existing native workflow; bounded source-identity investigation and independent error analysis run in parallel.

**Goal:** Review every completed Oct 23 game and keep the same review automatic.
**Architecture:** Source-backed final cache plus outcome-blind frozen stage selection feeds a deterministic date report, exposed in Performance.
**Tech Stack:** Existing Python standard library, SQLite, React and Playwright.
**Spec:** ../specs/2026-10-23-daily-review-design.md

## Constraints and review focus

Immutable forecasts; real source timestamps; no invented NBA IDs; no parameter
tuning from one day; report mixed versions and missing coverage. Tests cover
latest abstentions, replay/post-tipoff exclusion, corrected/future actuals,
same-pair stage comparison, DNP/status groups, partial totals, midnight dates,
overtime, missing play-by-play, and repeat-run stability.

## Tasks

- [x] Resolve MEM/GSW final identity failure from actual source evidence; add regression.
- [x] Add `nba_forecast/daily_review.py` with `review_day(directory,date,as_of)` and `build_daily_reviews(directory,as_of=None)`; write failing selection/metrics/cutoff tests first.
- [x] Wire runner, CLI and dashboard saved reports; add date-specific Performance panel and meaningful browser checks.
- [x] Generate all 12 game reviews; reconcile metrics with independent analysis and identify specific improvement priorities.
- [x] Verify full tests/build, real dashboard, pinned runner tick and automatic review; save local code and review evidence.


## Verified outcome

All 12 Oct 23 final boxes and 24 original forecast stages reviewed. Independent
selection/source audit matched the daily module exactly:292 projected outcomes,
points MAE 4.249585, minutes MAE 5.881693, interval coverage 187/292. Every final
belongs to an earlier model version; none used 0.5. Missing 13 projected actual
rows remain unscored, as do 65 absent out rows. All partial saved-total labels
are preserved. Historical aggregate sums are explicitly derived.

Source import fixed with an exact, unique historical NBA identity fallback for
explicit ESPN DNP entries only; no fuzzy IDs or inferred played identities.
MEM/GSW now passes 31-row reconciliation. Walter Clayton Jr.'s cross-team same-day
boxscore appearance is separately flagged for source review.

Root-cause investigation found stale starter protection can reserve over 240
minutes for 9 players and force supported bench roles tozero. Current 0.5 reproduces
this in a separately labeled posthoc replay. This remains an open model change
requiring rotation tests and multi-game evaluation; report implementation does
not silently tune point parameters. No reconciled play-by-play was available.

Checks:373 Python tests,34 desktop/mobile browser checks, targeted team-table
checks, JS helper suite and production build passed. Live desktop/mobile checks
show 12 game rows,24 team rows, expandable player evidence and game navigation,
without page/console errors or horizontal page overflow. Browser plugin absent;
installed Playwright used. Native runner tick 05:56:55–05:59:06 UTC completed with
zero errors and complete Oct 23 daily review. Source-correction regression covers
older saved dates and rescheduled-date invalidation. Unchanged reviews preserve
their timestamp; revisions retain prior outputs.

Pinned release `454cca83b95058c8a7d741356ac087af3ba49e1ef77e64d5ac34986d12087b8e`.
Previous service saved in production/verified-units/nba-forecast-before-daily-review.service.
Evidence and findings saved in datasets/daily-review-2026-10-23; full operational
receipt at production/daily-review-verification.json. No remote publication.
