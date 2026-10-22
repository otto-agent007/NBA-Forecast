# Verified NBA source samples

These are observed NBA responses, not simulated games or backdated forecasts.
The sibling provenance files record each download's actual UTC retrieval time,
source URL, byte count, and SHA256. A 2025 game downloaded in October 2026 was
first known to this pipeline in October 2026; its retrieval time must not be
changed to the game's date to manufacture a historical forecast.

| File | Source evidence |
| --- | --- |
| `boxscore_0022500001.json` | Houston at Oklahoma City, October 21, 2025; final regular-season boxscore. |
| `boxscore_0022600001.json` | Boston at Detroit, October 20, 2026; final regular-season boxscore. |
| `boxscore_0022600002.json` | Philadelphia at New York, October 20, 2026; final regular-season boxscore. |
| `boxscore_0022600003.json` | Oklahoma City at San Antonio, October 20, 2026; final regular-season boxscore. |
| `roster.json` | Identity-only extraction from [NBA league roster](https://www.nba.com/players): 519 active roster members across 30 teams. Availability is `unknown` for every player. |
| `snapshot.json` | The same roster and the next 24 hours of scheduled regular-season games at the recorded actual UTC cutoff. This is a point-in-time ingestion example; it becomes stale and is not a standing live forecast. |

Boxscores are unchanged response bytes from
`https://cdn.nba.com/static/json/liveData/boxscore/boxscore_<game_id>.json`.
The roster provenance distinguishes the original HTML hash from the normalized
artifact hash. The full source HTML and league schedule are reproducible by the
collector and are not included in these compact examples.

The schedule comes from
[NBA scheduleLeagueV2_1.json](https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json).
That endpoint changes over time and contains preseason games, regular-season
games, and an NBA Cup championship entry. The adapter accepts only game IDs
beginning `002`; it excludes `001`, `004`, and `006`. A boxscore must also have
`gameStatus == 3`. Scheduled forecast games must have `gameStatus == 1` and a
start time later than the cutoff.

The collector uses NBA-facing request headers because bare requests returned
HTTP 403 during verification. Access can still fail or schemas can change.
Requests have a 15-second timeout, three attempts only for transient failures,
a 12 MiB response limit, an exact endpoint allowlist, and no redirects. It keeps
the unchanged response plus provenance in the requested cache directory.
