# Game-day dashboard

The owner's NBA-app inspiration informs a dark sports navigation bar, horizontal
game strip, white content canvas, condensed matchup typography and blue actions.
The generated full-screen concept is the visual reference. All controls and data
are native UI, with official local SVG team marks and an independent NBA Forecast wordmark.

Tokens: white #fff, navy #0b182b, ink #07111f, blue #006cff, muted #667890,
border #dce3ec, soft #f1f5f9, amber #966400, red #b8223d. Radius 6px. Desktop
navigation 60px, game strip 112px, page gutters 46px, matchup band 136px.
Typography: condensed heavy headings and team names, readable sans-serif body,
14px controls, tabular numeric columns. A horizontal table and separate right
rail become a single column on phones; game strip scrolls without body overflow.

Primary screen: Games/Projections/Players/Performance navigation; date and game selectors;
matchup and T-30 time; player status/minutes/FGA/points table; search/status filters;
pregame checklist, evidence freshness, review queue and player detail dialog.
Players view covers the whole current roster; performance shows real sample counts,
error/calibration metrics and paired baselines, or an explicit no-results state.

Intentional concept deviations: actual feed names/statuses replace illustrative
rows; display source age, missing run states, correct actual local timezone, and
runner freshness even if they add a short line. No invented numerical forecasts,
fake scores, fake accuracy or healthy-by-omission. Refresh reloads saved evidence,
not a paid model or background job. UI is read-only in v1, with review inputs
visible and exportable; forecast and source writes remain in the existing CLI.

Architecture: React/Vite frontend; read-only Python dashboard exporter/server
reads schedule JSON and immutable SQLite ledger, selecting latest live pregame
run matching current game identity. Serves only built UI and /api/dashboard,
loopback-only, with no file browsing or mutation endpoints. Static export mode
supports a portable preview clearly labeled with its capture timestamp.

Verification: Python boundary tests for no DB mutation, latest eligible selection,
changed games, null data and empty states; frontend utility tests and build;
real browser interactions and mobile/desktop screenshots compared to the concept.


Updated direction: larger team logos, stronger condensed type and crisp navy/blue
surfaces. The projection board follows fantasy-app scanning conventions while
showing only modeled NBA statistics and actual saved evidence.

Fidelity checklist: dark navigation and blue selected underline; scrolling game
rail with real team marks; condensed uppercase matchup names; navy matchup band
with team-colored glow; white tabular player rows and restrained status pills;
right-hand evidence checklist on desktop, stacked on mobile. Intentional changes
from the concept: independent wordmark without NBA league logo, real roster/team
associations, full player list rather than six illustrative rows, explicit evidence
capture line and an added Projections tab requested after concept approval.
