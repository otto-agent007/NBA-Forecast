"""Conservative archived ESPN final fallback for NBA-identified observations.

No player IDs, missing played statistics or sub-minute precision are inferred.
Contradictory participation flags require explicit inactive/zero-count evidence
and retain a resolution audit. A failed required join rejects the entire game.
Source observation timestamps and optional-category errors survive normalization.
"""
from collections import defaultdict
from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo

from .injury_parser import normalized
from .reference import BASE, code, fetch_reference
from .validation import COUNTS, parse_time, validate_history

class ActualsPending(ValueError):
    """The independently matched game has not published explicit final status."""


class ActualsSourceConflict(ActualsPending):
    """An explicit final still has disagreeing source totals; wait for correction."""

    def __init__(self, *, game_id, team_id, provider_event_id, statistic,
                 player_total, team_total, metadata):
        super().__init__(f"ESPN team/player {statistic} totals mismatch: "
                         f"players={player_total}, team={team_total}; awaiting source correction")
        self.details = dict(game_id=game_id, team_id=team_id, provider_event_id=provider_event_id,
                            statistic=statistic, player_total=player_total, team_total=team_total,
                            source_url=metadata["source_url"], source_sha256=metadata["sha256"],
                            retrieved_at=metadata["retrieved_at"])


_PAIRS = {
    "fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("fg3m", "fg3a"),
    "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta"),
}
_OPTIONAL = {"rebounds": "reb", "assists": "ast", "steals": "stl",
             "blocks": "blk", "offensiveRebounds": "oreb"}
_LABELS = {"MIN": "minutes", "PTS": "points", "FG": "fieldGoalsMade-fieldGoalsAttempted",
           "3PT": "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
           "FT": "freeThrowsMade-freeThrowsAttempted", "TO": "turnovers",
           "REB": "rebounds", "AST": "assists", "STL": "steals",
           "BLK": "blocks", "OREB": "offensiveRebounds"}


def _count(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r"\d+", str(value)):
        raise ValueError("missing or invalid nonnegative count")
    return int(value)


def _minutes(value):
    if not isinstance(value, str):
        raise ValueError("MIN must be an explicit source string")
    if re.fullmatch(r"\d+:[0-5]\d", value):
        minutes, seconds = value.split(":")
        return int(minutes) + int(seconds) / 60, "seconds"
    if re.fullmatch(r"\d+", value):
        return int(value), "whole_minutes"
    if re.fullmatch(r"\d+\.\d+", value):
        return float(value), "decimal_minutes"
    raise ValueError("invalid source MIN")


def _season(game):
    gid = game["game_id"]
    if not isinstance(gid, str) or not re.fullmatch(r"002\d{7}", gid):
        raise ValueError("requires a regular-season NBA game ID")
    year = 2000 + int(gid[3:5])
    season = f"{year}-{str(year + 1)[-2:]}"
    if game.get("season_type") != "Regular Season" or game.get("season", season) != season:
        raise ValueError("NBA game season mismatch")
    return season


def _competition(comp, game):
    if parse_time(comp["date"]) != parse_time(game["start_time"]):
        raise ValueError("ESPN game time/date mismatch")
    competitors = comp["competitors"]
    sides = {c["homeAway"]: c for c in competitors}
    if len(competitors) != 2 or set(sides) != {"home", "away"}:
        raise ValueError("ESPN home/away teams are not unique")
    if game["home_team_id"] == game["away_team_id"]:
        raise ValueError("NBA teams are not distinct")
    for side, competitor in sides.items():
        if code(competitor["team"]["abbreviation"]) != code(game[side + "_tricode"]):
            raise ValueError("ESPN matchup identity mismatch")
    if str(sides["home"]["team"]["id"]) == str(sides["away"]["team"]["id"]):
        raise ValueError("ESPN teams are not distinct")
    return sides


def _final(comp):
    status = comp.get("status", {}).get("type", {})
    if (status.get("completed") is not True or status.get("state") != "post"
            or status.get("name") not in {"STATUS_FINAL", "STATUS_FINAL_OVERTIME"}):
        raise ActualsPending("ESPN game is not explicitly final")


def _metadata(metadata, url, start):
    if metadata["source_url"] != url:
        raise ValueError("ESPN source event identity mismatch")
    if not isinstance(metadata.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"]):
        raise ValueError("ESPN source archive hash is missing or invalid")
    observed = parse_time(metadata["retrieved_at"])
    if observed <= start or observed > datetime.now(timezone.utc):
        raise ValueError("ESPN actual observation must be after start and not in the future")
    return observed


def _identities(roster, history, aliases):
    people = roster["players"] if isinstance(roster, dict) else roster
    names, canonical, memberships = defaultdict(set), {}, defaultdict(set)
    # Prefer current official roster spelling for a known person, while keeping
    # exact historical spellings and team membership available for departed IDs.
    for person in list(history) + list(people):
        pid, tid, name = str(person["player_id"]), str(person["team_id"]), person["name"]
        if not pid or not tid or not isinstance(name, str) or not normalized(name):
            raise ValueError("invalid NBA player identity")
        names[(tid, normalized(name))].add(pid)
        canonical[(tid, pid)] = name
        memberships[pid].add(tid)
    alias_evidence = {}
    for alias in aliases:
        ids = {str(alias[k]) for k in ("nba_id", "NBAID", "player_id") if k in alias}
        if len(ids) != 1:
            raise ValueError("alias requires one explicit NBA identity")
        pid = ids.pop()
        nba_name, espn_name = alias["nba_name"], alias["espn_name"]
        if not all(isinstance(name, str) and normalized(name) for name in (nba_name, espn_name)):
            raise ValueError("alias requires explicit person names")
        teams = memberships.get(pid, set())
        if "team_id" in alias:
            teams = teams & {str(alias["team_id"])}
        matched = [tid for tid in teams if pid in names.get((tid, normalized(nba_name)), set())]
        if not matched:
            raise ValueError("alias NBA identity/name is not supported by roster/history")
        for tid in matched:
            key = (tid, normalized(espn_name))
            names[key].add(pid)
            alias_evidence[(key, pid)] = dict(alias)
    return names, canonical, alias_evidence


def _stats(values, keys):
    if not isinstance(values, list) or len(values) != len(keys):
        raise ValueError("ESPN player statistics columns are incomplete")
    return dict(zip(keys, values))


def _counts(stats):
    result = {"tov": _count(stats["turnovers"])}
    for source, (made, attempted) in _PAIRS.items():
        parts = stats[source].split("-")
        if len(parts) != 2:
            raise ValueError("missing or invalid made-attempted counts")
        result[made], result[attempted] = map(_count, parts)
    return result


def _normalize(summary, game, roster, metadata, history, name_aliases):
    season = _season(game)
    start = parse_time(game["start_time"])
    header = summary["header"]
    event_id = str(header["id"])
    if not re.fullmatch(r"\d{6,12}", event_id):
        raise ValueError("invalid ESPN event identity")
    observed = _metadata(metadata, BASE + "summary?event=" + event_id, start)
    if header["season"]["year"] != int(season[:4]) + 1 or header["season"]["type"] != 2:
        raise ValueError("ESPN season mismatch")
    comps = header["competitions"]
    if len(comps) != 1 or str(comps[0]["id"]) != event_id:
        raise ValueError("ESPN competition identity is not unique")
    comp = comps[0]
    sides = _competition(comp, game)
    _final(comp)
    provenance = {"summary": {k: metadata[k] for k in ("source_url", "retrieved_at", "sha256")}}
    known_at = metadata["retrieved_at"]
    if "scoreboard" in metadata:
        board_meta = metadata["scoreboard"]
        date = start.astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")
        board_observed = _metadata(board_meta, BASE + "scoreboard?dates=" + date, start)
        provenance["scoreboard"] = dict(board_meta)
        if board_observed > observed:
            known_at = board_meta["retrieved_at"]
    names, canonical, aliases = _identities(roster, history, name_aliases)
    player_boxes, team_boxes = summary["boxscore"]["players"], summary["boxscore"]["teams"]
    provider_ids = {str(c["team"]["id"]) for c in sides.values()}
    for boxes in (player_boxes, team_boxes):
        if len(boxes) != 2 or {str(b["team"]["id"]) for b in boxes} != provider_ids:
            raise ValueError("ESPN boxscore teams are incomplete or duplicated")
    rows, seen, espn_seen = [], set(), set()
    from .schedule import STATE_ZONES,CITY_ZONES
    venue=summary.get('gameInfo',{}).get('venue') or comp.get('venue') or {}
    address=venue.get('address') or {}
    venue_zone=CITY_ZONES.get(address.get('city')) or STATE_ZONES.get(address.get('state'))
    for side, competitor in sides.items():
        tid = str(game[side + "_team_id"])
        opponent = str(game[("away" if side == "home" else "home") + "_team_id"])
        provider_id = str(competitor["team"]["id"])
        player_box = next(b for b in player_boxes if str(b["team"]["id"]) == provider_id)
        team_box = next(b for b in team_boxes if str(b["team"]["id"]) == provider_id)
        raw_team_stats = team_box["statistics"]
        team_stats = {s["name"]: s["displayValue"] for s in raw_team_stats}
        if len(team_stats) != len(raw_team_stats):
            raise ValueError("duplicate ESPN team statistic")
        team_counts = {**_counts(team_stats), "pts": _count(competitor["score"])}
        team_optional_errors = {}
        for source, field in _OPTIONAL.items():
            try:
                team_counts[field] = _count(team_stats[source])
            except (KeyError, TypeError, ValueError) as exc:
                team_counts[field] = None
                team_optional_errors[field] = str(exc)
        for source, field in [("teamTurnovers", "team_turnovers"), ("totalTurnovers", "total_turnovers")]:
            if source in team_stats:
                team_counts[field] = _count(team_stats[source])
        if {"team_turnovers", "total_turnovers"} <= team_counts.keys():
            if team_counts["total_turnovers"] != team_counts["tov"] + team_counts["team_turnovers"]:
                raise ValueError("ESPN team turnover arithmetic mismatch")
        team_rows = []
        if len(player_box["statistics"]) != 1:
            raise ValueError("ESPN player statistic groups are not unique")
        group = player_box["statistics"][0]
        raw_keys = group.get("keys") or group.get("labels") or group.get("names")
        if not isinstance(raw_keys, list) or not raw_keys:
            raise ValueError("ESPN player statistic columns missing")
        keys = [_LABELS.get(key, key) for key in raw_keys]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate ESPN player statistic columns")
        for entry in group["athletes"]:
            athlete = entry["athlete"]
            espn_name = athlete.get("fullName") or athlete.get("displayName")
            if not isinstance(espn_name, str) or not normalized(espn_name):
                raise ValueError("ESPN athlete full identity missing")
            key = (tid, normalized(espn_name))
            matches = names.get(key, set())
            if len(matches) != 1:
                raise ValueError(f"unknown or ambiguous ESPN player identity: {tid} {espn_name}")
            pid = next(iter(matches))
            espn_id = str(athlete["id"])
            if pid in seen or espn_id in espn_seen:
                raise ValueError("duplicate ESPN player identity")
            seen.add(pid)
            espn_seen.add(espn_id)
            if type(entry.get("didNotPlay")) is not bool:
                raise ValueError("ESPN player participation must be explicit")
            row = dict(game_id=game["game_id"], season=season, season_type="Regular Season",
                       game_time=game["start_time"], known_at=known_at,
                       source_url=metadata["source_url"], source_sha256=metadata["sha256"],
                       source_provenance=provenance, provider_event_id=event_id,
                       provider_player_id=espn_id, source_name=espn_name,
                       player_id=pid, name=canonical[(tid, pid)], team_id=tid, opponent_id=opponent,
                       venue_timezone=venue_zone,venue_address=address,
                       status="dnp" if entry["didNotPlay"] else "played", optional_stat_errors={},
                       team_boxscore=team_counts, team_optional_stat_errors=team_optional_errors)
            if (key, pid) in aliases:
                row["identity_alias"] = aliases[(key, pid)]
            if "starter" in entry:
                if type(entry["starter"]) is not bool:
                    raise ValueError("ESPN starter must be explicit boolean evidence")
                row["starter"] = entry["starter"]
            if "period" in comp["status"]:
                period = _count(comp["status"]["period"])
                if period < 4:
                    raise ValueError("ESPN final period is invalid")
                row["game_period"] = period
            values = entry["stats"]
            if entry["didNotPlay"]:
                if values:
                    stats = _stats(values, keys)
                    counts = {**_counts(stats), "pts": _count(stats["points"])}
                    minutes, _ = _minutes(stats["minutes"])
                    if minutes or any(counts.values()):
                        raise ValueError("ESPN DNP contradicts source statistics")
                    for source in _OPTIONAL:
                        if source in stats and _count(stats[source]) != 0:
                            raise ValueError("ESPN DNP contradicts optional statistics")
                row.update({field: 0 for field in COUNTS})
                row.update({field: 0 for field in _OPTIONAL.values()})
                row.update(minutes=0, minutes_precision="not_played", minutes_source_value=None,
                           counts_origin="structural zero from explicit didNotPlay=true",
                           participation_reason=entry.get("reason"))
            else:
                stats = _stats(values, keys)
                row.update(_counts(stats), pts=_count(stats["points"]))
                resolved_dnp = (stats["minutes"] == "--" and entry.get("active") is False
                                and entry.get("reason") in {"COACH'S DECISION", "INACTIVE"}
                                and not any(row[field] for field in COUNTS))
                if resolved_dnp:
                    # Native ESPN finals can contradict didNotPlay=false with
                    # explicit inactive, coach-decision and zero-count evidence.
                    # Missing MIN alone is never evidence of nonparticipation.
                    minutes, precision = 0, "not_played"
                    row.update(status="dnp", status_basis="inactive_zero_counts_despite_didNotPlay_false",
                               counts_origin="explicit source zero counts with inactive participation evidence",
                               participation_reason=entry["reason"],
                               participation_audit={"didNotPlay": entry["didNotPlay"],
                                                    "active": entry["active"], "reason": entry["reason"],
                                                    "minutes_source_value": stats["minutes"],
                                                    "resolution": "DNP: explicit inactive plus exact reason and zero core counts"})
                else:
                    minutes, precision = _minutes(stats["minutes"])
                row.update(minutes=minutes, minutes_precision=precision,
                           minutes_source_value=stats["minutes"])
                for source, field in _OPTIONAL.items():
                    try:
                        row[field] = _count(stats[source])
                    except (KeyError, TypeError, ValueError) as exc:
                        row[field] = None
                        row["optional_stat_errors"][field] = str(exc)
                if resolved_dnp and any(row[field] for field in _OPTIONAL.values()):
                    raise ValueError("ESPN inactive DNP resolution contradicts optional statistics")
                if row["oreb"] is not None and row["reb"] is not None and row["oreb"] > row["reb"]:
                    raise ValueError("ESPN offensive rebounds exceed total rebounds")
            team_rows.append(row)
        if not team_rows or not any(row["status"] == "played" for row in team_rows):
            raise ValueError("ESPN final player boxscore empty")
        for field in COUNTS:
            player_total = sum(row[field] for row in team_rows)
            if player_total != team_counts[field]:
                raise ActualsSourceConflict(game_id=game["game_id"], team_id=tid,
                    provider_event_id=event_id, statistic=field, player_total=player_total,
                    team_total=team_counts[field], metadata=metadata)
        for field in _OPTIONAL.values():
            if team_counts[field] is not None and all(row[field] is not None for row in team_rows):
                player_total = sum(row[field] for row in team_rows)
                if player_total != team_counts[field]:
                    raise ActualsSourceConflict(game_id=game["game_id"], team_id=tid,
                        provider_event_id=event_id, statistic=field, player_total=player_total,
                        team_total=team_counts[field], metadata=metadata)
        rows.extend(team_rows)
    validate_history(rows)
    return rows


def normalize_espn_final(summary, game, roster, metadata, history=(), name_aliases=()):
    """Normalize one exact source-backed final, or raise ValueError atomically."""
    try:
        return _normalize(summary, game, roster, metadata, history, name_aliases)
    except (KeyError, TypeError, AttributeError, StopIteration, OverflowError) as exc:
        raise ValueError(f"incomplete or malformed ESPN final: {exc}") from exc


def fetch_espn_final(game, roster, directory, *, history=(), name_aliases=()):
    """Archive a unique exact-date final scoreboard join and its full summary."""
    try:
        season = _season(game)
        start = parse_time(game["start_time"])
        date = start.astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")
        board, board_meta = fetch_reference(BASE + "scoreboard?dates=" + date, directory)
        candidates = []
        for event in board.get("events", []):
            for comp in event.get("competitions", []):
                try:
                    _competition(comp, game)
                except (ValueError, KeyError, TypeError):
                    continue
                candidates.append((event, comp))
        if len(candidates) != 1:
            raise ValueError("ESPN scoreboard join is not unique")
        event, comp = candidates[0]
        _final(comp)
        if "season" in event and (event["season"]["year"] != int(season[:4]) + 1 or event["season"]["type"] != 2):
            raise ValueError("ESPN scoreboard season mismatch")
        _metadata(board_meta, BASE + "scoreboard?dates=" + date, start)
        event_id = str(event["id"])
        if str(comp["id"]) != event_id or not re.fullmatch(r"\d{6,12}", event_id):
            raise ValueError("ESPN scoreboard event identity mismatch")
        history, name_aliases = tuple(history), tuple(name_aliases)
        def verified(summary, meta):
            if str(summary["header"]["id"]) != event_id:
                raise ValueError("ESPN summary event differs from scoreboard")
            for summary_comp in summary["header"]["competitions"]:
                summary_sides, board_sides = _competition(summary_comp, game), _competition(comp, game)
                for side in ("home", "away"):
                    if (_count(summary_sides[side]["score"]) != _count(board_sides[side]["score"])
                            or str(summary_sides[side]["team"]["id"]) != str(board_sides[side]["team"]["id"])):
                        raise ValueError("ESPN scoreboard/summary team or final score mismatch")
            rows = normalize_espn_final(summary, game, roster, {**meta, "scoreboard": board_meta},
                                        history=history, name_aliases=name_aliases)
            if "period" in comp["status"]:
                period = _count(comp["status"]["period"])
                if period < 4 or any(row.get("game_period", period) != period for row in rows):
                    raise ValueError("ESPN scoreboard/summary final period mismatch")
                for row in rows:
                    if "game_period" not in row:
                        row.update(game_period=period, game_period_source=board_meta["source_url"])
            return rows

        summary_url = BASE + "summary?event=" + event_id
        summary, meta = fetch_reference(summary_url, directory)
        try:
            return verified(summary, meta)
        except ActualsSourceConflict as conflict:
            # A recent aggregate-only correction may temporarily precede player
            # corrections. Retry the current source once; never select an older
            # internally consistent archive over a known newer disagreement.
            try:
                summary, meta = fetch_reference(summary_url, directory, max_age=0)
            except OSError as exc:
                conflict.details["retry_error"] = str(exc)
                raise conflict from exc
            return verified(summary, meta)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"incomplete or malformed ESPN scoreboard: {exc}") from exc
