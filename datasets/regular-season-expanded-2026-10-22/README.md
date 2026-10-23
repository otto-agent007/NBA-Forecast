# Expanded regular-season evidence

Re-normalized official NBA final boxscores, adding rebounds, assists, steals,
blocks and available starter evidence. Prior dataset and immutable forecasts
remain unchanged. `manifest.json` records coverage and compressed-file hash;
`sources.json` records exact source URLs, raw SHA-256 and retrieval times.

Includes prior-season and captured current-season finals, never preseason.
`known_at` remains the actual raw retrieval time, not the historical game date.
Thus this is valid evidence for future forecasts; do not present a replay using
these downloads as a historical pregame prediction.

Reproduce normalization from archived raw NBA bytes and matching provenance:
`python -m nba_forecast enrich-history --archive <raw-dir> --output history.json`.
The full raw archive was captured separately; URLs alone do not guarantee that
NBA still serves identical historical bytes. Missing optional categories stay
null and have per-field coverage in profiles.
