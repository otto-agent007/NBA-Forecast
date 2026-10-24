# Forecast point totals

Show away-team points, home-team points and their combined game total for the
selected preliminary or final forecast. Sum unrounded supported player means;
round only the displayed values. Use regulation projections, without overtime
or implied betting lines. Player filters must not change these totals.

Use the existing player model, rather than introduce an independent team model
or extrapolate missing minutes. Mark totals partial if availability/production
is unresolved, fewer than five players are projected or fewer than 240 minutes
are assigned. Show projected minutes and withheld counts. A team with no
supported player projections has no numeric total; an incomplete matchup cannot
claim a numeric combined total when either team has no estimate.

Owner correction: a roster player with no positive-minute history should not
make a fully allocated rotation partial. Coverage schema 2 treats an exclusively
`cold_start` player with audited zero played games and available status as a
separate unprojected roster observation. Other unresolved roles/availability
still mark totals partial. No inactive/dressed status or zero production is
inferred. Display annotations use current coverage rules; saved aggregates stay
immutable and remain available as `saved_totals`.

Persist totals in new forecast artifacts. Derive totals from the original saved
predictions for older dashboard runs without modifying immutable ledger rows.
Validate new saved totals against the player rows. Existing player estimates
and injury/roster gates retain their behavior.

Implementation choice: use the existing Python and React components, with a
shared deterministic Python aggregation helper and no new dependencies.
