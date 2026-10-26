# Historical scoring priors captured October 23, 2026

ESPN career regular-season statistics for players mapped by exact names and team
roster identities to the NBA player IDs on the remaining October 23 slate.
The capture excludes CHI/MIL. It covers 351 mapped players; 330 have 829 completed
season rows from 2023-24, 2024-25 and 2025-26. Missing/failed responses remain
listed in the manifest. Rookie history is not invented. Player history follows
NBA identity across subsequent trades rather than a captured team assignment.

`priors.json.gz` contains per-player season totals and source/identity provenance.
`raw/` retains exact decompressed response bytes by SHA256. GP, points, shots and
turnovers are exact source totals; minutes are estimated from rounded season
MPG and explicitly labeled. Traded-player totals are used once after checking
against team stints. These are season aggregates, not fabricated game records.

Model 0.4 uses recent-season weights of 1, 0.65 and 0.4225 with up to 20 effective
games per season, then decays the prior as current games accumulate. Aggregate
priors replace overlapping game-history scoring priors; they are never added a
second time. Current-team role evidence still controls minutes. Sources were
retrieved at 22:23 UTC, before the regenerated forecasts and before tipoff.

Production uses `data/operations/production/historical-priors.json`, corresponding
statistics bytes in `historical-raw/`, and roster identity bytes in `references/raw/`.
The preflight gate verifies hashes, identity, season totals and retrieval cutoff.
Historical records are not current availability or lineup confirmation.


The capture recipe is retained in `collection/collect_historical_priors.py`.
It reads the existing production roster/reference cache, uses ten bounded
workers, writes new responses under `/tmp`, and validates traded-player totals.
Its requested seasons are deliberately fixed to this snapshot; a later capture
must receive a new date/version and preserve these original source bytes.
