"""Bounded public NBA ingestion. Availability is separate from roster membership.

LiveData IDs beginning 002 are regular-season games; 001/004/006 are never
accepted. Each fetch archives the exact response bytes with its observation time.
NBA feeds are undocumented interfaces and can change or refuse network access.
"""
from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
import urllib.request


SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"
ROSTER_URL = "https://www.nba.com/players"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nba.com/", "Origin": "https://www.nba.com"}
# Since about 2026-10-22 cdn.nba.com answers bare or outdated browser user agents
# with 403 Access Denied. A current Firefox user agent plus the nba.com Referer is
# accepted (Firefox 140 was refused, 150+ passed); bump the version if 403s return.
# www.nba.com pages still answer the bare HEADERS and time out on these, so only
# the CDN gets them.
CDN_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:158.0) Gecko/20100101 Firefox/158.0",
               "Referer": "https://www.nba.com/", "Accept": "application/json, text/plain, */*",
               "Accept-Language": "en-US,en;q=0.5"}
MAX_BYTES = 12 * 1024 * 1024
DEFAULT_CACHE = Path(".nba_forecast/raw")
STAT_FIELDS = {"fgm": "fieldGoalsMade", "fga": "fieldGoalsAttempted", "fg3m": "threePointersMade", "fg3a": "threePointersAttempted", "ftm": "freeThrowsMade", "fta": "freeThrowsAttempted", "tov": "turnovers", "pts": "points"}

OPTIONAL_STAT_FIELDS = {"reb": "reboundsTotal", "ast": "assists", "stl": "steals", "blk": "blocks",
                        "oreb": "reboundsOffensive", "dreb": "reboundsDefensive",
                        "pf": "foulsPersonal", "plus_minus": "plusMinusPoints"}

def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed


def _allowed_url(url, allow_roster=False):
    if not isinstance(url, str):
        raise ValueError("Source URL must be a string")
    if re.fullmatch(r"https://stats\.nba\.com/stats/leaguegamelog\?Counter=0&DateFrom=&DateTo=&Direction=DESC&LeagueID=00&PlayerOrTeam=T&Season=20\d{2}-\d{2}&SeasonType=Regular%20Season&Sorter=DATE", url):
        return
    if re.fullmatch(r"https://official\.nba\.com/nba-injury-report-20\d{2}-\d{2}-season/", url):
        return
    if re.fullmatch(r"https://ak-static\.cms\.nba\.com/referee/injury/Injury-Report_\d{4}-\d{2}-\d{2}_\d{2}_\d{2}[AP]M\.pdf", url):
        return
    if url.startswith("https://stats.nba.com/stats/leaguedashplayerclutch?"):
        from .clutch import clutch_url
        match = re.search(r"(?:[?&])Season=(20\d{2}-\d{2})(?:&|$)", url)
        if match and url == clutch_url(match[1]):
            return
        raise ValueError("Clutch URL must use the complete canonical regular-season request")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment:
        raise ValueError("Only exact allowlisted HTTPS NBA URLs are accepted")
    if url == SCHEDULE_URL or (allow_roster and url == ROSTER_URL):
        return
    if parsed.netloc == "www.nba.com" and re.fullmatch(r"/game/002\d{7}/box-score", parsed.path):
        return
    if parsed.netloc == "cdn.nba.com" and re.fullmatch(r"/static/json/liveData/boxscore/boxscore_002\d{7}\.json", parsed.path):
        return
    if parsed.netloc == "cdn.nba.com" and re.fullmatch(r"/static/json/liveData/playbyplay/playbyplay_002\d{7}\.json", parsed.path):
        return
    raise ValueError("Source URL is not an allowlisted NBA endpoint")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("NBA source redirects are not followed")


def archive_payload(raw, source_url, known_at, directory):
    """Archive unchanged source bytes, using their digest to avoid collisions."""
    _allowed_url(source_url, allow_roster=True)
    _timestamp(known_at)
    digest = hashlib.sha256(raw).hexdigest()
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    suffix = ".pdf" if source_url.endswith(".pdf") else ".html" if source_url == ROSTER_URL or "official.nba.com/" in source_url or source_url.endswith("/box-score") else ".json"
    raw_path = folder / (digest + suffix)
    meta_path = folder / (digest + ".provenance.json")
    metadata = {"source_url": source_url, "retrieved_at": known_at, "sha256": digest, "bytes": len(raw)}
    if not raw_path.exists():
        raw_path.write_bytes(raw)
    elif raw_path.read_bytes() != raw:
        raise ValueError("Archive digest collision or corrupted raw cache")
    if not meta_path.exists():
        meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return {**metadata, "raw_path": str(raw_path), "provenance_path": str(meta_path)}


def _record_fetch_failure(cache_dir, url, category, exc, attempt=None, status=None):
    """Keep bounded metadata, never request headers or remote response bodies."""
    record = {"observed_at": datetime.now(timezone.utc).isoformat(),
              "source_url": url, "category": category,
              "exception_type": type(exc).__name__, "attempt": attempt, "status": status}
    try:
        folder = Path(cache_dir)
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "fetch-failures.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
    except OSError:
        # Diagnostics must not replace the original retrieval exception.
        pass


def _fetch(url, cache_dir, allow_roster=False):
    _allowed_url(url, allow_roster=allow_roster)
    opener = urllib.request.build_opener(_NoRedirects())
    for attempt in range(3):
        try:
            headers = CDN_HEADERS if urlsplit(url).netloc == "cdn.nba.com" else HEADERS
            with opener.open(urllib.request.Request(url, headers=headers), timeout=15) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("NBA response exceeds the 12 MiB limit")
            observed = datetime.now(timezone.utc).isoformat()
            archive_payload(raw, url, observed, cache_dir)
            return raw, observed
        except HTTPError as exc:
            _record_fetch_failure(cache_dir, url, "http", exc, attempt + 1, exc.code)
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ValueError(f"NBA source {url} returned HTTP {exc.code}; cached files can be used offline") from exc
        except (URLError, TimeoutError) as exc:
            _record_fetch_failure(cache_dir, url, "network", exc, attempt + 1)
            if attempt == 2:
                raise ValueError(f"NBA source {url} unavailable after 3 bounded attempts ({type(exc).__name__})") from exc
        time.sleep(0.25 * (attempt + 1))
    raise ValueError("NBA source unavailable")


def fetch_json(url, *, cache_dir=DEFAULT_CACHE):
    """Fetch an allowlisted schedule or regular-season boxscore and archive it."""
    raw, _ = _fetch(url, cache_dir)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        _record_fetch_failure(cache_dir, url, "invalid_json", exc)
        raise ValueError(f"NBA response from {url} was not valid JSON; inspect archived payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("NBA response must contain a JSON object")
    return payload


def boxscore_url(game_id):
    if not isinstance(game_id, str) or not re.fullmatch(r"002\d{7}", game_id):
        raise ValueError("Expected a regular-season NBA game ID")
    return f"https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"


def _minutes(value):
    if not isinstance(value, str):
        raise ValueError("Missing player minutes")
    match = re.fullmatch(r"PT(?:(\d+)H)?(\d+)M(\d+(?:\.\d+)?)S", value)
    if not match or float(match[3]) >= 60:
        raise ValueError(f"Invalid LiveData minutes: {value}")
    return int(match[1] or 0) * 60 + int(match[2]) + float(match[3]) / 60


def normalize_boxscore(payload, source_url, known_at):
    """Normalize final regular-season rows; an absent player remains absent."""
    from .validation import shooting_percentages, validate_history

    _allowed_url(source_url)
    observed = _timestamp(known_at)
    try:
        game = payload["game"]
        game_id = game["gameId"]
        if not isinstance(game_id, str) or not re.fullmatch(r"002\d{7}", game_id):
            raise ValueError("Only regular-season game IDs (002) are accepted")
        if source_url != boxscore_url(game_id):
            raise ValueError("Boxscore source URL does not match its game ID")
        if game["gameStatus"] != 3:
            raise ValueError("Only final boxscores are accepted")
        game_time = game["gameTimeUTC"]
        if _timestamp(game_time) >= observed:
            raise ValueError("A final boxscore cannot be known before its game starts")
        start_year = 2000 + int(game_id[3:5])
        season = f"{start_year}-{str(start_year + 1)[-2:]}"
        rows = []
        for side, other in (("homeTeam", "awayTeam"), ("awayTeam", "homeTeam")):
            team, opponent = game[side], game[other]
            if not isinstance(team["players"], list) or not team["players"]:
                raise ValueError("Final boxscore has no player list")
            for player in team["players"]:
                played = player["played"]
                if played not in ("0", "1"):
                    raise ValueError("Player participation must be explicitly recorded")
                stats = player["statistics"]
                row = {"game_id": game_id, "season": season, "season_type": "Regular Season", "game_time": game_time, "known_at": known_at, "source_url": source_url, "player_id": str(player["personId"]), "name": player["name"], "team_id": str(team["teamId"]), "opponent_id": str(opponent["teamId"]), "minutes": _minutes(stats["minutes"]), "status": "played" if played == "1" else "dnp"}
                row.update({field: stats[source] for field, source in STAT_FIELDS.items()})
                row.update({field: stats.get(source) for field, source in OPTIONAL_STAT_FIELDS.items()})
                if type(row["plus_minus"]) is float and row["plus_minus"].is_integer():
                    row["plus_minus"] = int(row["plus_minus"])
                row.update(minutes_precision="seconds" if played == "1" else "not_played",
                           minutes_source_value=stats["minutes"])
                row.update(shooting_percentages(row))
                if 'starter' in player:
                    if player['starter'] not in ('0','1'):raise ValueError('invalid starter evidence')
                    row['starter']=player['starter']=='1'
                row['home']=side=='homeTeam'
                rows.append(row)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Incomplete or malformed NBA boxscore: {exc}") from exc
    validate_history(rows)
    return rows


class _NextDataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.inside = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False

    def handle_data(self, value):
        if self.inside:
            self.parts.append(value)


def normalize_roster(players, observed_at, season):
    """Normalize the complete official /players active roster response.

    `unknown` describes game availability. Roster membership is insufficient
    evidence to label someone available, questionable, or out.
    """
    _timestamp(observed_at)
    if not re.fullmatch(r"20\d{2}-\d{2}", season):
        raise ValueError("Invalid roster season")
    normalized = []
    seen = set()
    try:
        for player in players:
            if player["ROSTER_STATUS"] != 1:
                continue
            if str(player["TO_YEAR"]) != season[:4]:
                raise ValueError("NBA roster does not match requested season")
            player_id = str(player["PERSON_ID"])
            if player_id in seen:
                raise ValueError("Duplicate active player in roster")
            seen.add(player_id)
            normalized.append({"player_id": player_id, "name": f"{player['PLAYER_FIRST_NAME']} {player['PLAYER_LAST_NAME']}".strip(), "team_id": str(player["TEAM_ID"]), "status": "unknown"})
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Incomplete NBA roster response: {exc}") from exc
    source_players={str(p['PERSON_ID']):p for p in players}
    bio_fields={'team_code':'TEAM_ABBREVIATION','position':'POSITION','jersey':'JERSEY_NUMBER','height':'HEIGHT','weight':'WEIGHT','college':'COLLEGE','country':'COUNTRY','draft_year':'DRAFT_YEAR','draft_round':'DRAFT_ROUND','draft_number':'DRAFT_NUMBER'}
    for row in normalized:
        source=source_players[row['player_id']]
        row['bio']={k:source.get(v) for k,v in bio_fields.items()}
        row['team_code']=source.get('TEAM_ABBREVIATION')
    teams = sorted({player["team_id"] for player in normalized})
    if set(teams) != {str(team) for team in range(1610612737, 1610612767)}:
        raise ValueError("Official active roster must contain all 30 NBA teams")
    return {"observed_at": observed_at, "source_url": ROSTER_URL, "complete": True, "team_ids": teams, "players": sorted(normalized, key=lambda row: (row["team_id"], row["player_id"]))}


def fetch_roster(season, *, cache_dir=DEFAULT_CACHE):
    try:raw, observed_at = _fetch(ROSTER_URL, cache_dir, allow_roster=True)
    except (ValueError,OSError):
        from .roster_refresh import fetch_team_rosters
        return fetch_team_rosters(season,cache_dir)
    parser = _NextDataParser()
    try:
        parser.feed(raw.decode("utf-8"))
        players = json.loads("".join(parser.parts))["props"]["pageProps"]["players"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("NBA active roster schema changed or is incomplete") from exc
    return normalize_roster(players, observed_at, season)


def normalize_schedule(payload, as_of, until=None):
    """Return scheduled regular-season games strictly after cutoff, within 24h.

    An explicit `until` can shorten or lengthen that window. Snapshot callers
    reject windows containing more than one game for the same team.
    """
    cutoff = _timestamp(as_of)
    end = _timestamp(until) if until is not None else cutoff + timedelta(hours=24)
    if end <= cutoff:
        raise ValueError("Schedule window must end after as_of")
    games, seen = [], set()
    try:
        schedule = payload["leagueSchedule"]
        season = schedule["seasonYear"]
        for day in schedule["gameDates"]:
            for game in day["games"]:
                game_id = game["gameId"]
                if not isinstance(game_id, str) or not re.fullmatch(r"002\d{7}", game_id):
                    continue
                if str(2000 + int(game_id[3:5])) != season[:4]:
                    raise ValueError("Schedule season and game ID disagree")
                start = _timestamp(game["gameDateTimeUTC"])
                if game["gameStatus"] != 1 or not cutoff < start <= end:
                    continue
                if game_id in seen:
                    raise ValueError("Duplicate scheduled game")
                seen.add(game_id)
                games.append({"game_id": game_id, "start_time": game["gameDateTimeUTC"], "season_type": "Regular Season", "home_team_id": str(game["homeTeam"]["teamId"]), "away_team_id": str(game["awayTeam"]["teamId"])})
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Incomplete NBA schedule response: {exc}") from exc
    return sorted(games, key=lambda game: (game["start_time"], game["game_id"]))


def build_snapshot(roster, schedule, as_of, until=None):
    from .validation import validate_inputs

    snapshot = {"schema_version": 1, "season": schedule["leagueSchedule"]["seasonYear"], "as_of": as_of, "roster": roster, "games": normalize_schedule(schedule, as_of, until)}
    validate_inputs(snapshot, [], scope="league")
    return snapshot
