# Schedule-driven operations

## Daily and final checkpoints

The automatic tick captures the latest official injury PDF at **9:00 AM
America/Los_Angeles** each day, including days with no scheduled games. It
archives the index/PDF bytes and provenance, records one daily capture under
`injuries/daily/`, and retries failures on subsequent ticks. Restarting after
9 AM captures the report then, with the actual retrieval time; it never
backdates a missed capture. `injuries/status.json` exposes waiting, captured
or retrying state to the dashboard.

Each scheduled game gets preliminary forecasts before its final window,
starting at 9 AM Pacific on its game date and refreshed in 15-minute slots.
Earlier upcoming jobs are recorded as waiting, without treating unpublished
next-day reports as forecast failures. An early-morning game's final window
still opens at T−30, even before 9 AM. At tipoff minus 30 minutes, the next automatic
tick captures that game's official checkpoint and saves a final-stage run;
five-minute refreshes can retain subsequent late updates as independent runs.
The dashboard lets you inspect the latest preliminary and final forecasts
separately. Unknown availability and unsupported production remain blank.
Saving either stage now also requires the cross-check below to pass.
The live dashboard reloads saved evidence every 30 seconds without overlapping
requests, preserving the selected game, filters and any explicitly selected
forecast stage. Its default view switches to the final when that run arrives.
Static snapshot previews remain fixed until manually refreshed.

Each selected forecast shows away and home team point totals and their combined
game total. Model `regular-season-baseline-0.4.2` saves these aggregates with the
forecast; older dashboard runs derive them from their original saved player
predictions without changing ledger rows. Totals sum unrounded supported means,
then round for display. They cover regulation and remain conditional on player
participation and the projected rotation. They do not include overtime.

Unresolved availability/roles, fewer than five projected players or unallocated
team minutes marks the team's total partial; the game total inherits that status.
A fully allocated rotation remains complete when another roster player, outside
the projected starting five, has only
the `cold_start` flag, zero captured positive-minute appearances and sourced
available status. These unprojected players are disclosed separately. This does
not assert they will be inactive or invent zero scoring/minutes for them.
Projected minutes and review counts accompany each team. A team with no
supported projections and a game missing either team estimate stay blank.
Player searches, filters and sorting never change totals. No missing scoring
or minutes are extrapolated, and individual uncertainty bounds are not summed
into an unsupported game interval.

Coverage schema 2 separates these roster observations from rotation completeness.
The dashboard derives current coverage annotations from each run's original
player rows and exposes `saved_totals` separately. Original schema 1 aggregates
and ledger hashes remain unchanged and still validate under their original rules.

Every captured check is retained under `injuries/games/GAME_ID/records/`,
including checks for which roster or parsing failures prevent forecasting.
`final.json` preserves the first fresh T−30 checkpoint for the scheduled start;
`latest.json` tracks later checks. Rescheduling creates a new checkpoint while
the original records remain archived. A one-minute persistent timer targets
T−30 within its polling resolution; source latency and host outages can delay
it. NBA can publish further updates after our final checkpoint.

If a calendar/roster endpoint fails, the tick labels use of the saved calendar
or a roster still within the existing 48-hour freshness bound. Observation
times are preserved. Without a usable roster, it retains injury evidence and
withholds forecasts. Daily injury capture is independent of calendar access.
Source failures and retry status remain visible; enabling a timer alone does
not establish that every scheduled capture succeeded.

When NBA's main player directory is inaccessible, roster refresh uses all 30
official team roster pages. Each page must match the requested team and season;
player identities must be unique across the complete collection. Raw responses,
hashes and original retrieval times are retained, and the oldest team capture
bounds the assembled roster's observation time. Partial collections fail closed.
Sources are cached for at most 15 minutes. This does not bypass independent ESPN
membership checks or infer player availability.

The persistent runner uses a pinned, content-addressed copy of the Python
package. After a verified code change, create a new release with
`python3 -m nba_forecast.runner_release --directory data/operations/production/runner-releases`,
set the service's `WorkingDirectory` to the printed path, retain absolute data
and history arguments, then reload/restart the user service. Active ticks keep
one package version even while the worktree changes. The dashboard service
continues to use the worktree and requires its usual restart for Python changes.

## Commands

```bash
python -m nba_forecast fetch-schedule --directory data/calendar
python -m nba_forecast tick --directory data/operations/production \
  --history datasets/regular-season-2026-10-21/history.json.gz
python -m nba_forecast benchmarks --db data/operations/production/forecast.sqlite
```

Python 3.11+ is required. Install **Poppler (`pdftotext`)** for injury extraction
(e.g. `sudo apt-get install poppler-utils` on Ubuntu). Missing Poppler is explicit:
extraction failures block new forecasts and are retried. Injury captures remain
available even when a forecast is blocked. No paid model/API dependency is installed.

The tick refreshes the NBA season schedule, finds per-game jobs in the next
24 hours, and saves one preliminary or final run for each due game. The final
window opens at T−30; the report published at or immediately before that anchor
is preserved. The timer polls each minute; network/publication delays mean this
is not a guarantee of execution at exactly T−30:00. Missing final checkpoints
remain errors, never backdated successes. Later PDF editions are archived as
separate evidence. To deliberately revise after a later change, provide new
sourced review input before tipoff; the original run remains immutable.

Already-completed jobs are skipped. Changed tipoff times and changed review
content trigger new keys. SQLite is authoritative if a process crashes after
saving a forecast; lost output files are rebuilt from immutable run inputs.
Linux `flock` prevents overlapping ticks and releases on process death. Backfill
has its separate documented exclusive-file lock; do not confuse the two.

The tick prioritizes forecasts, then downloads uncached current-season finals,
then evaluates matching saved runs. Newly downloaded history enters the next
tick. Missing actual players stay pending; roster changes are not invented DNPs.
Postgame corrections require explicit re-fetch/import (cached finals are not
silently changed). A changed actual tipoff may fail strict ledger time matching;
that error requires review instead of silently rewriting the saved forecast.

Imported history and cached finals are merged by `known_at`: newer evidence
wins, with cache precedence on equal instants. This preserves explicit imported
corrections even when the local final cache contains an older download.
Routine ledger readers stream run metadata; saved history is loaded only when
rebuilding a missing profile or computing a selected run's benchmark. Existing
immutable inputs and hashes retain their original format. The ledger still
stores full inputs for every run, so storage and backup capacity must account
for repeated history snapshots.

## Preforecast cross-check

Before the automated tick or a `forecast --mode live` call executes the model,
a separate deterministic preflight gate validates the existing input contracts
and compares NBA evidence with ESPN's public scoreboard and team roster feeds:

- Matchup, home/away teams, exact tipoff and an unstarted scheduled status must agree.
- Every roster name on each playing team must match the independent team/season
  roster after exact normalization. Differences are reported for review; one
  provider never silently overwrites the other.
- The verified official injury extraction must completely cover both teams for
  this exact matchup and Eastern basketball date. A next-day report for the same
  team cannot satisfy today's game. The gate re-extracts coverage and statuses
  from the archived PDF after verifying its hash, official URL and publication
  chronology. Supplied coverage labels alone cannot pass. Applied availability
  must agree with the report or carry a matching newer sourced review.
  A clean missing team block can instead use the explicit zero-injury fallback
  described below; this does not override official submitted statuses or errors.
- Each team must have five unique eligible projected starter IDs supported by
  captured role/minutes evidence. Out players cannot occupy a starting slot.

Failures block model execution and ledger insertion, retaining the injury
checkpoint and an archived audit under `preflight/GAME_ID/records/`. Blocked jobs
retry on later ticks. New validator versions invalidate completed job keys.
Independent response bytes, retrieval times and SHA256 hashes are preserved
under `references/`; schedule responses are reused for at most five minutes and
rosters for fifteen. An unavailable reference blocks the forecast. Demo and
replay modes remain offline and do not assert this live cross-check passed.
Live CLI forecasts use `--cache-dir` (default `data/raw`) to locate those
hash-named injury PDFs; use the same directory used for `injury-check` and
`apply-injuries`.

Passing verifies input consistency, not official lineup confirmation or model
accuracy. Questionable players remain withheld. The dashboard distinguishes a
saved run's gate result from the latest attempt; older saved runs are immutable
and labeled unverified when they predate the gate.

Player outlooks select one projected five per team, preferring the latest
captured starters and then supported role/minutes evidence. Selection happens
before search, status filters or sorting. Sparse evidence leaves unresolved
slots, never invented names. Team buttons expose either roster directly;
observed Season PPG stays visible when projections are withheld, with season,
sample size and observation time in its tooltip. These averages can be newer
than a selected historical forecast and are not that run's model inputs.

## Early-season minutes and roster review

Model `regular-season-baseline-0.3.0` keeps prior-year DNPs in saved history and
participation counts, but estimates a healthy role from prior appearances.
Current-season DNPs still affect role estimates and trigger review. Current-team
observations establish a traded player's minutes role; older teams still inform
shooting and per-minute production. A new-team DNP without a positive-minute
role is withheld unless a sourced adjustment is supplied. Profiles and forecasts
use the same estimator. The existing 240-minute team budget remains, so a raw
role estimate may be scaled in the final team rotation.

Roster disagreements block by default. A short-lived `roster-reviews/GAME_ID.json`
list can name an explicitly researched peripheral disagreement, including game,
tipoff, team, observation/expiry, reviewer/reason, exact NBA-only and ESPN-only
names, and `quarantine_player_ids`. It requires an official roster captured within fifteen minutes, with a verified hash. The official
NBA team roster must have the same complete player ID/name set as the captured roster.
The exception cannot include a projected starter, ignore undeclared differences,
accept stale evidence, or project a disputed player. NBA-only discrepancies must
be quarantined as unknown; ESPN-only candidates receive no numeric forecast.
The original disagreement is preserved as a warning, and both the selected saved
run's warning and the latest attempt remain inspectable. This is a disclosed
source adjudication, not a claim of full independent agreement. A changed review
file triggers a new immutable job; expiry or new source differences blocks it.

## Explicit zero-injury reports

A team can have no player rows in the NBA PDF even when it has no injuries to
report. For a clean absent team block, preflight additionally requests ESPN's
exact game summary and requires an explicit team entry with `injuries: []`.
It validates event ID, regular-season year/type, tipoff and home/away teams,
archives the bounded response and hash, and requires retrieval within five
minutes of cutoff and before tipoff. Missing team/list fields never imply zero.
The official PDF must still verify and cover the basketball date; unsubmitted,
conflicting or unparsed team sections block supplementation. Existing official
or reviewed player statuses are preserved, including reviewed unknown status.

Eligible unknown players receive a conditional Not listed basis. They can get
supported numeric forecasts, with the same participation caveat as omitted
players in a complete official team report. Fresh roster, matchup, final-window
and five-starter checks still apply. The source provides no publication time;
only its actual retrieval time is asserted. The live CLI uses this same gate.

`availability/GAME_ID/records/` archives each captured update and supersession;
`latest.json` exposes the latest matching-game state. Rejected/conflicting team
reports never become positive UI claims. Nonempty or unavailable refreshes
supersede an older zero-injury claim while retaining the archive. A recovery
after tipoff is displayed separately and cannot produce a backdated forecast or
change a historical run's inputs. Daily 9 AM and T-30 official PDF archives remain
unchanged.

## Schedule and context

`data/calendar/latest.json` points to the latest normalized calendar;
`versions/` preserves each observation and `raw/` preserves source bytes and
hashes. Each version records added, removed and changed game IDs. TBD games,
unknown teams, postponements and missing venue zones remain explicit. The
initial capture has 1,206 entries, not a claimed complete 1,230-game assignment.
Refresh to pick up remaining Cup-related and rescheduled games.

UTC controls deadlines. Venue local time uses IANA zones; unfamiliar venues get
null local time, not a guessed home-team zone. Context includes calendar rest,
back-to-backs, three-in-four, road stretches, venue changes and time-zone shifts.
Rest uses Eastern basketball dates consistently; travel describes scheduled
venues, not measured flights, and no arbitrary fatigue penalty is applied.

## Availability and reasoning review

`apply-injuries` can be used independently with `--snapshot`, `--schedule`,
`--injury-check`, `--cache-dir` and a fresh `--output`. It verifies archived PDF
hashes, extracts layout text and matches exact normalized names within the
correct team/game/date. Available and Out are explicit; probable, doubtful and
questionable stay unresolved. Omitted/conflicting/unmatched players become
unknown. Missing team reports and NOT YET SUBMITTED remain review issues.

A tick writes `runs/RUN_ID/profiles.json`, `snapshot.json` and `forecast.json`.
Profiles cover every roster player and expose prior/current observations,
recent minutes/FGA, scoring attempt rates, starter evidence when actually
present, and potential vacancies. Prior cached history without starter fields
cannot establish starter/bench labels. No automatic vacancy redistribution is
performed. Numerical projections still require resolved availability.

Place reviewed evidence at `DIRECTORY/reviews/GAME_ID.json` before a tick:

```json
{
  "game_id": "0022600006",
  "availability": [
    {
      "player_id": "NBA_PLAYER_ID",
      "status": "available",
      "status_source_url": "https://SOURCE_URL",
      "status_observed_at": "ACTUAL_KNOWN_TIME_WITH_OFFSET",
      "status_published_at": "SOURCE_PUBLICATION_TIME_WITH_OFFSET"
    }
  ],
  "role_adjustments": []
}
```

This is a template, not usable evidence. Replace each placeholder with a
verified value. Role-adjustment objects follow [the existing input contract](usage.md).
Review timestamps must precede cutoff and remain recent; older publication
cannot supersede newer official status evidence. Fresh downloads do not make an
old publication new. Changed review content produces a new immutable run before
tipoff. Late review files cannot create a retroactive live prediction.

During a tick, an availability override superseded by newer official evidence
is skipped and logged as `superseded_review_availability`. The original review
is preserved in the saved snapshot along with the warning; other valid
overrides and role adjustments still apply. Invalid identities, sources and
future or inconsistent evidence timestamps still reject the review. Direct
`apply_review` callers remain strict unless they explicitly request this policy.

Routine collection, exact matching, calculations and validation are deterministic.
Use the forecast review queue for data verification, and capable reasoning for
coaching, new rotations, injury beneficiaries and matchup interpretation. The
pipeline produces this queue; it does not secretly invoke a paid model.

## Persistent execution

The repository includes user-systemd service/timer templates in `deploy/systemd/`.
On an always-on Linux host, replace both absolute repository paths in the service,
ensure `/usr/bin/python3` is Python 3.11+, install Poppler, and run one tick first.
Then copy the edited units into `~/.config/systemd/user/` and run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now nba-forecast.timer
systemctl --user list-timers nba-forecast.timer
journalctl --user -u nba-forecast.service
```

User services need an active user manager; configure lingering on the host if
runs must survive logout. These templates are not automatically installed by
fetch-schedule or tick. A temporary chat workspace is not a 24/7 deployment.

`last-tick.json`, append-only `ticks.jsonl`, service exit status and journald
surface failures and missed final windows. Configure your host monitor to flag
nonzero exit status and a stale heartbeat (no finished tick for 10 minutes).
No email/Slack messages are sent. Back up the entire persistent operations
directory, especially `forecast.sqlite`, schedule versions and raw evidence.
Do not expire raw files referenced by stored forecasts; plan retention/storage
before leaving minute-by-minute capture running for a season.

## Learning loop

`performance.json` measures error, minutes versus scoring-rate contributions,
coverage, bias and uncertainty intervals. `benchmarks.json` compares the same
selected projected player-games against (1) mean points in the last ten eligible
games and (2) expected minutes times their aggregate historical points/minute.
Only saved pregame history is used; explicit DNPs remain observations. Out-only
zeros are not used to make these paired comparisons look artificially good.
Missing or unresolved projections remain visible in the overall ledger coverage.

Current intervals remain heuristic. There is no claimed improvement until enough
prospective outcomes exist, no fabricated historical injury backtest, and no
automatic parameter promotion. Review errors by minutes, rates, roles and
availability, change one explicit hypothesis, version the model, and compare
subsequent results on the same population.

## Multi-season scoring priors (model 0.4)

The model can consume `historical-priors.json` containing source-stamped career
season totals for the three completed seasons before the forecast season. The
versioned October23 snapshot is in `datasets/scoring-priors-2026-10-23/`.
Preflight verifies response hashes, exact season totals, identity joins and the
retrieval cutoff. Minutes based on rounded season MPG are labeled estimates;
points, shots and game counts retain the source totals. Traded-player total rows
are used once. Source rosters establish player identity, not future availability.

These priors replace overlapping game-history scoring priors. Season weights are
1/0.65/0.4225, capped at20 effective games per season before current-season decay.
Rates use0.90 recency decay. Current-team role minutes remain separate. In the
team budget, observed starters and supported high-minute rotation roles are
preserved while excess bench estimates are scaled first. If the core alone
exceeds240 minutes, it is scaled too; missing minutes are never invented.
Player details disclose the historical PPG, games and source actually used.
Regenerated preliminaries remain selectable after a final run is saved.
