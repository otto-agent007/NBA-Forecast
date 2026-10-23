# Player intelligence expansion

Owner-approved scope: all five next steps (availability, role/minutes, multistat,
explainable player pages, continuous operations/feedback), plus shot charts and
NBA-site-style last games and season averages. Extend the existing dashboard PR.

Architecture: retain standard-library deterministic pipeline and immutable ledger.
Add optional boxscore stats without breaking older imports. Forecast rate-shrunk
REB/AST/STL/BLK, modeled 3PM and existing TOV using the same pregame evidence cutoff;
missing categories stay null. Include sourced minutes caps and role comparisons.
No automatic coaching decisions or inferred beneficiary redistribution.

Availability: exact PDF identity joins; missing/stale/incomplete submissions fail
closed. Only a fully parsed, complete dated team report may support a not-listed
conditional availability assumption. Preserve distinction from explicitly Available.
Unknown/Questionable remain withheld. Later reports override older pregame sources.
Refresh at bounded time slots and deduplicate each job; never rewrite past runs.

Profiles: deterministic aggregates of final regular-season game logs, excluding DNP
from per-game means, with per-field coverage and source links. Current and prior
seasons separate. Shot chart uses actual NBA play-by-play attempts only, reconciled
against final boxscores. Missing coordinates do not become invented dots. Publish
coverage and source times. Profile observations are not backdated forecast evidence.

Operations: reusable profile sync/backfill commands, restart-safe bounded runner,
health/coverage report, evaluation by stat and minutes/rate/availability errors.
Persistent host instructions and service installer; hosting availability inspected
before deployment. Do not claim 24/7 without an actual persistent service.

UI: retain approved sharp navy/blue/logo design. Expand player detail to a full
profile dialog with overview, game log, shot chart and prediction history. Expanded
projection board shows modeled categories; fantasy total deferred until scoring
rule selected. No fake live, fake calibrated interval or synthetic demo stats.

Implementation order: independent stat-model and profile/shot adapters; availability
and operation integration; profile API/UI; tests, real-source sync, browser QA;
independent review; extend unmerged PR. Routine work remains deterministic. Expert
reasoning queue retains auditable game-specific evidence/reviewer adjustments.

Acceptance: unit cases for leakage, stale/missing sources, report coverage, DNP
means, missing stats, duplicate shots/reconciliation; old ledger compatibility;
full Python/frontend tests/build; real current-season source capture; desktop/mobile
profile and chart interactions; document source and hosting limitations honestly.
