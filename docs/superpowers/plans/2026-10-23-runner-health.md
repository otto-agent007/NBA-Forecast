# Runner health repair

Observed failures: next-day preliminary jobs ran before the owner's 9 AM Pacific
checkpoint; the NBA player directory returned 403 while the saved Kings roster
missed a new member; an active tick mixed Python module signatures during editing.

- [x] Reproduce premature jobs, blocked Kings membership and mixed-version error.
- [x] Test and enforce 9 AM Pacific preliminary readiness, including DST/early finals.
- [x] Keep future jobs visible as waiting without submitting them to preflight.
- [x] Test and add an official 30-team roster fallback with complete identity/season
  validation, preserved raw source captures and oldest-observation freshness.
- [x] Capture a fresh full roster and confirm an automatic tick completes error-free.
- [x] Test and add content-addressed runner releases to isolate active ticks from edits.
- [x] Test/show waiting jobs and real errors separately in the dashboard.
- [x] Verify full suites/build, install the pinned runner, and observe a clean tick.
- [x] Save locally.

No ledger rows, availability gates, roster-disagreement checks or final deadlines
are weakened. Failed fallback collections never replace a complete roster. Source
failures and genuine due-job validation failures remain actionable errors.

Verification: 234 Python tests passed; JS helper suite passed; 28 desktop/mobile
browser checks passed; production build and whitespace checks passed. Live checks
at 1440 and 390 pixels show a healthy runner with five jobs waiting for Oct 24
9 AM Pacific, no runtime errors, and no horizontal overflow.

Installed pinned release:
`95f69ec510ee959cb1a551a6077d9d01700d7ce836658eb1b335bafd6223deaf`.
The installed runner started at 00:27:13 UTC and completed at 00:28:51 UTC with
status complete, zero errors and zero missed finals. The one-minute timer remains
active. The original unit is retained under production/verified-units/ for recovery.

NBA calendar retrieval still returns 403; its saved-calendar warning remains
visible and fresh independent matchup/time validation remains mandatory.
