# Backtest scoreboard

`python -m nba_forecast backtest` replays one season with the current model
code and scores it. It is the gate for model changes (see `AGENTS.md`): a
change to how forecasts are computed is judged here first, on the same games
and players as the committed reference, before anything is merged or
activated.

```bash
python -m nba_forecast backtest \
  --history datasets/regular-season-expanded-2026-10-22/history.json.gz \
  --team-games datasets/model-upgrade-2026-10-23/team-games.json.gz \
  --output /tmp/backtest-$(date +%Y%m%d-%H%M) \
  --reference datasets/backtest-2026-10-24
```

It takes about three minutes. `--limit N` replays only the last N
evaluation games (CI uses 400); `--json` prints the full scoreboard. The exit
code is 3 when the comparison verdict is `worse`.

## What it does

Games are taken in order. The first 30% of game dates are history only; every
game after that is forecast with the ordinary baseline model from the box
scores of earlier Eastern dates, never the same day. The roster is the box
score's own players, all treated as available. Player metrics use projected
players who actually played with positive minutes, the same population the
prospective interval calibration uses. Team points compare the projected team
sum with the regulation-equivalent final score; margins and winners use the
final score.

Output: `scoreboard.json` (metrics, parameters, code and data hashes) and
`rows.json.gz` (every projected player-game and team-game). With
`--reference`, the same player-games, teams and games are paired. For points,
minutes, team points and margin the mean change in absolute error is reported
with its standard error; `improved` and `worse` mean the change exceeds two
standard errors, otherwise `within_noise`. With 18,500 player-games that
threshold is about 0.03 points. Signed bias is scored separately for points,
team points and margin: a bias more than two standard errors from zero that
shrinks by at least one standard error is `improved`; one that appears or grows
by at least one standard error is `worse`. Bias matters on
its own for pricing, and a bias fix barely moves absolute error when game
noise is fifteen points. The overall verdict is `worse` if any metric is
worse, else `improved` if any is improved.

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

Earlier references: `datasets/backtest-2026-10-24-home-court/` (home court),
`datasets/backtest-2026-10-24/` (model 0.4.2).

## Reading the numbers

- **Projected players who did not play: 35% of projections.** The replay
  treats every listed player as available, so inactive and DNP players draw
  minutes from the ones who play. That is why bias is negative for low-minute
  players and the live model, which has injury reports, shows bias near zero.
  A participation model would move this number and is the next model item.
- **Participation** is scored on every projected row, played or not: the Brier
  score of the participation probability against actually playing, beside two
  references, a constant rate and "assume everyone plays". The replay has no
  injury reports, so this measures the recent-participation model alone.
- **Coverage** is measured at the heuristic half-width (1.70 sd). The nominal
  80% interval covers 76% here and 78–79% for players projected at 10+ minutes.
- **Home court** is a symmetric factor (home up, away down by the same
  ratio) estimated from flagged complete games before the cutoff and shrunk
  toward a 2025-26 prior; see `docs/design.md`. The replay flags history rows
  from the reconciled team games, so the estimate adapts during the season.

## What it cannot measure

No injury reports exist historically, so availability and participation are
not forecast. No prior-season aggregate priors or archive-bound enhancements
(pace, defense, rest, shot location, injury exits) are applied, so changes to
those still need the live `performance.json` benchmarks. A result here is
necessary, not sufficient: a change that improves the replay is expected to
show up live after enough games, and both are reported.

## Updating the reference

When a change is merged, run the full backtest, commit its two files as
`datasets/backtest-<date>/`, and point `AGENTS.md` and the CI step at it. The
old reference stays in the repository.
