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

## Home side (2026-10-24)

Identical rows to `regular-season-expanded-2026-10-22`, each with a `home`
flag (true for the home team's players) joined from the reconciled team games
in `datasets/model-upgrade-2026-10-23/team-games.json.gz`, whose home/away
sides come from ESPN's scoreboard matched to NBA game IDs. Rows of the 27
games that dataset excludes carry no flag. New box scores normalized by the
runner carry the flag directly. The model's home-court estimate learns from
flagged complete games; without flags it uses only its prior.
