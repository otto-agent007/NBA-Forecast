# Full player box scores

New forecasts expose estimated MIN:SS, PTS, REB, AST, STL, BLK, FGM/FGA, FG%,
3PM/3PA, 3P%, FTM/FTA, FT%, OREB/DREB, TOV, PF and signed +/-. Team grouping,
the projected five and role/points ordering are retained. Original forecasts
remain frozen: fields that were not saved in an older run remain unavailable.

Actual MIN:SS comes from a source with seconds. Rounded source minutes are
displayed as minutes without invented seconds. Projected seconds are estimates.
Shooting ratios are derived from counts; zero attempts have no percentage.
Missing optional observations do not become zeros or enter rate denominators.

## Statistical extension

`full-boxscore-1.0.0` is versioned separately from the point model and its
calibration artifact. It preserves the point mean, uncertainty and opportunity
volume. Expected two-point, three-point and free-throw makes receive a bounded
proportional probability adjustment so `PTS = 2*FGM + 3PM + FTM`, with makes
never exceeding attempts. An impossible scoring allocation is rejected.

The selected point model can change those inputs before this extension runs.
Version 0.6.0 adds [injury-aware minutes and location-adjusted shooting](forecast-refinements.md),
plus separately labeled winning-margin estimates.

Rebounds, assists, steals, blocks and personal fouls use per-minute rates with
observed exposure. The prior is capped at 600 effective minutes; available exact
season totals from the last three completed seasons replace overlapping prior
game evidence, weighted by recency (1, 0.65, 0.4225) and capped at 20 games per
season. Current observations decay by 0.97 per observation. Fixed regularizers
carry 60 minutes of exposure, not asserted player observations. Rebound splits
use paired offensive/defensive evidence with a 20-rebound regularizer and sum
to the projected total. Fouls are capped at six. A missing stat has no estimate.

Plus/minus uses observed on-court differential per minute, shrunk toward zero
with 300 pseudo-minutes. It is a noisy descriptive estimate, not an individual
impact rating. Extra-stat uncertainty is not calibrated. The feedback ledger
tracks each stat's errors and sample count; percentage errors have no artificial
minutes decomposition. No claim of improved future accuracy follows from
collecting more fields.

Verified shot locations supply a spatial attempt model: observed zone shares
allocate projected two-point and three-point attempts separately. Unknown
locations and unsupported shot categories remain explicit. This does not add an
unvalidated shooting-quality multiplier to the point model. A prepared snapshot
binds eligible shot evidence to immutable hashes and its real cutoff; preflight
replays the archived sources and rejects tampering or future-known inputs.

## Automatic postgame collection

The runner collects final boxes after pregame jobs. When the NBA LiveData feed
is unavailable, official NBA game pages provide exact NBA game/team/person IDs,
full boxes and play-by-play with NBA legacy shot coordinates. Source bytes,
retrieval times and SHA256 hashes are retained. Game identity, played players,
final scores, shot makes/attempts and rebound identities must reconcile. Complete
official finals additionally require both teams' regulation/overtime minutes and
on-court plus/minus sums to agree with the game. Each player's time is given to the
second, so a team's sum may miss the game by half a second per played player (at
least 2 s): 0022600142 has both teams 3 s short, in LiveData and on the page alike.
A missing or broken row costs minutes, not seconds.

A listed player whose comment the parser does not recognize, with no clock and no
counts, did not play: the row is a DNP that keeps the comment as its reason and a
`participation_audit`. Counts with no clock still contradict each other and fail the
page.

Each tick fills optional fields (rebounds, assists, steals, blocks, starter) into
captured finals from archived LiveData finals. Every response is archived before
it is checked, so the archive also holds boxes read before LiveData turned final;
those are skipped, and a damaged archive is left out and reported as an
`enrich_finals` warning, since this step only adds optional fields.

### One bad source costs one game

Every response is archived before it is checked, and stat corrections, layout
changes and republished pages arrive unannounced, so one game's source can stop
replaying at any time. That costs that game, never the tick or every forecast:

- **Merging captured finals** (`_merge_finals`): a final that cannot be read or
  validated is left out and its game keeps the input history's rows; a final whose
  season, tipoff or teams differ from the input history's replaces that game. Both
  are tick errors (`finals_cache`), and the tick goes on. Player sync merges the
  same way and lists them under `skipped_sources`.
- **Forecast preparation**: a game whose recap page, shot collection or archives no
  longer replay is left out of role context, shot profiles and shot quality, exactly
  as if it had not been collected, and reported once per tick as a warning
  (`role_context_source`, `shot_profile_source`, `shot_quality_source`). Role context
  first falls back to the game's next newest pages, up to three. Preflight replays
  what was prepared, so the run's evidence is still verified; bad evidence is never
  used, only no longer fatal.
- **Final sync and evaluation**: anything that escapes their per-game handling is a
  tick error (`final_sync`, `evaluate_runs`); later steps still run.
- **Postgame**: an unreadable provenance file is skipped by the archive scan.

Called directly, the preparation and enrichment functions still raise on any source
problem.

Source corrections retain the previous final cache by hash and record per-field
before/after values. They create new evaluation revisions rather than rewriting
forecasts. Explicit source-backed DNPs missing from the other source stay labeled
as unmatched; there is no invented identity join.

Final discovery attempts at most four uncached games per tick and retains a
persistent current-season retry queue, including games older than 48 hours.
An unavailable ESPN identity or roster can fall back to the verified official
NBA final and play-by-play. Canceled games are excluded. Cached finals must still
match their scheduled game, tipoff and team identities.

The player/shot collector attempts up to 16 games per tick. New games take priority; failures retry fairly
with a bounded delay instead of blocking the backlog for six hours: after 1, 2
and 4 minutes, then every 5 minutes, and hourly once the game is more than six
hours past tipoff (a source that still fails then is usually waiting on a
correction, and each attempt can cost three 15 s page timeouts). Adapter
version changes invalidate old failure delays. Every played player is included,
including zero-attempt appearances and players absent from the current roster.
All FGA/FGM/3PA/3PM reconcile per player. Official team heaves are kept separately
from individual field-goal attempts. Missing locations stay visible and retryable.

The player detail panel shows the full actual game log and shot coverage. The
main board, team outlook and Projections show the full projected box score.

```sh
python3 -m nba_forecast sync-players --directory data/operations/production \
  --history datasets/regular-season-expanded-2026-10-22/history.json.gz --max-shot-games 50
python3 -m unittest discover -s tests -q
cd dashboard
npm test
npm run build
npm run test:e2e
```
