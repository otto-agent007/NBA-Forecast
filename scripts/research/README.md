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
| `early_season.py` | The model against the close by weeks since opening night, and whether players absent with no minutes for the team this season are a systematic margin miss | seeded early-season replays (`docs/backtest.md`, "Early-season replay"), team games, closing lines, histories, T−30 reports |
| `previous_ids.py` | A previous season's box scores under the next season's player IDs, for seeding 2025-26 from ESPN's 2024-25 | the two histories |
| `clutch.py` | Whether clutch shooting persists year to year beyond a player's season shooting, against what chance alone gives | the NBA clutch archive (`datasets/clutch-2026-10-22`), the gate's histories |
| `close_teacher.py` | Whether the model's disagreements with the close predict the close's misses; the "teacher" regressions of close − model on schedule and standings features; a strength backbone from earlier closing spreads; the same situations and the opener over the 2007-26 archive | a reference's `rows.json.gz`, team games, histories, closing lines; optionally `market/report.json` and the research notes' `all_games.json` |
| `rating_harness.py` | Re-scores a reference's margins under a candidate team rating without a replay (exact: the `current` variant reproduces the reference); results-only rebuilds against the market-implied strength of `margins.market_strengths` | a reference's `rows.json.gz`, team games, histories, closing lines (through `close_teacher.load`) |
| `live_replay.py` | How the code of the checkout it sits in scores on the live forecasts' own inputs: each finished game's final pregame run (or `--which first`) re-forecast from its saved snapshot and history, `frozen` (the snapshot as stored; a run of the same release must come back byte for byte) or `rebuilt` (the prepared model inputs, team ratings and market strength included, prepared again as of the run), scored beside what was published and the close; `--compare` pairs two outputs game by game | a production copy (never production): `state.json`, the ledger's `run_records` and `histories` by key, `model-artifacts`, `environment-finals`, `market/game-lines`, `finals` |
| `lineup_oracle.py` | An upper bound, labelled as one: how much of the live gap to the close closes if the final pregame forecast knew who played (O1), also who started (O2), also everyone's minutes (O3), applied as input overrides before `model.forecast` so the model's own allocation, absence term and scoreline react; O0 installs the hooks and must reproduce the reference replay; `tables` pairs each level with the reference (margin, gap to the close, winner Brier, team and player points, minutes, the split by availability surprise, and which margin half moved) | `live_replay.py`'s inputs and a reference rebuilt output; the copy's `finals` |
| `live_components.py` | Which live-only inputs help or hurt live accuracy: replays the live ledger (`live_replay.py`, rebuilt mode) with role context, the shot-quality adjustment, prior-season aggregate priors, all three, the team-rating absence term, or its non-official inputs switched off before `model.forecast` (shallow snapshot copy; the package is unchanged; `none` must reproduce the reference byte for byte), and prints `lineup_oracle.py tables` against actual results plus a per-variant audit of what each component did. Heavy: run through the flock/scope/guard wrapper, overnight. |
| `stats_lite.py` | OLS with date-clustered errors for the above | |

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
