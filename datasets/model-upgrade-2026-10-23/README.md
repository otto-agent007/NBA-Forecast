# Versioned model evidence

`artifact.json.gz` is the exact initial 0.5.0 activation candidate, including fitted
context coefficients, calibration quantile, parameters, team evidence and validation
report. Its decompressed canonical SHA256 is recorded in `manifest.json`.
`team-games.json.gz` contains the 1,217 reconciled game records;
`source-manifest.json.gz` retains the 27 excluded games and reasons.

The normalized NBA player history is in
`../regular-season-expanded-2026-10-22/history.json.gz`. ESPN summary/scoreboard
requests are separately hash-bound in each game record. The larger raw request
archive is local in the research directory; its absolute path and SHA256 are in
the manifest. Source data was captured after the historical games. Its real
retrieval timestamps are preserved; these are reconstruction inputs.

`report.json` records chronological partitions, parameter selection, evaluation
metrics and scope. Development inspected earlier aggregate evaluation results;
the report explicitly limits independence claims. Player metrics condition on
actual played status with positive minutes. Historical injury allocation and
the live aggregate scoring priors require prospective assessment.

See [the model operation guide](../../docs/model-upgrade.md) for mechanisms,
activation, automatic residual collection and rollback. Nothing here overwrites
original saved forecasts or pretends that historical reconstructions were live
pregame forecasts. Recovery is local on the same disk; no remote backup is implied.
