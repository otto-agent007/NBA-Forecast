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
  the market's 8.35.)
- The margin σ is fitted from paired finals (about 15.6), not assumed at 12.
- Role minutes already decay at 0.70–0.75 per game, close to DARKO's.

## The ranked changes and where they stand (2026-10-25)

| # | Change | Status |
|---|---|---|
| 1 | Cluster the gate's standard errors | Done: two-way, date × player. No materiality floor yet |
| 2 | Market and naive baselines on the scoreboard, encompassing test | Done: `market_metrics` |
| 3 | Official injury reports in the replay, second season | Done: 2024-25 and 2025-26 with T−30 reports |
| 4 | Blend the player-sum margin with a team rating, preseason prior | Done: 50/50, prior 10 games at 0.6 × last season; the rating loses 0.1 per minute of reported-out regulars (2026-10-26). Reweighting and roster continuity tested on branches (below) |
| 5 | Calibrate the interval's two tails separately | Done: `calibration.two_tailed_quantiles` |
| 6 | Measured status play rates; return-from-absence minutes ramp | Rates done: factors on the role rate fitted on 12:30 PM ET reports (questionable .64, doubtful .04); raw play rates 89/49/3%, close to the outside source's 87/48/4%. Ramp open |
| 7 | Market composite as a separate, labeled output | Open: a product decision; it would make the edge report echo the price |
| 8 | Blowout minutes haircut | Open |
| 9 | Situational priors (back-to-back, 3-in-4, altitude, eastward travel) | Open: `context_effects` exists but only the research path uses it |
| 10 | Player-impact ratings (box prior, then RAPM) | In progress: `exp/player-impact` |
| 11 | A ~1:15 PM PT injury capture | Open: captures are 9 AM PT and T−30 |

Margin experiments of 2026-10-25, all on branches in the `repo-gate` worktree,
none merged:

- `exp/margin-reweight`: 0.4 × player sum + 0.8 × team rating. Gate
  improved (winner Brier −0.0028 ± 0.0016, margin within noise), but the
  weights were fitted on the same two seasons.
- `exp/roster-continuity`: seeds the replay with last season's finals (within
  noise, `datasets/backtest-2026-10-25-seeded-prior`); continuity "games" mode
  improved Brier by 0.0005.
- `exp/player-impact`: adjusted plus-minus from 2024-25 ESPN player +/-
  (`datasets/espn-plus-minus-2024-25`), shrunk toward a box-score rating, as
  the team-rating prior. Gate improved against the reference; on its own,
  within noise. It cannot run live without 2025-26 plus-minus, and whole-game
  plus-minus cannot separate teammates who share minutes.

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
