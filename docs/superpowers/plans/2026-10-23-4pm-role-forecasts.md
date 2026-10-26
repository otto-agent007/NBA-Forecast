# Four-PM forecast readiness and early-season role estimates

Goal: correct conditional minutes for Paul George and similar early-season/team-change cases, resolve the five 4 PM Pacific games' data blockers, and verify final checkpoints at 3:30 PM Pacific.

Root cause: George's actual Boston opener was 30:35. Model 0.2.1 used 82 prior-season games including 45 DNPs as a minutes role prior, diluting his forecast to 17.06 despite conditional participation. His prior-season played-game average was 30.68. Removing old DNPs alone raises all rotation estimates and triggers team scaling, so validate the complete team budget and current-team role adaptation as well.

- [x] Regress prior-season DNP dilution, current-team role changes, future-evidence exclusion and team budgets; preserve recent current-season DNP review and sourced restrictions.
- [x] Estimate role minutes from prior appearances, cap transferred role prior once positive-minute current-team evidence exists, retain shooting stabilization and auditable role components. Apply shared logic to profiles and forecasts; bump model version.
- [x] Independently investigate NYK/BOS and ATL/CHA roster discrepancies without fabricating roster or availability. Preserve source observations and archives; allow only evidenced reconciliation.
- [x] Rebuild preliminary forecasts with the fixed model, inspect all five matchup gates and numeric/minute coverage, run regression/build checks and independent review.
- [x] Verify persistent timer, exact 3:30 PM final checkpoint plan, and final run/checkpoint readiness with visible reasons for any blocked sources. Preserve every earlier saved run.


Verification: 213 Python tests, 12 JavaScript cases, 16 desktop/mobile browser checks, production build and whitespace checks passed. Real live dashboard at1440x1000 and390x844 shows Paul George26.6 forecast minutes and30.6 actual minutes in his player detail, with no app exceptions or framework overlay. All five4PM matchups have saved model0.3.0 preliminary runs and passed preflight; every team has five starter slots and total numeric minutes<=240.

The researched discrepancy files expire at23:00Z and preserve NBA/ESPN disagreement. Fresh official NBA team rosters are hash-archived; McCullar/Ajayi numeric forecasts remain null and Knecht is not silently added to canonical membership. Independent review found that selected-run warnings could disappear behind later audit state; saved warnings are now rendered separately and the changed-latest browser case passes.

At verification time the final window was still in the future. Existing minute forecast timer remains active for22:30Z(3:30PM Pacific). A date-bounded audit timer nba-forecast-fourpm-audit-20261023.timer starts22:33Z and repeats each minute through22:59Z. Its service was smoke-tested successfully; output data/operations/production/final-four-pm-audit.json correctly says pending before the window. The audit checks five saved final runs, model version, actual T-30 injury evidence, pregame save time, five starter IDs,240-minute budgets and null forecasts for quarantined players. It exits nonzero for needs_attention and retains dated audit records. No final capture or outcome accuracy is claimed before execution. No remote publication.
