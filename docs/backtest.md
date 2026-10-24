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

## Reference: 2026-10-24 (home court)

`datasets/backtest-2026-10-24-home-court/`: model `regular-season-baseline-0.4.3`
baseline path, 868 games, 18,548 played player-games.

| Players by projected minutes | n | points MAE | bias | minutes MAE | 80% coverage | width |
|---|---|---|---|---|---|---|
| all | 18,548 | 4.87 | −1.73 | 6.32 | 75.9% | 13.3 |
| 0–10 | 3,906 | 4.16 | −3.21 | 8.36 | 65.6% | 5.9 |
| 10–20 | 5,510 | 4.65 | −2.15 | 7.05 | 78.2% | 12.5 |
| 20–30 | 6,337 | 5.08 | −1.21 | 5.39 | 79.3% | 16.3 |
| 30+ | 2,795 | 5.86 | +0.01 | 4.12 | 77.9% | 18.5 |

Teams: points MAE 10.08, bias −1.72. Games: margin MAE 12.17, home bias −0.14,
winner accuracy 64.3% (the model picked the home team 58% of the time; home
teams won 55%).

Against the previous reference (`datasets/backtest-2026-10-24/`, model 0.4.2
without a home-court term: margin MAE 12.25, home bias −1.66, team points MAE
10.11, winner accuracy 63.9%), the home-court term left player points
unchanged, moved margin and team-point absolute error by −0.08 and −0.03
(within noise), and removed the margin home bias (−1.66 → −0.14, about three
standard errors). It was accepted on the bias criterion.

For scale: predicting a player from the mean of his own last 15 played games
gives MAE 4.68 on this data, and knowing his actual minutes with his recent
scoring rate gives 3.82. The remaining lever in points is minutes.

## Reading the numbers

- **Projected players who did not play: 35% of projections.** The replay
  treats every listed player as available, so inactive and DNP players draw
  minutes from the ones who play. That is why bias is negative for low-minute
  players and the live model, which has injury reports, shows bias near zero.
  A participation model would move this number and is the next model item.
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
