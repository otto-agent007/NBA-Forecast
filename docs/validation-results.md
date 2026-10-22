# First-version verification — October 21, 2026 (Pacific)

## Executed

- `python -m unittest discover -s tests -v`: **73 tests passed** on Python
  3.12.14. Includes source adapters, timestamp/identity validation, model
  behavior, append-only ledger, aggregate selection and subprocess CLI flow.
- `python -m compileall -q nba_forecast`: passed.
- Offline synthetic forecast → SQLite save → actuals evaluation → aggregate
  exclusion: passed. Synthetic results make no model-accuracy claim.
- All four checked-in official boxscore SHA256 hashes verified and normalized.
  Combined input: 119 observations and eight complete team boxscores.
- Official full-pool fixture: 519 unique output rows; 172 awaiting availability
  review and 347 off slate. No numeric projections manufactured from unknown
  availability. These counts describe the fixture's specific cutoff/slate.
- Real network CLI smoke: `fetch-boxscore` for `0022600001`, then `fetch-slate`,
  then `validate`: 28 observed history rows, 519 roster players, five upcoming
  games, league scope valid. Fresh raw responses/provenance archived locally.
- Independent code review found no remaining critical/important findings after
  the sparse zero-attempt uncertainty fix. Later focused regression tests
  cover current-season-only role shifts and exact floating-point minute limits.

## Review fixes

Positive minutes without any observed scoring attempts now yields an explicit
review requirement and null projections, rather than a zero-width zero-point
interval. SQLite replacement, update and delete paths cannot silently overwrite
stored predictions or evaluation revisions. Latest-run selection occurs before
looking at results, and missing latest evaluations cannot fall back to a more
favorable older run. Team-minute scaling is bounded even at floating-point
precision. Single-game shooting misses preserve the prior efficiency baseline.

## Limits of this evidence

The 73 tests establish implementation behavior, not predictive skill. No model
has been fitted, tuned or declared calibrated on the included games. Current
sources establish roster membership, not availability. Prospective numeric
forecasts require sourced statuses and a broader regular-season baseline.
Roster completeness is a source assertion: all 30 teams and unique player IDs
are checked, but the program cannot prove the official source omitted nobody.

GitHub CI separately runs the offline suite on Python 3.11, 3.12 and 3.13. Local
verification here is Python 3.12; remote check status is visible on the PR.

## Backfill and injury-check follow-up — October 21, 2026 (Pacific)

- 91 offline tests passed, including resumable imports, corrupt-cache rejection
  across repeated attempts, regular-season filtering, compressed history input,
  T−30 selection, final-stage evidence requirements and ledger stage integrity.
- A live official NBA injury-index/PDF capture succeeded. A separate replay
  exercised the final-stage CLI and SQLite save for one game: 519 roster rows,
  34 needing review and 485 with no game. No numeric predictions were invented
  from unresolved availability; this replay makes no prospective accuracy claim.
- Final-stage evidence is checked both at the model boundary and in input
  validation. The ledger rejects a stage that disagrees with its input snapshot.
- Independent review findings about repeated corrupt-cache resumes and a direct
  model-call validation bypass were fixed with regression tests.

The historical export is described in the dataset README. Retrieval timestamps
remain actual download times, so this newly acquired dataset cannot establish
what was known before historical tipoffs. No historical injury coverage or
leakage-free retrospective performance is claimed.
