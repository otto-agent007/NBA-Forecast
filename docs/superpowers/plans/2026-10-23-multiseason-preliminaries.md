# Multi-season scoring and complete preliminary refresh

User requested fresh preliminary forecasts for all eleven remaining October23
games, excluding CHI-MIL, and scoring estimates grounded in more than opening
night. Brunson had74 prior-season appearances already, but uniform team-minute
scaling cut his34.82-minute role to29.28, lowering his forecast to21.14 points.

- [x] Reproduce dilution by speculative bench priors and regress supported core
  allocation. Protect observed starters and supported high-minute rotation roles;
  scale excess bench minutes first, preserving the regulation budget.
- [x] Strengthen early-season production-rate priors and collect verified
  2023-24/2024-25/2025-26 career totals across the remaining slate. Preserve exact
  points/shots/GP, label rounded-MPG minute estimates, verify raw hashes and NBA
  to ESPN identity, avoid duplicate traded-player totals and double counting.
- [x] Integrate these as source-stamped season priors distinct from game history.
  Exclude future-known sources; retain current-team role estimation and all
  availability/lineup gates. Keep history attached to NBA identity across trades.
- [x] Resolve researched Portis suffix identity and retain Hinson as an unresolved
  ESPN-only candidate. Official roster proof, expiry and numeric quarantine apply.
- [x] Regenerate eleven preliminary runs under model0.4, preserving every earlier
  run and the original CHI-MIL game. Prefer finals by default while retaining
  selectable regenerated preliminaries.
- [x] Expose historical seasons, PPG, sample sizes and source in player detail;
  test the model/source/browser paths and request independent review.

Evidence:215tests initially, then217 after archive/alias regressions;18 desktop
and mobile browser checks; production build. Raw historical archive351 players,
330 with history,829 season rows,373 source/identity responses preserved in the
versioned dataset. Season aggregates replace overlapping priors. One review
correction fixed the reported effective prior mass; it did not alter projections.

All eleven requested preliminary runs were saved22:32–22:39UTC with nonempty
historical priors and passed saved preflight gates. The refresh receipt is
`data/operations/production/requested-preliminary-refresh.json`. CHI-MIL's latest
saved run remains19:50:16UTC. Brunson now34.82 minutes/25.23 points. His source
seasons are28.73/26.0/26.04PPG over77/65/74 games respectively. Remaining departure
from a season average reflects minutes, efficiency regularization and matchup.

The five4PM final forecasts were also captured before tipoff and passed the
scheduled saved-run audit by22:40UTC; the first3:33 audit correctly reported
pending/needs-attention while source retries were completing. No claim of exact
3:30 execution is made. Official roster evidence cache now uses the same
15-minute bound as independent roster evidence; observations are never backdated.


Final verification:217 Python tests,12 JavaScript cases and18 browser checks pass.
Real desktop1440x1000 and mobile390x844 inspection confirms Brunson25.2/34.8 and
all three historical PPG/sample rows in his detail with no app exceptions or
framework overlay. Browser plugin unavailable; installed Playwright used.
The versioned dataset and consolidated capture recipe are retained locally;
production source archives and all original runs remain intact. No remote push.
