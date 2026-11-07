# Closing-line diagnostics

The Performance page now separates the historical reference from saved live
forecasts. Both show margin MAE, absolute disagreement, disagreement-size
buckets, and the slope of actual − close on model − close. Historical residual
regressions compare the same fixed features against close − model, actual −
model and actual − close. The persistent-team section distinguishes trailing
team-season disagreement from the game-day remainder.

These are the definitions from [the November 6 study](beating-the-close-2026-11-06.md).
Nothing in these diagnostics changes a forecast, rating weight, model version,
accuracy gate, or betting decision. The study's proposed sourced-prior gate
exception is **not** implemented. A teacher effect supported by the outcome
residual is a candidate for an independently evaluated change; it is not a pass.

## Reproduce and display

```bash
python -m nba_forecast.close_teacher \
  --reference datasets/backtest-2026-11-06-rating-weight \
  --datasets datasets \
  --output /tmp/close-teacher.json
python -m unittest tests.test_close_teacher -v
```

The command reads the reference game rows, each season's player history and
team-game schedule, and its ESPN closing-line archive, exactly as the study.
The derived JSON lists every source file and SHA-256; it has no wall-clock
stamp, so identical inputs produce identical results. CI repeats the archived
research checks and publishes this JSON as `close-teacher-diagnostics`.

For an operational dashboard, generate the same derived report with
`--output OPERATIONS_DIRECTORY/market/close-teacher.json`. The dashboard reads
that report without rewriting it. Otherwise it reads the committed November 6
snapshot at `datasets/backtest-2026-11-06-rating-weight/close-teacher.json`, if
present. Missing historical evidence is displayed as unavailable. Refresh a
snapshot deliberately when a new evaluated model becomes the reference; it
never silently represents current live performance.

A normal backtest with `--market` writes these diagnostics to
`metrics.market.close_teacher`, for the pooled sample and each season. The
historical teacher's fixed 12-column design needs multiple seasons and the
relevant situations to identify every term; insufficient or singular samples
are explicitly unavailable. Existing `encompassing` metrics retain their
conservative OLS floor. New study coefficients use only the study's G/(G−1)
date-clustered covariance, and report **one** standard error, not a 95% interval.
The study uses the source timestamp's calendar date (UTC in the archives),
whereas the existing backtest gate clusters on Eastern dates.

New live market reports automatically include `summary.close_teacher`, keyed
by provider. This avoids treating multiple books' rows for one game as
independent games. Live reports supply published model margins, final margins,
and the existing selected closing lines; no implied player/rating decomposition
is inferred from the obsolete 0.5 rating weight. Live teacher regressions are
not fitted from incomplete standings context.

## Reading the measurements

- Margin and closing margin are home minus away; closing margin is negative
  home spread. Positive close − model means the close favors the home team
  more than the model does.
- The fixed features are an intercept, home/away back-to-back (gap <1.5 days),
  home/away 3+ days (gap ≥2.5 days), March–April, late home/away bottom-8 and
  top-4 ranks by prior-date win percentage, and 2024–25/2025–26 indicators
  relative to 2023–24. Stable schedule order breaks tied records, as in the study.
- Rest and standings use the complete matched schedule, including warmup;
  only evaluated games enter the regressions. Future outcomes never update
  earlier standings. Actual outcomes use full final scores, including overtime.
- Disagreement buckets are [0,2), [2,4), [4,6), [6,99), exactly as the script.
  Values ≥99 are counted separately. Covers exclude zero disagreement and
  pushes. Fewer than 20 games, fewer than two dates, or a singular design
  produces a reason, not zero coefficients or invented certainty.
- Persistent means are signed toward each team and reset per season. Each
  team needs at least ten earlier evaluated games. Standing view = home mean
  minus away mean; game-day remainder = model − close − standing view. The
  two slopes jointly predict actual − close. Identical-time games cannot
  enter one another's histories.
- The standing variance share is Var(view)/(Var(view)+Var(remainder)), the
  study's descriptive ratio, **not** explained variance or R². Team-season
  mean SD uses the full sample and is explicitly descriptive, not a prior.

The historical regression test asserts the 2,604-game headline, published
back-to-back/standings coefficients and uncertainty, 6+ bucket coverage, and
persistent variance share, then checks all three fits against the independent
original research loader and OLS. The original 91-game live report is operational
data, not a committed fixture; subsequent reports naturally have different
sample sizes. No replacement live results are fabricated.
