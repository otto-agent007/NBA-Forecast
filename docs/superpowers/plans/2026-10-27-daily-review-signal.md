# Daily Review: Signal From Noise Implementation Plan

> The nightly review scores every frozen forecast correctly but tells us little about what to change. Five fixes make it a model-improvement tool. None changes a forecast, so the backtest gate does not apply; each fix is verified on synthetic nights and on the saved 2026-10-26 review.

**Goal:** After each slate, read one page that says which misses were noise, which were the model's, whether they are recurring, and how the night went against the close.
**Architecture:** `nba_forecast/daily_review.py` gains a miss classifier, probability-weighted participation metrics and a market section; a new `daily-reviews/recurrence.json` is rebuilt from the saved per-night files; the tick fetches LiveData play-by-play so `postgame.py` runs again. The dashboard's DailyReview panel shows the new sections.
**Tech Stack:** Standard library Python, the existing React panel, Playwright fixtures.
**Found on 2026-10-27:** no LiveData play-by-play has ever been archived in production, because the shot sync completes every game from the official game page first and never asks for it; `postgame/` has only `missing_reason` files, and the review's blowout and exit evidence is empty. A direct fetch of `playbyplay_0022600127.json` with `data.CDN_HEADERS` returned 585 actions, so the source works; the plain headers get 403.

## Constraints

- Original frozen forecasts only; never re-forecast. A classification is a label on a miss, not a correction to it.
- Nothing in the review may read a forecast after its outcome in a way that changes what is scored; labels may use the final box score, the play-by-play and the next day's injury report, which are outcomes.
- Deterministic: the same inputs give the same `input_signature`. Tests build synthetic nights; a final check reruns the review of 2026-10-26 and compares with the hand summary of that night (207 projected players played, points MAE 4.33, 39 projected did not play).
- One night identifies questions. The recurrence file is what turns questions into candidates for a gated change.

## Tasks, in order

### 1. Play-by-play back into the tick (unblocks the exit and blowout labels)

- [x] In `player_sync.sync_player_data` (or a sibling `postgame_sync`), for each finished current-season game without `raw/*playbyplay_<gid>.json`, fetch `shots.playbyplay_url(gid)` through `data.fetch_json(url, cache_dir=raw)` so the archive and provenance land where `postgame.build_postgames` scans; bounded to a few games per tick, retried on the queue like shots. The LiveData box score is already archived by `operations._collect_finals`.
- [x] Backfill the 52 finished 2026-27 games once (a CLI flag or the first ticks), then confirm `postgame/<gid>.json` carries a timeline and `daily-reviews/<date>.json` shows `play_by_play_games` equal to the slate.
- [x] Tests: a tick with a stubbed fetch archives the play-by-play once, skips games that have it, and `build_postgames` picks it up; a 403 stays a warning.

### 2. Probability-weighted participation (small, do it first among the review changes)

- [x] In `_summarize`, replace the raw `projected_dnp` count with: `expected_minutes_lost` (sum of participation probability times minutes over projected players who did not play), `expected_dnp_minutes` (sum of one minus probability times minutes over every projected player, what the model itself expected to lose), the night's participation Brier, and `mean_probability` against `played_share`. Keep the raw count beside them.
- [x] The queue's `participation` item fires on the Brier and on the gap between mean probability and played share, not on the count; its evidence lists the players with probability at least 0.5 who did not play.
- [x] Dashboard: the DailyReview panel shows the weighted figure, labelled as such.
- [x] Test: a night where ten deep-bench players at probability 0.1 sit out reads as a small weighted loss and no queue item; a starter at 0.95 who sits out does fire.

### 3. Classify every projected miss

- [x] In `_stage`, add `miss_class` and `miss_evidence` to each projected player row, assigned in this order, first match wins:
  - `did_not_play`: actual status dnp or zero minutes.
  - `early_exit`: actual minutes at most half the projection and at most 20, with evidence from any of: a `role_context` injury-exit event for the game, the next day's official report listing the player, or the play-by-play's last substitution out with no return before the final two minutes. Without evidence, `exit_unexplained`.
  - `blowout`: the postgame `low_leverage_start` exists, and the player is a starter with at least six minutes below projection or a reserve with at least six above. Fallback when play-by-play is missing: final margin at least 20 with the same minute test, labelled `blowout_assumed`.
  - `foul_trouble`: five or more personal fouls and at least six minutes below projection.
  - `role_shift`: absolute minutes error at least 8, and `role_shift_repeated` when the player's previous two box scores for the team moved the same way against his projections then.
  - `volume`: minutes within five, attempts off by at least five.
  - `shooting_variance`: minutes within five and attempts within three; the points error is conversion.
  - `within_noise`: everything else.
- [x] In `_summarize`, the night's absolute points and minutes error by class, each class's share, and the split into noise classes (`early_exit`, `blowout`, `shooting_variance`, `foul_trouble`) and model classes (`did_not_play`, `role_shift`, `volume`).
- [x] The queue's `minutes` item lists only model-class misses, with the class in each evidence row; noise-class misses go to an `explained_misses` list, not the queue.
- [x] Tests: one synthetic night exercising each class, order of precedence, the fallback labels, and the share arithmetic.
- [x] Check against 2026-10-26: Markkanen (36 to 6 minutes), Derrick White (34 to 18) and Booker's 3 points must land in noise classes; Hopkins (13 to 31), Josh Green (4 to 25) and Bamba (11 to 29) in `role_shift`.

### 4. Fold in the market

- [x] In `review_day`, for each game with a verified final, add `market`: the model's margin and total from the frozen final-stage totals, the ESPN close (`market_lines.closing_lines`), the Kalshi close (`kalshi_lines.closing_kalshi_line`), the actual, and each one's absolute error; reuse the row logic of `market_lines.market_report` rather than copying it.
- [x] In `_summarize`, the night's model and close margin MAE, the games where the model lost at least three points to the close, and for each of those the team-total errors with the model-class misses that drove them (the join between fix 3 and the market).
- [x] Dashboard: a per-game row in the DailyReview panel: model, close, Kalshi, actual.
- [x] Test: a synthetic night with one game where the model beats the close and one where it loses by five; the losing game appears with its drivers.

### 5. Thread across nights

- [x] `daily_review.build_recurrence(directory, nights=14)` reads the saved `daily-reviews/<date>.json` files (never the ledger) and writes `daily-reviews/recurrence.json`: per player, nights projected, mean signed minutes and points error, counts by `miss_class`, and the current streak of same-sign minutes errors of at least six; per team, mean signed team-total error and its streak, and expected minutes lost to DNPs.
- [x] The queue gains `recurring_minutes` (a player with a streak of three or more), `recurring_team_total` (a team three nights the same sign) and `recurring_dnp` (a player at probability 0.5 or more sitting out twice in a week), each with the nights as evidence. These are the items worth turning into a gated experiment.
- [x] `build_daily_reviews` rebuilds the recurrence file when any night changes; the dashboard shows a "Repeating" table.
- [x] Tests: three synthetic nights with one repeating player and one repeating team produce exactly those two items; a changed night rebuilds the file; the file is deterministic.

## Verification

- `python3 -m unittest discover -s tests` and the dashboard unit and Playwright suites pass; the Playwright fixture for the DailyReview panel gains the new sections.
- `python3 -m nba_forecast daily-review --directory data/operations/production --date 2026-10-26` reproduces the night: the class shares account for the whole absolute error, the weighted DNP loss is far below the raw 394 minutes, and the market section matches `market/report.json` for the nine games.
- After the first live night: `play_by_play_games` equals the slate, and the queue has no item whose evidence is a noise-class miss.

## Done 2026-10-27, and where it departs from this plan

- Fix 1 lives in `postgame.collect_sources`, run by the tick after the player sync. The LiveData box score had never been archived either (finals come from the nba.com page and ESPN, so `_collect_finals` skips them), so both feeds are fetched. Games are chosen by a cached final, since the saved calendar still called finished games scheduled (43 of 52). One game per tick within 40 seconds: the CDN took 20 to 120 seconds a game on 2026-10-27. The backfill is the first ticks. On a production copy 51 of the 52 finished games reconciled. Two feed quirks are now handled: 0022600111 labels its closing marker period 5 in a regulation game (dropped when it carries the final score), and 0022600101 re-sends two first-quarter actions after the game end (put back by game clock). 0022600009's first half is out of order throughout; it stays a retried warning.
- `_selected` opens only the chosen run per game and stage (byte-identical reviews and market report on a production copy; 26 s to 1-3 s), which pays for the extra ledger reads of fixes 3 and 5. The last four nights are re-reviewed every build, because exit evidence comes from each team's next forecast.
- Fix 2: `participation_low` fires when far more players enter than the probabilities expect (z of -2 or less). That happened on 2026-10-25 and 2026-10-26: mean probability 0.79 against 0.87 played.
- Fix 3: the exit classes also need the player 6+ minutes short. Play-by-play evidence counts only for a starter whose last recorded exit has no later action (a period can start without a substitution event; postgame-1.1 records each player's last action). A reserve's short night with no report or recap goes on to the role tests: on 2026-10-26 all 35 such rows were reserves playing garbage time only. Booker lands in `volume` (18 projected attempts, 7 taken in 34 minutes), not in noise.
- Fix 4: the night's market summary is the review's top-level `market`, since it does not depend on the stage.
- Fix 5: team-total streaks count misses of 5+ points (three same-sign nights happen by chance for about a quarter of teams); minutes streaks skip noise-class nights; `recurring_dnp` also needs the forecasts to have given the week's DNP count at most a 5% chance.

## Out of scope

Changing any forecast. Fixing what the review finds (early-season roles, doubtful players at midday, team-points bias) stays behind the backtest gate, one hypothesis at a time.
