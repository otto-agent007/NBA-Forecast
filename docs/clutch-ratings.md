# Historical clutch comparisons

This extension describes regular-season late-game production in the verified
2023–24, 2024–25 and 2025–26 archive. It does **not** alter forecasts, assign
an overall-impact score, or establish that a player improves under pressure.
The same-season non-clutch baseline and game/possession-level contexts needed
for those claims are not part of this archive.

## Reproduce and open

From the repository root, using Python 3.11+ and Node 22:

```sh
python -m nba_forecast.clutch_ratings --output data/operations/live/clutch.json
npm ci --prefix dashboard
npm run build --prefix dashboard
python -m nba_forecast dashboard --directory data/operations/live
```

Open `http://127.0.0.1:8765` and choose **Clutch**. Use the same operational
`--directory` you normally use. The report is prepared explicitly; the dashboard
only reads it through `/api/clutch`. It does not run research on every page
load or trigger network requests, ledger writes, model calls, or tuning.
A missing report has an explicit unavailable state; malformed/unsupported
reports return HTTP 503. Refresh data reloads the saved report.

For the existing portable snapshot workflow, also generate:

```sh
python -m nba_forecast.clutch_ratings --output dashboard/public/clutch.json
```

Generate `dashboard/public/dashboard.json` using the existing dashboard export
command, rebuild, and open `/?snapshot=1`. Both generated files are ignored by
git. The report contains full-precision calculations; rounding is display-only.
Use the same Python runtime for byte-identical simulation output. The CLI prints
record counts and writes atomically only after successful calculation.

The source directory defaults to `datasets/clutch-2026-10-22`; use `--archive`
for an explicit compatible archived copy. Both compressed and decompressed
SHA-256 hashes are checked. Original responses are revalidated using the
canonical query filters and rows must exactly equal the normalized archive.
Two-point accounting is checked in addition to the collector's scoring checks.
Provenance URLs/hashes and retrieval dates remain in the report. No source
archive or forecast module is rewritten.

## Dimensions and denominators

- **Scoring:** points per qualifying appearance (`PTS/GP`) and points per clutch
  minute. GP is games with a clutch appearance, not all regular-season games.
- **Opportunity:** FGA/MIN, FTA/MIN and MIN/GP. FGA/MIN is not touches, possession
  share or NBA usage percentage. Attempt mix is 3PA/FGA.
- **Efficiency:** separate 2P, 3P and FT conversion; eFG accounts for three-point
  value. Raw TS includes free throws with the conventional approximate 0.44
  factor. It is not an exact endgame possession estimate.
- **Playmaking:** AST/MIN. Recorded assists are not potential assists, pass
  quality or total creation.
- **Turnovers:** TOV/MIN and raw AST/TOV. Lower TOV/MIN is favorable only holding
  responsibility constant; responsibility is not controlled here. AST/TOV is
  missing when TOV=0, never infinity.

`2P%=(FGM−FG3M)/(FGA−FG3A)`, `eFG%=(FGM+0.5*FG3M)/FGA`, and
`TS%=PTS/[2*(FGA+0.44*FTA)]`. Undefined denominators return null, not zero.
Free throws contribute to scoring; intentional fouls, technicals and ordinary
shooting fouls cannot be separated using these aggregates.

## Qualification, ratings and shrinkage

Display eligibility defaults to **20 clutch games and 50 clutch minutes**.
eFG rankings additionally require 30 FGA. 2P/3P/FT rankings require 30 attempts
of the relevant type. Pooled views apply those same minimums to pooled totals.
These are provisional display filters, not empirically calibrated reliability
cutoffs. Turn off Qualified only to inspect every archived player. No rating is
assigned to unqualified rows, even when shown or selected for comparison.

For each dimension, the rating is the adjusted value's midrank percentile in
that period's eligible cohort: `100*(number below + 0.5*number tied)/N`.
TOV/MIN reverses direction. Ratings are not scores out of 100 in a calibrated
skill model. Search and display filters do not change the reference cohort.
Ties receive the same rating. There is no composite rating.

For each event rate with count C and minutes M:

`adjusted rate = (C + 50 * other-player rate)/(M+50)`.

The prior baseline is count/exposure over **all other players** in the period,
including small samples. Excluding the player avoids counting their observations
twice. These smoothed descriptive means have no claimed calibrated intervals.
The displayed prior share is `50/(M+50)`. When no peer exposure exists, the
rate falls back to the raw rate and no prior is applied.

For each shot type, let m be makes, n attempts, and μ the other-player rate
with Jeffreys smoothing: `(peer makes+0.5)/(peer attempts+1)`.
The posterior is `Beta(m+30μ, n−m+30(1−μ))`.
Its mean is `(m+30μ)/(n+30)`; prior share is `30/(n+30)`.
2P, 3P and FT are modeled separately, so eFG is not incorrectly treated as a
binomial proportion. Adjusted eFG combines the adjusted 2P/3P probabilities
using the player's **observed** three-point attempt share. eFG can exceed 100%.
A shot type with no attempts has a prior-only estimate and no raw estimate.

Uncertainty uses 1,024 seeded posterior samples per shot type. Seed inputs are
period, player ID and shot type, hashed with SHA-256. Approximate 5th/95th sample
quantiles form the displayed 90% model interval. eFG draws combine independent
2P and 3P draws at fixed shot mix. This is a transparent working model, **not
calibrated frequentist coverage**, a future-game interval or uncertainty about
causal impact. It conditions on fixed peer priors and ignores game clustering,
shot difficulty, opponent, lineup and role changes. Real uncertainty can be
larger; intervals also have Monte Carlo approximation error. Overlap is only a
visual caution, not a formal significance test or pairwise win probability.

The sensitivity panel repeats posterior means at **half/default/double** prior
strength: 25/50/100 minutes and 15/30/60 attempts. These defaults were not tuned
to make named stars rank highly or to maximize historical predictive accuracy.

## Three seasons and missing evidence

All 1,459 player-season rows and 675 distinct players are retained. Pooled
estimates sum counts and minutes first; they never average percentages or use
recency weighting. Players can have different available years, explicitly shown.
Haliburton and Lillard have no 2025–26 archived row; that season remains missing.
Missingness does not establish injury, retirement or why a player is absent.

Player trends show every available season and the range among qualified seasons.
A missing year is not interpolated. League repeatability is Pearson correlation
of the within-season midrank percentiles among players qualified in both adjacent
years. It is not exactly a Spearman coefficient re-ranked within the intersection.
Samples differ by metric and selection can bias conclusions. It is descriptive,
not a held-out forecast test. Only two transitions exist, so avoid stable-talent
claims. All-three-season survival is never a global inclusion requirement.

## Exact scoring decomposition

For each pair of adjacent observed seasons:

`PTS/GP = (MIN/GP) * [2*(2PA/MIN)*2P% + 3*(3PA/MIN)*3P% + (FTA/MIN)*FT%]`.

Group the inputs into court time (MIN/GP), attempt rates/mix (three attempt
rates), and conversion (three probabilities). Evaluate all six orders of
switching groups from the old to the new season and average each group's
marginal change. This symmetric Shapley accounting sums exactly to the observed
PTS/GP change and reverses sign when seasons are swapped. No preferred ordering
is imposed. If either season or a shot-type denominator is absent, the
explanation is unavailable rather than filling in an unobserved conversion rate.
Effects are arithmetic, not causal estimates of coaching, skill or confidence.

## Gate before forecasting

Nothing in this report is connected to `model.py`, the runner or forecast weights.
Before a separate prospective change:

1. Acquire compatible same-season **non-clutch** baselines (subtract clutch
   counts/exposure from matching full-season totals only after reconciling
   definitions), plus date-resolved game/possession evidence. Season aggregates
   cannot be sliced into authentic earlier-season observations.
2. Freeze the evaluation target, eligible population, forecast origins, tuning
   procedure, loss metrics and practical acceptance margin before examining a
   final holdout. Include missing-player and changed-team cases without survivor
   filtering. Keep team-game outcomes clustered when estimating uncertainty.
3. Compare the current baseline with (a) closing-minute/opportunity information,
   and (b) opportunity plus clutch conversion information. Avoid adding clutch
   points on top of a baseline that already contains those minutes and points.
4. Use chronological training/validation/holdout periods. Fit prior means,
   strengths, recency weights and thresholds only on training data available
   before each forecast origin. Same-period priors here are descriptive and
   must not be reused for a historical in-season forecast.
5. Measure improvement on the **whole eligible slate**, plus predeclared close-game
   subsets, without choosing the sample using the eventual close finish. Require
   practical improvement, uncertainty analysis and calibration checks; a better
   clutch leaderboard alone is insufficient.
6. Label any retrospective study using these October 22 retrievals as potentially
   affected by later source corrections. Start immutable point-in-time captures
   before claiming a genuinely prospective test. Three seasons alone provide
   limited independent temporal validation.

## Checks

```sh
python -m unittest discover -s tests -v
python -m compileall -q nba_forecast
npm test --prefix dashboard
npm run build --prefix dashboard
```

Regression coverage includes original-source/hash replay, named-player totals,
missing seasons, small perfect shooting samples, denominator handling, peer
exclusion, weighted pooling, deterministic generation, exact and reversible
scoring decomposition, qualification/filtering and read-only endpoint behavior.
