# Season Operations Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task by task.

**Goal:** Make the approved daily forecast loop reproducible and schedule-aware.
**Architecture:** Small adapters feed existing validated snapshots and immutable
SQLite runs. New pure functions handle calendar, role evidence and comparisons;
a restartable tick coordinates IO.
**Tech Stack:** Python 3.11+, standard library, optional system Poppler for PDFs.
**Spec:** ../specs/2026-10-21-season-operations.md

## Constraints and review focus

No preseason, backdating, fuzzy identity joins, invented availability, automatic
merges or automatic model tuning. Test TBD/postponement, time zones/DST, PDF page
continuations, late-known history, and interrupted/duplicate tick recovery.

## Tasks

- [x] Calendar: tests/test_schedule.py first; schedule.py normalizes full season,
  saves immutable versions, computes contextual features and due jobs. Verify
  TBDs remain unschedulable, updates retained, rest uses local basketball dates.
- [x] Injury ingestion: tests/test_injury_parser.py first; injury_parser.py extracts
  dated game/team/player/status rows, preserves unmatched evidence and applies
  explicit statuses to a deep copy. Test ambiguous names and missing coverage.
- [x] Profiles: tests/test_profiles.py first; profiles.py emits all players and
  current/prior/recent opportunity evidence plus team vacancies and review tiers.
- [x] Comparisons: tests/test_benchmarks.py first; benchmarks.py reads outcome-blind
  selected live runs and computes paired baseline metrics from stored history.
- [x] Operations: tests/test_operations.py first; operations.py supplies a locked,
  checkpointed tick, per-game forecasts, final-result reconciliation and reporting.
  Add CLI commands and systemd templates with absolute-path setup instructions.
- [x] Run offline suite and live source smoke, capture schedule/profile artifacts,
  review the complete diff, update PR and verify remote CI. Do not merge.
