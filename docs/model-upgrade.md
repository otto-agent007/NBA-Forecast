# Forecast model 0.5.0

The separately versioned [full-box extension](full-boxscore.md) adds coherent
shooting lines, rebound splits, fouls, plus/minus and archived shot profiles while
preserving this point model and its calibration.

The player model retains multi-season scoring priors, current-team role evidence,
five projected starters per team, availability checks and immutable checkpoints.
The optional hash-bound enhancement artifact adds four mechanisms:

- Estimate regulation possessions from both teams' FGA + 0.44 FTA - offensive
  rebounds + total turnovers. Recent team estimates are shrunk toward league
  evidence. Pace adjusts opportunity relative to the player's existing team pace;
  opponent defense uses points per possession. Sparse evidence stays neutral.
- Allocate sourced out-player vacancies only to supported available teammates.
  Historical role caps, manual restrictions, 240 team minutes and FGA/FTA budgets
  constrain the changes. Unknown availability never becomes an assumed vacancy.
  Any capacity left unallocated is explicit.
- Apply bounded numerical rest, congestion, home/away and venue-timezone effects
  from an observational ridge fit. Unknown venues stay unknown; venue changes
  approximate travel and do not establish actual flight schedules.
- Replace the heuristic interval width with an empirical residual quantile when
  enough matching evidence exists. Until then the heuristic half-width is
  `intervals.HEURISTIC_Z80` = 1.70 sd: the normal 1.28 covered only 63% of the
  first prospective played outcomes, whose pooled 80% quantile was 1.70 (the
  retrospective reconstruction gave 1.88). Prospective calibration pools
  standardized residuals across every model version whose `points_sd` is built
  the same way (the interval family), so a new model key refines the sample
  instead of restarting it; the fitted artifact is still installed for, and
  bound to, one model key. It requires at least 20 games and 200 played,
  positive-minute player outcomes. This conditional population excludes DNPs
  from interval fitting; ordinary ledger performance still includes DNPs.

## Evidence and limits

[The saved report](../datasets/model-upgrade-2026-10-23/report.json) separates
training, selection, calibration and evaluation dates. Of 1,244 source games,
1,217 passed cross-source reconciliation; 27 exclusions remain documented. The
full calendar still supplies rest context even for excluded statistical games.
Final evaluation contains 266 games and 5,664 positive-participation player rows.
Player point MAE changed from 5.0855 to 5.0837; team point MAE from 10.3595 to
10.1435. Nominal 80% intervals covered 77.87% of 5,661 eligible outcomes.

These are retrospective reconstructions from later-collected source data.
Earlier development results were inspected, so this is not a pristine independent
holdout. The reconstructed player model uses game-history priors and boxscore
rosters; the live model's three-season aggregate priors and new injury allocation
require prospective assessment. Future interval coverage is not guaranteed.
Historical results do not establish a betting edge.

## Artifacts and operations

The dataset directory contains deterministic compressed team evidence, the exact
initial fitted artifact, validation report, exclusion manifest and SHA256 manifest.
The larger original request archive remains in the local research directory;
its location and checksum are recorded. This is same-disk recovery.

Production uses `model-upgrade-active.json` pointing to an immutable
`model-artifacts/<sha256>.json`. Each prepared snapshot binds the artifact,
contemporary final evidence and real source cutoff; preflight reproduces those
inputs from the archive. Future-known, stale, tampered or research-only live
inputs fail validation. Newer final records are appended by hash, preserving
earlier cutoffs. Postgame source disagreements remain visible and retryable.

The runner prioritizes pregame forecasts before postgame collection. It imports
strictly reconciled final actuals, updates possession evidence, assesses original
saved intervals and publishes model status on the dashboard's Performance tab.
When new prospective residual evidence meets the thresholds, it creates a new
immutable calibration artifact. Existing point parameters and forecasts stay
unchanged. The artifact digest participates in the next checkpoint's job key.

Pinned runner releases prevent worktree edits from mixing code versions during
a tick. To roll back, stop the runner timer, restore the baseline service's
WorkingDirectory from `production/verified-units`, remove the active upgrade
pointer (retain an archived copy), reload systemd and restart the timer. Do not
delete source archives or rewrite the ledger. Existing saved forecasts retain
their original model version, parameters and timestamps.

Reproduce the research with `python3 -m nba_forecast.research_evaluation --history
datasets/regular-season-expanded-2026-10-22/history.json.gz --team-games FILE
--directory NEW_DIRECTORY`, after decompressing `team-games.json.gz` to FILE.
Fresh acquisition through `nba_forecast.research_game_data` records its actual
retrieval time and can produce different exclusions if providers revise data.
