# Production model artifact, 2026-11-09

`artifact.json.gz` is the model artifact production ran on 2026-11-09
(`regular-season-baseline-0.6.0`, model key `0168f8c388858dc7…`, active
artifact `5a4466c0…` activated 2026-11-10T02:59:35Z, release `db02339b…`,
main 1385e80), for `backtest --production-path`. Exported with:

```bash
python3 scripts/research/export_production_artifact.py --directory data/operations/production \
  --against datasets/model-upgrade-2026-10-23/artifact.json.gz \
  --output datasets/production-artifact-2026-11-09
```

which reads the operations directory without writing to it and verifies each
archive hash. Two things are removed, both recorded in `manifest.json`: the
runner package digest (`enhancements._read` binds a refined artifact to the
release that carries it) and the prospective interval calibration, fitted on
live outcomes a replay must not read; the replay sets it aside anyway and fits
its own walk-forward interval. Everything the replay reads is production's:
model version, model key, known-at, parameters, context model and the 27
excluded history games (their hashes are under `replay_reads`). It is not an
installable artifact.

The live artifact is refined from the 0.5.0 artifact in
`datasets/model-upgrade-2026-10-23/` (byte-identical to its parent). Its
parameters, context model and excluded games are equal to that one's; its
model version, key and known-at are not. The version turns on the refined
branches (`model_versions.uses_refinements`), which change nothing in the
replay without their live inputs (`role_context`, `shot_quality`): on a
replay of the last 60 games of each season the two artifacts gave the same
forecasts except on the day the limit cuts, where the replay reads the market
strength at a later first game. A new live artifact with other parameters or
another context model needs a new export and a new production-path reference.

The context model was fitted on 504 games of 2025-26 up to 2026-01-15, so that
season is partly in-sample for it.
