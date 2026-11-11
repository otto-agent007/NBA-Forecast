# Beating the close: what the evidence allows (2026-11-06)

The owner asked what it would take for the system to beat the closing line.
This page answers with measurements, not opinion: the three replayed seasons
(`datasets/backtest-2026-11-06-rating-weight`, 2,604 games with a model margin,
a closing line and a result), the live market report (91 games, 2026-10-24 to
2026-11-05) and the research notes' closing-line archive (22,694 games,
2007-08 to 2025-26). Every number below comes from
`scripts/research/close_teacher.py` (see the end for the command).
(The reference is PR #30's; on `main`'s `backtest-2026-11-06-participation` the
pooled gap is +0.39 and the slope +0.08 ± 0.08, the same picture.)

**The answer in three lines.** Beating the close on average is not a
realistic target for a public-data model, and the model's disagreements with
the close carry no usable information today. What is realistic is a ladder:
first match the close on spreads, which the close itself can teach us to do;
then take edge where the market is slow (information between the opener and
tip) or thin (totals, player props). Nothing in the record supports skipping
the first rung.

## Where the model stands

| Sample | Model margin MAE | Close | Gap | Slope of actual − close on model − close | Model's side covers |
|---|---|---|---|---|---|
| Replay, 2,604 games | 11.25 | 10.90 | +0.35 | +0.121 ± 0.080 | 50.6% |
| Replay 2023-24 (867) | 11.24 | 10.82 | +0.42 | +0.10 ± 0.14 | 50.6% |
| Replay 2024-25 (869) | 11.13 | 10.69 | +0.44 | +0.07 ± 0.12 | 50.3% |
| Replay 2025-26 (868) | 11.39 | 11.18 | +0.20 | +0.19 ± 0.16 | 50.9% |
| Live, 91 games | 12.41 | 10.41 | +2.00 | −0.35 ± 0.10 | 44.0% |

Errors are one date-clustered standard error. The slope is the test that
matters: a slope of 1 would mean the model is right and the close wrong when
they differ; 0 means the disagreement is noise. Blending the model into the
close at any weight gains nothing (10.898 at weight 0, 10.901 at 0.1, 11.25 at
1). Model and close have the same spread (SD 8.23 and 8.22) and correlate
0.91.

- **The big disagreements are input errors, not insight.** Games where the
  model and the close differ by 6+ points are 7.8% of the replay and 47% of
  the gap (model 12.24 against 10.09); the model's side covers 48.5% and the
  slope is +0.04 ± 0.11. The model leans toward the underdog in 71% of them.
  Live, the 11 such games average 20.9 points of error.
- **Late season is where the slope turns negative:** December +0.35 ± 0.21,
  January +0.32 ± 0.18, February +0.19 ± 0.17, March −0.02 ± 0.14, April
  −0.12 ± 0.20. The gap is +0.69 in April.
- **Live, both halves are worse than the close and the player sum is the
  weaker one:** player-sum half 12.80 (slope −0.40 ± 0.10), rating half 12.16
  (−0.23 ± 0.10). The live model disagrees with the close by 3.80 points a
  game against 2.69 in the replay, so live inputs produce disagreements the
  replay never sees.
- **Totals are the one live line with a positive slope:** model 14.44 against
  the line's 13.88, slope +0.21 ± 0.18, the model's side covers 52.7%. Small
  sample, worth watching.

## Can anything beat the close?

The archive answers for situations a model can know in advance. Regressing
the close's own miss (actual − close) on back-to-backs, two days off, March
and April, and late-season bottom-8 and top-4 teams by record:

| Situation (share of games) | All seasons | 2007-2015 | 2015-2022 | 2022-2026 |
|---|---|---|---|---|
| Home back-to-back (15%) | −0.13 ± 0.25 | −0.22 ± 0.36 | +0.25 ± 0.44 | −0.53 ± 0.56 |
| Away back-to-back (26%) | +0.21 ± 0.19 | +0.28 ± 0.28 | +0.12 ± 0.33 | +0.35 ± 0.51 |
| Home 3+ days (23%) | −0.14 ± 0.20 | −0.31 ± 0.30 | +0.10 ± 0.35 | −0.14 ± 0.47 |
| Mar–Apr home bottom-8 (7%) | −0.86 ± 0.38 | −1.09 ± 0.54 | −0.59 ± 0.67 | −0.73 ± 0.83 |
| Mar–Apr away bottom-8 (7%) | +0.03 ± 0.35 | −0.43 ± 0.49 | −0.18 ± 0.62 | +1.42 ± 0.79 |
| Mar–Apr home top-4 (3%) | −0.28 ± 0.49 | −0.21 ± 0.63 | −0.91 ± 0.91 | +0.65 ± 1.19 |
| Mar–Apr away top-4 (3%) | +0.80 ± 0.46 | +1.51 ± 0.66 | +1.19 ± 0.79 | −1.24 ± 1.05 |

Everything sits within about one standard error of zero except fading a
bottom-8 home team in March and April, which covers 54.2% ± 1.2 over 1,682
games. It is one of nine tests, the same team on the road shows nothing
(49.4%), and the top-4 pattern flips sign in the latest era, so it is a lead
for the live test below, not an edge. The close prices rest, schedule and
standings. (The 2023-24 closes are Betfair's and the later ones ESPN BET's or
DraftKings'; the archive's 2007-22 lines are SBRO consensus closes.)

**Where the edge actually is: time.** On 17,772 games with both an opener and
a close (2007-22) the close beats the opener by only 0.08 points of MAE, and
the average move is 1.15 points. But when a line moves 1.5+ points, a third
of games, the side it moved toward covers the opener 57.7% of the time and
the close 50.0%; actual − open regressed on close − open has slope 0.99. The
move is fully informative, and whoever knows the news first collects it. Our
night-before forecast predicts about a tenth of the move
(`docs/research-lessons-2026-10.md`, "Closing-line value"); 52.4% at the
opener, about 0.8 points of CLV a bet, is break-even.

## The close as teacher

The replay's margin miss has a standard deviation of 14.3 points. The model's
miss against the close has 3.5. Regressing close − model on features the model
can see therefore has sixteen times the power of regressing outcomes on them:
a one-point effect on a quarter of games is five standard errors against the
close and one against results. The outcomes then confirm the sign. Same
features, three targets, date-clustered errors:

| Feature | close − model | actual − model | actual − close |
|---|---|---|---|
| Home back-to-back | −1.17 ± 0.18 | −1.60 ± 0.78 | −0.43 ± 0.78 |
| Away back-to-back | +0.87 ± 0.17 | +1.38 ± 0.74 | +0.51 ± 0.74 |
| Home 3+ days | +0.10 ± 0.18 | +1.06 ± 0.66 | +0.96 ± 0.66 |
| Away 3+ days | −0.17 ± 0.20 | −0.15 ± 0.77 | +0.02 ± 0.73 |
| March–April | +0.36 ± 0.18 | +0.57 ± 0.78 | +0.21 ± 0.78 |
| Mar–Apr home bottom-8 | −1.56 ± 0.27 | −2.30 ± 1.07 | −0.73 ± 1.05 |
| Mar–Apr away bottom-8 | +0.96 ± 0.28 | +2.32 ± 0.94 | +1.37 ± 0.91 |
| Mar–Apr home top-4 | −0.64 ± 0.27 | +0.52 ± 1.36 | +1.16 ± 1.30 |
| Mar–Apr away top-4 | +0.83 ± 0.33 | −0.40 ± 1.16 | −1.23 ± 1.14 |
| 2024-25, 2025-26 (vs 2023-24) | +0.35 ± 0.16, +0.35 ± 0.17 | +0.24, +0.43 | −0.12, +0.08 |

What it says:

- **Rest.** The model has no rest term (`docs/field-notes-2026-10-24.md`
  found no team-points effect and stopped there). The close charges a home
  team on the second night about 1.2 points more than we do and an away team
  0.9, and the outcomes agree at 1.6 and 1.4. Both back-to-back terms together
  are worth only 0.02 points of margin MAE (11.251 to 11.232, in sample),
  which is why the gate can never see them and the teacher can.
- **Tanking.** In March and April the close marks a bottom-8 team down 1.6
  points at home and 1.0 away beyond the model, and the outcomes say 2.3 each
  side. The rating half is a season-average margin, so a team that quit in
  March still carries its November.
- **Rested stars.** The close also marks top-4 teams down late (0.6 to 0.8),
  but the outcomes do not confirm it, so that one stays a note.
- **Home edge by season.** The 2023-24 model margins sit 0.35 further from
  the close than the later seasons', the direction of the shrinking home
  court the research measured.
- **Schedule and standings explain only 5% of the disagreement.** The rest is
  team strength and game-day information. A third of the disagreement
  variance (37%) is a standing per-team view, SD 1.45 points per team-season.
  Neither part is informative ahead of time: each team's mean disagreement
  over its 10+ earlier games has slope +0.07 ± 0.12 on the close's later
  misses and covers 49.3% when it exceeds 1.5 points; the game-day remainder
  +0.03 ± 0.10.
- **The strength backbone is the biggest structural lever.** A ridge rating
  of the thirty teams and a home edge, fitted to nothing but *earlier* closing
  spreads of the season with a 7-day half-life, scores 11.30 on the same games
  alone, level with the whole model. Blended half and half with the model it
  scores 11.12, which is 37% of the gap with no new information. The same
  rating fitted to earlier results instead scores 11.54 (best blend 11.20), so
  the gain is the market's denoised view of team strength, not the method.
  With the close in the regression the backbone adds nothing (−0.02 ± 0.09)
  and the model +0.15 ± 0.08. Our rating half, by contrast, is a plain
  season-average margin padded with last season's, neither opponent-adjusted
  nor recency-weighted.

## What to do, in order

1. **Put the teacher on the scoreboard.** Extend `market_metrics` with the
   close − model regression on a fixed feature list, the standing-view versus
   game-day split, and the disagreement buckets with their slopes. No model
   change. Then amend `AGENTS.md`: a change with a sourced prior whose
   teacher coefficient exceeds four standard errors, and whose sign the
   outcomes confirm, passes the gate at `within_noise` on margin MAE. The
   research page already allows a "sourced-prior exception" for situational
   effects; this makes it measurable.
2. **A rest term, then a late-season standings term.** About one point per
   team on the second night of a back-to-back, applied to the rating half;
   then the tank adjustment for bottom-8 teams from March. Both are what the
   market prices and we do not.
3. **Rebuild the rating half.** Opponent-adjusted and recency-weighted, with
   earlier closing spreads as the target or part of it; expect 0.10 to 0.15
   on the gate. This is a decision for the owner: fitting the strength layer
   to market history makes it market-dependent, while the game-day layer
   stays ours. The measurement says the strength layer is the part the
   market does better, so the trade looks right.
4. **Fix live before chasing edge.** The live gap is 2.0 points, five times
   the replay's, and comes from the player-sum and participation layer the
   replay cannot see (`docs/season-operations.md`, daily reviews; PR #28's
   first week). Until live disagreements shrink to the replay's 2.7 points,
   none of the above shows up in money.
5. **Then test edge where it can exist.** Three places, each measurable:
   - A live closing-line-value test against the first captured line, placebo
     first (`scripts/research/clv.py`), after a few hundred games; the model
     has to predict about 0.8 points of the move a bet. Reacting to the
     injury report and the T−60 inactive list within a tick is the realistic
     path to that, now that captures have a budget.
   - Totals: the live slope is positive and a 25% blend beats the line on 91
     games. Measure it in the daily review before anyone acts on it.
   - Player props: the player-level model is the asset the spread market
     does not have, and the only missing piece is a line source
     (`docs/field-notes-2026-10-24.md`, open item 1).

What not to do, because the record is in: fade the market on big
disagreements (48% cover), reweight the halves further (PR #30 is the last of
it), add travel or long-rest terms, or build bigger models
(`docs/research-lessons-2026-10.md`, "Set aside").

### Follow-up on item 3 (2026-11-07)

The rating half was re-scored offline on the same 2,604 games with the
replay's own windows (`scratch/2026-11-07-rating/harness.py`; its "current"
variant reproduces the reference margins exactly, so each candidate's effect is
the exact replay effect). Results-only rebuilds do not move it: recency
weighting with a 20 to 40 day half-life +0.03 to +0.12 MAE, an opponent-adjusted
ridge rating −0.01 ± 0.01, capped margins +0.03. Season-to-date average margin
is already the efficient estimate of a constant strength; the noise is in the
results, not the estimator. The gain is in the market's strength estimate:
a ridge fit of each earlier game's closing margin on a home edge and two team
strengths (half-life 7 days, ridge 0.1), blended at 0.7 into the rating, gives
−0.090 ± 0.025 on the gate, −0.08 to −0.10 in every season, and raises the
actual-on-close slope from 0.12 to 0.19; 0.5 gives −0.075 ± 0.018, 1.0
−0.100 ± 0.036. A game's own line is never read, only lines of games already
played, so the comparison with the close stays fair. Implemented in
`margins.market_strengths` (the owner's decision is the pull request that
carries it); the rating weight itself was left at 0.65, where the harness
suggests 0.75 would add another 0.06 with wider error, to be gated separately.
The ± in this paragraph is one date-clustered standard error, the harness's
convention; the gate's own tables quote two. On the gate each season's verdict
is improved, carried by the winner Brier; the margin error alone is improved in
2024-25 (−0.102 ± 0.087) and within noise in the other two seasons (−0.083 and
−0.085, ± 0.087 and 0.088). The harness is `scripts/research/rating_harness.py`.

## Limitations

- The teacher converges the model toward the close by construction. That is
  the point for the first rung, and the reason the slope test, not the MAE
  gap, must remain the measure of edge: a model that only echoes the close
  has a slope of zero and no edge.
- The back-to-back and standings coefficients are fitted in sample on the
  replay; the archive confirms the direction over nineteen seasons for rest
  (the close prices it) but a merged term must pass the gate's paired rule as
  in item 1.
- The backbone's half-life and ridge were chosen from three settings on the
  same games; the blend weight likewise. The improvement is large relative to
  that search but a production version needs the gate.
- Live figures are 91 games in the first three weeks of a season, half of
  them before PR #28; the replay figures start at the 30% mark of each
  season. The two are not the same regime.

## Reproduce

```bash
python3 scripts/research/close_teacher.py --reference datasets/backtest-2026-11-06-rating-weight \
  --live data/operations/production/market/report.json
```

About seven seconds, standard library only. The archive section needs the
research notes' `all_games.json`
(`../research_data/NBA forecasting methods and lessons/`, outside the repo);
without it the script says so and skips that section.
