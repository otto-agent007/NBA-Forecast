# Full-boxscore capture and activation evidence

The capture covers all 28 completed games represented in the current-season
history: 642 played-player appearances and 4,942 field-goal attempts, all with
verified NBA legacy shot coordinates. This includes all 12 October 23 games and
all 275 players who played that day. Zero-attempt appearances remain covered.

`finals.json.gz` contains normalized official finals with precise minutes,
rebound splits, fouls, signed plus/minus and shooting ratios. Corrections retain
the original rows and source provenance. `shots.json.gz` contains reconciled
attempts and per-player coverage. `official-raw.tar.gz` preserves unchanged NBA
HTML and source metadata; the manifest hashes every saved artifact.

`historical-priors.json.gz` restores previously omitted exact optional counts
from 829 already-archived season records for 351 players. Source times and hashes
remain the original ones. It does not invent game logs or exact aggregate minutes.

The verified code adds `full-boxscore-1.0.0` alongside the existing point model.
`model-integration.json` is an arithmetic/provenance diagnostic using the next
eight scheduled matchups and assumed participation. It is neither a live
forecast nor an accuracy evaluation. It verifies source replay, box/zone
identities and unchanged point means, opportunities and interval values.

`data-activation.json` records the prior final/prior hashes preserved during
installation. `activation.json` records the actual active release and runner
verification. Original pregame forecasts were not rewritten. The full ledger and
prior normalized-cache archives remain in `data/operations/production`.

These are local, same-disk evidence and recovery files. Nothing was published or
merged remotely. See [the model and collection documentation](../../docs/full-boxscore.md).
