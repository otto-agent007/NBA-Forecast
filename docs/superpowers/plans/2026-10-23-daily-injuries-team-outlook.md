# Daily injury capture and team outlook

**Goal:** Automatically capture the official report and preliminary forecasts at 9:00 AM Pacific, capture each game's final checkpoint at T−30, and show player outlooks by team and recent starter/bench role.

**Owner decisions:** Daily capture at 9:00 AM Pacific; automatic final capture 30 minutes before each game's tipoff; a preliminary forecast before the final; starters first within separate teams rather than alphabetical first names.

**Approach:** Extend the existing one-minute tick and persistent user-systemd timer. Archive a daily source independently of the slate; retain per-game checks and both forecast stages. Display recent observed starters, bench and unverified roles, ordered by minutes with surname ties. The recent-role classification does not assert confirmed starting lineups. Preserve model formulas, immutable inputs and existing prospective selection policy.

## Implementation

- [x] Daily capture: add a 9:00 AM Pacific due policy, latest official PDF capture, immutable daily/checkpoint evidence, restart-safe retries and tests for midnight/DST/no-game/failure cases. Preliminary runs already refresh every 15 minutes outside T−30; retain this behavior and make daily capture independently visible.
- [x] Forecast history: expose latest preliminary and final runs separately in the read-only dashboard, with stage-specific selection, reschedule protection and tests.
- [x] Rotation evidence: derive descriptive roles from the last five captured appearances on the current team, without using future evidence or assuming roles after a trade. Materialize this evidence for all roster players and withheld/out forecasts.
- [x] Team outlook: group by team, then starters/bench/unverified; order by minutes, with surname ties; retain filters, projections and details. Add preliminary/final controls and daily/final report status. Verify unit and browser regressions.
- [x] Activation: build and test, independently review, install the generated user services, run a live tick, enable the one-minute timer, and open the live dashboard. Confirm next trigger and actual saved evidence. Preserve the existing inspection snapshot separately.

## Validation and boundaries

Use red/green tests for the new behaviors, then the full Python/JavaScript/browser suites and production build. Record final timing as actual retrieval time; missing or stale reports remain visible failures and retry before tipoff. A one-minute timer targets T−30 within its polling resolution and depends on this host and NBA source availability. Final means our checkpoint, not the last update NBA will publish. Future/unknown availability and unsupported players continue to abstain. No historical forecasts will be backdated.

## Execution and validation

- Independent review identified a selected-run provenance issue in Sidebar; its new browser regression failed first, then passed after separating selected forecast evidence from current checkpoint state.
- Real systemd validation exposed the existing installer's quoted WorkingDirectory bug. A regression with a repository path containing spaces failed first, then passed after generating a literal absolute path. The actual generated units passed `systemd-analyze --user verify`.
- A tick shares source index/PDF captures across game jobs, preserving actual retrieval timestamps. This avoids repeated identical downloads delaying T−30 jobs.
- 173 Python tests, nine JavaScript unit cases, ten desktop/mobile browser cases, Python compilation and the production build pass.
- User-systemd timer and dashboard services were installed and enabled on this host; user lingering was already enabled. The timer polls every minute and daily capture uses 09:00 America/Los_Angeles with DST handling.
- Today's first daily report was captured at 2026-10-23T19:08:24Z (12:08 PM Pacific), since this installation occurred after 9 AM. It preserves the actual capture time and source PDF published at 19:00Z; tomorrow's daily target is 9 AM Pacific.
- The first operational tick captured 12 game injury checks and saved 12 prospective preliminary runs with numeric supported projections. The API exposes 519 player profiles and both stage slots. The operational dashboard is open at http://127.0.0.1:8765/; the old inspection snapshot remains under data/inspection.
- Live limitation: schedule, roster, boxscore and shot sources returned HTTP403 from this host. Injury-index/PDF downloads succeeded. Saved calendar and roster fallback retain original timestamps, roster expiry remains 48 hours, and UI freshness warnings expose this. The first tick reported partial status for failed historical final downloads. Seeding the already-validated final cache avoids these redundant downloads; the active timer continues retrying source refreshes. Do not claim all source refreshes succeeded or that a future final checkpoint has already executed.

- Seeded the 14 complete, already-validated current-season finals from the bundled dataset into the operational final cache, retaining exact original observations. This prevents redundant failing downloads for evidence already available locally. Future unobserved outcomes still require source retrieval.
- Final real-browser smoke: actual API displays Chicago Bulls and Milwaukee Bucks in separate team sections, each with starter/bench/unverified groups and 15 numeric supported projections for the selected game. No JavaScript errors or mobile page overflow.
