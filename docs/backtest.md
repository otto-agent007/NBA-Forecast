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
  --reference datasets/backtest-2026-11-06-rating-weight
```

Each `--history`/`--team-games` pair is one season, replayed on its own from its
own box scores; `--injuries` and `--market` take one file per season in the same
order. The seasons' rows are pooled into one set of metrics and one comparison
(game dates never overlap, so the date clusters stay independent), and each
season also reports its own metrics, market comparison and verdict under
`seasons`. The gate is the pooled verdict; a season-level `worse` under a pooled
pass must be explained. It takes about eleven minutes. `--limit N` replays only
the last N evaluation games of each season (CI uses 400); `--json` prints the
full scoreboard. The exit code is 3 when the pooled verdict is `worse`. A
limited replay reports the 80% interval score but does not gate on it: the
walk-forward interval calibration starts cold at the first replayed game where
the full reference's was warm, so on identical code the two differ there (CI on
2026-10-26 read +0.017 ± 0.028 with every other metric exactly zero). The full
replay gates it.

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
noise is fifteen points. A change also has to be material: `improved` and `worse` need the
mean paired change to exceed a floor of 0.5% of the reference level
(`backtest.MATERIALITY`) as well as two standard errors, because two standard
errors on 55,000 player-games resolve effects of a few hundredths of a percent
and ten metrics are gated at once. On the committed references the floor would
have changed two verdicts: the newcomers change's `worse` on minutes
(+0.009, 0.2%) and the status-rates change's `improved` on T-minus-30 points
(−0.0011, 0.02%), both to `within_noise`; every merged change's headline
improvement clears it. The overall verdict is `worse` if any metric is
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

`metrics.market.close_teacher` adds the November 6 study's descriptive teacher
regressions, disagreement buckets, and persistent-team versus game-day split.
It is also computed for the pooled seasons; its uncertainty definition is kept
separate from the legacy encompassing metric and accuracy gates. See
[Closing-line diagnostics](close-teacher-diagnostics.md) for reproduction,
source grouping limitations, and dashboard output.

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
  --reference datasets/backtest-2026-11-06-rating-weight-midday
```

Each game takes the newest report by 12:30 PM Eastern on game day
(`injury_history --anchor 12:30`; `datasets/injury-reports-*-midday/`). At midday
2,852 (2024-25) and 2,684 (2025-26) players are probable, questionable or
doubtful, against 382 and 110 at the T-minus-30 checkpoint. Over both seasons
they played 89.4% (probable, 1,199), 48.6% (questionable, 3,687) and 3.2%
(doubtful, 650); 0.4% of the 15,112 listed out played. Midday metrics, model
0.4.8: points MAE 4.59, minutes 5.03, participation Brier 0.119, projected
players who did not play 21.9%, margin 11.53.

## Early-season replay

The gate keeps each season's first 30% of dates as history because a season
replays alone. Live forecasts never start alone: opening night reads last
season's box scores and the team ratings carry last season's prior.
`--previous-history` and `--previous-team-games` (one per season) seed the
replay the same way: the player history, the ratings' prior and the margin
dispersion start from the previous season, and `--warmup-fraction 0` scores the
season from opening night:

```bash
python -m nba_forecast backtest \
  --history datasets/espn-season-2023-24/history.json.gz datasets/espn-season-2024-25/history.json.gz datasets/regular-season-expanded-2026-10-22/history.json.gz \
  --team-games datasets/espn-season-2023-24/team-games.json.gz datasets/espn-season-2024-25/team-games.json.gz datasets/model-upgrade-2026-10-23/team-games.json.gz \
  --injuries datasets/injury-reports-2023-24/statuses.json.gz datasets/injury-reports-2024-25/statuses.json.gz datasets/injury-reports-2025-26/statuses.json.gz \
  --market datasets/market-closing-2023-24/closing-lines.json.gz datasets/market-closing-2024-25/closing-lines.json.gz datasets/market-closing-2025-26/closing-lines.json.gz \
  --previous-history datasets/espn-season-2022-23/history.json.gz datasets/espn-season-2023-24/history.json.gz datasets/espn-season-2024-25/history-nba-ids.json.gz \
  --previous-team-games datasets/espn-season-2022-23/team-games.json.gz datasets/espn-season-2023-24/team-games.json.gz datasets/espn-season-2024-25/team-games.json.gz \
  --warmup-fraction 0 --output /tmp/backtest-early-$(date +%Y%m%d-%H%M) \
  --reference datasets/backtest-2026-11-06-early-season
python3 scripts/research/early_season.py --run /tmp/backtest-early-<stamp>
```

2025-26 uses NBA player IDs and 2024-25 ESPN's, so its seed is 2024-25 under
2025-26's IDs, matched by name (`scripts/research/previous_ids.py`; 28,888 of
32,554 rows; the rest are players who left the league). Without the two flags
the replay is unchanged: on 2026-10-27 the gate reproduced every row of
`datasets/backtest-2026-10-26-absence`.

Reference `datasets/backtest-2026-10-27-early-season/`, 2026-10-27, model 0.4.8
baseline path, 3,676 games; the 3,674 with T-minus-30 reports and closing lines
(the gate's closes) by window:

| Window | Games | Model margin MAE | Close | Gap | Team-points bias |
|---|---|---|---|---|---|
| Weeks 1–2 | 270 | 10.71 | 10.33 | +0.38 | −0.96 |
| Weeks 3–4 | 313 | 9.74 | 9.35 | +0.39 | −0.87 |
| Weeks 5–8 | 470 | 10.41 | 10.29 | +0.12 | −0.72 |
| Gate window | 2,621 | 11.23 | 10.90 | +0.33 | −0.85 |

The reference was rerun after team totals were reconciled to the margin
(reference 2026-10-27, team totals agree with the margin): margins and every
bias are identical, so the table stands; team points MAE −0.102 ± 0.040.

The replayed model is no worse against the close early than late. Seeding also
moves the gate window: against the reference (± two standard errors, as the
gate prints), 2023-24 and 2024-25 together improve on margin (−0.086 ± 0.075),
winner Brier (−0.0019 ± 0.0013), team
points and team-points bias (−0.98 → −0.63); 2025-26 is within noise
everywhere. The unseeded gate therefore slightly understates the model that runs
live, which always has last season.

What the 2026-10-26 WAS @ CHA case suggested, absent players with no minutes for
the team this season (the absence adjustment counts them as zero while the
rating still carries last season's prior), is not a systematic miss. Over the
first 30% of dates, 696 of 1,053 games have a reported-out player who averaged
15+ minutes for the team last season and has not played for it yet (29 minutes
a game); per ten such minutes, home minus away, the model misses by −0.04 ±
0.16 points and the close by +0.08 ± 0.16 (one date-clustered standard error),
the close sitting 0.12 ± 0.04 below the model. Counting those minutes at the live 0.1 a minute would move the
model by −0.5 per ten minutes, about three standard errors the wrong way.

What it still cannot see: the replay runs the baseline path (`model.py`), not
production's (`candidate.py`: rotation redistribution of absent players'
minutes, pace and defense volume budgets, rest and travel context). Rerunning
the 24 live games of 2026-10-24..27 that ran the production path both ways with
the same inputs, the extra layers move each player little (0.27 points on
average) but in the same direction across a team: team totals move 2.5 points
(bias −0.84 → −2.79) and the player-sum margin 2.8. On those games they did not
help (player-sum MAE 10.61 → 10.66, team points 9.38 → 9.52), a sample too small
to decide. In WAS @ CHA every player's minutes were the same on both paths; the
team-level factors took the player sum from CHA +8.1 to +13.7 (close +2.5,
actual +7).

## Production path

The gate replays `model._baseline_forecast`. Production runs
`candidate.forecast_candidate`, which adds the redistribution of absent
players' minutes to teammates, pace and defense factors with team volume
budgets, and the fitted rest and travel context. `--production-path ARTIFACT`
replays that path: each game gets the pace and defense environment built from
the team games of earlier dates (a labelled historical reconstruction), the
rest and travel context from the season calendar, and the artifact's
parameters and fitted context model. `datasets/model-upgrade-2026-10-23/artifact.json.gz`
carries the same parameters and context model as the live 0.6.0 artifact.
To replay it, add `--production-path datasets/model-upgrade-2026-10-23/artifact.json.gz`
to the gate command; `AGENTS.md` still gates on the baseline path. Live-only inputs are still absent: recap role context, shot
profiles, aggregate historical priors. The context model was fitted on
2025-26, so that season is in-sample for it.

First run, 2026-10-27, against `datasets/backtest-2026-10-26-absence` (the
baseline path), ± two standard errors:

| Metric | Production path minus baseline | Verdict |
|---|---|---|
| Player points MAE | −0.021 ± 0.005 | within the materiality floor |
| Minutes MAE | −0.016 ± 0.005 | within the floor |
| Team points MAE | +0.32 ± 0.16 | worse |
| Team-points bias | −1.23 → −3.11 | worse |
| Margin MAE | −0.018 ± 0.090 | within noise |
| Winner Brier | −0.0020 ± 0.0018 | improved |

Each layer switched off, against the full production path:

| Off | Player points | Team points | Team-points bias | Winner Brier |
|---|---|---|---|---|
| Minutes redistribution | +0.031, worse | −0.34, improved | −3.11 → −0.15 | within noise |
| Pace factor | within noise | within noise | within noise | within noise |
| Defense factor | within noise | +0.38, worse | −3.34 | +0.0032, worse |
| Rest and travel | within noise | +0.08, worse | −3.51, worse | within noise |

The redistribution moves minutes to the right players but takes about three
points off each team's projected total. The 24 live games of 2026-10-24..27
rerun both ways show the same pattern (team-points bias −0.84 baseline, −2.79
production). The defense factor earns its place; pace at strength 0.25 does
nothing measurable.

Rerun with team totals reconciled to the margin, against
`datasets/backtest-2026-10-27-scoreline` (the reference
`datasets/backtest-2026-10-27-production-path/` is this run): team points
+0.37 ± 0.16 and team-points bias −1.23 → −3.11, worse; winner Brier −0.0020 ±
0.0018, improved; the rest as before. On this path the reconciliation itself
cut team points by 0.074 ± 0.040 with everything else unchanged; it moves
totals between the two teams, so the shortfall's bias is untouched. The
layer table above predates it.

`rotation_allocation.redistribute` inferred the season from `as_of`, which in a
replay is the day it runs, so it found no history for 2023-24 and 2024-25 and
moved no minutes there; it now takes the forecast's season, which is the same
season live.

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

## Reference: 2026-10-27 (team totals agree with the margin)

`datasets/backtest-2026-10-27-scoreline/` (T-minus-30) and
`datasets/backtest-2026-10-27-scoreline-midday/` (preliminary): the absence
references with each team's projected players scaled so the two team totals
differ by the margin location and the game total is unchanged; each total
moves half the gap (`scoreline.reconcile`). Points, their SD and interval and
the expected makes scale together; the margin is still located from the
player model's own totals, so it does not move.

T-minus-30, against the absence reference (± two standard errors): team
points −0.120 ± 0.042 (9.45 → 9.33), improved in each season (2023-24 −0.111,
2024-25 −0.107, 2025-26 −0.142); margin, winner Brier, minutes and
participation unchanged by construction; points −0.0015 ± 0.0016, interval
score −0.011 ± 0.011 and the three biases within noise.
Preliminary, against the absence midday reference: team points −0.081 ±
0.042 (9.37 → 9.29), improved in each season; everything else unchanged or
within noise. On the 2026-10-27 production forecast for POR @ LAL the totals
went from 112.7–109.5 (Portland by 3.2, the player sum) to 111.4–110.7, the
published Portland by 0.7, with the game total 222.2 and the margin
unchanged; the largest change to a player was 0.33 points.

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
`performance.json` benchmarks. The gate replays each season without the previous
one's box scores, so its first weeks are history only; the early-season replay
above seeds them.
A result here is necessary, not sufficient: a change that improves the replay is
expected to show up live after enough games, and both are reported.

## Updating the reference

When a change is merged, run the full backtest, commit its two files as
`datasets/backtest-<date>/`, and point `AGENTS.md` and the CI step at it. The
old reference stays in the repository.

## Reference: 2026-10-28 (roster players absent from every box score)

`datasets/backtest-2026-10-28-participation/` (T-minus-30) and
`datasets/backtest-2026-10-28-participation-midday/` (preliminary): the
scoreline references with one participation change. A player in none of his
team's box scores, not even as inactive, now counts the team's games since his
last game for another team as absences instead of keeping his role prior
alone. Live, that is a G League assignee such as Micah Peavy, projected at
0.70 and 0.82 to play with 16–19 minutes in two OKC games he was not part of.

The replay's roster is each game's own box score, so the players this targets
never appear in it; it sees only debuts, a player's first row with a team.
Against the scoreline references every metric is within noise: participation
Brier +0.0002 ± 0.0001 at T-minus-30 (floor 0.0004) and +0.0001 ± 0.0001
preliminary (floor 0.0006), points, minutes, team points, margin, winner Brier,
the interval score and the three biases unchanged to the reported precision.
Its benefit can only be measured live, on the projected players a final box
score leaves out (scored since `absent_from_final`).

## Reference: 2026-11-03 (short bench)

The reference is unchanged. `fix/short-bench` lifts three rules that switch
off below eight projected players (the minutes fill, the possession-budget
raise and the replacement credit for unallocated minutes) when every other
roster player is out on an explicit report status: such a team plays
everyone, shares the shortfall equally up to 48 minutes each and scores any
rest at the replacement rate (totals schema 6). The replay's roster is each
game's box score plus the players the report lists, and nine of its 7,406
team-games had seven or fewer players with minutes, none of them with every
other player listed out, so the rule never fires: both replays, T-minus-30
against `backtest-2026-10-28-participation` and preliminary against its
midday reference, reproduce the reference rows byte for byte (`rows.json.gz`
digests equal) and every metric reads `+0.0000 ± 0.0000`, within noise.
The change is a bug fix measured live: Hawks at Pacers on 2026-11-02, the
one short bench this season, replayed from its saved snapshot gives 106.4
Pacers points and Hawks by 11.9 (actual 104 and Hawks by 4; close 10.5)
where production showed 60.9 and no margin.

## Reference: 2026-11-06 (rotation participation and the minutes budget)

`datasets/backtest-2026-11-06-participation/` (T-minus-30),
`datasets/backtest-2026-11-06-participation-midday/` (preliminary) and
`datasets/backtest-2026-11-06-early-season/` (seeded from opening night; its
scoreboard is paired with an unchanged-code control run the same day, because
`backtest-2026-10-27-early-season` predates later changes). Model code
`regular-season-baseline-0.4.8`, three changes:

- Rotation priors `.79` / `.82` → `.98` / `.99` (`model.PARTICIPATION_PRIORS`,
  20–28 and 28+ minutes). The window already leaves out reported-out games, so
  the prior has to be the rate on games the player was available for; the
  all-games rates counted injuries twice. Bench priors stay at `.18` / `.70`.
- Expected minutes above 240 scale every player down alike. The protected
  rotation used to keep its minutes and the bench took the whole excess.
- The bench fill cap rises from a quarter to double (`model.UPLIFT_CAP`,
  `(.10, 1.)`); the rotation cap stays at a tenth.

Live before the change (0.6.0, 2,457 player-games selected by the daily
reviews): mean P 0.765 against 0.824 who played, low in every probability
bucket; untagged rotation players who had played every window game were
forecast 0.95 and played 0.998. Players who played were over-projected by +1.2
minutes (+1.8 at 30+ forecast minutes) and +0.60 points (`performance.json`):
with probabilities low, the 240-minute budget inflates minutes when playing.
The replay shows the same direction, smaller: 0.817 against 0.840 at T-minus-30,
about 0.045 low in each season's first three weeks.

| Replay, against its reference | Verdict | Participation Brier | Points | Minutes | Interval score | Points bias |
|---|---|---|---|---|---|---|
| T-minus-30 (gate) | improved | −0.0007 ± 0.0001 | −0.014 ± 0.009 | +0.002 ± 0.024 | −0.094 ± 0.043 | +0.07 → −0.09, within noise |
| Preliminary (midday) | improved | −0.0007 ± 0.0002 | −0.035 ± 0.007, improved | −0.070 ± 0.019, improved | −0.163 ± 0.047, improved | +0.21 → +0.05, improved |
| Early season, all weeks | improved | −0.0008 ± 0.0001 | −0.013 ± 0.007 | +0.000 ± 0.021 | −0.066 ± 0.036 | +0.15 → −0.04, improved |
| Early season, weeks 1–2 | improved | −0.0020 ± 0.0009 | −0.038 ± 0.026, improved | −0.058 ± 0.060 | +0.013 ± 0.093 | +0.48 → +0.05, improved |

Team points, margin, winner Brier and the team-point and margin biases are
within noise in every replay. Mean P at T-minus-30 is 0.831 against 0.840 who
played, within 0.03 in every minutes bucket; the early-season replay's first
three weeks run about 0.02 low instead of 0.045. On its own, points bias is
worse in two seasons: T-minus-30 2024-25 +0.02 → −0.11 and 2025-26 +0.03 →
−0.15, and preliminary 2025-26 +0.05 → −0.17; it improves in the others.
Grouped by the reference's forecast minutes, the same players at 30+ minutes
go from points MAE 5.716 to 5.685 and bias +0.75 to +0.10, but their minutes
MAE goes from 4.31 to 4.48 (bias +1.74 to +0.53); every bench group improves.

The production path (`--production-path`, not gated), against the same code
without the change: participation −0.0007 ± 0.0001, improved; points −0.004,
minutes +0.015, team points +0.022 ± 0.011, within noise; points bias −0.10 →
−0.27, worse. That path already projects below actual in the replay, while
live it projects players who play +0.60 points and +1.2 minutes above actual,
so the same shift moves live toward zero. Check it in `performance.json`
after a week of games.

Tried on the gate and not merged:

| Variant | Minutes | Points bias | Verdict |
|---|---|---|---|
| Priors only: `.52/.82/.98/.99`, `.18/.70/.98/.99`, `.18/.82/.98/.99` | +0.037, +0.025, +0.032, worse | −0.20, −0.15, −0.18, worse | worse |
| Proportional scale-down, rotation priors, caps `.10/.25` | −0.005 | −0.17, worse | worse |
| Same, caps `.25/.50` | +0.027, worse | −0.10, worse | worse |
| Same, caps `1/1` | +0.049, worse (30+ minutes MAE 4.74) | −0.07 | worse |
| Same, caps `.10/.40`, `.10/.60` | within noise | −0.13, −0.11, worse | worse |

The priors alone fail because the bench-only scale-down pushes the extra
expected minutes onto the bench, already projected short. With the
proportional scale-down, the team-games the budget pins at 240 (90% of rows)
come out unbiased in minutes (+0.03), and the rest is the tenth of team-games
left short of 240, mostly eight to ten projected players, whose minutes when
playing ran two minutes low under the old bench cap. The 2026-10-25 note above
("role priors measured on available games only") is the first of these.

The live data had a second, smaller cause, fixed without a model change: the
16 games of 2026-10-20..23 ran before the live checkpoints began, so they had
no T-minus-30 report record and a player listed out in them counted as a DNP
in his window. `recover-evidence` added the records on 2026-11-06 (82 roster
players, 88 player-games); on the live rows that raised those players' rates
from 0.53 to 0.62 against 0.73 who played, 0.003 overall.

## Reference: 2026-11-06 (rating weight 0.65)

`datasets/backtest-2026-11-06-rating-weight/` (T-minus-30), model code
`regular-season-baseline-0.4.8`, one change: the margin location's team-rating
weight, `margins.PARAMETERS['rating_weight']`, `.5` → `.65` (the player sum
keeps `.35`). The method string and the absence term are unchanged; the model
key changes with the parameters, as it does for every margin-parameter change.

Why now: live, the published margin stands further from the closing line than
the rating half alone. On 2026-11-02 (54 games) the close averaged 10.24
points, the blend 12.21, the rating half 11.95 and the player sum 12.64; on
2026-10-29 (32 games) the player-sum half trailed the close by +1.9 ± 0.7
points of size. The gate on 2026-11-02 found 0.65 and 0.80 both improved the
winner Brier against the scoreline reference; 0.65 had the tighter verdict and
less margin bias (0.80: +0.05 → +0.17), so 0.65 is the value, re-gated here
against the participation reference.

Against `backtest-2026-11-06-participation`: **improved** (pooled). Winner
Brier −0.0014 ± 0.0007 (0.2056 → 0.2042, floor 0.0010), improved; margin
−0.037 ± 0.038 (11.29 → 11.25, floor 0.056), within noise; points −0.0004,
minutes +0.0000, team points −0.004, participation +0.0000 and the interval
score −0.0013, within noise; points bias −0.09 → −0.09, team-points bias
−1.23 → −1.23 and margin bias +0.06 → +0.12 (se 0.28), within noise. By season:
2023-24 improved, 2024-25 within noise, 2025-26 improved. Winners 68.1% →
68.6%; the closing line stays 10.90 on the same games.

The preliminary replay (midday reports, `datasets/backtest-2026-11-06-rating-weight-midday/`, against `backtest-2026-11-06-participation-midday`) is within noise on every metric: winner Brier −0.0006 ± 0.0007, margin +0.005 ± 0.036 (11.44), team points +0.006 ± 0.013, the interval score +0.001 ± 0.003, margin bias +0.11 → +0.16 (se 0.29), each season within noise. It is not required for a margin-parameter change and is committed so the midday reference matches the code.

Two caveats. The absence term (0.1 points per minute of reported-out regulars
on the rating) was sized when the rating carried half the location; at 0.65 it
moves the blend 0.065 a minute instead of 0.05, and the gate measured the
combination, not the term alone. And 0.65 is one of two values tried on these
seasons, so part of the gain is selection on the sample; the live check is
`market/report.json`, where the model's margin error against the close (12.41
against 10.41 on 91 games through 2026-11-05) should narrow over the next few
weeks of games.
