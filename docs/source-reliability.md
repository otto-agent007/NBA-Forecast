# Sources and retrieval reliability

Investigated 2026-10-22. Source expansion must preserve metric definitions,
regular-season scope, observation timestamps, and original provider attribution.

## Source roles

- NBA LiveData/CDN: primary schedule, boxscore and play-by-play evidence.
- Official NBA injury PDFs: authoritative availability snapshots. A check at
  tipoff minus 30 minutes is an operational checkpoint, not a guarantee that
  subsequent updates cannot occur.
- StatMuse: proposed structured research and independent cross-check source.
  Its documented API returns typed responses; only validated `stats` rows qualify
  as numerical evidence. Written answers or `unknown` results are not data imports.
  Validate player identity, season, season type and exact split definition before
  accepting results. Fourth-quarter stats are not interchangeable with clutch stats.
- Other historical tables: corroboration only until definitions and coverage are
  reconciled. Agreement between sites can reflect a shared upstream source.

StatMuse documentation reviewed:
https://developer.statmuse.com/docs/asking-questions/
https://developer.statmuse.com/docs/quickstart/
https://www.statmuse.com/terms

API access requires account credentials; MCP offers an account connection.
No StatMuse integration or account connection was activated in this change.
Documentation labels access personal/non-commercial; confirm appropriate access
before product deployment. Retain provider attribution with any imported results.
Do not assume the API supports clutch filters without verifying returned evidence.

## Investigation evidence

A direct urllib probe with our existing NBA headers and a 12-second timeout:

- Schedule URL: HTTP 200, about 4.38 seconds, Content-Type text/plain.
- Lowercase leaguedashplayerclutch URL: HTTP 500, about 3.30 seconds.
  Parameters: LeagueID=00, Season=2024-25, SeasonType=Regular Season,
  PerMode=Totals, ClutchTime=Last 5 Minutes, AheadBehind=Ahead or Behind,
  PointDiff=5.

This demonstrates endpoint/request-specific failure, not a general network outage.
It does NOT establish whether the cause is missing parameters, server failure,
or infrastructure filtering. Next diagnosis should compare the complete request
contract against a known working client and vary one parameter at a time.
Earlier research also tried a mixed-case endpoint and a page whose HTML did not
contain the statistical rows. Neither is a validated substitute for the API.

## Diagnostic repair

Previously failed fetch attempts left no durable records; final network errors
omitted endpoint and exception class. The existing fetcher now appends metadata
for HTTP, network and JSON parsing failures to `fetch-failures.jsonl` in its raw
cache directory. Records contain timestamp, URL, category, exception class,
attempt number and HTTP status where applicable. No headers or response bodies
are added to this log. Invalid JSON responses retain the existing raw archive.
Logging is best-effort if storage is unwritable; the original error still surfaces.

Existing bounded retries remain unchanged. This is observability repair, not a
claim that the external NBA Stats failure is fixed. Schema/normalization failures
are still raised by their validators and are not yet part of this fetch log.

## Follow-up acceptance criteria

1. Reconcile full clutch request parameters and reproduce any fix live.
2. Connect StatMuse using authorized account access; verify exact split support.
3. Add provider identity mappings and preserve nullable/missing values.
4. Compare overlapping samples before enabling fallback; quarantine disagreements.
5. Add provider health and stale-data coverage to the dashboard. Never silently
   relabel cached observations as fresh or replace missing stats with zeros.

Validation: three regression tests failed before the diagnostic repair;
`python -m unittest discover -q` passes all 147 tests after the repair.

## Follow-up: complete request succeeded

Compared the request against the `swar/nba_api` client:
https://github.com/swar/nba_api/blob/master/src/nba_api/stats/endpoints/leaguedashplayerclutch.py

The abbreviated query returned HTTP 500. The complete parameter contract with
unchanged headers returned HTTP 200 in 5.19 seconds and 498 player rows. Missing
request parameters were the actionable defect; the individual minimal required
subset was not isolated. No proxy, access-control bypass or header workaround
was needed. The canonical request now lives in `nba_forecast/clutch.py` and is
accepted by the existing bounded fetcher and raw archive allowlist.

Three full-season requests then succeeded with 469, 498 and 492 rows respectively.
See [the archived evidence](../datasets/clutch-2026-10-22/README.md).
All response filters are checked against the request before publishing results.

StatMuse discovery returned no available plugin in this session (the directory
may contain additional plugins). Its documented MCP endpoint is
https://api.statmuse.com/mcp and requires account sign-in and active API access.
No credentials were requested or pasted; account connection remains outstanding.

Exact clutch support remains unverified. A public example demonstrates why:
https://www.statmuse.com/nba/ask/most-clutchs-point-in-nba-this-season
returned full regular-season scoring totals, despite clutch wording in the query.
This is not proof of universal lack of clutch support, but the response is
unsuitable as clutch evidence and was not imported. StatMuse remains a proposed
cross-check source, not an enabled automatic fallback.

Validation after adding the canonical clutch request and validator: 150 Python
tests passed. Live retrieval validated all 1,459 returned player-season rows.
