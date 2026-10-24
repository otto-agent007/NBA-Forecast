# Full player box scores and complete postgame shots

Implement the requested full projected/actual box score, preserving the frozen forecasts and honest source precision. NBA game-page archives supplement the blocked live-data endpoint. Every played player, including zero-attempt appearances and players absent from today's roster, must have explicit shot coverage.

1. Normalize exact minutes, rebound splits, fouls, signed plus/minus and derived shooting ratios. Reconcile official final boxes and retain source revisions.
2. Add a separately versioned full-box statistical layer. Keep the existing point mean and interval model unchanged; allocate bounded expected makes consistently with that mean. Project other counts from exposure-weighted evidence and verified three-season totals, with per-field missingness. Plus/minus is a shrunk, descriptive on-court estimate, not a player impact rating.
3. Bind prior-game shot profiles to archived hashes and the forecast cutoff. Use observed zone shares to allocate projected two/three-point attempts; do not infer a new scoring advantage from a single game's locations.
4. Drain all completed games with bounded retries, reconcile every player's attempts/makes, and include zero-attempt coverage. Materialize full player pages and full projection/game-log tables.
5. Test arithmetic, nulls, signed stats, denominators, time/source boundaries, revisions and backlog behavior. Collect tonight's real games, inspect the desktop/mobile UI, and activate a pinned runner release only after passing checks.

Original pregame runs remain unchanged. Historical pages must identify any legacy forecast fields that were not saved; no retrospective numbers are represented as original projections.
