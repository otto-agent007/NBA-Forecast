# NBA clutch research archive — 2026-10-22

Official NBA Stats regular-season totals: final five minutes, margin within five.
2023–24: 469 player rows; 2024–25: 498; 2025–26: 492. All returned players
are retained, not just the currently active roster or named stars.

Top-level season files contain validated normalized rows and exact query filters.
`raw/` contains gzip-compressed original responses and retrieval provenance.
The manifest hashes both compressed files and original uncompressed bytes.
Decompress before comparing the original payload hash with provenance.

Reproduce from the repository root:

```sh
python -m nba_forecast.clutch --output data/clutch 2023-24 2024-25 2025-26
```

The command leaves uncompressed JSON for inspection; this committed archive uses
deterministic gzip to reduce size. Validation rejects wrong filters, malformed
rows, duplicate IDs, invalid counts and inconsistent scoring identities.

These are season aggregates retrieved today. They must not be passed off as
historical pregame observations. Absent players remain missing, not zero.
No model weights changed. Source availability and corrections can change results.

Brunson, consistent official definition:

| Season | GP | PTS | PTS/GP | AST | TOV | FGM/FGA |
|---|---:|---:|---:|---:|---:|---:|
| 2023–24 | 30 | 106 | 3.53 | 20 | 6 | 36/85 |
| 2024–25 | 28 | 156 | 5.57 | 28 | 8 | 52/101 |
| 2025–26 | 32 | 116 | 3.63 | 22 | 11 | 40/82 |

PTS/GP is calculated from totals and rounded for display. The earlier 2023–24
Hoops Junkie figure used a different possession-boundary definition; use this
NBA series for consistent comparisons instead of blending the two providers.
