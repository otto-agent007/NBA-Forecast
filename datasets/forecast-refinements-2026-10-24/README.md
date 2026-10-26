# Forecast refinements release evidence

Model: `regular-season-baseline-0.6.0`.

This release adds source-confirmed injury-exit treatment for normal minutes, bounded shot-location adjustments, and a team-margin probability explorer. See [the method and limitations](../../docs/forecast-refinements.md).

## Verification

- Python: 523 tests passed after all review fixes.
- Dashboard: all three Node test files and the production build passed.
- Browser: 44 desktop/mobile tests passed. After the final percentage-formatting change, all four margin tests passed again.
- The independent review found three Important issues and no Critical issue. All three findings were reproduced and fixed with regression tests: injury appearances entering minute-cap learning, incomplete final boxes entering margin feedback, and ambiguous injury prose entering role exclusions. `checks/independent-review-before-fixes.md` is the original review, not a list of outstanding findings.
- Logs and screenshots are in `checks/`. `synthetic-margin-controls-*` uses **synthetic fixtures**, not actual forecasts. `live-model-validation-desktop.png` and `live-dashboard.json` verify the real activated dashboard, with zero matched outcomes for the new model and no page errors.

## Real-source diagnostic

`diagnostic/summary.json` and the eight compressed snapshot/forecast pairs replay official archived sources. **Every roster player is assumed available for this diagnostic. These files are not live pregame forecasts or accuracy measurements.**

All eight upcoming games passed source replay and output validation. Four complete games produced estimated margin distributions; four with incomplete player coverage withheld probabilities. The same 28 official game sources and 4,942 shot locations were checked for each snapshot. Location adjustments affected 66 players.

Confirmed exits for Cooper Flagg and Yves Missi were excluded from normal-minute estimation while retaining their actual scoring and shooting evidence. Under the diagnostic availability assumption, Flagg's normal minutes changed from 29.88 to 33.38 and Missi's from 19.23 to 21.81. Current injury reports still determine availability in actual forecasts.

## Activation and recovery

`activation.json` records the model artifact, pinned runner, previous configuration, local unit backups, verification tick, dashboard version and unchanged 9 AM Pacific resume timer. The tick completed without errors, created no forecasts before the daily checkpoint, and retained all 300 saved runs. The upstream NBA schedule request returned HTTP 403; the existing cached calendar remained in use with its original observation time. `verification.json` records the checks and file hashes. `SHA256SUMS` covers this evidence directory except itself.

The new artifact resets previous calibration and accuracy claims. Margin probabilities are explicitly uncalibrated estimates. Existing saved forecasts are retained, and new-model outcome evaluation starts with future forecasts.

The diagnostic snapshots refer to immutable official raw sources retained in the local production archive; this evidence directory is not a standalone copy of all upstream data. The new package is retained under the content-addressed `data/operations/production/runner-releases/` directory.
