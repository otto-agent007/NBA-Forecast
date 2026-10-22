# Schedule-driven operations

## Commands

```bash
python -m nba_forecast fetch-schedule --directory data/calendar
python -m nba_forecast tick --directory data/operations/production \
  --history datasets/regular-season-2026-10-21/history.json.gz
python -m nba_forecast benchmarks --db data/operations/production/forecast.sqlite
```

Python 3.11+ is required. Install **Poppler (`pdftotext`)** for injury extraction
(e.g. `sudo apt-get install poppler-utils` on Ubuntu). Missing Poppler is explicit:
preliminary runs can retain unknown availability, while final extraction failures
block the final job and are retried. No paid model/API dependency is installed.

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
