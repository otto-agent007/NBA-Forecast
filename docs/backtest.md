# Backtest scoreboard

`python -m nba_forecast backtest` replays three seasons with the current model
code and scores them. It is the gate for model changes (see `AGENTS.md`): a
change to how forecasts are computed is judged here first, on the same games
and players as the committed reference, before anything is merged or
activated.

```bash
python -m nba_forecast backtest \
  --history datasets/espn-season-2023-24/history.json.gz datasets/espn-season-2024-25/history.json.gz datasets/regular-season-expanded-2026-10-22/history.json.gz \
  --team-games datasets/espn-season-2023-24/team-games.json.gz datasets/espn-season-2024-25/team-games.json.gz datasets/model-upgrade-2026-10-23/team-games.json.gz \
  --injuries datasets/injury-reports-2023-24/statuses.json.gz datasets/injury-reports-2024-25/statuses.json.gz datasets/injury-reports-2025-26/statuses.json.gz \
  --market datasets/market-closing-2023-24/closing-lines.json.gz datasets/market-closing-2024-25/closing-lines.json.gz datasets/market-closing-2025-26/closing-lines.json.gz \
  --output /tmp/backtest-$(date +%Y%m%d-%H%M) \
  --reference datasets/backtest-2026-10-26-absence
```

Each `--history`/`--team-games` pair is one season, replayed on its own from its
own box scores; `--injuries` and `--market` take one file per season in the same
order. The seasons' rows are pooled into one set of metrics and one comparison
(game dates never overlap, so the date clusters stay independent), and each
season also reports its own metrics, market comparison and verdict under
`seasons`. The gate is the pooled verdict; a season-level `worse` under a pooled
pass must be explained. It takes about eleven minutes. `--limit N` replays only
the last N evaluation games of each season (CI uses 400); `--json` prints the
full scoreboard. The exit code is 3 when the pooled verdict is `worse`.

## What it does

Games are taken in order. The first 30% of game dates are history only; every
game after that is forecast with the ordinary baseline model from the box
scores of earlier Eastern dates, never the same day. The roster is the box
score's own players, active and inactive, each with the status the official
injury report gave them (see "Injury reports" below); without `--injuries` every
player is available, as before October 2026. Player metrics use projected
players who actually played with positive minutes, the same population the
prospective interval calibration uses. Team points compare the projected team
sum with the regulation-equivalent final score; margins and winners use the
final score.

Output: `scoreboard.json` (metrics, parameters, code and data hashes) and
`rows.json.gz` (every projected player-game and team-game). With
`--reference`, the same player-games, teams and games are paired. For points,
minutes, team points and margin the mean change in absolute error is reported
with its standard error; `improved` and `worse` mean the change exceeds two
standard errors, otherwise `within_noise`. The standard errors are clustered,
because errors are not independent: player-games in one game share overtime,
blowouts and the 240-minute budget, a role change follows a player all season,
and a date shares its news. Player rows are two-way clustered by Eastern date
and player, team rows by date and team, game rows by date (Cameron, Gelbach and
Miller), and a standard error is never taken below the independent one. Each
comparison reports `design_effect`, the variance ratio to independence. On the
newcomers change it was 3.47 for minutes, 2.04 for team points and 1.07 for
points; under independence a design effect of 3.47 makes the two-standard-error
rule fire on pure noise about 28% of the time instead of 5%. Re-scoring every
committed reference with clustering changed one verdict: the usage budget's
points bias (`worse` → `within_noise`). Signed bias is scored separately for points,
team points and margin: a bias more than two standard errors from zero that
shrinks by at least one standard error is `improved`; one that appears or grows
by at least one standard error is `worse`. Bias matters on
its own for pricing, and a bias fix barely moves absolute error when game
noise is fifteen points. The overall verdict is `worse` if any metric is
worse, else `improved` if any is improved.

`--market datasets/market-closing-2025-26/closing-lines.json.gz` adds
`metrics.market`: on exactly the replayed games that have a closing line, the
model, the closing line and a walk-forward point-differential rating (home edge
plus the season-to-date average margin difference, earlier dates only, ten games
per team) side by side — margin MAE, winner accuracy and Brier (the close's
moneylines de-vigged proportionally) — plus the team points the close implies,
scored on the same regulation-equivalent target as the model. The encompassing
slope regresses the close's miss on the model's disagreement with it (Fair and
Shiller); a slope two standard errors above zero means the model knows something
the close did not. These are reported, not gated. The lines come from ESPN's core
odds endpoint by event ID (`python -m nba_forecast.market_history`), archived
with hashes; ESPN's game summary drops its pickcenter once a game is final.

## Injury reports

`--injuries` gives each game the newest official injury report published by its
T-minus-30 checkpoint, the report a live final-stage forecast reads
(`nba_forecast/injury_history.py`; `datasets/injury-reports-*/`). Reports were
hourly until 2025-12-21 (named `..._07PM.pdf`, published at half past) and every
15 minutes from 2025-12-22; an absent slot falls back to the previous one, up to
three hours. The production parser joins each row to the season's box-score
players. A listed player keeps the report's status: out means no minutes;
probable, questionable and doubtful scale the participation probability
(`model.STATUS_PARTICIPATION`). A player the report omits is available. A player
it lists but the box score omits (ESPN drops some inactives) is added and scored
as not playing. Every game in the three seasons has a report: 2025-26 3 minutes
old on average at the checkpoint, 2024-25 10, 2023-24 9.5.

At the checkpoint almost every tag is resolved: of 21,682 players listed out
over 2024-25 and 2025-26 two played (2023-24: 3 of 9,723), and only 382
(2024-25) and 110 (2025-26) are still probable, questionable or doubtful (2023-24:
426). Over 2024-25 and 2025-26 those played 91%
(probable, 142), 56% (questionable, 311) and 3% (doubtful, 39). The status
factors are fitted where most tags are, the midday reports (see "Preliminary
replay" and the status-rates reference).

What the reports change, same model, 2025-26, against the minute-buckets
reference (`datasets/backtest-2026-10-25-two-seasons/scoreboard.json`,
`comparison`):

- Projected players who did not play: 35.7% → 15.8%, the live rate (about 14%).
- Minutes −0.10 ± 0.07, participation Brier −0.016 ± 0.003 (0.153 → 0.104),
  interval score −0.50 ± 0.14: improved. Points and margin within noise.
- Player points bias −0.39 → +0.57 and team points bias −0.92 → −1.91: worse.
  Once outs are known, players who play are over-projected while teams come
  out low: mean P(plays) 0.78 against 0.84 who played. The participation rate
  still counted a player's injury absences against him, so a player back from
  injury was down-weighted twice; the reported-out reference fixes that.

The replay also now takes team points and the margin's player sum from the
model's own `game_totals` (schema 4: the weighted player sum plus unallocated
minutes at the replacement rate), as production does; it used to re-sum player
points. Without injury reports that changes nothing (team points +0.0007 ±
0.0208); with them it is 1.6 points of the old team-points bias.

## Preliminary replay

The same command with the midday reports replays the preliminary forecasts,
made hours before tip while most tags are open. A change to availability,
participation or reported statuses must also pass it:

```bash
python -m nba_forecast backtest \
  --history datasets/espn-season-2023-24/history.json.gz datasets/espn-season-2024-25/history.json.gz datasets/regular-season-expanded-2026-10-22/history.json.gz \
  --team-games datasets/espn-season-2023-24/team-games.json.gz datasets/espn-season-2024-25/team-games.json.gz datasets/model-upgrade-2026-10-23/team-games.json.gz \
  --injuries datasets/injury-reports-2023-24-midday/statuses.json.gz datasets/injury-reports-2024-25-midday/statuses.json.gz datasets/injury-reports-2025-26-midday/statuses.json.gz \
  --market datasets/market-closing-2023-24/closing-lines.json.gz datasets/market-closing-2024-25/closing-lines.json.gz datasets/market-closing-2025-26/closing-lines.json.gz \
  --output /tmp/backtest-midday-$(date +%Y%m%d-%H%M) \
  --reference datasets/backtest-2026-10-26-absence-midday
```

Each game takes the newest report by 12:30 PM Eastern on game day
(`injury_history --anchor 12:30`; `datasets/injury-reports-*-midday/`). At midday
2,852 (2024-25) and 2,684 (2025-26) players are probable, questionable or
doubtful, against 382 and 110 at the T-minus-30 checkpoint. Over both seasons
they played 89.4% (probable, 1,199), 48.6% (questionable, 3,687) and 3.2%
(doubtful, 650); 0.4% of the 15,112 listed out played. Midday metrics, model
0.4.8: points MAE 4.59, minutes 5.03, participation Brier 0.119, projected
players who did not play 21.9%, margin 11.53.

## Two seasons

The second season, 2024-25 (`datasets/espn-season-2024-25/`), comes from ESPN game
summaries because the NBA edge took four to six minutes per request from this
host in October 2026. Minutes are whole minutes and IDs are ESPN's; see that
dataset's README. 1,229 of the 1,230 counted games are present (one ESPN box
score's shot totals do not add up), 869 are evaluated.

It is out of sample for every change made before it existed. Replaying it with
the code before and after each 2026-10-24 change, without injury reports:

| Change | 2025-26 (where it was chosen) | 2024-25 |
|---|---|---|
| Margin team-rating blend | margin −0.19 ± 0.12, winner Brier −0.0048 ± 0.0027: improved | margin −0.62 ± 0.39, winner Brier −0.0066 ± 0.0050: improved |
| Two-tailed walk-forward interval | interval score −0.19 ± 0.13: improved | +0.015 ± 0.032: within noise |
| Per-minutes-bucket tails | interval score −0.20 ± 0.11: improved | −0.08 ± 0.07: improved |

On 2024-25 the model's margin MAE is 11.37 against the closing line's 10.69
(ESPN BET for every game) and the naive point-differential rating's 11.50; winner
Brier 0.2109 against 0.1964. The gap to the close, 0.68 points, matches 2025-26's.

## Reference: 2026-10-26 (absences in the team rating)

`datasets/backtest-2026-10-26-absence/` (T-minus-30) and
`datasets/backtest-2026-10-26-absence-midday/` (preliminary): player model
`regular-season-baseline-0.4.8` unchanged, margin method
`..._team_rating_absence_blend_discrete_normal_v3`. The team-rating half of the
margin loses 0.1 points per minute a game of each reported-out regular: a
player averaging 15 or more minutes for the team this season who played one of
its last ten games (`model._absence`, `margins.absence_adjustment`). The player
sum already lost those players; the season-to-date rating, earned with them,
did not. On the three-season reference the blended margin missed by 0.49
points (standard error 0.06) per ten such minutes (home minus away), the close
by 0.17, about
equally in each season; fitted on two seasons and scored on the third, a
correction of that size cut margin MAE in all three. 0.1 is the round value
(the rating carries half the location). Rerunning the player model with the
absentees restored and moving the rating by the change in the player sum was
worse instead (margin +0.13 ± 0.09); why was not investigated.

T-minus-30, against the three-season reference (± two standard errors): margin
−0.186 ± 0.108 (11.48 → 11.29), winner Brier −0.0046 ± 0.0020 (0.2101 →
0.2055): improved; player, team-point and interval metrics unchanged. Every
season improves:

| Season | Margin MAE | Close | Gap | Winner Brier | Encompassing slope |
|---|---|---|---|---|---|
| 2023-24 | 11.40 → 11.26 | 10.82 | 0.44 | 0.213 → 0.209 | −0.06 → +0.09 ± 0.14 |
| 2024-25 | 11.27 → 11.13 | 10.69 | 0.44 | 0.210 → 0.205 | −0.08 → +0.02 ± 0.13 |
| 2025-26 | 11.76 → 11.48 | 11.18 | 0.30 | 0.207 → 0.203 | −0.09 → +0.13 ± 0.13 |

The encompassing slope (one standard error shown) is now above zero in every
season, though not by two standard errors. Midday: winner Brier −0.0018 ±
0.0016, improved; margin −0.067 ± 0.077 within noise, smaller because
questionable and doubtful players are not counted as absent. Not measured: the
first 30% of each season's dates are history only, so absences in the first
weeks are not replayed. A regular out since opening night has no minutes for
the team yet and is not counted, while the early rating is mostly last
season's, which he helped earn.

## Reference: 2026-10-26 (three seasons)

`datasets/backtest-2026-10-26-three-seasons/` (T-minus-30) and
`datasets/backtest-2026-10-26-three-seasons-midday/` (preliminary): model
`regular-season-baseline-0.4.8`, unchanged, with 2023-24 added as the first
season. 2023-24 is the first season under the Player Participation Policy and
the 65-game awards minimum, like the two already replayed. Its box scores come
from ESPN summaries (`datasets/espn-season-2023-24/`, the In-Season Tournament
final excluded), its closing lines from Betfair Sportsbook
(`datasets/market-closing-2023-24/`; half its spreads are whole numbers) and its
injury reports from the league's hourly PDFs (`datasets/injury-reports-2023-24*/`).
The 2024-25 and 2025-26 rows are identical to the status-rates references (every
paired change is exactly zero), so the old references carry over unchanged.

| Season (T-minus-30) | Points MAE | Minutes MAE | Margin MAE | Close | Naive rating | Winners | Winner Brier | Team points bias |
|---|---|---|---|---|---|---|---|---|
| 2023-24 (867 games) | 4.47 | 4.83 | 11.40 | 10.82 | 11.47 | 65.4% | 0.213 | −0.62 |
| 2024-25 (869 games) | 4.53 | 4.83 | 11.27 | 10.69 | 11.50 | 67.4% | 0.210 | −1.34 |
| 2025-26 (868 games) | 4.49 | 4.85 | 11.76 | 11.18 | 11.90 | 69.7% | 0.207 | −1.74 |

Pooled over 2,604 games and 55,332 played player-games: points MAE 4.50 (bias
+0.06), minutes 4.84, 80% coverage 80.0% (interval score 19.69), participation
Brier 0.088, team points 9.45 (bias −1.23), margin 11.48, winners 67.5%, winner
Brier 0.2101. Midday: points 4.56, minutes 5.00, participation Brier 0.116,
margin 11.51; 2023-24 alone 4.52, 4.94 and 11.46. The gap to the close is 0.58
in every season. 2023-24 is the model's weakest against the naive rating (0.07
of margin MAE, winner Brier tied), and the team-points shortfall grows season by
season. The third season narrows the pooled standard errors by about a fifth.

## Reference: 2026-10-25 (status rates)

`datasets/backtest-2026-10-25-status-rates/` (T-minus-30) and
`datasets/backtest-2026-10-25-midday/` (preliminary): model
`regular-season-baseline-0.4.8`. The reported-status factors on the role rate
(`model.STATUS_PARTICIPATION`) are fitted on each season's midday replay rows
(a tagged player's probability is role rate × factor, so the least-squares
factor is Σ role·played / Σ role²) and checked on the other season: probable
1.11 / 1.13, questionable .63 / .65, doubtful .040 / .048 for 2024-25 / 2025-26.
Rounded to 1.0 (probable plays like an untagged player), .64 and .04 from .9,
.55 and .15, every status's Brier improves on both held-out seasons (2025-26:
probable 0.146 → 0.125, questionable 0.258 → 0.253, doubtful 0.032 → 0.028).

Midday, against model 0.4.7: points −0.011 ± 0.002, minutes −0.017 ± 0.004,
participation Brier −0.0008 ± 0.0004, points bias +0.21 → +0.14: improved; the
rest within noise; both seasons improve. T-minus-30, against the reported-out
reference: points −0.0011 ± 0.0007 and participation −0.0001 ± 0.0001 improved,
the rest within noise (few tags remain at the checkpoint).

## Reference: 2026-10-25 (reported-out games)

`datasets/backtest-2026-10-25-reported-out/`: model `regular-season-baseline-0.4.7`.
The participation rate no longer counts games a player's official report listed
him out of (`model._participation`, `unavailable`): the current report already
says whether he is out, so the rate is his chance of playing when available.
The replay passes each player's earlier reported-out games in
`snapshot['reported_unavailable']`; live ticks build the same map from each
earlier game's final-stage checkpoint (`injury_capture.reported_unavailable`).

Against the two-season reference, pooled: points −0.074 ± 0.010, minutes −0.130
± 0.021, participation Brier −0.0174 ± 0.0018 (0.103 → 0.086), interval score
−0.054 ± 0.044, points bias +0.50 → +0.03: improved; team points, margin and
winner within noise (team points bias −1.66 → −1.54). Both seasons improve on
their own. Points MAE 4.58 → 4.51, minutes 4.97 → 4.84; mean P(plays) 0.82
against 0.84 who played.

Tried and not merged: role priors measured on available games only (under 10
minutes .52, 10–20 .82, 20–28 .98, 28+ .99 in 2024-25; 2025-26 within .01 of
those, against the current .18/.70/.79/.82). Mean P(plays) rose to 0.85, but the
240-minute budget then cut every player's conditional minutes: minutes +0.045 ±
0.015, interval score +0.059 ± 0.036 and points bias +0.03 → −0.24, all worse,
while team points bias barely moved (−1.54 → −1.50). The remaining team-points
shortfall is not in the participation probabilities.

## Reference: 2026-10-25 (two seasons, injury reports)

`datasets/backtest-2026-10-25-two-seasons/`: model `regular-season-baseline-0.4.6`
(the minute-buckets model, unchanged) on 2024-25 and 2025-26 with injury reports
and production team totals. 1,737 games, 36,923 played player-games. Pooled:
points MAE 4.58 (bias +0.50), minutes 4.97, 80% coverage 80.0% (interval score
19.84), participation Brier 0.103, team points 9.52 (bias −1.66), margin 11.51,
winners 68.6%, winner Brier 0.2081.

| Season | Points MAE | Minutes MAE | Margin MAE | Winners | Closing line margin MAE |
|---|---|---|---|---|---|
| 2024-25 (869 games) | 4.59 | 4.93 | 11.26 | 67.3% | 10.69 |
| 2025-26 (868 games) | 4.58 | 5.01 | 11.75 | 69.8% | 11.18 |

Its comparison with the minute-buckets reference covers 2025-26 only (the old
reference has no 2024-25 rows) and reads `worse` on the two biases above: that
is the replay seeing what live runs see, not a model change. The model was not
changed, so it keeps its version and key.

## Reference: 2026-10-24 (minute buckets)

`datasets/backtest-2026-10-24-minute-buckets/`: the two-tailed reference with
group-conditional (Mondrian) tails: once an expected-minutes bucket (0-10,
10-20, 20-30, 30+) has 200 earlier outcomes it gets its own two-tailed
quantiles, else the pooled ones apply (`calibration.minute_bucket`). The live
artifact records the same `minute_buckets`; on 2026-10-24 none had 200 outcomes,
and the pooled live tails were −1.65 and +2.10 sd (absolute 1.73).

Coverage 79.6% (above 11.0%, below 9.4%), width 14.4; under 10 minutes 12.5% /
7.3%, 20-30 and 30+ about 10% each side. Interval score −0.20 ± 0.11 (design
effect 3.13, improved); every other metric unchanged.

## Reference: 2026-10-24 (two-tailed intervals)

`datasets/backtest-2026-10-24-two-tailed/`: the team-rating reference with the
replay's 80% interval calibrated walk-forward the way production calibrates it.
Once 200 played outcomes from 20 earlier games exist, the heuristic ±1.70 sd is
replaced by conformal quantiles of the earlier signed standardized residuals,
each tail at 10% (`calibration.two_tailed_quantiles`); production's prospective
artifact now fits the same two tails. Points are right-skewed, so a symmetric
width over-covered the low side (8.2% below) while missing high outcomes (15.0%
above).

Coverage 76.8% → 79.0% (above 11.3%, below 9.7%), mean width 13.0 → 14.1
points; the 80% interval score (Winkler: width plus ten times any miss, a proper
score that is now gated) improved −0.19 ± 0.13 (design effect 3.30); every other
metric unchanged. By expected minutes the tails still differ (under 10 minutes:
17.8% above, none below; 30+: 7.3% above, 13.8% below), which per-bucket
calibration would address next.

## Reference: 2026-10-24 (team rating)

`datasets/backtest-2026-10-24-team-rating/`: the newcomers baseline with the
margin location blended half and half with a team rating (`margins.team_ratings`):
each team's season-to-date average final margin padded with ten pseudo-games of
0.6 times last season's average, plus the window's home edge. Team totals and
player projections are unchanged; the margin forecast's location (and so its
winner probability) moves, and the gate now scores margins from the game rows'
own location. The weights are round values from the research, not fitted on
this season.

Margin MAE 11.87, winner accuracy 69.5%, winner Brier 0.2096; every other
metric as the newcomers reference. Against it: margin −0.19 ± 0.12 and winner
Brier −0.0048 ± 0.0027, both **improved**, every other metric within noise. The
blend helps in every month (April 13.90 → 13.50). The closing line on the same
games: 11.18 and 0.1928. Margins remain compressed (slope of actual on projected
1.35); a scale fitted on this one season is not the next step — a second season is.
On the six week-one 2026-27 games with captured lines, the blend would have cut
the live margin error from 16.4 to 10.7 points (market 5.2).

## Reference: 2026-10-24 (newcomers)

`datasets/backtest-2026-10-24-newcomers/`: model `regular-season-baseline-0.4.6`
baseline path, 868 games, 18,598 played player-games. A player listed by a new
team without a positive-minute appearance for it yet is projected
conditionally on playing from his earlier role instead of withheld (flag
`new_team_role_unobserved`); early-season listings count toward the
participation rate from the first one.

Points MAE 4.58 (bias −0.39), minutes MAE 5.11, coverage 76.8%, winner Brier
0.214, participation Brier 0.153, team points MAE 9.63 (bias −0.92), margin
MAE 12.07, winner accuracy 68.3%.

Against the team-usage reference the gate said **worse** on one metric,
minutes MAE +0.009 ± 0.004 (0.2% of 5.10), with every other metric within
noise; the second documented exception. The 187 such newcomer rows in the
replay played 7% of the time (mean probability 0.15) and, when they played,
their conditional minutes were right (13.1 projected, 13.6 actual); the
minutes change is the small share of the 240-minute budget their expected
minutes now take from teammates. The policy shipped because withholding them
blocked whole team totals and margins (Philadelphia on 2026-10-24: three such
players, no margin forecast) for a reason the data does not support.

## Reference: 2026-10-24 (team usage)

`datasets/backtest-2026-10-24-usage/`: model `regular-season-baseline-0.4.5`
baseline path, 868 games, 18,548 played player-games. Each team's expected
possessions used are held to its own usage level (see season-operations.md,
"Team possession budget").

| Players by projected minutes | n | points MAE | bias | minutes MAE | 80% coverage | width |
|---|---|---|---|---|---|---|
| all | 18,548 | 4.58 | −0.36 | 5.10 | 76.9% | 13.1 |
| 0–10 | 2,352 | 2.99 | −1.05 | 5.73 | 79.0% | 6.2 |
| 10–20 | 5,218 | 4.06 | −0.88 | 6.04 | 76.6% | 10.7 |
| 20–30 | 6,676 | 4.80 | −0.26 | 4.77 | 76.3% | 14.4 |
| 30+ | 4,302 | 5.76 | +0.49 | 4.14 | 77.2% | 17.6 |

Winner Brier 0.214, log loss 4.148. Participation: Brier 0.153. Teams: points
MAE 9.63, bias −0.91. Games: margin MAE 12.06, home bias −0.27, winner
accuracy 68.3%.

Against the participation reference the gate said **worse**, and the change
shipped anyway; this is the one documented exception. Every error metric
moved the right way (points MAE −0.007 ± 0.003, team points MAE −0.10 ± 0.09,
winner Brier −0.0037 ± 0.0036, margin MAE −0.10 within noise, winner accuracy
66.4% → 68.3%), while two signed biases grew by about one standard error:
points −0.32 → −0.36 and team points −0.43 → −0.91. The replay's summed
player usage was 0.7% above the regulation usage teams actually recorded, so
the budget removes about that much; the points bias moves because the model's
points per possession used were already slightly low. The live evidence that
decided it: in the first week of 2026-27 the stacked rosters projected 10–14%
above any usage they had shown, the eight team-games projected more than 5%
above their actual usage missed high by ten points, and on 2026-10-24 the
model's margins ran about twice the market's on every game (mean absolute gap
11 points, 4 after the budget). The replay cannot show this: it skips the
first 30% of the season and its rosters change little mid-season, which is
when rates learned in other roles stop adding up.

## Reference: 2026-10-24 (participation)

`datasets/backtest-2026-10-24-participation/`: model `regular-season-baseline-0.4.4`
baseline path, 868 games, 18,548 played player-games.

| Players by projected minutes | n | points MAE | bias | minutes MAE | 80% coverage | width |
|---|---|---|---|---|---|---|
| all | 18,548 | 4.59 | −0.32 | 5.10 | 77.0% | 13.1 |
| 0–10 | 2,352 | 2.99 | −1.04 | 5.73 | 79.0% | 6.2 |
| 10–20 | 5,218 | 4.06 | −0.86 | 6.04 | 76.6% | 10.8 |
| 20–30 | 6,676 | 4.81 | −0.23 | 4.77 | 76.3% | 14.4 |
| 30+ | 4,302 | 5.77 | +0.59 | 4.14 | 77.3% | 17.7 |

Winner Brier 0.218 (see Margin probabilities below).
Participation: Brier 0.153 (constant rate 0.228; assume everyone plays 0.352);
mean probability 0.65, played share 0.65. Teams: points MAE 9.73, bias −0.43.
Games: margin MAE 12.16, home bias −0.19, winner accuracy 66.4%.

Against the previous reference (`datasets/backtest-2026-10-24-home-court/`,
model 0.4.3: points MAE 4.87, minutes MAE 6.32, team points MAE 10.08 with
bias −1.72, margin MAE 12.17, winner accuracy 64.3%), the participation model
improved points error by 0.28, minutes error by 1.22, team points error by
0.35 and both biases, with margin error within noise. Two things did it:
participation probabilities weight the 240-minute budget, and current-season
DNPs no longer dilute a player's minutes when he plays (they lower his
probability of playing instead). A team's probabilities are scaled into the
range the rules and the data allow: at most 13 (the active list) and, with ten
or more projected players, at least ten expected. Expected minutes below 240 raise supported
players' conditional minutes by at most 10% (protected rotation) or 25%
(bench); larger fills over-projected starters, no fill left team totals short.

Earlier references: `datasets/backtest-2026-10-24-usage/` (model 0.4.5),
`datasets/backtest-2026-10-24-participation/` (model 0.4.4),
`datasets/backtest-2026-10-24-home-court/` (home court), `datasets/backtest-2026-10-24/` (model 0.4.2).

## Margin probabilities

Since 2026-10-24 the scoreboard also scores the production margin distribution:
a discrete normal centred on the projected margin (home minus away p-weighted
team points) whose scale is the paired-final outcome dispersion of earlier game
days, exactly as `margins.prepare_margin_inputs` builds it live (once 100
finals are known). It reports the winner Brier score and log loss of the actual
margin, a reliability table of the home-win probability by decile, and the
location bias and scale factor that would maximise the likelihood of the actual
margins on the same bounds the live prospective calibration uses (±8 points,
scale 0.7–1.4), together with a chronological holdout (fit on the first half,
scored on the second). The gate pairs the winner Brier by game like the other
metrics; a reference saved without margin rows is scored with the current
run's prior scale.

On the 2026-10-24 participation reference (868 games): winner Brier 0.218, log
loss 4.157, mean prior sd 15.7 against a residual sd of 15.4, best correction
location +0.00 and scale 0.98 with a holdout log loss gain of 0.0004. The
uncalibrated distribution is calibrated to within noise, so no replay-fitted
correction ships; the live prospective calibration remains the mechanism once
it has 64 training and 32 validation games. The one visible pattern is in the
0.5–0.6 bins, where home teams the model makes 55–65% favourites won 63–70%
(about two standard errors), consistent with a slightly small home edge; the
replay's margin home bias is −0.19 points. Rest was checked at the same time:
team points show no rest effect (back-to-back +0.39, two days +0.39, three or
more +0.59 points of actual minus projected), and margins only a one-standard-
error hint (home on a back-to-back −1.8 ± 1.6), so no rest term was added.

## Reading the numbers

- **Projected players who did not play: 15.8% of projections** with injury
  reports, the live rate; 35.7% without them, when out players drew minutes.
- **Participation** is scored on every projected row, played or not: the Brier
  score of the participation probability against actually playing, beside two
  references, a constant rate and "assume everyone plays". With injury reports
  it measures the recent-participation model and the reported statuses together.
- **Coverage** is measured at the heuristic half-width (1.70 sd). The nominal
  80% interval covers 76% here and 78–79% for players projected at 10+ minutes.
- **Home court** is a symmetric factor (home up, away down by the same
  ratio) estimated from flagged complete games before the cutoff and shrunk
  toward a 2025-26 prior; see `docs/design.md`. The replay flags history rows
  from the reconciled team games, so the estimate adapts during the season.

## What it cannot measure

The replay reads one report per game (T-minus-30, or 12:30 PM for the
preliminary replay), not every edition a live run sees. No prior-season
aggregate priors or archive-bound enhancements (pace, defense, rest, shot
location, injury exits) are applied, so changes to those still need the live
`performance.json` benchmarks. Each season replays without the previous one's
box scores (2024-25 uses ESPN IDs), so the first weeks lack prior-season form.
A result here is necessary, not sufficient: a change that improves the replay is
expected to show up live after enough games, and both are reported.

## Updating the reference

When a change is merged, run the full backtest, commit its two files as
`datasets/backtest-<date>/`, and point `AGENTS.md` and the CI step at it. The
old reference stays in the repository.
