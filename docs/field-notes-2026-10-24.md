# Field notes, 2026-10-24: what one long session learned

Written for the next agent. Everything here was measured or decided on
2026-10-24 (season 2026-27, week one); the code and `docs/` hold the mechanics,
this holds the reasons, the numbers and the traps. Read `AGENTS.md` first.

## The owner's goals and standing policies

- Goals: player props (points, minutes) and game margins. Accuracy first.
- Forecasts must not be blocked for "dumb reasons". Provider disagreements,
  pending reports, a two-way player nobody has seen shoot, a newcomer the team
  has listed but not played: notes, never blocks. The official NBA roster and
  injury report are sufficient inputs.
- The schedule is downloaded once per season and checked once a day (8 AM PT).
- The owner compares the model with Kalshi and DraftKings lines by hand; a
  model that disagrees with the market by 10+ points on a total or margin is
  presumed wrong until shown otherwise.

## How production runs (what is not obvious from the code)

- User systemd units: `nba-forecast.timer` ticks `python -m nba_forecast tick`
  every minute; `nba-dashboard.service` serves 127.0.0.1:8765. Both run from a
  pinned copy under `data/operations/production/runner-releases/<sha>/`
  (`WorkingDirectory`), never from the working tree. The dashboard's static
  assets, however, are served straight from `dashboard/dist` in the repo
  (`--assets`), so `npm run build` changes the live page without a restart.
- Deploy recipe after a commit: `python3 -m nba_forecast.runner_release
  --directory <prod>/runner-releases` prints the release path; from that
  directory `python3 -m nba_forecast.refinement_upgrade --directory <prod>
  --base-artifact e25bec66… --output data/review-candidates/<name>/artifact.json`
  builds the 0.6.0 artifact bound to the package digest; write
  `<prod>/model-upgrade-active.json` `{artifact_sha256, activated_at}` (back
  the previous one up under `<prod>/activation-backups/<sha>/`), check with
  `enhancements.load_active`, then `sed` the `WorkingDirectory` of both units,
  `systemctl --user daemon-reload`, restart only the dashboard. The next tick
  re-forecasts every pregame game (about 2.5 minutes of CPU once; idle ticks
  cost 3.5 s).
- Forecast jobs are keyed by evidence (`:evidence:<digest>` of the injury
  report edition, ESPN injury list and roster), not by time. A model
  activation re-forecasts pregame games on the next tick; games that have
  tipped keep their last pregame run (no "stale model" bug, just tip-off).
- Finding a game's latest run in the ledger: `json_extract(snapshot_json,
  '$.operation_key') LIKE '<game_id>:%'` on `runs`; `forecast_json` holds
  `predictions`, `game_totals`, `margin_forecasts`. Snapshots carry the whole
  roster of every team, so searching `snapshot_json` for a name matches every
  game.
- Market lines: `<prod>/market/game-lines/<game_id>.jsonl` (DraftKings via
  ESPN pickcenter, appended on change), `market/report.json` compares closing
  lines with saved forecasts. ESPN has no player props; no props source exists
  yet (ask the owner).
- `datasets/regular-season-expanded-2026-10-24-home/history.json.gz` is the
  tick's `--history` (2025-26 with home flags). cdn.nba.com returns 403 from
  this host, so 2024-25 cannot be backfilled that way.

## What the replay cannot see, and what to use instead

The backtest replays 2025-26 from the 30% mark of the season with box-score
rosters (no outs, no injury report). It is excellent for rate and minutes
changes and useless for three things that dominate week one of a season:

1. Rosters where players' rates were learned in other roles. Usage is
   additive in the model; in the replay rosters barely change.
2. Teams with several outs (expected minutes well under 240).
3. Early-season participation (one listing per player).

For those, compare with the market. On 2026-10-24 the model's margins ran
about twice the market's on every game (mean absolute gap 11 points, totals
+9) while the replay said the margin distribution was calibrated. The
comparison script is three joins: latest run per game, `market/game-lines`,
`game_totals` and `margin_forecasts`. Make it a habit before trusting a
week-one forecast.

## Numbers worth keeping (2025-26 unless stated)

- Team usage, FGA + 0.44·FTA + TOV per 240 team minutes: league 112.6,
  between-team sd 2.5 (range 107.5–117.3), within-team game sd 6.1. Points per
  possession used 1.021.
- Usage per minute by season-average minutes bucket: <10 min .362, <20 .381,
  <28 .431, 28+ .561; league .469. Points per minute: .332 / .377 / .438 /
  .583; league .479. The replacement constants (.38 usage, .38 points) are the
  <20-minute bucket.
- Players with minutes per team-game: mean 10.8, quartiles 10/10/12, max 15;
  listed in the box score 17.6 on average (so priors measured on listings
  include inactives, which is why the live floor of ten expected players
  exists).
- Participation base rates by role minutes: <10 .18, <20 .70, <28 .79, 28+
  .82. Live week one, 35% of projected players did not play in the replay
  (which has no injury report); live it is about 14%.
- Margin distribution: prior sd from paired finals 15.2–16.6; residual sd
  15.4; winner Brier 0.214 (constant-sd baselines 0.217–0.218); mean P(home)
  .525 vs home win share .552; the 0.5–0.6 probability bins win 63–72%, about
  two standard errors of underconfidence on home favourites; home bias −0.27.
  Live prospective margin calibration had zero samples (needs 64 training and
  32 validation games).
- Rest: no team-points effect (back-to-back +0.39, two days +0.39, three or
  more +0.59 actual minus projected); margins only a one-standard-error hint
  (home on a back-to-back −1.8 ± 1.6). No rest term was added.
- Week-one live totals (23 team-games with pregame runs): bias +4.5, MAE
  11.3; teams projected more than 5% above their actual usage +10.1, the rest
  +2.1 and −0.7. Decomposition over players who played: minutes −7.8, rates
  +5.7, DNP remainder +6.6.
- Newcomers listed but not yet played for the team (replay, 187 rows): played
  7% of the time, mean probability .15, conditional minutes right when they
  played (13.1 projected vs 13.6).

## Decisions and the reasoning behind them

- Participation is a probability per player; projections stay conditional on
  playing; totals and margins weight by the probability (totals schema 3+).
  Team probabilities are scaled into [10, 13] expected players (13 is the
  active-list rule; ≥10 because teams used at least ten players in three
  quarters of games and the priors were measured on listings). The floor
  never raises a player with a reported status: a questionable player at 1.0
  is refused by the ledger, and that failed one game live before the fix.
- Unallocated minutes enter team totals at the replacement rate when at
  least eight players are projected (totals schema 4). Player projections are
  never raised for it. Withheld players whose only reasons are missing
  evidence are covered by the residual, not blocking.
- Team possession budget, bounds .85–1.10, raising only with eight projected.
  The replay's own factor distribution was centred at .992 with 0.2% clipping
  either side; the small negative bias it introduced is real and accepted.
- Uplift of conditional minutes when expected minutes fall short: 10% core,
  25% bench, never past 48. Larger fills over-projected starters; none left
  totals short.
- The backtest gate was overridden twice (usage budget: bias grew one SE;
  newcomers: minutes MAE +0.009). Both are written up in `docs/backtest.md`.
  If a third case appears, consider a materiality floor in the paired rule
  (two standard errors on 18,000 rows flags 0.2% effects) rather than a third
  exception.
- NBA rules verified from current sources: 13 active maximum, 8 minimum,
  inactive list changeable until 60 minutes before tip; injury report by 5 PM
  local the day before (1 PM game day for the second of a back-to-back),
  game-day report 11 AM–1 PM local, then continual updates; two-way players
  limited to 50 active games (90 combined when under 15 standard contracts);
  Player Participation Policy fines $100k / $250k / +$1M for resting stars in
  national-TV and Cup games, no two stars at once, home/road balance.
  Modeled: the 13 cap. Not modeled: the two-way clock, the PPP (stars are
  more likely to play national-TV games), and the T−60 inactive list, which
  would be the best participation signal there is; no feed for it exists on
  this host.

## Traps

- `game_totals` is recomputed from predictions when a run is saved and when
  margins are built, and compared canonically with the saved totals. Any
  term in a total must be derivable from the predictions alone (hence the
  replacement rate is a constant, not a per-run estimate).
- The enhanced path (`candidate.py`) derives its rates from the baseline's
  *projected* numbers (`expected_fga / minutes`), so anything the baseline
  scales is already in them. Team-level budgets target a level, which makes
  applying them twice harmless; multiplicative adjustments would compound.
- `_participation` counts listings (DNP rows) for the team as games since
  joining; a player with no rows for the team gets the role prior. Keep the
  window inside the current season.
- `usage_per_48` in `_complete_team_games` is a team-game total normalized to
  48 game minutes, i.e. already per 240 player minutes. Do not multiply by 5.
- Tests build teams of five players at 48 minutes with high rates; the usage
  budget scales them. Assert against `components.team_usage.factor`, not
  fixed attempt counts.
- `pkill -f` with a pattern that matches the shell running it kills the
  shell; bracket the first letter (`[n]ba_forecast`). Never chain `sleep`s;
  use an `until` loop or run in the background.
- The 7 GB `forecast.sqlite.pre-history-dedupe` is a hard link kept from the
  ledger migration; the owner may delete it.

## Open items, in the order they matter

1. A player props line source (the owner mentions Kalshi and DraftKings).
2. A pregame inactive-list feed (T−60) and the Player Participation Policy.
3. Two-way 50-game clocks from the injury report's two-way designations.
4. The home-favourite underconfidence once the live margin calibration has
   96 games; check the 0.5–0.6 bins first.
5. Watch the live team-points bias after the usage budget; if it runs
   negative for two weeks, the 0.38 replacement rate or the .85 floor is the
   first suspect.
