# Explicit zero-injury report recovery

Goal: recover Milwaukee's available injury information and prevent a verified zero-injury team from being dropped from future forecasts.

Evidence: today's official PDF contains CHI@MIL and Chicago statuses but no Milwaukee team block. ESPN's exact CHI@MIL event (401909848) has an explicit Milwaukee team entry with an empty injury list; SI's exact dated matchup explicitly says no current injuries. The collector currently asks ESPN only for roster/schedule and treats every absent PDF team block as unavailable. An empty team list in a verified game response is materially different from missing injury fields or a missing team.

Chosen fix: retain the conservative official PDF parser; add a narrow fallback using archived ESPN game-summary evidence. Validate event/season/team/home-away/tipoff identity and an explicit team injuries list. Only an explicit empty list can fill a missing official team block; never override official NOT YET SUBMITTED, conflicting/unparsed rows, or existing statuses. Numeric projections remain conditional on participation, with this basis preserved. Do not infer availability from roster Active status or missing/empty global feeds.

- [x] Add failing tests for explicit empty vs absent team/list; wrong game/time/season; malformed/conflicting official coverage; stale/future evidence; preserving confirmed statuses and conditional forecast labeling.
- [x] Extend bounded archived ESPN references with exact event IDs and game summaries; verify raw response hash/identity and publish game-specific no-injury capture.
- [x] Apply fallback before model execution; revalidate the source and roster membership in preflight. Preserve original captures and never weaken failures unrelated to this specific missing-team case.
- [x] Publish a distinct latest injury update for the already-started Bucks game; preserve its original saved forecast and retrieval times. Never create a backdated forecast.
- [x] Verify unit/integration/browser regressions, inspect real Bucks recovery, independent review, build/restart local dashboard, commit locally.

Constraints: no paid credentials, no remote publication, exactly five projected starters unchanged. Future 9 AM/T-30 capture behavior is preserved. Current game already started, so newly recovered information is displayed separately from original forecast inputs.


Completed verification: 204 Python tests, 12 JavaScript cases, 14 Playwright desktop/mobile checks, production build and whitespace check. Real live dashboard inspected at 1440x1000 and 390x844 with the recovered Milwaukee report, 18 roster rows, five projected starters, original unsupported forecast values preserved, and no application exceptions/framework overlay. Browser plugin unavailable; used installed Playwright. External NBA headshots can fail and use existing fallback portraits.

Real recovery: exact CHI-MIL game 0022600099 / ESPN401909848, actual latest capture 2026-10-23T20:35:51.554162Z, SHA256 a44df441bac7ff0af84af30049749cbf9274e76606889931411ad89e1132d9c0. Official archived PDF re-parsed and both team rosters independently matched. Recovery was after tipoff, so zero players were retroactively modified and no new live forecast was created for that game. Dashboard displays the recovery separately from earlier blocked attempts.

Independent review found publication of rejected source reports and failure to supersede old zero claims. Both reproduced in failing tests and fixed; newer unavailable/nonempty refreshes clear the current zero claim while retaining archives. Official PDF header/publication matching also gates supplementation. Human reviews, including explicit unknowns, are preserved. No provider publication time is invented. The dashboard and daily/final timer are active. No remote publication.
