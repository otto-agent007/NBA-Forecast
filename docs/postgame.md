# Postgame review

## Daily forecast review

The persistent runner rebuilds the last four Pacific-date reviews (today and the
three nights before) after final-result collection. The **Performance** tab shows the selected date's review:
each game's actual score, saved preliminary/final totals, player and minutes error,
range coverage, paired stage comparison and evidence-backed improvement questions.
Final boxes are enough for these comparisons; detailed play-by-play coverage is
reported separately. A report becomes complete when every scheduled game has a
reconciled final. Missing player rows remain unscored, even if an out player would
usually be absent from the provider's boxscore.

```sh
python -m nba_forecast daily-review --directory data/operations/production
python -m nba_forecast daily-review --directory data/operations/production --date 2026-10-23 --output /tmp/oct23-review.json
```

The selector takes the latest whole live league forecast per game/stage before
opening outcomes, preserving abstentions, original model versions and creation
times. Saved partial totals retain their original designation. Earlier forecasts
without saved aggregate totals are labeled as sums derived from their frozen
player forecasts. Partial totals and overtime are excluded from complete
regulation-total metrics; unknown final duration is excluded as well. DNPs remain
in projected-player error, and out forecasts have separate metrics.

Reports live in `daily-reviews/YYYY-MM-DD.json`; content-hash revisions preserve
the earlier review. The index watches final and postgame evidence plus schedule
identity for previously reviewed dates. A later ingested correction regenerates
that older date even after it leaves the current four-night window. This does not
add unbounded historical provider polling. Unchanged reports keep their timestamp.

Review priorities distinguish participation, overfull protected rotations, minutes,
attempts, and interval coverage. Components are accounting decompositions;
small forecast attempt denominators can produce unstable terms. The report also
flags identities reported for multiple teams on one date. New validated outcomes
enter the existing history/calibration process; model formula changes still
require tests and multi-game evaluation.

### Signal from noise

Every projected player with a final row carries a `miss_class` and its
`miss_evidence`, first match wins:

| Class | Rule | Group |
| --- | --- | --- |
| `did_not_play` | DNP or zero minutes | model |
| `early_exit` | at most half the projected minutes, at most 20, and 6+ short, with evidence: a recap injury exit or a listing (out, doubtful, questionable) in the team's next frozen forecast, or a starter's last recorded exit with no later action, before the final two minutes and any garbage time, not fouled out | noise |
| `exit_unexplained` | the same short night for a starter with no evidence; a reserve's goes on to the tests below | unexplained |
| `blowout` | a sustained-lead window in the play-by-play, with a starter 6+ minutes under or a reserve 6+ over | noise |
| `blowout_assumed` | the same minutes test on a final margin of 20+ when no play-by-play exists | noise |
| `foul_trouble` | five or more fouls and 6+ minutes under | noise |
| `role_shift` | minutes off by 8+ (`role_shift_repeated` when the team's two previous games missed the same way by 4+) | model |
| `volume` | minutes within 5, attempts off by 5+ | model |
| `shooting_variance` | minutes within 5, attempts within 3 | noise |
| `within_noise` | everything else | noise |

`miss_classes` gives each class's and group's share of the night's absolute
points and minutes error. The `minutes` and `attempts` queue items list only
model-class and unexplained misses; noise-class ones go to `explained_misses`.
The labels read outcomes only (the final box, the play-by-play, later reports);
they never change what is scored.

`participation` weighs DNPs by the forecasts' own probabilities:
`expected_minutes_lost` (probability times minutes over projected players who sat)
against `expected_dnp_minutes` (what the forecasts expected to lose), the
participation Brier against its expectation, and z-scores for the played count
and the Brier. The `participation` queue item fires when either z-score is 2 or
more; `participation_low` when far more players entered than expected. The raw
`projected_dnp` count stays beside them.

Each game with a verified final carries `market`: the model's margin and total
(the market report's rows), the ESPN close, the Kalshi close and the actual. The
top-level `market` gives the night's margin MAE for each and, for every game the
model lost to the close by three points or more, each team's error with the
signed points of model-class misses, noise-class misses and unprojected players.

`daily-reviews/recurrence.json` threads the last 14 saved nights (never the
ledger): per player, mean signed errors, counts by class and the current streak of
same-sign minutes misses of 6+ (noise-class nights skipped); per team, the
team-total streak of same-sign misses of 5+ and expected minutes lost to DNPs.
Its queue (`recurring_minutes` at three straight nights, `recurring_team_total` at
three, `recurring_dnp` for a player at probability 0.5+ sitting twice in seven
days) is what is worth turning into one gated experiment. It is rebuilt whenever
a night changes, and the dashboard shows it as **Repeating**.

After collecting final boxscores and player/shot data:

```sh
python -m nba_forecast sync-players --directory data/operations/production --history datasets/regular-season-expanded-2026-10-22/history.json.gz --max-shot-games 12
python -m nba_forecast postgame --directory data/operations/production
python -m nba_forecast dashboard --directory data/operations/production
```

Select a completed game's date and the **Postgame** tab. Select a matchup, filter
by team, and click any player for recorded substitutions and takeaways. `tick`
archives the LiveData box score and play-by-play of every game with a cached
final (`postgame.collect_sources`: one game per tick within 40 seconds, newest
first, retried after 5 minutes doubling to 6 hours; `postgame/sources.json` keeps
the queue) and then builds postgame reports. A game is complete once its archived
pair passes the reconciliation below. Missing sources stay unavailable until
captured. There are no paid model calls or automatic changes to forecasts. Unchanged input signatures
reuse reports. Reports can be regenerated; pregame ledger records remain immutable.

## Evidence and descriptive rules

Reports read hash-verified archived NBA FINAL boxscores and play-by-play. Game
identity, every player's field-goal makes/attempts and threes, team made free
throws, final score, period endings and event chronology must reconcile. Invalid
reports become explicit missing-data results. Source URLs, original retrieval
times and SHA256 hashes are retained. The archive determines coverage; period
endings plus scoring reconciliation do not prove every non-scoring event exists.

Game labels use fixed, versioned heuristics in this order:

- Comeback win: eventual winner previously trailed by at least 15.
- Early runaway: eventual winner reaches a 20-point Q1 lead and never falls below
  10 afterward.
- Late blowout: final margin at least 20, with a second-half lead reaching 20
  and never falling below 15 afterward.
- Competitive finish: final margin within eight. Otherwise, other game pattern.

A shaded fourth-quarter window begins when the eventual winner leads by 20 and
never drops below 15 afterward. This is not a win-probability model or proof of
when a coach considered the game decided. First-quarter dominance alone is not
labeled garbage time. Full-game production totals do not explain the cause of an
opening run; that requires reviewing the relevant possessions.

Rotation display shows recorded substitutions, not inferred five-player lineups
or complete stints. A final recorded second-half exit with at least two minutes
remaining and a sustained margin of at least 20 at exit / 15 afterward raises
**possible blowout rest** for review. That is not confirmed coaching intent,
injury exclusion, or a verified claim that no later unrecorded return occurred.
Entries during the shaded window flag caution before increasing next-game roles.
Foul/injury/coaching explanations require additional sourced review.

## Forecast comparison and feedback

The dashboard's existing selector chooses the latest eligible live pregame run
before examining outcomes, matching scheduled tipoff and teams and excluding
replays. An abstention remains an abstention; an older numeric forecast is not
substituted. No pregame run means actual-only reporting, never a hindsight fit.
All participants, including explicit DNPs, remain in the report.

Postgame errors use **actual minus predicted** (the existing aggregate performance
view retains its documented predicted-minus-actual bias convention). For positive
predicted minutes M and attempts F, with predicted points P and actual m/f/p:

- Minutes effect: `(m - M) * P / M`
- Shot-volume effect: `(f - m * F / M) * P / F`
- Conversion + free-throw effect: `p - P - minutes_effect - shot_volume_effect`

These sum exactly to total points error but are an accounting decomposition, not
causal attribution. The last component includes shot mix, free throws and opponent
adjustment; it must not be called pure shooting luck. Zero denominators suppress
components. Unsupported predictions show no error. Blowouts and DNPs still count
in existing accuracy evaluation; the new labels never filter them out.

Review notes and `review_queue` identify minutes deviations of at least five,
late entries and possible blowout rest. Expensive reasoning is reserved for these
questions. No priors, weights, minutes or intervals change automatically. A future
change must use multiple games, a versioned decision and out-of-sample validation.

## Verification and limitations

The first run produced 14 reports from captured games with no reconciliation
errors. Lakers–Warriors: 127–105; Luka's final recorded exit Q4 12:00 at +34,
Curry's Q3 4:29 at -29. Portland–Phoenix: 124–83; Portland reached 20–0 at Q1 6:08.
These are captured source observations, not a claim of current season completeness.

`tests/fixtures/postgame-lakers.json` retains the complete observed boxscore and
only the play-by-play fields consumed by this analyzer, with original-source
provenance. It is a reduced fixture, not bytes matching the original raw hash.
Tests exercise actual player exits, malformed/incomplete timelines, game-pattern
counterexamples and the error identity. Synthetic threshold cases are explicitly
test inputs, not NBA evidence.

For static dashboard export, copy the operations `postgame/` directory into
`dashboard/public/postgame/` before building and use `?snapshot=1`. A persistent
host is still required for unattended execution; this change does not activate one.
