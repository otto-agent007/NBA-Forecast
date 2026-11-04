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
starting at 9 AM Pacific on its game date and rebuilt whenever its evidence
changes: a new official report edition for the game, ESPN's injury list, or the
roster. The T−30 final checkpoint always produces a run.
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

Unresolved availability/roles or fewer than five projected players mark the
team's total partial; the game total inherits that status. Regulation minutes
the projected rotation leaves unallocated (outs, doubtful players, a thin bench)
are still played by someone: totals schema 4 scores them at the replacement
rate, 0.38 points per minute (the 2025-26 minutes-weighted rate of players
averaging under 20 minutes), as a separate disclosed `residual_points` term,
when at least eight players are projected; with fewer the deficit stays a
disclosed partial, unless every other roster player is out on an explicit
injury-report status (schema 6, `short_bench`): then the bench is short, not
unknown. Everyone available is expected to play, the shortfall is shared
equally among them up to 48 minutes each (the eight replayed seven-player
team-games averaged 42.6, 40.0, 39.0, 35.7, 33.9, 27.5 and 21.3 minutes by
rank), the possession budget may raise their rates, and whatever is left is
replacement production. Hawks at Pacers on 2026-11-02 is the case: the 6 PM report put ten
of seventeen Pacers out, the seven left were projected for 115 minutes, the
total showed 60.9 and the margin went unavailable for the last ninety minutes
before tip; six Pacers played 34 to 45 minutes and scored 104. A withheld roster player outside the projected starting five
whose only reasons are missing evidence (`cold_start`,
`no_scoring_opportunity_evidence`) and whose sourced status is available is
covered by that residual and does not make the total partial. Schema 5 covers
such a player when they carry a reported tag (probable through doubtful) too:
the tag only lowers the chance they take any of those minutes. Under schema 4,
two questionable Indiana and Brooklyn rookies withheld the 2026-10-25 margin. Player
projections are never raised for the residual. Earlier schemas (1–4) keep their
saved semantics. Projected minutes, residual minutes and review counts accompany
each team. A team with no supported projections and a game missing either team
estimate stay blank. Player searches, filters and sorting never change totals,
and individual uncertainty bounds are not summed into an unsupported game
interval.

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

The season calendar is downloaded once and checked once a day, on the first
tick after 8 AM Pacific (before the 9 AM forecasts), or on the next tick after a
forecast finds a moved tipoff (`schedule/fetch-state.json`). If NBA's download
fails, ESPN's slate for today and tomorrow verifies near-term games against the
saved calendar, and that checked copy is used for the rest of the day
(`schedule_source: espn_verified_cached_nba`). If both fail, the saved calendar
stays in use (`unverified_cached_nba`, with a warning) and the check retries
after five minutes, doubling up to three hours. A calendar check never withholds
forecasts: each forecast's preflight confirms its own matchup and tipoff against
ESPN's scoreboard. The dashboard marks a game final once its final box score is
captured.

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

The tick reads the saved NBA season calendar (checked once a day, above),
finds per-game jobs in the next 24 hours, and saves one preliminary or final
run for each due game. The final
window opens at T−30; the report published at or immediately before that anchor
is preserved. The timer polls each minute; network/publication delays mean this
is not a guarantee of execution at exactly T−30:00. Missing final checkpoints
remain errors, never backdated successes: a tipped game without its final run
is recorded once in `missed-finals.json` (game, tipoff, the tick that first saw
it) and keeps the tick `partial` for 48 hours after the tipoff; older misses stay
on the record and in the dashboard's count without marking every later tick.
Later PDF editions are archived as
separate evidence. To deliberately revise after a later change, provide new
sourced review input before tipoff; the original run remains immutable.

After an outage, `python -m nba_forecast recover-evidence --directory DIR
--games ID... --daily-report DATE...` fetches what the ticks would have
captured, as labeled late captures that never replace a live record: each
game's T-30 report edition (`injuries/games/GAME_ID/final.json`, which later
forecasts read for a player's reported-out games), ESPN's recorded closing line
(`market/game-lines`, preferred to a stale pregame capture and flagged
`close_recovered` in the market report), Kalshi's last pre-tip prices from its
candlesticks (`market/kalshi`, timed by the exchange) and the 9 AM Pacific daily
report. ESPN's injury list and game summaries as they stood at the time cannot
be recovered, so a team the official report left uncovered stays unresolved.
The missed checkpoints themselves stay on the record.

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
immutable inputs and hashes retain their original format. New ledgers store
each distinct input history once (`histories`, keyed by `history_sha256`);
the `runs` view still returns every run's complete row, including
`history_json` and its insertion-order `rowid`. A ledger created before this
change keeps working unchanged until it is rebuilt with the ticks stopped:

```bash
python -m nba_forecast dedupe-histories --directory data/operations/production
```

The rebuild holds the tick lock and the ledger write lock, compares every run
and evaluation revision value-for-value before replacing the file, and leaves
the original beside it as `forecast.sqlite.pre-history-dedupe` (a hard link)
for the owner to delete. A conflicting history under one digest aborts it.

An idle tick does little work. Live runs are rescored only when one of their
`finals/` files or their latest evaluation revision changes
(`evaluation-state.json`). Performance, benchmarks, calibration, postgame and
daily reviews are the derived service's job, not the tick's: `python -m
nba_forecast derived` runs from its own one-minute timer (`nba-derived.timer`,
at :30, below the tick's CPU priority) and rebuilds them only when the ledger,
finals contents, archived box scores or play-by-play, calendar games, active
model, roster presence, Pacific date, code or an explicit request
(`derived-request.json`, written by a tick that recovered run artifacts)
changes, and at least every six hours. A rebuild takes minutes (on 2026-10-28
the performance report alone took 84 s and the ticks that rebuilt took 5 to 10
minutes, so near-tip Kalshi captures were skipped and the T−30 checkpoints ran
late); beside the tick it costs the checkpoints and captures nothing. The tick
carries the last rebuild's summary, errors and warnings into its report
(`derived_reports`, `derived-reports.json`); the service writes
`last-derived.json` and appends `derived.jsonl`, and each tick report lists its
phase `timings`. The ledger runs in SQLite's write-ahead log mode, so the
service's long reads never hold up the tick's writes. Outcome discovery still
runs every tick and keeps the model status's outcome summary current.

A forecast job prepares the league-wide shot, shot-quality and recap evidence
from the immutable archives and preflight replays the same archives to verify
it. Each archived source replays to the same result whatever game is being
forecast, so before 2026-11-02 every game of a slate replayed every source of
the season twice: a forecast cost 30 to 35 s, 23 to 30 of them in that
preparation, and a fifteen-game slate 8 minutes per injury-report edition,
beyond the tick's ten-minute start limit (the NBA publishes an edition every
fifteen minutes on a game day, and each edition re-forecasts the slate). Now
each source is replayed once per tick process and each league-wide history
map built once per history object (`nba_forecast/replay_cache.py`): the first
game of a tick pays the replays (about 20 s), each further game 6 to 8 s, and
the forecasts are byte-identical. The replay key carries the archive digests,
the rows replayed against and each archive file's size and modification time,
so a rewritten archive is replayed and rejected as before. The replay cost of
that first game grows with the season's archives; a cache that outlives the
process would be the next step.

## Participation

Every projected player has a `participation_probability`: the share of the
team's last ten games (since he joined it) in which he appeared with positive
minutes, leaving out games his official injury report listed him out of (each
earlier game's final-stage checkpoint, `injuries/games/<id>/final.json`, parsed
again against the current roster; a game without one counts as before), shrunk
by three pseudo-games toward the 2025-26 rate for his role
(18% under 10 minutes a game, 70% for 10–20, 79% for 20–28, 82% above), then
scaled by a reported status (probable 1.0, questionable 0.64, doubtful 0.04,
day-to-day 0.5; fitted on the midday reports of 2024-25 and 2025-26). A team's probabilities are scaled into the range the rules
and the data allow: down if they sum past the 13-player active list, up to at
least ten when ten or more players are projected (teams used at least ten
players in three quarters of 2025-26 games; the priors were measured on
everyone a box score lists, and the live roster has already lost its out
players). The window stays inside the current season; before a team's third
game the role prior stands alone. A player listed by a new team without a
positive-minute appearance for it yet (flag `new_team_role_unobserved`) is
projected conditionally on playing from his earlier role, with those listings
lowering his probability, instead of withheld. Questionable players are projected conditionally on
playing instead of withheld; unknown players remain withheld. Minutes and points for a
player are what he gets when he plays; the 240-minute budget, team totals,
game totals and margins weight them by the probability (totals schema 3). The
ledger scores the probability against actual participation (Brier, in
`metrics.participation`), and the dashboard shows "N% to play" under 95%.

Since 2026-10-27 the team totals agree with the margin forecast
(`scoreline.py`). The margin is located from the player model's totals blended
with the team rating; each team's projected players are then scaled so the
two totals differ by that location and the game total is unchanged, each
total moving half the gap. A player's points, their SD and interval and his
expected makes (reallocated over the same attempts) scale together, and
`components.scoreline` keeps his player-model mean, from which the margin is
always located, so a saved forecast reproduces its own margin. On the
three-season replay this cut team points MAE by 0.12 with player points
unchanged (`docs/backtest.md`, reference 2026-10-27). The market report's
`player_sum_margin` is the one the margin forecast saved.

## Market lines

Each tick archives the sportsbook lines ESPN's game summary carries (spread,
total and moneyline per provider, with opening and current values) for every
upcoming game at the forecast cadence (hourly while the game waits for the
morning checkpoint, every 15 minutes within a day, every 5 minutes in the last
30), under `market/game-lines/GAME_ID.jsonl` with source URL, hash and
retrieval time; a record is appended only when a provider's lines change. The
last capture before tipoff is the closing line. `market/report.json` compares
each finished game's latest pregame forecast with its closing line: the
model's and the market's absolute error against the final margin and total,
the record of model leans at several edge thresholds, and winner Brier scores.
The model margin is the published one, the margin forecast's location (the
player sum blended with the team rating), with `player_sum_margin` beside it;
before 2026-10-27 the report scored the player sum, which on that week's games
read 12.15 against the published margin's 11.38.
Leans on a small sample are not an edge. Player props are not captured; ESPN
does not publish them.

Since 2026-10-27 each tick also captures Kalshi's game markets as a second
source (`nba_forecast/kalshi_lines.py`, `market/kalshi/GAME_ID.jsonl`, raw
responses under `market/kalshi/raw/`): the home winner market's bid-ask mid as
a win probability, and the spread and total as the strikes where the traded
probabilities cross one half, interpolated. It runs at the same cadence plus
every minute in the last five before tip, because an exchange's last price is
the real close. The dashboard lists Kalshi beside the sportsbook, and
`market/report.json` carries `kalshi_margin`, `kalshi_abs_error` and a Kalshi
winner Brier. Measured on 2026-10-27 over 23 games, ESPN's sportsbook line sat a
median 0.33 points from Kalshi at tip (`scripts/research/line_lag.py`).

## Preforecast cross-check

Before the automated tick or a `forecast --mode live` call executes the model,
a separate deterministic preflight gate validates the existing input contracts
and compares NBA evidence with ESPN's public scoreboard, team roster and game
injury feeds. It blocks a forecast only when the forecast would be wrong:

- invalid inputs (`input_contract`) or corrupt injury report evidence
  (`injury_provenance_invalid`);
- applied availability contradicting its source (`availability_conflict`);
- ESPN showing the game started, finished or postponed (`matchup_not_scheduled`);
- ESPN listing the matchup at a different tipoff (`tipoff_changed`), which also
  triggers a calendar check so the next slot uses the new time.

Everything else is a visible note (`notes`) on a forecast that still runs:

- The official NBA roster is authoritative. ESPN roster differences are noted
  (`roster_difference`); ESPN often omits two-way players, whom the official
  injury report covers. Players ESPN lists but NBA does not have no forecast.
  Spellings that differ only by a generational suffix ("Bobby Portis Jr." /
  "Bobby Portis") are matches when, without suffixes, both rosters still name
  each player exactly once (`name_suffix_matches`).
- The verified official injury extraction is the availability source for a team
  it completely covers for this matchup and Eastern date. The gate re-extracts
  coverage and statuses from the archived PDF after verifying its hash, official
  URL and publication chronology. Report names written "Udeh, Jr., Ernest" keep
  the suffix with the surname; a spelling that differs only by suffix joins a
  player only when it names exactly one teammate.
- Until the official report covers a team (for example "NOT YET SUBMITTED"),
  ESPN's injury list for that game supplies availability
  (`status_basis: espn_injury_list`, note `official_report_pending`): Out and
  suspended players are out, Day-To-Day and unrecognized statuses are
  questionable (no numeric forecast), and unlisted players are available,
  conditional on participation (flag `availability_official_report_pending`).
  The official report replaces it as soon as it covers the team. A clean missing
  team block can still use the explicit zero-injury fallback described below.
  With neither source, players without a status get no numeric forecast
  (`injury_status_unavailable`).
- An unconfirmed matchup, unavailable or stale ESPN evidence and an unresolved
  starting five (starters only label team totals) are notes.

A blocked job keeps its injury checkpoint and an archived audit under
`preflight/GAME_ID/records/`. A failed job is retried after fifteen minutes
(five within the final window) (`state.json` `failed_jobs`, tick
`deferred_jobs`) instead of every minute. Rosters are reused for three hours
for preliminary forecasts and fifteen minutes at the final checkpoint. New validator versions invalidate completed job keys.
Independent response bytes, retrieval times and SHA256 hashes are preserved
under `references/`; schedule responses are reused for at most five minutes and
rosters for fifteen. Demo and
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

The repository includes user-systemd service/timer templates in `deploy/systemd/`
(the tick, the derived-report service and their one-minute timers; the
installer `scripts/install_services.py` writes all of them, and the dashboard's,
from `nba_forecast/units.py`). On an always-on Linux host, replace the absolute
paths in both service templates, ensure `/usr/bin/python3` is Python 3.11+,
install Poppler, and run one tick first. Then copy the edited units into
`~/.config/systemd/user/` and run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now nba-forecast.timer nba-derived.timer
systemctl --user list-timers nba-forecast.timer nba-derived.timer
journalctl --user -u nba-forecast.service -u nba-derived.service
```

User services need an active user manager; configure lingering on the host if
runs must survive logout. These templates are not automatically installed by
fetch-schedule or tick. A temporary chat workspace is not a 24/7 deployment.

The units run a pinned copy of the package under
`data/operations/production/runner-releases/<digest>/`, never the working tree.
After a change is merged, deploy it with one command from the repository root:

```bash
python3 -m nba_forecast.deploy --directory data/operations/production --dry-run
python3 -m nba_forecast.deploy --directory data/operations/production
```

It pins the release, builds the refined model artifact bound to that package
digest from the active model's parent, backs up and repoints the activation
while holding the tick's and the derived service's locks (a running tick or
rebuild finishes first; `--wait` sets how long to wait, 900 s by default),
writes the derived service and its timer for a host that does not have them
yet and enables the timer, rewrites `WorkingDirectory` in every service unit,
reloads systemd and restarts the dashboard, and writes
`production/deployed.json` with the commit, package digest, release, artifact
and the units written or rewritten. Run it again and it changes nothing. The
next tick re-forecasts every pregame game under the new artifact; the next
derived run refits the prospective interval calibration, which is bound to the
model key.

`last-tick.json` and `last-derived.json`, the append-only `ticks.jsonl` and
`derived.jsonl`, service exit status and journald surface failures and missed
final windows. Configure your host monitor to flag nonzero exit status and a
stale heartbeat (no finished tick for 10 minutes; no finished derived run for
an hour).
No email/Slack messages are sent. Back up the entire persistent operations
directory, especially `forecast.sqlite` with its write-ahead log
(`forecast.sqlite-wal` and `-shm`, present while a connection is open; a copy
without them lacks the latest commits), schedule versions and raw evidence.
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

Intervals use the measured heuristic half-width (1.70 sd; see
`docs/model-upgrade.md`) until the prospective calibration is ready. That
calibration pools residuals across model versions sharing the interval family,
so it keeps accumulating through code changes. There is no automatic parameter
promotion. Every change to how a forecast is computed is judged on the fixed
season replay first (`python -m nba_forecast backtest`, `docs/backtest.md`,
rule in `AGENTS.md`): paired against the committed reference, a change must not
score worse beyond noise, and a new model feature must score better. Review
errors by minutes, rates, roles and availability, change one explicit
hypothesis, run the backtest, then compare live results on the same population.

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
exceeds240 minutes, it is scaled too. A shortfall raises supported players' conditional
minutes by a bounded share; the rest is scored only in the team total, at the
replacement rate, never in player projections.

Team possession budget (model 0.4.5). A team uses about the same number of
possessions every night: in 2025-26, FGA + 0.44·FTA + TOV per 240 team minutes
averaged 112.6, with a spread of 2.5 between teams and 6.1 from game to game.
Player rates learned in other roles (last season's first option, a new
signing's old team) do not add up to that on their own; in the first week of
2026-27 the stacked rosters projected 10–14% above any usage they had ever
shown, and the eight team-games projected more than 5% above their actual
usage missed high by ten points. After the minutes budget, each team's
expected usage (probability-weighted) is scaled to its own recency-weighted
level (0.90 per game, shrunk to the league with ten pseudo-games), less the
bench rate (0.38 per minute) for minutes left to replacements, within 0.85 to
1.10; raising needs at least eight projected players, lowering applies to any
team. Manual adjustments are locked and count as fixed usage. The enhanced
path applies the same budget again after pace and vacancy allocation, with
the pace factor on the target. Each projection carries the audit under
`components.team_usage` and the flag `team_usage_scaled` or `team_usage_raised`.
Player details disclose the historical PPG, games and source actually used.
Regenerated preliminaries remain selectable after a final run is saved.
