# Official regular-season backfill

Captured October 21–22, 2026 (UTC); this is a fixed index snapshot, not a live feed.

- 1,235 completed games; 43,405 player-game rows; 618 distinct players.
- Games by season: {"2025-26": 1230, "2026-27": 5}.
- All indexed finals imported and validated; no failed games. Preseason and playoffs excluded.
- `manifest.json` records source URLs, per-game normalized hashes and actual retrieval times.
- `indexes.json` records the official regular-season index queries and coverage.
- Export SHA256: `e6ab896058755a4831b8cc629ce3dd98374d9a0f0feb521fcb4bace60df6b75a`.

Use `--history datasets/regular-season-2026-10-21/history.json.gz` with the forecast CLI.
Refresh with `python -m nba_forecast backfill --seasons 2025-26 2026-27 --directory data/history`.
The packaged snapshot is an input dataset; use a separate working directory for new downloads.

These are postgame observations downloaded now. Their `known_at` timestamps are
not backdated, so they cannot be used to claim pregame knowledge in historical
backtests. This snapshot does not contain historical injury reports, coaching
judgments or historical active-roster snapshots. Rookies and missing baselines
still require review; distinct historical players do not equal the current pool.
The current-season count reflects finals present in the index when captured,
not necessarily every game that finished later that night.

Exact raw NBA responses and retrieval metadata are retained separately in
`NBA_Official_Raw_Backfill_2026-10-21.zip`; normalized observations are in this PR.
