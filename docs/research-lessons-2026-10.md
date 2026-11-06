# Research lessons, 2026-10: what the field does and what we took from it

Deep research on 2026-10-24 surveyed how NBA forecasting is done (game
models, player projections, availability, player-impact ratings, evaluation,
the open-source ecosystem) and checked its claims against this repo's replay.
The full report, the six topic notes and the closing-line odds dataset live
outside the repo:

- `../../reports/NBA forecasting methods and lessons.md`
- `../../research_notes/NBA forecasting methods and lessons/`
- `../../research_data/NBA forecasting methods and lessons/` (SBRO 2007-22 and
  ESPN 2022-26 closing lines, about 22,800 games, with the analysis scripts)

This page keeps the conclusions, the corrections, where each recommendation
stands and why some ideas were set aside. "(RC)" marks numbers the research
computed from that odds dataset; sources for the rest are cited in the report.

## Where we stand against the close

The gate's market comparison (`market_metrics`, reference
`datasets/backtest-2026-10-26-absence`), on the replay's own games:

| Season | Model margin MAE | Close | Naive rating | Model Brier | Close Brier | Encompassing slope |
|---|---|---|---|---|---|---|
| 2023-24 (867 games) | 11.26 | 10.82 | 11.47 | 0.209 | 0.199 | +0.09 ± 0.14 |
| 2024-25 (869 games) | 11.13 | 10.69 | 11.50 | 0.205 | 0.196 | +0.02 ± 0.13 |
| 2025-26 (868 games) | 11.48 | 11.18 | 11.90 | 0.203 | 0.193 | +0.13 ± 0.13 |

The gap to the close was 1.2 points of margin MAE when the research ran. It
was 0.58 in every season before reported-out regulars entered the team rating
(2026-10-26, `docs/backtest.md`) and is 0.30–0.44 now. The encompassing slope
was about −0.08 then; it is now slightly above zero in every season, though
not by two standard errors, so where the model disagrees with the close the
close is still at least as right. Team points trail the market's implied
totals by about 0.6–0.8 MAE.

Outside benchmarks (different samples): the 2025-26 close missed by 10.9 and
picked 69% of winners at Brier 0.194 (RC); Dunks & Threes' live model 11.0
(2024-26); NBA_AI's best 2025-26 ensemble 11.45 and 0.205; a points-per-game
baseline 11.93, 68.3% and 0.211.

### Where the remaining gap is (2026-10-26)

On the absence reference, pooled, the model trails the close by 0.39 points of
margin MAE:

- **Late season:** March and April hold 58% of it. April is the worst at
  +0.80 a game, down from +1.35 before the absence adjustment; March is +0.48,
  December to February +0.24 to +0.28. Tanking and shut-down teams the close
  marks down further, such as Toronto 2023-24 and Memphis and Washington
  2025-26 in March and April, were the largest single pieces.
- **Big disagreements:** games where model and close differ by 6+ points are
  8% of games and 47% of the gap.
- **Underdog lean:** 72% of the gap comes from games where the model leans
  further to the underdog than the close does, down from 85%.
- **Back-to-backs:** games with one or two teams on the second night trail by
  0.44 and 0.51, against 0.35 for neither. Before the adjustment the figures
  were 0.76 and 0.68 against 0.48.
- **Margins are no longer compressed:** the model's margin SD is 7.9 against
  the close's 8.2, and the best stretch on the actual margin is 1.01, down
  from 1.17. Regressing the actual margin on both model and close gives the
  model +0.13, up from −0.003.
- **Team points:** the bias grows by season, −0.6, −1.3 and −1.7 for 2023-24
  to 2025-26.

## Closing-line value (2026-10-25)

Does the model know something the opening line has not priced?

- **The test:** regress the line's move (close − open) on the forecast's
  distance from the opener, with date-clustered standard errors. Opening and
  closing home spreads come from the archived ESPN core odds responses
  (`data/market-history/<season>/references/raw/`, gitignored, hash-checked).
  The provider is the closing line's for 2024-25 and 2025-26.
- **The forecast:** a replay with the official report published by 8:30 PM ET
  the night before (`datasets/injury-reports-*-night-before`,
  `injury_history --anchor 'D-1 20:30'`). That is about when live captures
  first see the openers. T−30 and midday forecasts read news posted after
  the open, so their CLV overstates the edge.
- **A placebo:** the naive point-differential rating, which knows only the
  standings, should score about zero if the opener prices public
  information. Run it first on any new line source.

Model 0.4.8, before the absence adjustment:

| | Placebo slope | Night-before slope | CLV per bet |
|---|---|---|---|
| 2024-25 | +0.004 ± 0.022 | +0.100 ± 0.030 | about +0.11 |
| 2025-26 | +0.022 ± 0.032 | +0.063 ± 0.034 | about +0.12 |

One standard error shown.

- **Real but small:** against the opener, betting the night-before forecast
  covered 50.8% ± 1.2. Break-even is 52.4%, which needs about 0.8 points of
  CLV per bet.
- **Not an underdog drift:** the model's 5+ point underdog leans moved 0.62
  more toward the dog than other games at the same opening spread.
- **2023-24 cannot be used.** Its closing lines are Betfair's, which post no
  opener, and ESPN BET's openers are soft. The placebo alone scores
  +0.235 ± 0.035, still +0.15 with results four weeks old, steadily from
  December to March. The moves were real corrections and the close is sound,
  but a forecast that knows only the standings collects that CLV.
- **Live:** captures in `production/market/game-lines` start 2026-10-24. A live
  test needs a few hundred games, with the placebo run first.

## Lessons that held

- The closing line is the bar for game outcomes. Pre-game models that beat it
  are rare, and the claims usually turn out to be leakage: when one popular
  repo stopped feeding a game's own stats into its features, accuracy stayed
  similar but its edge against the odds disappeared.
- About 68% winners is the floor for a sensible model, not an achievement.
- Minutes, not per-minute rates, drive player-level error. EPM's minutes beat
  a season average by 14.5% against 2–5% for rate stats.
- Weight recency per stat and shrink each stat by its own padding: role
  forgets in 1–3 games, volume in weeks, shooting over seasons.
- Availability is the biggest single information shock, and only stars move a
  line by several points.
- Home court has roughly halved, to 1.7–1.9 points (RC). Back-to-backs cost
  1.6–2.3 points, and the close prices them fully (RC).
- Player-based team ratings beat results-based ones only modestly; blending
  the two works best. FiveThirtyEight gave Elo about 35%, between 0% and 55%
  depending on roster continuity.
- A box-score prior carries most of the player-impact signal. Raw one-season
  RAPM is noisy (year-to-year r 0.41 against 0.75–0.85 for prior-based
  metrics), and every modern metric is ridge plus-minus on lineup stints
  shrunk toward a box-score prior.
- Judge with proper scores and calibration, not accuracy or betting returns,
  and cluster the standard errors of correlated errors.

## Corrections to the notes

The notes made claims that the repo's rows contradicted. Do not act on the
uncorrected versions:

- Home court is not too large. The 1.45% factor is split as √1.0145 home and
  1/√1.0145 away, about +1.65 points, in line with the market.
- Margins are compressed, not over-dispersed: the slope of actual on
  projected margin was 1.31, rising to 1.81 in April. Shrinking them makes
  MAE worse. (Still true on 2026-10-25: slope about 1.2, model SD 6.6 against
  the market's 8.35.) Most of the compression was absences. After the absence
  adjustment (2026-10-26) the best stretch is 1.01, and the model SD is 7.9
  against the close's 8.2.
- The margin σ is fitted from paired finals (about 15.6), not assumed at 12.
- Role minutes already decay at 0.70–0.75 per game, close to DARKO's.

## The ranked changes and where they stand (2026-10-25)

| # | Change | Status |
|---|---|---|
| 1 | Cluster the gate's standard errors | Done: two-way, date × player, with a 0.5% materiality floor since 2026-10-26 (`docs/backtest.md`) |
| 2 | Market and naive baselines on the scoreboard, encompassing test | Done: `market_metrics` |
| 3 | Official injury reports in the replay, second season | Done: 2024-25 and 2025-26 with T−30 reports |
| 4 | Blend the player-sum margin with a team rating, preseason prior | Done: 50/50 on 2026-10-24, 0.35/0.65 since 2026-11-06 (`backtest-2026-11-06-rating-weight`: winner Brier −0.0014 ± 0.0007, margin within noise; live the player-sum half sat further from the close than the rating half); prior 10 games at 0.6 × last season; the rating loses 0.1 per minute of reported-out regulars (2026-10-26). Roster continuity tested on a branch (below) |
| 5 | Calibrate the interval's two tails separately | Done: `calibration.two_tailed_quantiles` |
| 6 | Measured status play rates; return-from-absence minutes ramp | Rates done: factors on the role rate fitted on 12:30 PM ET reports (questionable .64, doubtful .04); raw play rates 89/49/3%, close to the outside source's 87/48/4%. Ramp open |
| 7 | Market composite as a separate, labeled output | Open: a product decision; it would make the edge report echo the price |
| 8 | Blowout minutes haircut | Open |
| 9 | Situational priors (back-to-back, 3-in-4, altitude, eastward travel) | Open: `context_effects` exists but only the research path uses it |
| 10 | Player-impact ratings (box prior, then RAPM) | In progress: `exp/player-impact` |
| 11 | A ~1:15 PM PT injury capture | Open: captures are 9 AM PT and T−30 |

Margin experiments of 2026-10-25, all on branches pushed to GitHub, none
merged:

- `exp/margin-reweight`: 0.4 × player sum + 0.8 × team rating. Gate
  improved (winner Brier −0.0028 ± 0.0016, margin within noise), but the
  weights were fitted on the same two seasons. Superseded: the stretch it
  added was mostly the absence effect, merged on 2026-10-26. The direction
  survived the absence change: on 2026-11-06 the rating weight went from 0.5
  to 0.65 (`backtest-2026-11-06-rating-weight`, below in `docs/backtest.md`).
- `exp/roster-continuity`: seeds the replay with last season's finals (within
  noise, `datasets/backtest-2026-10-25-seeded-prior`); continuity "games" mode
  improved Brier by 0.0005.
- `exp/player-impact`: adjusted plus-minus from 2024-25 ESPN player +/-
  (`datasets/espn-plus-minus-2024-25`), shrunk toward a box-score rating, as
  the team-rating prior. Gate improved against the reference; on its own,
  within noise. It cannot run live without 2025-26 plus-minus, and whole-game
  plus-minus cannot separate teammates who share minutes.

## What helped, measured (2026-10-27)

Each merged model change against the reference just before it, on the same
replayed games (`docs/backtest.md`, one "Reference" section each), ± two
clustered standard errors where the scoreboard has them. Largest first:

| # | Change | Against the version before |
|---|---|---|
| 1 | Participation probabilities weight the 240-minute budget; a DNP lowers a player's chance of playing instead of diluting his minutes when he plays (0.4.4, `backtest-2026-10-24-participation`) | points MAE 4.87 → 4.59, minutes 6.32 → 5.10, team points 10.08 → 9.73, winners 64.3% → 66.4% |
| 2 | Games the official report listed a player out of leave his participation rate (0.4.7, `backtest-2026-10-25-reported-out`) | points −0.074 ± 0.010, minutes −0.130 ± 0.021, participation Brier 0.103 → 0.086, points bias +0.50 → +0.03 |
| 3 | The margin blends the player sum half and half with a team rating (`backtest-2026-10-24-team-rating`) | margin −0.19 ± 0.12, winner Brier −0.0048 ± 0.0027; on 2024-25, which did not choose it, margin −0.62 ± 0.39 |
| 4 | Reported-out regulars lower the team rating (`backtest-2026-10-26-absence`) | margin −0.186 ± 0.108 (11.48 → 11.29), winner Brier −0.0046 ± 0.0020; gap to the close 0.58 → 0.39 |
| 5 | Opponent defense factor (production layer since 0.5.0; `docs/backtest.md`, "Production path") | switched off: team points +0.38 and winner Brier +0.0032, both worse |
| 6 | Explicit home court (0.4.3, `backtest-2026-10-24-home-court`) | home margin bias −1.66 → −0.14; margin MAE −0.08 ± 0.10 |
| 7 | Two-tailed intervals, then per-minutes-bucket tails (`backtest-2026-10-24-two-tailed`, `-minute-buckets`) | interval score −0.19 ± 0.13 and −0.20 ± 0.11; 80% coverage 76.8% → 79.6% |

Small or nothing: the reported-status factors (T−30 points −0.0011 ± 0.0007;
preliminary points −0.011 ± 0.002, minutes −0.017 ± 0.004), rest and travel
(switched off, team points +0.08), pace at strength 0.25 (nothing
measurable), the team usage budget (points −0.007 ± 0.003, team points
−0.10 ± 0.09, but team-points bias −0.43 → −0.91; shipped for the week-one
rosters the replay cannot see) and newcomer projections (minutes +0.009 ±
0.004; shipped because withholding them blocked whole margins).

One layer hurts: the minutes redistribution helps player points (switched
off, +0.031 worse) but takes about three points off each team's total
(team-points bias −0.15 without it, −3.11 with it).

Start to now, 2025-26, the same 868 games: points MAE 4.87 → 4.49, minutes
6.32 → 4.85, margin 12.25 → 11.48, winners 63.9% → 69.6%
(`backtest-2026-10-24`, model 0.4.2, against the 2025-26 row of
`backtest-2026-10-26-absence`). Part of it is the injury reports added to the
replay, an input production already had: with the model unchanged they cut
projected players who did not play from 35.7% to 15.8% and minutes by 0.10,
with points and margin within noise (`docs/backtest.md`, "Injury reports").

## Set aside, and why

These are low priority, not disproven. Each either showed no effect once the
obvious factors were controlled for, is already captured another way, or is
too small for the gate to see.

- **Travel distance.** No robust effect after controlling for team strength,
  rest and altitude: −0.27 points per 1,000 miles for the visitor (SE 0.21)
  over 2007–26, and the market prices it at about zero (RC). Two exceptions
  are kept in recommendation 9: Denver and Utah's extra home edge and home
  teams after eastward time-zone travel (about −0.9 per hour, weakly
  identified).
- **Three or more days of rest.** No benefit: −0.01 home, −0.29 visitor, SE
  about 0.4 (RC). Only the back-to-back is real, and the close prices it. The
  replay found no rest effect on team points either (field notes).
- **NBA Cup dummies.** No effect after controls (RC). Cup games other than
  the final count in the standings and belong in the training data.
- **Defense-vs-position tables.** One practitioner backtest found them
  "mostly noise" (opponent mix and switching). No out-of-sample test of
  opponent adjustments for player points was found anywhere, and EPM's whole
  context-aware model gains only 2–5% on rate stats, so the ceiling is small.
  If tried, use shrunk team-level opponent rates, not per-position tables.
- **Raw with-or-without-you splits.** On/off splits are very noisy: DPM gives
  on/off a weight of P/(P + 10,000) offensive possessions, a third at 5,000.
  The regularized version is what `exp/player-impact` builds. (The report's
  "about 1,000 possessions" understates the padding; the notes say 10,000.)
- **Bigger networks.** NBA_AI's 27M-parameter transformer was its worst
  2025-26 model (11.97 MAE, 63.3% winners), no better than its points-per-game
  baseline, while a small MLP and its ensemble did best (11.42–11.45). The
  repo is standard-library only, so this was never cheap either.
- **Clutch shooting.** Not a skill the data can see (2026-10-27,
  `scripts/research/clutch.py`, NBA clutch archive `datasets/clutch-2026-10-22`).
  Over 76 consecutive-season pairs with 40+ clutch shooting attempts in both
  (median 66 a season), true shooting correlates 0.72 year to year over the
  season but 0.19 in the clutch, and clutch minus season correlates 0.11
  (95% bootstrap −0.12 to 0.32). Its spread across player-seasons, 0.068 of
  TS%, is what chance gives on those attempts (0.067). Season efficiency, which
  the model already uses, is the best guess for a player's late-game shooting.
  And only 23–25% of games each season finished within five points.

Situational effects in general are real but small: a 2-point correction on
35% of games is worth about 0.04 points of margin MAE, z ≈ 0.9 on one season.
They need the two-season gate or a sourced-prior exception.

## What the research could not find

- A closing-line MAE for player props. The only figures are self-reported
  (a vendor's 4.75 and a hobby model's 4.62 for points); ours is 4.58 on a
  different sample. No free prop-line archive exists.
- How accurate Kalshi and Polymarket game prices are against sportsbook
  closes.
- Any out-of-sample test of how teammates' minutes and usage should be
  redistributed when a player is out.
