# Research scripts

The analyses behind the numbers in `docs/research-lessons-2026-10.md`, so they
can be rerun on a new reference. Standard library only; run from anywhere, the
`--datasets` default is the repository's `datasets/`.

| Script | Question | Inputs |
|---|---|---|
| `gap_breakdown.py` | Where the model's margin MAE is lost against the close: compression, month, back-to-backs, size of disagreement, spread size, underdog lean, team-seasons | a backtest reference's `rows.json.gz`, team games, closing lines |
| `absence_minutes.py` | How much the margin misses per ten minutes of reported-out regulars, home minus away; the measurement behind `margins.absence_adjustment` | the same, plus the histories and T−30 injury reports |
| `clv.py` | Closing-line value: does a night-before forecast move the opener? With the point-differential placebo | a night-before replay's `rows.json.gz`, closing lines, the archived ESPN odds responses under `data/market-history/` (gitignored) |
| `stats_lite.py` | OLS with date-clustered errors for the above | |

## Reproduced on 2026-10-26

`gap_breakdown.py --reference datasets/backtest-2026-10-26-absence`: gap +0.393
pooled; March and April 33% and 25% of it, April +0.80 a game; games where
model and close differ by 6+ points are 8% of games and 47% of the gap; 72%
of the gap in games where the model leans further to the underdog; model SD
7.87 against the close's 8.22, best stretch 1.010; actual on model and close
together +0.128 and +0.943; back-to-backs 0.44 and 0.51 against 0.35.

`absence_minutes.py --reference datasets/backtest-2026-10-26-three-seasons`:
model miss −0.49 ± 0.06 per ten reported-out minutes, close −0.17 ± 0.05,
each season within 0.08 of the pooled figure; on the absence reference the
model's slope is +0.01 ± 0.06.

`clv.py` on night-before replays (`injury_history --anchor 'D-1 20:30'`,
`datasets/injury-reports-*-night-before`), one standard error:

| | Placebo slope | Night-before slope | CLV per bet | Covered the opener |
|---|---|---|---|---|
| 2024-25, before the absence adjustment | +0.004 ± 0.021 | +0.100 ± 0.031 | +0.11 | 50.2% ± 1.7 |
| 2025-26, before the absence adjustment | +0.022 ± 0.029 | +0.063 ± 0.040 | +0.12 | 51.5% ± 1.7 |
| 2024-25, with it (margin method v3) | +0.004 ± 0.021 | +0.128 ± 0.029 | +0.22 | 52.4% ± 1.7 |
| 2025-26, with it | +0.022 ± 0.029 | +0.078 ± 0.048 | +0.10 | 51.8% ± 1.7 |

The first two rows are the research page's table. The absence adjustment
roughly doubles the 2024-25 line movement in the forecast's direction and
leaves 2025-26's within its error; both remain short of the 0.8 points a bet
needs to clear the vig. The replays themselves are not committed (two seasons,
about eight minutes each); rerun the backtest with the night-before statuses to
regenerate them.
