# Injury-aware role minutes, shot locations and margin probabilities

`regular-season-baseline-0.6.0` adds three independently audited inputs to the existing point model. Older snapshots retain their original model behavior and saved forecasts remain immutable.

## Injury-shortened appearances

The forecast preparation reads archived official NBA recaps, reconciles exact game/team/player identities and final box scores, and retains the original retrieval timestamp and source hash. Only explicit, same-game injury exits with supported subject/return language are classified. Short minutes, an ordinary substitution, a pregame injury mention, a different person's injury and ambiguous dates do not establish an exit. Contradictory return evidence suppresses classification. Unsupported wording stays unconfirmed; no parser claims complete news coverage.

Confirmed appearances are excluded from normal-role minutes, minute variance and observed minute capacity. Their actual shots, makes, points, turnovers and playing exposure remain in production-rate estimates and evaluation. Starting facts are retained. If excluding the shortened appearance leaves no supported normal role on a new team, the model withholds minutes rather than resurrecting a previous team's role. Current availability and explicit game-specific minute caps retain precedence.

## Location-adjusted shooting

`court-location-1.0.0` retains historical shooting priors and the existing current-season exposure. It replaces a bounded share of observed current makes with an expectation from court zones. The reference pool excludes all shots by the target player. This measures location, not defender distance or full shot difficulty.

Reference zones need at least 50 attempts from 10 other players. Player evidence needs at least 10 supported located attempts, two games and 80% weighted current-family coverage. Recency uses the existing 0.985 decay including DNP observations. Reference rates shrink with 100 pseudo-attempts; replacement strength is `0.35*N/(N+50)`, and the shooting-probability change is capped at two percentage points. Both increases and decreases are possible. Inadequate evidence leaves the original estimate exactly intact. Free-throw efficiency is unchanged.

Source replays, matching current makes/exposure, peer-pool identities, hashes and cutoff checks are required before live use. The player panel explains injury exclusions and the point effect of supported location adjustments. New point means flow through full-box score arithmetic and team sums.

## Winning-margin explorer

Complete supported rotations are required for both teams. The saved home-minus-away player-point difference supplies the location of an explicitly uncalibrated, discrete final-score approximation. Historical paired final-score dispersion supplies an uncertainty prior; shared game effects are retained through paired covariance, and overtime outcomes are not scaled to regulation. This is outcome dispersion, not a claim of calibrated prediction-error coverage. Point intervals from earlier model versions are not reused as margin probabilities.

At least 100 eligible games are required; at most the latest 1,230 are used. The finite distribution has integer margins from -120 to 120 excluding zero. Extreme tail mass is retained at endpoints, with a symmetric 0.000001 floor per tail. Lines at or beyond the support endpoints are unavailable, and estimates too near the support boundary are withheld. The dashboard accepts lines from -50 through 50.

The selected team's final margin must be **strictly greater** than the entered line for YES. NO is its complement, including equality. This is a threshold calculation; the entered number is not a captured market quote or a statement of any exchange's contract terms. There is no joint-ticket probability or automatic trade. Overtime is included in source outcomes; its effect on the final margin is approximated, not modeled as a separate overtime possession simulation.

Daily reviews select the latest saved pregame run before reading outcomes. They score winner Brier error, a ranked-probability score and fixed diagnostic thresholds once complete final boxes reconcile. Partial player actuals remain usable for player evaluation but cannot establish a game-margin result. These metrics begin with future saved forecasts, not reconstructions of past winners.

## Configuration and verification

The refined artifact has a new configuration key bound to the entire pinned Python package. Previous calibration and accuracy claims are removed. Both source-backed preparation and independently replayed preflight checks are required. Daily and T-minus-30 scheduling are retained.

Prepare a reviewable artifact without activation:

```sh
python3 -m nba_forecast.refinement_upgrade --directory data/operations/production --output /tmp/refined-model.json
```

The diagnostic script uses real archived data and **assumes all roster players are available** to isolate model changes. It never saves live forecasts or changes the original actuals:

```sh
python3 scripts/verify_refined_forecasts.py --directory data/operations/production --history datasets/regular-season-expanded-2026-10-22/history.json.gz --output /tmp/refined-check
```

Validate with Python unittest discovery, dashboard unit/build checks, and the desktop/mobile Playwright suite. This release implements new methods; improved future accuracy remains to be measured.

The October 24 local activation, tests, independent review and real-source diagnostic are recorded in [the release evidence](../datasets/forecast-refinements-2026-10-24/README.md). The diagnostic and synthetic browser fixtures are clearly separated from actual saved forecasts.

## Schedule denial and prospective margin calibration follow-up

An October 24 check found HTTP 403 for the season schedule and a separate scoreboard path on `cdn.nba.com` from this host. This is consistent with access being denied to the CDN, rather than proving that the schedule file path is wrong. The canonical NBA schedule remains the season and game-ID source. If its CDN request fails, an ESPN daily scoreboard verifies today's and tomorrow's games against that saved NBA calendar using an exact home/away team pair and a bounded tipoff window. Only uniquely matched games can receive updated start/status fields. The original NBA bytes and observation time remain unchanged; each ESPN response is archived with its own retrieval time and hash. An unmatched due game is withheld and reported for review. If both sources fail, all due games are withheld.

Margin calibration collects the latest saved pregame distribution for each game and model key before reading its final. Each final must reconcile with the archived official NBA game page, its hash, team identities and scores. A bounded location and scale correction uses fixed, nonoverlapping 112-game attempts. A deterministic Eastern game-day boundary leaves at least 64 training games across eight dates and at least 32 later validation games. Training final evidence must have been known before the first validation forecast. The first result for each window is frozen, so adding games cannot retry a failed validation window. Before installation, the saved runs and original finals are reselected and the fit and scores are recomputed from the embedded evidence. A correction is installed only if later saved games show no worse ranked probability score and no material winner-Brier regression. Baseline and corrected distributions are saved for audit. Previously saved forecasts never change. This finite-sample validation cannot guarantee future accuracy. The October 24 runner remains stopped at the owner's request; no calibration has been activated.

As of the October 24 work, model 0.6.0 has **zero** eligible forecast/final pairs. Its current margin probabilities remain uncalibrated estimates. The existing player-points prospective interval workflow continues to collect its own same-model outcomes. The owner paused the morning runner during this work; new code is prepared and tested separately until that pause is lifted.
