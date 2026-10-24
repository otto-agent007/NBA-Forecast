# Schedule and margin calibration implementation

- [x] Reproduce CDN denial and identify a working near-term schedule source without losing NBA game identities.
- [x] Add source-bound ESPN calendar crosscheck and near-term fail-closed behavior; test normal, changed, missing and malformed cases.
- [x] Connect the verified calendar to runner and dashboard, preserving the full original NBA archive.
- [x] Add exact-model prospective margin sample collection and chronological correction/validation with proper scores.
- [x] Prepare and apply only validated future calibration artifacts; show pending versus validated evidence in the dashboard.
- [x] Run focused and full checks, review source/temporal boundaries and prepare a local pinned candidate; retain the owner's deliberate runner pause pending an explicit resume.

October 24 observations: the NBA CDN returned 403 for both the season schedule and a separate live scoreboard path under minimal and browser-like headers. NBA.com schedule HTML responded 200 but did not fully stream within a 15-second bounded read. The ESPN daily scoreboard responded 200. After the ambiguity fixes, an isolated crosscheck matched all 15 October 24 and 25 games to cached NBA IDs, teams and tipoff times with zero blocked or unmatched events. No automatic NBA game ID was inferred from an ESPN ID.

The owner clarified that the 9 AM run had been deliberately paused. A mistakenly restarted timer was stopped and disabled immediately, the interrupted tick saved zero forecasts, and the ledger remained at 300 runs. Subsequent checks are isolated from the live runner. Model 0.6.0 currently has zero eligible saved forecast/final pairs, so empirical margin calibration cannot be claimed now.

The final matcher limits automatic same-pair tipoff changes to 36 hours and requires completed/live ESPN events to match a cached NBA game already due within one hour. Longer or ambiguous moves are surfaced without assigning an NBA ID. The selected overlay flows into postgame player collection and daily review. The dashboard labels the independent cross-check and its timestamp separately from the original NBA capture, including partial, expired and unverified states.

The calibration gate now uses first-attempt-frozen, nonoverlapping 112-game windows. An Eastern game-day split must leave at least 64 training games on eight dates and 32 later validation games. Archived NBA HTML, hash, game identity, team scores and final-row provenance must agree. Before promotion, saved runs and original finals are reselected; at load, the fit and proper scores are replayed from embedded evidence. The active correction is displayed separately from the latest assessment.

Final local checks: 558 Python tests, three Node test files, dashboard build, eight focused desktop/mobile browser checks, clean diff, and an independent review with no remaining Important or Critical findings. The isolated live ESPN check matched 15 October 24/25 games with zero blocked, unmatched, invalid or changed entries. A new code-bound model candidate and pinned package are saved under `data/review-candidates/schedule-margin-2026-10-24/`; the candidate loads successfully in isolation. The production active pointer remains `4ba19a8b9dbf5bfacb795a63c515c8065121c545a4f93c7ecf09201506348194`, the ledger has 300 runs, and the timer/service are inactive; the timer remains disabled. No production forecast tick or model activation was performed during final verification.
