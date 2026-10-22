# Season operations

Extend the existing deterministic Python pipeline with a versioned official season
calendar, conservative injury extraction, full-pool role evidence, restartable
scheduled execution, and prospective paired baseline comparisons. The owner
approved this sequence in chat. Keep PR #1 reviewable and unmerged.

Schedule snapshots preserve all regular-season entries including TBDs, source
bytes, retrieval time and content hash. UTC controls execution; venue-local time
uses explicit IANA zones. Never infer a time from a TBD placeholder. Calendar
context records rest days, back-to-backs, three-in-four, road stretches and venue
changes; it does not apply untested scoring penalties. Unknown venue zones remain
null. Coverage counts reveal missing/unassigned games rather than promising 1230.

Injury extraction uses Poppler layout text, date/matchup/team context and exact
normalized roster-name matching. No fuzzy identity joins. Only explicit Available
and Out become resolved states; probable/doubtful/questionable stay uncertain.
Omission is unknown. Unknown rows, missing teams and NOT YET SUBMITTED are visible
review items. Evidence applies only to the matching game/date/team; preserve PDF
hashes and the existing T-30 anchor. No claim of automatic medical interpretation.

Profiles cover every current roster player, with as-of-filtered regular-season
minutes and attempt rates, prior/current split, recent roles and out-teammate
vacancies. Vacancies trigger reasoning review, not fabricated redistribution.
Starter/bench labels require actual starter evidence (otherwise unknown).

A one-shot operations tick refreshes the calendar and history, creates separate
per-game preliminary/final runs, evaluates stored runs against final boxscores,
and writes reports/review queues. A persistent SQLite ledger is mandatory.
Checkpoint keys include schedule version/tipoff and stage; recover saved runs
after crashes, serialize ticks, and expose errors/missed windows. A local systemd
timer runs every minute; install/start is a separate deployment operation, not
something this temporary workspace can promise to keep running. No API-model
charges or notifications to other people.

Paired baseline comparisons use each selected forecast's immutable input history,
never future outcomes or backdated data. Compare player averages and expected
minutes times historical scoring rate on the same supported population. Record
MAE/RMSE, minutes/rate decomposition and interval coverage; no auto-promotion or
calibration claims without prospective evidence.
