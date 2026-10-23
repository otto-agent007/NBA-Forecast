# Clutch comparison findings — 2026-10-22

Calculated from the hash-verified historical archive using
`python -m nba_forecast.clutch_ratings`. This is descriptive research, not a
forecast improvement claim. See [methods](../clutch-ratings.md).

## Five requested players

Pooled adjusted estimates (no recency weighting). Coverage differs: Lillard
and Haliburton have no 2025–26 row. Each estimate is smoothed against other
players in the pooled archive; these are not matched-context skill rankings.

| Player | Seasons | Clutch GP | Minutes | PTS/min | FGA/min | eFG% | AST/min | TOV/min |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Jalen Brunson | 3 | 90 | 355.6 | 0.989 | 0.703 | 52.6 | 0.183 | 0.068 |
| Tyrese Haliburton | 2 | 60 | 216.5 | 0.796 | 0.541 | 57.1 | 0.211 | 0.043 |
| Kevin Durant | 3 | 114 | 434.7 | 0.760 | 0.522 | 51.5 | 0.099 | 0.073 |
| Damian Lillard | 2 | 64 | 235.5 | 0.778 | 0.543 | 45.6 | 0.158 | 0.061 |
| Shai Gilgeous-Alexander | 3 | 83 | 294.7 | 1.094 | 0.633 | 57.0 | 0.145 | 0.053 |

Useful distinctions: Brunson's adjusted attempt rate is highest among these
five, SGA's adjusted point rate is highest, and Haliburton's adjusted assist rate
is highest. These are sample descriptions, not claims that one is the best
closer. In particular, eFG does not include free throws and pooled samples cover
different years. The dashboard exposes intervals and the underlying shot counts.

## Brunson: court time explains much of the per-game swing

Raw points per qualifying game are 106/30, 156/28 and 116/32. The final ratio
is exactly 3.625 (displayed as 3.63 by the frontend); calculations and decisions
use full precision, not rounded display strings.

| Transition | Observed PTS/GP change | Court-time contribution | Attempt-rate/mix contribution | Conversion contribution |
|---|---:|---:|---:|---:|
| 2023–24 → 2024–25 | +2.038 | +1.601 | −0.395 | +0.833 |
| 2024–25 → 2025–26 | −1.946 | −1.192 | −0.644 | −0.110 |

Each row uses symmetric three-factor accounting, averaging all six replacement
orders. Unrounded contributions sum to the observed change; displayed rounding
may produce a 0.001 discrepancy. This does not establish why court time or shot
outcomes changed. It does show why PTS/GP alone is not a stable player rating.

## Exploratory repeatability

Correlation of adjusted within-season percentile ranks among players qualifying
in both adjacent seasons:

| Dimension | 2023–24 → 2024–25 | Paired players | 2024–25 → 2025–26 | Paired players |
|---|---:|---:|---:|---:|
| PTS/min | 0.663 | 103 | 0.721 | 92 |
| FGA/min | 0.736 | 103 | 0.734 | 92 |
| AST/min | 0.744 | 103 | 0.625 | 92 |
| TOV/min | 0.471 | 103 | 0.408 | 92 |
| eFG% | 0.177 | 46 | 0.152 | 39 |

The observed opportunity ordering repeats more strongly than shooting efficiency
in these selected samples. This supports investigating opportunity and conversion
separately. Different qualification cohorts, small samples, fixed priors and only
two transitions prevent a strong general conclusion. It does not establish that
clutch rates add predictive information beyond ordinary role and shooting data.

## Remaining research

Matched non-clutch baselines, date-resolved exposure, intentional-foul context,
shot difficulty, lineup/opponent adjustments and prospective evaluation remain
separate work. Do not incorporate these historical aggregates or their
same-period priors into historical pregame predictions. Forecast weights are
unchanged.

## Implementation validation

- All **160 Python tests**, **8 frontend tests**, Python compile checks and the
  Vite production build pass. No production dependency was added.
- All 1,459 original player-season records replay. Repeated report generation
  is deterministic in the tested Python runtime. All available decomposition
  residuals are below 1.4e-15 points/game.
- Local Chromium checked the built dashboard at **1440×1000** and **390×844**:
  page identity, meaningful render, no framework overlay, no page/console errors,
  and no document horizontal overflow. Wide comparison tables scroll internally.
- Interactions verified: search; all individual seasons and pooled view;
  metric-specific eligibility; raw/adjusted toggle; selecting/removing players;
  missing Haliburton/Lillard season rows; player detail; shot counts and
  uncertainty; trend metric; prior sensitivity; all six repeatability dimensions;
  explicit unavailable-report state; navigation back to Games.
- The cloud browser could not reach the workspace loopback server. Local
  Playwright used temporary Chromium tooling outside the repository. Browser
  and server ran in the same isolated command environment. Safari, Firefox,
  physical-device touch behavior and deployed hosting were not tested.
- Independent review found no substantive calculation issues. Archive files,
  forecasting code, model weights and operational runner remain unchanged.
