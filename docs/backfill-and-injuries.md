# Historical backfill and the final pregame checkpoint

## History

Run from the repository root:

```bash
python -m nba_forecast backfill --seasons 2025-26 2026-27 --directory data/history
```

The official NBA `LeagueGameLog` team index supplies completed regular-season
IDs. Two opposing final team rows are required; unfinished games are excluded.
Each ID is then checked against a final NBA LiveData boxscore. The collector
rejects preseason/playoffs/Cup-final IDs, preserves exact raw source bytes,
records actual retrieval times, and validates normalized counts and identities.

Four downloads run concurrently by default; `--workers 8` is the maximum. Every game completes an atomic checkpoint.
The directory has `games/`, `raw/`, `indexes.json` and `manifest.json`. The
compressed `history.json.gz` export is produced only after all requested games
pass. Failed games remain listed; the CLI exits nonzero for incomplete backfills.
Rerun the same command to resume. Existing normalized observations retain their
original `known_at`; cached checksums survive failed retries. A corrupt file
must be investigated and repaired/re-fetched, not silently accepted.

Only run one collector per directory. If a process is forcibly terminated,
verify it has stopped before removing its stale `backfill.lock`. A normal exit
removes the lock. The combined export is derived and rebuilt; per-game facts and
raw archives persist. This is historical evidence collected now, not a backtest
of what the application knew at the original game time.

Use the export directly:

```bash
python -m nba_forecast forecast --snapshot data/session/snapshot.json \
  --history data/history/history.json.gz --db data/forecast.sqlite \
  --output data/session/preliminary.json
```

## The owner's T−30 policy

The final forecast uses the official injury report available **30 minutes before
each game's tipoff**. Earlier forecasts are preliminary. The code selects the
newest report published no later than that checkpoint, allowing at most a
15-minute publication gap. It records later reports separately so the anchor
cannot silently move. This is our forecast policy, not an assertion that late
NBA updates can never occur or that accuracy has been measured.

Official policy/source:
https://official.nba.com/nba-injury-report-2026-27-season/

The NBA describes day-before and game-day submission windows and continual
updates. Its linked PDFs carry Eastern Time publication timestamps. The code
converts them using `America/New_York`, including daylight-saving offsets.

At/after T−30 and before tipoff, create a slate limited to the final window:

```bash
mkdir -p data/final-session
python -m nba_forecast fetch-slate --minutes-ahead 30 \
  --output data/final-session/snapshot.json
python -m nba_forecast injury-check --snapshot data/final-session/snapshot.json \
  --output data/final-session/injury-check.json
```

`injury-check` archives the official index and the selected PDFs with hashes,
publication times and actual download times. Its output lists each game,
checkpoint time, preliminary/final-review/started phase, source freshness and
whether a newer report is available. Missing or stale reports are explicit.

Review the actual PDF for both teams, then supply sourced player availability in
the snapshot. This version does not parse PDF player names or infer that players
omitted from a report are healthy. Probable/doubtful entries must remain withheld
unless resolved into a supported existing status; do not silently map probable
to available. Check `NOT YET SUBMITTED` and game/team coverage yourself.

After review, update `snapshot.as_of` to the actual current evidence cutoff;
it must include the injury-check download and still precede tipoff. Preserve
source publication times; do not rewrite them to make old evidence look new.

```bash
python -m nba_forecast forecast --snapshot data/final-session/snapshot.json \
  --history data/history/history.json.gz --stage final \
  --injury-check data/final-session/injury-check.json \
  --db data/forecast.sqlite --output data/final-session/final.json
```

A final-stage run requires captured, fresh T−30 evidence for **every included
game**, and an actual check in each game's 30-minute pregame window. Mixed early
and late games should use separate snapshots. All 30 roster teams remain in the
snapshot so full-pool coverage is preserved. Unknown availability still yields
no numeric forecast, even in a run labeled final.

The full report metadata is saved inside the immutable forecast input. A later
status change creates a new run; old forecasts stay intact. Aggregate feedback
continues selecting the latest eligible pregame run per player/game/model,
regardless of its preliminary/final label. No scheduler or unattended injury
parser is installed by these one-shot commands.
