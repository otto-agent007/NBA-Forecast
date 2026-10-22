"""Auditable, deterministic v0.1 scoring baseline (standard library only).

This is an evidence-only baseline, not a claim of betting edge. ``forecast``
expects the validated contract in docs/design.md but independently excludes
history that was played/known after the cutoff, non-regular seasons, and seasons
other than the snapshot season and its immediate predecessor.

Minutes and opportunity totals use at most six prior-season pseudo-games. Their
weight decays by 0.70 per current-season game; current games decay by 0.75 per
older game. DNPs are observed zero minutes, not fabricated missing observations.
Shooting changes more slowly: current attempts decay by 0.985 per older game;
previous attempts are capped at 250 two-point, 200 three-point, and 100 free-throw
attempts. Fixed regularization defaults are 0.52/0.35/0.75 with 40/50/20 pseudo
attempts respectively. These are modeling constants, not asserted current NBA
averages. They only smooth efficiency for supported players; they never create
minutes or shot attempts for players without positive-minute evidence. Players
with no observed FGA or FTA are withheld: absence of scoring opportunities in
a small sample does not establish certain zero-point production.

Points = 2*E[2PA]*P(2PM) + 3*E[3PA]*P(3PM) + E[FTA]*P(FTM), multiplied
by a bounded opponent points-allowed proxy. Manual game-specific adjustments
alter minutes and attempt/turnover rates, with full source/reviewer audit.
Projected team minutes above 240 are scaled down proportionally; deficits are
flagged rather than filled with invented bench production. Individual minutes
are capped at 48: this v0.1 does not forecast overtime.

Intervals are uncalibrated heuristics: binomial shot variance plus shrunk excess
historical shot residual variance and minutes uncertainty. They are evaluated
prospectively by the ledger, not presented as established 80% coverage.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from math import sqrt
from statistics import fmean, pvariance

MODEL_VERSION = "regular-season-baseline-0.1.0"
NUMERIC_FIELDS = (
    "expected_minutes", "expected_fga", "expected_fta", "expected_tov",
    "usage_proxy", "points_mean", "points_sd", "points_lower80", "points_upper80",
)
EFFICIENCY_DEFAULTS = {
    "two_point": (0.52, 40.0, 250.0),
    "three_point": (0.35, 50.0, 200.0),
    "free_throw": (0.75, 20.0, 100.0),
}


def _time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Model timestamps must include a timezone")
    return result


def _previous_season(season):
    start = int(season[:4]) - 1
    return f"{start:04d}-{(start + 1) % 100:02d}"


def _chronological(rows):
    return sorted(rows, key=lambda r: (_time(r["game_time"]), r["game_id"], r["player_id"]))


def _attempts(row, kind):
    if kind == "two_point":
        return row["fgm"] - row["fg3m"], row["fga"] - row["fg3a"]
    if kind == "three_point":
        return row["fg3m"], row["fg3a"]
    return row["ftm"], row["fta"]


def _shooting(previous, current):
    result = {}
    for kind, (default, strength, cap) in EFFICIENCY_DEFAULTS.items():
        prior_made = sum(_attempts(r, kind)[0] for r in previous)
        prior_attempts = sum(_attempts(r, kind)[1] for r in previous)
        prior_scale = min(1.0, cap / prior_attempts) if prior_attempts else 1.0
        weighted = [(r, 0.985 ** age) for age, r in enumerate(reversed(current))]
        current_made = sum(_attempts(r, kind)[0] * w for r, w in weighted)
        current_attempts = sum(_attempts(r, kind)[1] * w for r, w in weighted)
        numerator = prior_made * prior_scale + current_made + default * strength
        denominator = prior_attempts * prior_scale + current_attempts + strength
        result[kind] = {
            "percentage": numerator / denominator,
            "prior_made": prior_made, "prior_attempts": prior_attempts,
            "prior_effective_attempts": prior_attempts * prior_scale,
            "current_weighted_made": current_made,
            "current_weighted_attempts": current_attempts,
            "default_percentage": default, "default_pseudo_attempts": strength,
            "prior_attempt_cap": cap, "current_decay_per_game": 0.985,
        }
    return result


def _opportunities(previous, current):
    prior_weight = min(len(previous), 6) * 0.70 ** len(current)
    weighted = [(r, 0.75 ** age) for age, r in enumerate(reversed(current))]
    # Each previous row gets an equal fraction of the prior pseudo-game mass.
    if previous:
        weighted += [(r, prior_weight / len(previous)) for r in previous]
    mass = sum(w for _, w in weighted)
    totals = {field: sum(r[field] * w for r, w in weighted)
              for field in ("minutes", "fga", "fg3a", "fta", "tov")}
    minutes = totals["minutes"] / mass if mass else 0.0
    rates = {field: totals[field] / totals["minutes"] if totals["minutes"] else 0.0
             for field in ("fga", "fg3a", "fta", "tov")}
    # A 4-minute prior SD stabilizes small samples without creating production.
    observed_variance = (sum(w * (r["minutes"] - minutes) ** 2 for r, w in weighted)
                         / mass if mass else 0.0)
    minute_variance = (observed_variance * mass + 16.0 * 4) / (mass + 4)
    return minutes, rates, minute_variance, {
        "prior_effective_games": prior_weight, "current_weight": mass - prior_weight,
        "prior_decay_per_current_game": 0.70, "current_decay_per_game": 0.75,
        "minutes_before_adjustment": minutes, "per_minute_rates": rates.copy(),
        "observed_minutes_variance": observed_variance,
    }


def _complete_team_games(history):
    """Require both sides and compatible 240 + 25*OT minute totals (±1 minute).

    Missing DNP rows cannot bias point totals; missing positive-minute rows
    normally fail this check. This is a completeness proxy, not authentication
    of upstream data. Counts and scoring identities are checked by validation.
    """
    groups = defaultdict(lambda: defaultdict(list))
    for row in history:
        groups[row["game_id"]][row["team_id"]].append(row)
    complete = []
    for game_id in sorted(groups):
        teams = groups[game_id]
        if len(teams) != 2:
            continue
        a, b = sorted(teams)
        totals = {t: sum(r["minutes"] for r in rows) for t, rows in teams.items()}
        overtime = {t: round((total - 240.0) / 25.0) for t, total in totals.items()}
        if any(ot < 0 or abs(totals[t] - (240 + 25 * ot)) > 1
               for t, ot in overtime.items()) or overtime[a] != overtime[b]:
            continue
        if any(len([r for r in rows if r["minutes"] > 0]) < 5 for rows in teams.values()):
            continue
        if any(r["opponent_id"] != (b if t == a else a)
               for t, rows in teams.items() for r in rows):
            continue
        duration = 48 + 5 * overtime[a]
        for team, opponent in ((a, b), (b, a)):
            complete.append({"team_id": team, "game_id": game_id,
                             "game_time": _time(teams[team][0]["game_time"]),
                             "points_allowed": sum(r["pts"] for r in teams[opponent]) * 48 / duration})
    return complete


def _opponent_proxy(team_id, complete):
    games = sorted((g for g in complete if g["team_id"] == team_id),
                   key=lambda g: (g["game_time"], g["game_id"]))
    league = fmean(g["points_allowed"] for g in complete) if complete else None
    weights = [0.90 ** age for age in range(len(games))]
    allowed = (sum(g["points_allowed"] * w for g, w in zip(reversed(games), weights))
               / sum(weights) if games else None)
    shrinkage = len(games) / (len(games) + 10.0)
    raw = 1 + 0.5 * shrinkage * (allowed / league - 1) if games and league else 1.0
    return {"method": "shrunk_complete_boxscore_points_allowed_proxy_per_48",
            "opponent_id": team_id, "complete_games": len(games),
            "complete_game_ids": [g["game_id"] for g in games],
            "points_allowed_per_48": allowed, "reference_points_allowed_per_48": league,
            "shrinkage": shrinkage, "unbounded_factor": raw,
            "factor": min(1.05, max(0.95, raw)), "bounds": [0.95, 1.05],
            "caution": "Not possession-adjusted defense; sensitive to schedule and pace."}


def _expected_points(fga, fg3a, fta, shooting):
    p2 = shooting["two_point"]["percentage"]
    p3 = shooting["three_point"]["percentage"]
    pft = shooting["free_throw"]["percentage"]
    return 2 * (fga - fg3a) * p2 + 3 * fg3a * p3 + fta * pft


def _shot_variance(fga, fg3a, fta, shooting):
    p2 = shooting["two_point"]["percentage"]
    p3 = shooting["three_point"]["percentage"]
    pft = shooting["free_throw"]["percentage"]
    return 4 * (fga - fg3a) * p2 * (1 - p2) + 9 * fg3a * p3 * (1 - p3) + fta * pft * (1 - pft)


def _project_numbers(prediction, minutes, rates, shooting, rows, minute_variance, factor):
    fga, fg3a, fta, tov = (minutes * rates[k] for k in ("fga", "fg3a", "fta", "tov"))
    points = _expected_points(fga, fg3a, fta, shooting) * factor
    binomial = _shot_variance(fga, fg3a, fta, shooting) * factor ** 2
    residuals = [r["pts"] - _expected_points(r["fga"], r["fg3a"], r["fta"], shooting)
                 for r in rows if r["minutes"] > 0]
    empirical = pvariance(residuals) if len(residuals) > 1 else 0.0
    historical_shot_variance = fmean(_shot_variance(r["fga"], r["fg3a"], r["fta"], shooting)
                                   for r in rows if r["minutes"] > 0)
    historical_minutes = fmean(r["minutes"] for r in rows if r["minutes"] > 0)
    residual_exposure_scale = minutes / historical_minutes
    excess = (max(0.0, empirical - historical_shot_variance)
              * len(residuals) / (len(residuals) + 8) * residual_exposure_scale ** 2)
    rate = points / minutes if minutes else 0.0
    minutes_component = rate ** 2 * minute_variance
    sd = sqrt(binomial + excess * factor ** 2 + minutes_component)
    prediction.update(expected_minutes=minutes, expected_fga=fga, expected_fta=fta,
                      expected_tov=tov, usage_proxy=fga + .44 * fta + tov,
                      points_mean=points, points_sd=sd,
                      points_lower80=max(0.0, points - 1.2815515655 * sd),
                      points_upper80=points + 1.2815515655 * sd)
    prediction["components"]["scoring"] = {
        "expected_two_point_attempts": fga - fg3a, "expected_three_point_attempts": fg3a,
        "expected_free_throw_attempts": fta, "opponent_multiplier": factor,
        "points_before_opponent": points / factor,
        "usage_proxy_definition": "FGA + 0.44*FTA + TOV; opportunity count, not USG%",
    }
    prediction["components"]["uncertainty"] = {
        "method": "uncalibrated_heuristic_binomial_plus_excess_residual_plus_minutes",
        "shot_variance": binomial, "empirical_residual_variance": empirical,
        "historical_shot_variance": historical_shot_variance,
        "shrunk_excess_residual_variance": excess * factor ** 2,
        "minutes_variance": minute_variance, "minutes_points_variance": minutes_component,
        "residual_exposure_scale": residual_exposure_scale,
        "residual_games": len(residuals), "normal_80pct_z": 1.2815515655,
    }


def forecast(snapshot, history):
    """Return every roster player with auditable projections or explicit abstention.

    No I/O, randomness, wall-clock reads, or input mutation. Input validation is
    the caller's boundary; this function also enforces evidence time/season
    filtering. Nonprojected uncertain/off-slate numeric fields are null; out
    projections are exactly zero. Review flags do not silently alter availability.
    """
    if snapshot.get("forecast_stage") == "final":
        from .injuries import validate_final_check
        if "injury_check" not in snapshot:
            raise ValueError("final forecast requires injury checkpoint evidence")
        validate_final_check(snapshot, snapshot["injury_check"])
    as_of = _time(snapshot["as_of"])
    season = snapshot["season"]
    previous_season = _previous_season(season)
    accepted, excluded = defaultdict(list), Counter()
    eligible = []
    for row in history:
        if (row["season_type"] != "Regular Season"
                or row["season"] not in (season, previous_season)
                or _time(row["game_time"]) >= as_of or _time(row["known_at"]) > as_of):
            excluded[row["player_id"]] += 1
            continue
        accepted[row["player_id"]].append(row)
        eligible.append(row)
    complete = _complete_team_games(eligible)
    games = {team: game for game in snapshot["games"] if game["season_type"] == "Regular Season"
             for team in (game["home_team_id"], game["away_team_id"])}
    adjustments = {(a["player_id"], a["game_id"]): a
                   for a in snapshot.get("role_adjustments", [])
                   if _time(a["observed_at"]) <= as_of}
    predictions, working = [], {}
    for player in sorted(snapshot["roster"]["players"], key=lambda p: (p["team_id"], p["player_id"])):
        pid, team = player["player_id"], player["team_id"]
        rows = _chronological(accepted[pid])
        previous = [r for r in rows if r["season"] == previous_season]
        current = [r for r in rows if r["season"] == season]
        played = [r for r in rows if r["status"] == "played" and r["minutes"] > 0]
        game = games.get(team)
        p = {"player_id": pid, "name": player["name"], "team_id": team,
             "game_id": game["game_id"] if game else None,
             "start_time": game["start_time"] if game else None,
             "status": "needs_review", "reason": "", "history_games": len(rows),
             "flags": [], "components": {"availability": {
                 k: deepcopy(v) for k, v in player.items() if k.startswith("status")},
                 "history": {"previous_season_games": len(previous),
                             "current_season_games": len(current), "played_games": len(played),
                             "dnp_games": sum(r["status"] == "dnp" for r in rows),
                             "excluded_rows": excluded[pid],
                             "game_ids": [r["game_id"] for r in rows]},
                 "manual_adjustment": None}}
        p.update({field: None for field in NUMERIC_FIELDS})
        predictions.append(p)
        if not game:
            p.update(status="no_game", reason="Team has no regular-season game on this slate.")
            continue
        if (_time(snapshot["roster"]["observed_at"]) > as_of
                or (player.get("status_observed_at")
                    and _time(player["status_observed_at"]) > as_of)):
            p["reason"] = "Roster or availability evidence was not known at the forecast cutoff."
            p["flags"].append("late_known_roster_or_status")
            continue
        if player["status"] == "out":
            p.update(status="out", reason="Sourced roster status is out.")
            p.update({field: 0.0 for field in NUMERIC_FIELDS})
            continue
        if player["status"] in ("questionable", "unknown"):
            p["reason"] = "Availability must be resolved before assigning minutes."
            p["flags"].append("availability_" + player["status"])
            continue
        if not played:
            p["reason"] = "No positive-minute current/previous regular-season evidence."
            p["flags"].append("cold_start")
            continue
        if not any(r["fga"] or r["fta"] for r in played):
            p["reason"] = "Positive minutes but no observed scoring opportunities; production remains unsupported."
            p["flags"].append("no_scoring_opportunity_evidence")
            continue
        p.update(status="projected", reason="Evidence-based baseline; review listed role and rotation flags.")
        if len(played) < 5:
            p["flags"].append("short_history")
        if rows[-1]["team_id"] != team or any(r["team_id"] != team for r in current):
            p["flags"].append("team_change")
        # Prefer an established current-season role over a stale prior-season
        # comparison; the most recent three games can then reveal a new role.
        baseline = current[-13:-3] if len(current) >= 6 else previous
        recent = current[-3:] if len(current) >= 6 else current[-5:]
        if baseline and recent:
            old_minutes = fmean(r["minutes"] for r in baseline)
            new_minutes = fmean(r["minutes"] for r in recent)
            minute_threshold = max(5, old_minutes * .20)
            if abs(new_minutes - old_minutes) >= minute_threshold:
                p["flags"].append("minutes_change")
            old_mins, new_mins = sum(r["minutes"] for r in baseline), sum(r["minutes"] for r in recent)
            old_rate = sum(r["fga"] + .44 * r["fta"] + r["tov"] for r in baseline) / old_mins if old_mins else 0
            new_rate = sum(r["fga"] + .44 * r["fta"] + r["tov"] for r in recent) / new_mins if new_mins else 0
            if ((old_rate and abs(new_rate / old_rate - 1) >= .20)
                    or (not old_rate and new_rate > 0)):
                p["flags"].append("opportunity_change")
            p["components"]["role_comparison"] = {
                "baseline": "earlier_current_season" if len(current) >= 6 else "previous_season",
                "baseline_game_ids": [r["game_id"] for r in baseline],
                "recent_game_ids": [r["game_id"] for r in recent],
                "baseline_minutes": old_minutes, "recent_minutes": new_minutes,
                "minutes_change_threshold": minute_threshold,
                "baseline_opportunities_per_minute": old_rate,
                "recent_opportunities_per_minute": new_rate,
                "opportunity_change_fraction_threshold": .20,
            }
        if any(r["status"] == "dnp" for r in current[-3:]):
            p["flags"].append("recent_dnp_role_review")
        minutes, rates, variance, opportunities = _opportunities(previous, current)
        shooting = _shooting(previous, current)
        adjustment = adjustments.get((pid, game["game_id"]))
        if adjustment:
            p["components"]["manual_adjustment"] = deepcopy(adjustment)
            minutes += adjustment["minutes_delta"]
            rates = {k: v * adjustment["opportunity_multiplier"] for k, v in rates.items()}
            p["flags"].append("manual_role_adjustment")
        minutes = min(48.0, max(0.0, minutes))
        opponent_id = game["away_team_id"] if team == game["home_team_id"] else game["home_team_id"]
        opponent = _opponent_proxy(opponent_id, complete)
        if not opponent["complete_games"]:
            p["flags"].append("opponent_proxy_unavailable")
        p["components"].update(opportunities=opportunities, shooting=shooting, opponent=opponent)
        working[pid] = (minutes, rates, shooting, rows, variance, opponent["factor"])
    for team in sorted(games):
        team_predictions = [p for p in predictions if p["team_id"] == team]
        total = sum(working[p["player_id"]][0] for p in team_predictions if p["status"] == "projected")
        scale = min(1.0, 240.0 / total) if total else 1.0
        deficit = max(0.0, 240.0 - total * scale)
        assigned_minutes = 0.0
        for p in team_predictions:
            p["components"]["rotation"] = {"team_minutes_before_scaling": total,
                "regulation_budget": 240.0, "scale": scale, "unallocated_minutes": deficit,
                "method": "Scale excess only; never redistribute missing minutes automatically."}
            if deficit > 1e-8:
                p["flags"].append("team_minutes_unresolved")
            if p["status"] != "projected":
                continue
            minutes, rates, shooting, rows, variance, factor = working[p["player_id"]]
            if scale < 1:
                p["flags"].append("team_minutes_scaled")
            # Reserving the remaining budget also prevents floating-point sums
            # such as 240.00000000000003 from exceeding the exact output cap.
            allocated = min(minutes * scale, max(0.0, 240.0 - assigned_minutes))
            assigned_minutes += allocated
            effective_scale = allocated / minutes if minutes else 1.0
            _project_numbers(p, allocated, rates, shooting, rows, variance * effective_scale ** 2, factor)
        projected_team = [p for p in team_predictions if p["status"] == "projected"]
        output_total = sum(p["expected_minutes"] for p in projected_team)
        if output_total > 240.0:
            # Python's compensated sum can differ from the running allocation
            # sum. Correct its residual by a negligible one-billionth-minute margin.
            last = projected_team[-1]
            minutes, rates, shooting, rows, variance, factor = working[last["player_id"]]
            allocated = max(0.0, last["expected_minutes"] - (output_total - 240.0) - 1e-9)
            effective_scale = allocated / minutes if minutes else 1.0
            _project_numbers(last, allocated, rates, shooting, rows, variance * effective_scale ** 2, factor)
    for p in predictions:
        p["flags"] = sorted(set(p["flags"]))
    reasoning_flags = {"team_change", "minutes_change", "opportunity_change", "recent_dnp_role_review",
                       "team_minutes_unresolved", "team_minutes_scaled", "manual_role_adjustment",
                       "opponent_proxy_unavailable"}
    review_flags = reasoning_flags | {"short_history"}
    queue = [{"player_id": p["player_id"], "team_id": p["team_id"], "game_id": p["game_id"],
              "status": p["status"], "reason": p["reason"], "flags": p["flags"].copy(),
              "tier": ("reasoning_review" if p["status"] == "projected"
                       and reasoning_flags.intersection(p["flags"]) else "data_verification"),
              "priority": "high" if p["status"] == "needs_review" or
                          {"team_change", "minutes_change", "opportunity_change", "team_minutes_scaled"}.intersection(p["flags"]) else "normal",
              "action": "Resolve availability and review sourced coaching/rotation evidence; any adjustment must be game-specific."}
             for p in predictions if p["status"] == "needs_review"
             or (p["status"] == "projected" and review_flags.intersection(p["flags"]))]
    counts = Counter(p["status"] for p in predictions)
    return {"model_version": MODEL_VERSION, "as_of": snapshot["as_of"], "season": season,
            "predictions": predictions, "review_queue": queue,
            "forecast_stage": snapshot.get("forecast_stage", "preliminary"),
            "coverage": {"roster_players": len(snapshot["roster"]["players"]),
                         "pool_rows": len(predictions), "by_status": dict(sorted(counts.items())),
                         "eligible_history_rows": len(eligible), "excluded_history_rows": sum(excluded.values()),
                         "complete_team_boxscores": len(complete),
                         "calibration": "Intervals are heuristic until prospective coverage is measured."}}
