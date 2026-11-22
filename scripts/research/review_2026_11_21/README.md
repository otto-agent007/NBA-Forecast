# Review of 2026-11-21: where the model stands against the markets, and what moves minutes

Standard library only. The live scripts read an operations directory and write
nothing (`NBA_OPERATIONS`, default `data/operations/production`); the replay
scripts read a reference's `rows.json.gz` and the gate's histories
(`NBA_DATASETS`, default `datasets/`; first argument: the reference folder's
name, default `backtest-2026-11-12-margins-v5`). Live tables print ± one
standard error clustered by Pacific game date, replay tables ± one standard
error clustered by game.

| Script | Question |
|---|---|
| `live_margins.py` | Margin and total error against the DraftKings close by margin method and by week; the model's and the close's bias |
| `live_totals.py` | The game-total shortfall before and after 2026-11-10, and what adding a constant per team would have done |
| `market_sides.py` | Did the side the model leaned to win: DraftKings spreads and totals at −110, Kalshi winner and points rungs at the mid |
| `props_medians.py` | Points against Kalshi's implied medians: by size and direction of disagreement, by line, by the model's minutes miss; the error with actual minutes |
| `props_at_the_quote.py` | The same rungs at the recorded bid and ask after fees (now in `market/props-report.json` as `taking_the_model_side`) |
| `props_other_stats.py` | Rebounds, assists and threes against Kalshi's medians |
| `props_skew.py` | Whether centring the rung probability on the mean rather than the median costs anything (it does not); reads the scored runs' forecasts from the ledger by run id once (`NBA_PROPS_FORECASTS` caches them) |
| `props_whatif.py` | First-order effect of a scoring-level change and of a minutes ceiling (not adopted) on the Kalshi medians |
| `props_big_misses.py` | Which situations carry the gap to Kalshi: returns, role changes, forecasts away from the last-five average |
| `live_players.py`, `live_minutes.py`, `live_participation.py` | Live player bias, minutes by forecast level, team sums, participation calibration by period |
| `replay_rows.py` | The same minutes, participation, team-total and margin-slope checks on a reference's rows |
| `high_minutes.py` | Counting role minutes above a knee at a slope: knee and slope picked on two seasons, scored on the third (first order, no reallocation). Not adopted: on the gate the minutes it frees go to teammates through the 240-minute budget and cost them more than the top gains (`docs/backtest.md`, 2026-11-22) |
| `role_decay.py` | The role-minutes decay (slower is worse) and the lineup-context weights (decay, mismatch weight and blend picked on two seasons, scored on the third) |
| `blowout_minutes.py`, `blowout_minutes_fit.py` | Minutes bias by the model's own projected margin (large), and why a blowout term does not lower absolute error (not adopted) |
| `gate_variants.py` | Gate runs side by side (`table`: each scoreboard's comparison with its reference, ± two standard errors as the gate prints them), and two replays paired player by player by the base's projected minutes (`pair`: ± one standard error by game) |
| `live_candidate_props.py` | A candidate's points on the live Kalshi medians and on every played player, from two `live_replay.py` outputs of the same games (± one standard error by Eastern game date) |
| `live_candidate_totals.py` | A candidate's game totals on the live games from the same two outputs: bias and absolute error of each code and of the closing total, before and from 2026-11-10 (± one standard error by Eastern game date) |
