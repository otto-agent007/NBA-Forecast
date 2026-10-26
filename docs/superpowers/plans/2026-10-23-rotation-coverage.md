# Rotation coverage correction

The owner identified that unprojected roster players without playing history
should not make an otherwise fully allocated Heat/Pistons total partial.

- [x] Reproduce current full-240-minute totals flagged only for cold-start players.
- [x] Add failing tests for cold-start-only full rotations, incomplete minutes,
  unresolved availability/roles and original aggregate-schema compatibility.
- [x] Add coverage schema 2; retain schema 1 validation for immutable saved totals.
- [x] Recompute display coverage from original saved player evidence; retain saved totals.
- [x] Add a failing desktop/mobile test and separate cold-start roster notes in the UI.
- [x] Run complete suites/build and verify the actual saved Heat/Pistons final.
- [x] Save the correction locally.

No player scores, availability, projected minutes or stored past forecasts are
changed. Model version 0.4.2 identifies new runs with the corrected coverage schema.

Verification: 227 Python tests passed, JS helper suite passed, 26 desktop/mobile
browser checks passed, production build and whitespace checks passed. Live checks
at 1440 and 390 pixels show Heat/Pistons as Projected with the original 242.4
combined total. Archived schema-1 aggregates still validate unchanged. Missing
minutes, unresolved status/role and a cold-start player in the projected five
retain partial status.
