# Research scripts

The analyses behind the numbers in `docs/research-lessons-2026-10.md`, so they
can be rerun on a new reference. Standard library only; run from anywhere, the
`--datasets` default is the repository's `datasets/`.

| Script | Question | Inputs |
|---|---|---|
| `gap_breakdown.py` | Where the model's margin MAE is lost against the close: compression, month, back-to-backs, size of disagreement, spread size, underdog lean, team-seasons | a backtest reference's `rows.json.gz`, team games, closing lines |
| `absence_minutes.py` | How much the margin misses per ten minutes of reported-out regulars, home minus away; the measurement behind `margins.absence_adjustment` | the same, plus the histories and T−30 injury reports |
| `clv.py` | Closing-line value: does a night-before forecast move the opener? With the point-differential placebo | a night-before replay's `rows.json.gz`, closing lines, the archived ESPN odds responses under `data/market-history/` (gitignored) |
| `line_lag.py` | How stale the ESPN closing line is against Kalshi's price at tip, on the live captures | `production/market/game-lines`, finals, Kalshi's public API (cached under `data/research/kalshi`) |
| `fallback_margins.py` | Why live pregame runs published the player-sum margin fallback, when, how wrong against the result next to the same game's nearest estimated run, and what the market report scores once a partial-roster sum is no margin | `news_timing.py extract`'s per-(run, game) file (branch measure/news-timing), the operations copy's schedule, finals and game lines |
| `early_season.py` | The model against the close by weeks since opening night, and whether players absent with no minutes for the team this season are a systematic margin miss | seeded early-season replays (`docs/backtest.md`, "Early-season replay"), team games, closing lines, histories, T−30 reports |
| `previous_ids.py` | A previous season's box scores under the next season's player IDs, for seeding 2025-26 from ESPN's 2024-25 | the two histories |
| `clutch.py` | Whether clutch shooting persists year to year beyond a player's season shooting, against what chance alone gives | the NBA clutch archive (`datasets/clutch-2026-10-22`), the gate's histories |
| `close_teacher.py` | Whether the model's disagreements with the close predict the close's misses; the "teacher" regressions of close − model on schedule and standings features; a strength backbone from earlier closing spreads; the same situations and the opener over the 2007-26 archive | a reference's `rows.json.gz`, team games, histories, closing lines; optionally `market/report.json` and the research notes' `all_games.json` |
| `evidence_key_extract.py` | One read of the live runs of a window into a compact per-run summary (game evidence, history digest, model key, margin, players) | an operations directory (read-only; a copy preferred) |
| `evidence_key_validate.py` | Replays the per-game evidence key over saved live runs: how many re-forecasts it makes, and whether any forecast change happened with the key unchanged (a miss) | the extract above; file write times of the same directory |
| `evidence_key_verify_tick.py` | On a copy, keys every archived report check of a date with the real parser and roster, checks the league-input digest is deterministic and cheap, and every game's full key is stable | a production copy, the tick's history file |
| `derived_trigger_simulation.py` | Replays the derived service's trigger under the old rule and with run-only changes waiting 30 minutes: rebuilds and busy hours | an operations directory (read-only): two ledger timestamp columns, `derived.jsonl`, file write times |
| `tick_memo_check.sh` | Main against this branch on one production copy: merged history, player pages and index identical; history-phase and sync CPU; prepared inputs and forecasts of the newest games identical, cold and warm (helpers `tick_memo_tick_identity.py`, `tick_memo_prepare_identity.py`, `tick_memo_snapshots.py`, `tick_memo_compare.py`, `tick_memo_offline.py`) | a production copy (never production: it makes hard-linked views), two checkouts |
| `dashboard_compare_servers.py` | Serves one frozen operations copy from main and from this branch and checks every game's calendar fields, each day's details, the other top-level keys and the daily reviews are equal; page and endpoint timings | a frozen production copy, two checkouts |
| `rating_harness.py` | Re-scores a reference's margins under a candidate team rating without a replay (exact: the `current` variant reproduces the reference); results-only rebuilds against the market-implied strength of `margins.market_strengths` | a reference's `rows.json.gz`, team games, histories, closing lines (through `close_teacher.load`) |
| `derived_equivalence.py` | Main's full derived rebuild against this branch's cached one on the same copy: every output file identical, cold, warm and after new runs, with timings | a production copy (or `--ledger` subset), two checkouts |
| `derived_gate_check.py` | On a copy: the cached interval-calibration fit equals its direct re-fit (the activation gate), and margin samples through the cache equal the direct ledger read; what each costs | a production copy, `PYTHONPATH` this checkout |
| `subset_ledger.py` | Copies the newest N runs of a ledger, with their histories and evaluations and original rowids, for the checks above | a copy's ledger |
| `live_replay.py` | How the code of the checkout it sits in scores on the live forecasts' own inputs: each finished game's final pregame run (or `--which first`) re-forecast from its saved snapshot and history, `frozen` (the snapshot as stored; a run of the same release must come back byte for byte) or `rebuilt` (the prepared model inputs, team ratings and market strength included, prepared again as of the run), scored beside what was published and the close; `--compare` pairs two outputs game by game | a production copy (never production): `state.json`, the ledger's `run_records` and `histories` by key, `model-artifacts`, `environment-finals`, `market/game-lines`, `finals` |
| `stats_lite.py` | OLS with date-clustered errors for the above | |
| `rescore_after_deploy.py` | What the first ticks after a deploy spend re-scoring live runs, main's tree against a branch's, and whether the drained evaluations equal main's | a private copy of an operations folder (ledger, `finals/`, `evaluation-state.json`) |
| `run_ids_cold.py` | What listing a ledger's run IDs costs from a cold page cache through the `runs` view against `run_records` | a copy of a ledger |
| `sqlite_tmpdir_probe.py` | Where this host's SQLite writes a spilled sort's temporary files under each way of choosing the directory | a scratch directory |
| `stored_run_bytes.py` | Where a stored run's bytes are: `forecast_json` on-game / off-game rows / rest, `snapshot_json` by key, the `runs/<run_id>/` files, the evaluations; growth per day and what a compaction would remove | a copy of an operations directory (read-only, rows by rowid) |
| `stored_forecast_equivalence.py` | Whether leaving the off-slate rows out of stored forecasts (`store.stored_forecast`) changes anything a reader uses: replay, stored rows, evaluations, derived reports, dashboard, and main reading slim runs | a copy of an operations directory, a main checkout and the branch |

## Reproduced on 2026-10-26

`gap_breakdown.py --reference datasets/backtest-2026-10-26-absence`: gap +0.393
pooled; March and April 33% and 25% of it, April +0.80 a game; games where
model and close differ by 6+ points are 8% of games and 47% of the gap; 72%
of the gap in games where the model leans further to the underdog; model SD
7.87 against the close's 8.22, best stretch 1.010; actual on model and close
together +0.128 and +0.943; back-to-backs 0.44 and 0.51 against 0.35.

`absence_minutes.py --reference datasets/backtest-2026-10-26-three-seasons`:
model miss −0.49 ± 0.06 per ten reported-out minutes, close −0.17 ± 0.05,
each season within 0.08 of the pooled figure; on the absence reference the
model's slope is +0.01 ± 0.06.

`clv.py` on night-before replays (`injury_history --anchor 'D-1 20:30'`,
`datasets/injury-reports-*-night-before`), one standard error:

| | Placebo slope | Night-before slope | CLV per bet | Covered the opener |
|---|---|---|---|---|
| 2024-25, before the absence adjustment | +0.004 ± 0.021 | +0.100 ± 0.031 | +0.11 | 50.2% ± 1.7 |
| 2025-26, before the absence adjustment | +0.022 ± 0.029 | +0.063 ± 0.040 | +0.12 | 51.5% ± 1.7 |
| 2024-25, with it (margin method v3) | +0.004 ± 0.021 | +0.128 ± 0.029 | +0.22 | 52.4% ± 1.7 |
| 2025-26, with it | +0.022 ± 0.029 | +0.078 ± 0.048 | +0.10 | 51.8% ± 1.7 |

The first two rows are the research page's table. The absence adjustment
roughly doubles the 2024-25 line movement in the forecast's direction and
leaves 2025-26's within its error; both remain short of the 0.8 points a bet
needs to clear the vig. The replays themselves are not committed (two seasons,
about eight minutes each); rerun the backtest with the night-before statuses to
regenerate them.

`line_lag.py` on 2026-10-27, 23 games of 2026-10-24 to 2026-10-26 (first week; seven
had their last ESPN change more than 90 minutes before tip): spread staleness
ESPN minus Kalshi at tip, median 0.33 points, mean 0.53, mean signed +0.07; the
part that moved after ESPN's last change, median 0.16, mean 0.19, never a full
point; book difference at the same moment, median 0.30. One outlier, MEM@UTA,
where ESPN's spread (UTA −1.5) disagreed with its own even moneyline and Kalshi
had MEM −1.5. Win probability: median 0.8 points, four games over 2. Against the
results on those games: ESPN close MAE 8.15, Kalshi at tip 8.07, model 10.97
(the published margin; 11.62 was the player sum the market report scored
before 2026-10-27).

## Run on 2026-10-27

`early_season.py` on the three seeded replays: gap to the close +0.38 in weeks
1–2 (270 games), +0.39 in weeks 3–4, +0.12 in weeks 5–8 and +0.33 in the gate
window; per ten uncounted last-season minutes over the first 30% of dates, model
miss −0.04 ± 0.16, close −0.12 ± 0.04 relative to the model.

## Run on 2026-11-10

`live_replay.py` with main `1385e80` on the 134 finished live games with a
pregame run (`docs/backtest.md`, "Live replay"): on the 122 with a close,
rebuilt margin MAE 11.77 against 12.51 published and the close's 10.76, today −
close +1.01 ± 0.29 (one standard error, Eastern-date clusters); without the
four games published 10+ points from the close +1.04 ± 0.29, and today −
published +0.03 ± 0.12. Every v4 run came back byte for byte.

## Run on 2026-11-06

`close_teacher.py --reference datasets/backtest-2026-11-06-rating-weight`:
slope of actual − close on model − close +0.121 ± 0.080 pooled, best blend
weight on the model 0.1 for no gain; 6+ point disagreements 7.8% of games and
47% of the gap, the model's side covering 48.5%; the teacher regression gives
home back-to-back −1.17 ± 0.18, away +0.87 ± 0.17, March–April bottom-8
home −1.56 ± 0.27 and away +0.96 ± 0.28, with the outcomes agreeing in sign;
a rating fitted to earlier closing spreads (7-day half-life) scores 11.30
alone and 11.12 blended half and half with the model's 11.25 against the
close's 10.90. Over the 22,694-game archive no situation beats the close
beyond one standard error except fading a March–April bottom-8 home team
(54.2% ± 1.2, one of nine tests); lines that move 1.5+ points cover the
opener 57.7% on the side of the move. Written up in
`docs/beating-the-close-2026-11-06.md`.

## Run on 2026-11-07

`rating_harness.py --reference datasets/backtest-2026-11-06-rating-weight`
(18 s): the `current` variant reproduces the reference's 11.251 margin MAE
with a change of +0.000, so each candidate's change is the exact replay
effect. One date-clustered standard error. Results-only rebuilds of the
rating do not move it: recency weighting with a 20-day half-life +0.112 ±
0.036 and 40-day +0.035 ± 0.022, margins capped at 20 +0.028 ± 0.017, an
opponent-adjusted ridge rating −0.007 ± 0.010 (5 pseudo-games) and −0.008
± 0.008 (10), the ridge with a 30-day half-life +0.054 ± 0.028. The strength
implied by earlier closing margins (`margins.market_strengths`, 7-day
half-life, ridge 0.1) blended into the rating: 0.5 share −0.075 ± 0.018,
0.7 share −0.090 ± 0.025, 1.0 share −0.100 ± 0.036, each season between
−0.08 and −0.10; a 14-day half-life −0.078 ± 0.022 and a ridge of 1 −0.064
± 0.023. The slope of actual − close on model − close rises from +0.121 to
+0.186 at the 0.7 share. The 0.7 share is what `backtest-2026-11-07-market-strength`
gated (margin −0.090 ± 0.050 at two standard errors, winner Brier −0.0023 ±
0.0011, improved).

## Measured on 2026-11-09 (operations)

`rescore_after_deploy.py` on the last 400 runs (352 evaluation revisions) of the
2026-10-29 production ledger, in a private copy, with that day's
`evaluation-state.json` (written by another release, as after a deploy); main
at 1385e80 against this branch at 2a2f403, one call standing for one tick. The
host's disk was saturated by a production copy, so seconds are slow and vary
with the page cache (main's natural call ran with a cooler cache than its forced
call).

| Scenario | Calls | First call s | Slowest call s | Re-scored or re-checked runs | Revisions | End state |
|---|---|---|---|---|---|---|
| main, the code as deployed | 1 | 234.03 | 234.03 | 299 | 0 | start |
| branch, same | 7 | 20.52 | 77.33 | 400 | 0 | start |
| main, a release that changes evaluation (`--changed-absences`) | 1 | 60.82 | 60.82 | 299 | 33 | `c3b5b7a8` |
| branch, same | 3 | 20.30 | 20.42 | 400 | 33 | `c3b5b7a8` |

The drained branch leaves the evaluations table identical to main's (row
count, newest rowid, SHA-256 of run, revision and actuals hash in key order),
and its next call takes 0.34 s. A call is held to the budget plus one run
except for what comes before the re-checks, chiefly listing the runs (the
77.33 s call was not broken down). `run_ids_cold.py` on the same copy, cold:
through the `runs` view 255.86 s and 415.7 MB read, from `run_records` 30.51 s
and 1.1 MB, identical rows; warm, 0.096 s against under 0.001 s. `_run_ids`
and the derived service's `_ledger_state` (row counts through the same view,
every minute) now read `run_records`. The dashboard's `_ledger_key` and other
readers still count or list through the view.

## Run on 2026-11-09

`stored_run_bytes.py --directory scratch/2026-10-29-derived/production-copy --runs 6 --step 40`
(six live runs of 10-28 and 10-29 read by rowid; their `runs/<run_id>/` files read,
read-only, from production): `forecast_json` 2.084 MB a run, of which the
game's 35 rows 0.927 MB, the other 487 rows 0.945 MB (45.3%; every one `no_game`
with no game; 447 kB of it each player's history game IDs, 291 kB null
fields) and the rest 0.212 MB (`allocation_audit` 0.180). `snapshot_json`
0.863 MB: `model_enhancements` 0.347, `shot_quality` 0.190, `role_context`
0.093, the other teams' roster entries 0.095 (11.1%), the game's 0.017. Run
files: `forecast.json` 2.084 MB, `snapshot.json` 0.863, `profiles.json` 0.831
(0.766 of it other teams' players). An evaluation's `report_json` is about
164 kB, 39 kB of it `no_game` exclusions. At 240 runs a day the ledger rows
grow 707 MB a day before and 481 MB after (−227 MB), the run files 907 → 680 MB.

`stored_forecast_equivalence.py` on the same copy, 24 live runs of 10-26 to
10-29 UTC (12 final, 12 preliminary, three model keys), the newer 15 saved
slim in the mixed ledger and 9 kept as stored: main and the branch recompute
the same bytes 24/24, none of them its stored forecast (saved by releases
before 10-30; main's and the branch's validators both reject all 24 stored
forecasts, "Forecast margins disagree…", so the 15 slim runs were inserted
with their own hash); 15/15 slim runs equal the stored forecast in every
top-level key and every row of the game; evaluations equal except
`sample_counts.predictions`, `sample_counts.excluded` and `excluded` (486.2
`no_game` entries fewer a report, 163.7 → 124.4 kB), and main scoring the
mixed ledger writes the same bytes 21/21; derived reports: 64 of 72 files
identical, the rest differ only in the off-slate counters
(`performance.json` `off_slate_rows_ignored`, `collection_exclusions.off_slate`
in the calibration and model status), the quoted `forecast_sha256` and the
digests over it (daily-review `input_signature`, `residual_evidence_sha256`),
9/9 such digests checked by recomputation; main on the mixed ledger writes the
same 75 files; dashboard game payloads equal 1206/1206 (12 showing a slim
run). With `--dev-recompute` (today's forecasts from the same snapshots, so
both ledgers save through `save_run`): 15/15 slim runs through the branch's
`save_run`, the same results.

The newest runs, on 2026-11-10 (the fresh production copy was not ready; files
read, read-only, from production's `runs/<run_id>/`):
`stored_run_bytes.py --files-from production --newest-folders 6` (runs of
02:16 to 02:52 UTC): `forecast_json` 2.186 MB, the other teams' 486 rows 0.977
MB (44.7%), the game's 36 rows 0.916, the rest 0.293 (`allocation_audit`
0.261); `snapshot_json` 1.196 MB (`model_enhancements` 0.415, `shot_quality`
0.366, `role_context` 0.126, other teams' roster 0.095, 8.0%); `profiles.json`
0.914 MB, 0.840 of it other teams' players. At 240 runs a day: ledger rows 812
→ 577 MB a day (−235 MB), run files 1031 → 796 MB. The 4106 run folders hold
about 4.0 GB of off-game rows in the ledger and as much again in their
`forecast.json` (estimate: sample mean times count).
`stored_forecast_equivalence.py --folders 30 --files-from production` (runs of
00:37 to 02:52 UTC, final and preliminary): main's and the branch's validators
accept all 30 stored forecasts, the branch accepts all 30 in the stored shape,
which is exactly the stored forecast without the other teams' rows, as many as
`coverage.by_status.no_game`: 30/30. After `stored_forecast` began validating the
whole forecast before leaving rows out (review finding B2-1), the same 30/30, and
per run (median over the 30, `nice -n 19` on the busy host): the added
`_validate_forecast` of the whole forecast 24 ms, all of `stored_forecast` (JSON
round trip of snapshot and forecast plus that validation) 132 ms, `save_run`'s own
validation of the stored shape 17 ms.

On the fresh copy (`scratch/2026-11-09-rebuild/production-copy-1109`, copied
2026-11-10T10:11:59Z, 4106 runs, 16.80 GB), after the B2-1 fix:
`stored_run_bytes.py --runs 8 --step 60` (runs of 11-08 to 11-10):
`forecast_json` 2.228 MB, off-game rows 0.981 (44.0%), the game's rows 0.981,
the rest 0.267 (`allocation_audit` 0.234); `snapshot_json` 1.139 MB, other
teams' roster 0.096 (8.4%); `profiles.json` 0.914, 0.843 of it other teams'
players; evaluation `report_json` 162–168 kB, 39.3–39.7 kB of it `no_game`
exclusions. Ledger rows 808 → 573 MB a day at 240 runs (−235 MB), run files
1027 → 792 MB. A compaction of what is stored would remove about 4.03 GB of
the ledger's `forecast_json` (4106 runs), 0.29 GB of `report_json` (7372
revisions) and 4.03 GB of `forecast.json` files (estimates: sample mean times
count).
`stored_forecast_equivalence.py --sample-days 5 --per-day 3 --replay all`: 29
runs of 11-06 to 11-10 UTC (14 final, 15 preliminary, four model keys), 18
slim, 11 kept as stored; main and the branch recompute the same bytes 29/29;
21/29 reproduce their stored forecast exactly (every run of model keys
`bed6565e` and `0168f8c3`), the 8 of 11-06 and 11-07 (keys `717925c7`,
`35523790`) differ under main the same way in `game_totals`,
`margin_forecasts` and `predictions`, and both validators reject them the same
way; all 18 slim runs saved through the branch's `save_run`, 18/18 equal to the
stored forecast in every top-level key and game row; evaluations differ only in
`sample_counts.predictions`, `sample_counts.excluded` and `excluded` (487.9
entries fewer, 166.2 → 126.7 kB), main writes the same revisions 29/29 from the
mixed ledger, and the full ledger's reports equal production's own 29/29;
derived reports 154 of 162 files identical, the rest only off-slate counters,
the stored hash and digests over it (7/7 recomputed; the refitted interval
calibration artifact differs only in `off_slate` and
`residual_evidence_sha256`, hence its file name and `artifact_sha256`); main on
the mixed ledger writes the same 165 files; dashboard game payloads equal
1206/1206 (12 showing a slim run). RESULT OK.
