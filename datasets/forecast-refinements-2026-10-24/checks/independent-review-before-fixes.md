# Independent refinement review

Review target: `/mnt/2TBHDD/NBA-Forecast/repo`, base `11a0065`, initial patch SHA256 `360ef35ca2e2ca1982bb7bd9aaa5006a7dfe711acaf8a7f2af20d082c9f1d016`.

Read-only review of the approved spec, plan, patch and implementation. No broad test rerun, activation, commits, or repository edits. Findings below describe the reviewed implementation before the controller's fixes.

## Important: injury exclusions do not reach allocation minute-cap learning

`nba_forecast/candidate.py:45-46` passes all usable historical rows to `redistribute`. `nba_forecast/rotation_allocation.py:245` selects those rows, and lines 98-115 learn the recipient minute cap without consulting `profile.role_minutes_evidence.injury_shortened_role_game_ids`.

A recipient with healthy minutes `[30,30,30,30,30,30,30,34,38]` followed by a verified injury-shortened 10-minute appearance receives a 34-minute observed cap/allocation. Removing only the injury row from minute-cap evidence yields 38 minutes. The profile already contains the exclusion, but the allocator ignores it. This lets the excluded injury appearance lower normal-role capacity and change final scoring.

Fix: maintain separate healthy-role minute rows and actual production/attempt rows through allocation; retain the original rows for rates and shot capacities. Add a regression verifying the observed minute cap and output minutes with a confirmed out teammate.

Reproduced with `redistribute`, `tests.test_rotation_allocation.inputs/profile/evidence`, and `tests.test_model.current/player`, using the existing cutoff and a locked-rate recipient. Actual output:

```text
current integration: minutes=34.0, minute_cap=34, injury_row_used=True
without excluded minute row: minutes=38.0, minute_cap=38, injury_row_used=False
```

## Important: incomplete final caches can score margin probabilities

`nba_forecast/daily_review.py:129-133` sums available player points and immediately calls `score_margin`. The existing `_actuals` validator can accept partial player records for both teams without team totals or complete duration. A non-tied partial sum is therefore treated as the final margin, contaminating winner and threshold Brier scores.

Reproduction: initialize `tests.test_daily_review.DailyReviewTests`; its final cache contains one 32-minute player per team and no `team_boxscore`. Increment the first player's FGM by 1 and PTS by 2, attach `tests.test_margins.small_distribution()` with the fixture's game/team IDs to a newly selected forecast, then call `report()`.

```text
minutes_by_team={'T1':32,'T2':32}
team_boxscore_present=False
coverage.verified_finals=1
margin_evaluation.status='evaluated'
margin_evaluation.actual_home_margin=2
margin_evaluation.home_win_brier=0.09000000000000002
```

Fix: require complete paired final-score evidence specifically before margin feedback, while retaining supported individual-player evaluation from partial final caches. Test incomplete duration, missing final score evidence, and complete regulation/overtime scores.

## Important: injury parser accepts non-current or non-subject injury claims

`nba_forecast/role_context.py:165-182` uses a limited temporal blacklist and a broad injury-description wildcard; lines 203-210 miss explicit return prose with a conjunction before the pronoun.

The following each produces an injury exclusion for Flagg (`1642843`) using the existing `RoleContextTests` full raw-archive preparation fixture (game played Friday October 23, 2026):

```text
Flagg did not play in the second half after he injured his left shoulder in the season opener on Tuesday.
Cooper Flagg left the game with a teammate who had a left shoulder injury and did not return.
Cooper Flagg left the game with a left shoulder injury and did not return. However, he returned in the third quarter.
```

Reproduce by assigning each paragraph to `case.page['story']['content']`, calling `case.install()`, then `case.prepare(history)`, with a fresh `RoleContextTests` fixture for each case. Every result contains `events[0].player_id == '1642843'`.

Fix: require unambiguous current-game and player-owned injury causality, and abstain on explicit return contradictions even when introduced by connecting words. Add these variants to the archived-source parser regressions.

## Compliance verdict

No Critical findings. Resolve the three Important findings before activation. Otherwise, reviewed implementation follows the design: current availability is authoritative; actual scoring/exposure is retained; location replacement preserves historical ability, excludes the target from the reference pool, and has bounded exact fallbacks; paired final-score dispersion is explicitly uncalibrated and includes overtime evidence; zero-margin outcomes are excluded; strict YES/NO equality and away inversion are correct; partial forecasts and legacy snapshots withhold probabilities; package-bound artifacts, prepared-source replay and immutable margin consistency are present. No additional Important issue found in the remaining code/provenance/UI paths. Passing broad-test claims were provided by the controller, not independently rerun here.
