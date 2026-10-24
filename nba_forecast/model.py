"""Auditable deterministic scoring baseline and versioned enhancement dispatch.

This is an evidence-only baseline, not a claim of betting edge. ``forecast``
expects the validated contract in docs/design.md but independently excludes
history that was played/known after the cutoff, non-regular seasons, and seasons
other than the snapshot season and its immediate predecessor.

Role minutes retain at most six prior appearance pseudo-games, decaying by 0.70
per current game; current role games decay by 0.75. Prior-season DNPs remain in
the audit without diluting healthy role minutes. Current-team observations govern
a traded player's role. Scoring opportunity rates use a stronger 20-game prior
with 0.90 decay. Verified season aggregates from the last three completed seasons
replace overlapping prior scoring evidence, with season-age weights 1/0.65/0.4225.
These totals are not synthetic game observations. Estimated aggregate minutes
are explicitly labeled; points and shot totals come from the source archive.
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
Supported starters and high-minute rotation roles are preserved when excess
bench estimates push a team above 240 minutes. The core is scaled only if it
alone exceeds the budget. Deficits stay explicit. Individual minutes
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
from .rotation import rotation_role
from .historical import season_prior
from .totals import game_totals
from .model_versions import uses_refinements

MODEL_VERSION = "regular-season-baseline-0.4.2"
NUMERIC_FIELDS = (
    "expected_minutes", "expected_fga", "expected_fta", "expected_tov",
    "expected_reb", "expected_ast", "expected_fg3m", "expected_stl", "expected_blk",
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


def _shooting(previous, current, historical=None):
    result = {}
    for kind, (default, strength, cap) in EFFICIENCY_DEFAULTS.items():
        prior_made = sum(_attempts(r, kind)[0] for r in previous)
        prior_attempts = sum(_attempts(r, kind)[1] for r in previous)
        if historical:
            t=historical['totals']
            prior_made,prior_attempts=(t['FGM']-t['FG3M'],t['FGA']-t['FG3A']) if kind=='two_point' else (t['FG3M'],t['FG3A']) if kind=='three_point' else (t['FTM'],t['FTA'])
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


def _opportunities(previous, current, team_id=None, historical=None, *, excluded_role_game_ids=None):
    prior_dnp=sum(r['status']=='dnp' for r in previous)
    previous=[r for r in previous if r['status']=='played' and r['minutes']>0]
    prior_weight = min(len(previous), 20) * 0.90 ** len(current)
    weighted = [(r, 0.90 ** age) for age, r in enumerate(reversed(current))]
    # Each previous row gets an equal fraction of the prior pseudo-game mass.
    if previous:
        weighted += [(r, prior_weight / len(previous)) for r in previous]
    mass = sum(w for _, w in weighted)
    totals = {field: sum(r[field] * w for r, w in weighted)
              for field in ("minutes", "fga", "fg3a", "fta", "tov")}
    if historical:
        t=historical['totals'];decay=.90**len(current)
        prior_weight=t['GP']*decay
        mass=prior_weight+sum(.90**age for age,_ in enumerate(reversed(current)))
        for field,key in (('minutes','MIN'),('fga','FGA'),('fg3a','FG3A'),('fta','FTA'),('tov','TOV')):
            totals[field]=t[key]*decay+sum(r[field]*(.90**age) for age,r in enumerate(reversed(current)))
    # Participation is assessed separately. Prior-year absences do not describe
    # a healthy player's rotation minutes. A new team supplies its own role;
    # older teams still inform per-minute production and shooting efficiency.
    role_previous=previous;role_current=current
    current_team=[r for r in current if r['team_id']==team_id] if team_id else []
    if current_team:
        role_previous=[r for r in previous if r['team_id']==team_id]
        role_current=current_team
    # Only normal-role learning excludes a verified early injury exit. All
    # observed attempts, makes, turnovers and exposure above remain intact.
    excluded=set(excluded_role_game_ids or ())
    role_previous=[r for r in role_previous if r['game_id'] not in excluded]
    role_current=[r for r in role_current if r['game_id'] not in excluded]
    role_prior_weight=min(len(role_previous),6)*.70**len(role_current)
    role_weighted=[(r,.75**age) for age,r in enumerate(reversed(role_current))]
    if role_previous:role_weighted += [(r,role_prior_weight/len(role_previous)) for r in role_previous]
    role_mass=sum(w for _,w in role_weighted)
    minutes=sum(r['minutes']*w for r,w in role_weighted)/role_mass if role_mass else 0.
    rates = {field: totals[field] / totals["minutes"] if totals["minutes"] else 0.0
             for field in ("fga", "fg3a", "fta", "tov")}
    # A 4-minute prior SD stabilizes small samples without creating production.
    observed_variance = (sum(w * (r["minutes"] - minutes) ** 2 for r, w in role_weighted)
                         / role_mass if role_mass else 0.0)
    minute_variance = (observed_variance * role_mass + 16.0 * 4) / (role_mass + 4)
    audit = {
        "prior_effective_games": prior_weight, "current_weight": mass - prior_weight,
        "prior_decay_per_current_game": 0.90, "current_decay_per_game": 0.90,
        "minutes_before_adjustment": minutes, "per_minute_rates": rates.copy(),
        "observed_minutes_variance": observed_variance,
        "prior_dnp_excluded":prior_dnp,"role_prior_effective_games":role_prior_weight,
        "role_current_team_game_ids":[r['game_id'] for r in current_team],
        "role_prior_team_ids":sorted({r['team_id'] for r in role_previous}),
        "role_team_id":team_id,"role_minutes_policy":"Prior appearances and current-team role; current-season DNPs remain role evidence.",
    }
    if excluded_role_game_ids is not None:
        audit.update(injury_shortened_role_game_ids=sorted(excluded),
            normal_role_supported=any(r['minutes']>0 for r in role_previous+role_current),
            role_minutes_policy='Verified injury-shortened games excluded from normal minutes only; actual production and exposure retained.')
    return minutes, rates, minute_variance, audit


def _multistat(previous, current):
    """Exposure-weighted rates, regularized with fixed modeling constants.

    Missing observations never enter the denominator. Prior exposure is capped
    at 600 minutes; current evidence decays slowly to resist isolated outliers.
    Defaults are regularizers, not asserted league averages or player evidence.
    """
    result = {}
    for stat, default in (("reb", .15), ("ast", .09), ("stl", .025), ("blk", .02)):
        prior = [r for r in previous if r.get(stat) is not None and r["minutes"] > 0]
        present = [r for r in current if r.get(stat) is not None and r["minutes"] > 0]
        prior_minutes = sum(r["minutes"] for r in prior)
        scale = min(1., 600 / prior_minutes) if prior_minutes else 1.
        weighted = [(r, scale) for r in prior] + [(r, .97 ** age) for age, r in enumerate(reversed(present))]
        exposure = sum(r["minutes"] * w for r, w in weighted)
        count = sum(r[stat] * w for r, w in weighted)
        result[stat] = {
            "rate": (count + 60 * default) / (exposure + 60) if exposure else None,
            "observed_games": len(weighted), "weighted_minutes": exposure,
            "weighted_count": count, "default_rate": default, "pseudo_minutes": 60,
            "prior_minutes_cap": 600, "current_decay_per_game": .97,
            "game_ids": [r["game_id"] for r, _ in weighted],
            "uncertainty_method": "uncalibrated_heuristic_poisson_plus_minutes",
        }
    return result


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
    quality=prediction['components'].get('shot_quality')
    if quality and 'baseline_percentages' in quality:
        baseline={kind:{'percentage':value} for kind,value in quality['baseline_percentages'].items()}
        original_points=_expected_points(fga,fg3a,fta,baseline)*factor
        quality.update(points_without_location_adjustment=original_points,
            points_delta=points-original_points)
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
    prediction["expected_fg3m"] = fg3a * shooting["three_point"]["percentage"]
    for stat, evidence in prediction["components"]["multistat"].items():
        rate = evidence["rate"]
        mean = minutes * rate if rate is not None else None
        prediction["expected_" + stat] = mean
        evidence["heuristic_sd"] = (sqrt(mean + rate ** 2 * minute_variance)
                                     if mean is not None and minutes > 0 else 0.0 if mean == 0 else None)
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
    if snapshot.get('model_enhancements'):
        from .candidate import forecast_candidate
        result = forecast_candidate(snapshot,history)
    else:
        result = _baseline_forecast(snapshot,history)
    from .boxscore_model import extend_boxscore
    from .shot_profiles import apply_shot_profiles
    result=apply_shot_profiles(extend_boxscore(result,snapshot,history),snapshot)
    if uses_refinements(snapshot):
        from .margins import build_margin_forecasts
        result['margin_forecasts']=build_margin_forecasts(snapshot,result)
    return result


def _baseline_forecast(snapshot, history):
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
            if player.get('status_basis')=='roster_membership_review':
                p['reason']='Roster membership discrepancy is quarantined for source review; no numeric forecast.'
                p['flags'].append('roster_membership_review')
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
        if player.get("status_basis") in ("not_listed_complete_report","not_listed_verified_empty_report"):
            p["flags"].append("availability_not_listed")
            p["reason"] = ("Conditional on participation: player was not listed in the complete injury report; "
                           "participation is not confirmed. Review listed role and rotation flags.")
            if player.get('status_basis')=='not_listed_verified_empty_report':
                p['reason']='Conditional on participation: a verified game-specific team report lists no injuries. Participation and starting lineup are not confirmed.'
        if len(played) < 5:
            p["flags"].append("short_history")
        if any(r["team_id"] != team for r in previous+current):
            p["flags"].append("team_change")
        # Prefer an established current-season role over a stale prior-season
        # comparison; the most recent three games can then reveal a new role.
        excluded_role=None
        if uses_refinements(snapshot):
            from .role_context import role_excluded_game_ids
            excluded_role=role_excluded_game_ids(snapshot,pid,previous+current)
        normal_current=[r for r in current if r['game_id'] not in (excluded_role or ())]
        normal_previous=[r for r in previous if r['game_id'] not in (excluded_role or ())]
        baseline = normal_current[-13:-3] if len(normal_current) >= 6 else [r for r in normal_previous if r['status']=='played' and r['minutes']>0]
        recent = normal_current[-3:] if len(normal_current) >= 6 else normal_current[-5:]
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
                "baseline": "earlier_current_season" if len(normal_current) >= 6 else "previous_season",
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
        historical=season_prior(snapshot,pid)
        minutes, rates, variance, opportunities = _opportunities(previous,current,team,historical,excluded_role_game_ids=excluded_role)
        if excluded_role:
            p['flags'].append('injury_shortened_role_excluded')
            p['components']['role_context']={'excluded_game_ids':sorted(excluded_role),
                'events':[deepcopy(e) for e in snapshot['role_context']['events'] if e['player_id']==pid and e['game_id'] in excluded_role],
                'policy':'Normal-role minutes exclude verified injury exits; observed production remains in the model.'}
            if not opportunities['normal_role_supported'] and not adjustments.get((pid,game['game_id'])):
                p.update(status='needs_review',reason='Only injury-shortened evidence is available for this team role; normal minutes need supported observations or a sourced restriction.')
                p['flags'].append('normal_role_unobserved')
                p['components']['opportunities']=opportunities
                continue
        if (any(r['team_id']==team for r in current) and
            not any(r['team_id']==team and r['minutes']>0 for r in previous+current) and
            not adjustments.get((pid,game['game_id']))):
            p.update(status='needs_review',reason='No positive-minute appearance in the new team role; reviewed rotation evidence is required.')
            p['flags'].append('new_team_role_unobserved')
            p['components']['opportunities']=opportunities
            continue
        shooting = _shooting(previous, current,historical)
        if uses_refinements(snapshot):
            from .shot_quality import adjust_shooting
            before={kind:detail['percentage'] for kind,detail in shooting.items()}
            shooting,quality=adjust_shooting(shooting,snapshot,pid)
            quality['baseline_percentages']=before
            quality['adjusted_percentages']={kind:detail['percentage'] for kind,detail in shooting.items()}
            p['components']['shot_quality']=quality
            if quality.get('scoring_changed'):p['flags'].append('location_shooting_adjustment')
        if historical:p['components']['historical_prior']=historical
        p["components"]["multistat"] = _multistat(previous, current)
        recent_role = (current or previous)[-5:]
        normal_recent_role=[r for r in recent_role if r['game_id'] not in (excluded_role or ())]
        starts = [r["starter"] for r in recent_role if "starter" in r]
        p["components"]["rotation_role"] = {
            "label": ("frequent_starter" if sum(starts) >= 3 else "mixed_or_bench" if len(starts) >= 3 else "unknown"),
            "method": "Descriptive last-five-game evidence; no inferred coaching decisions.",
            "game_ids": [r["game_id"] for r in recent_role],
            "starter_observations": len(starts), "starts": sum(starts),
            "recent_mean_minutes": fmean(r["minutes"] for r in normal_recent_role) if normal_recent_role else None,
            "recent_dnp_count": sum(r["status"] == "dnp" for r in recent_role),
        }
        p['components']['current_team_rotation']=rotation_role(player,rows,excluded_game_ids=excluded_role)
        adjustment = adjustments.get((pid, game["game_id"]))
        if adjustment:
            p["components"]["manual_adjustment"] = deepcopy(adjustment)
            minutes += adjustment["minutes_delta"]
            if "minutes_cap" in adjustment:
                minutes = min(minutes, adjustment["minutes_cap"])
                p["flags"].append("minutes_restriction")
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
        core={p['player_id'] for p in team_predictions if p['status']=='projected' and
              p['components'].get('current_team_rotation',{}).get('observations',0)>0 and
              (p['components']['current_team_rotation'].get('last_game_started') is True or
               (working[p['player_id']][0]>=28 and (p['components']['current_team_rotation'].get('recent_minutes') or 0)>=24))}
        core_total=sum(working[pid][0] for pid in core)
        core_scale=min(1.,240./core_total) if core_total else 1.
        bench_total=total-core_total
        bench_scale=min(1.,max(0.,240.-core_total*core_scale)/bench_total) if bench_total else 1.
        deficit=max(0.,240.-core_total*core_scale-bench_total*bench_scale)
        assigned_minutes = 0.0
        for p in team_predictions:
            scale=core_scale if p['player_id'] in core else bench_scale
            p["components"]["rotation"] = {"team_minutes_before_scaling": total,
                "regulation_budget": 240.0, "scale": scale, "unallocated_minutes": deficit,
                "protected_rotation_player_ids":sorted(core),"protected_rotation_minutes":core_total,
                "method": "Preserve supported starters and high-minute rotation roles; scale excess bench estimates first. Missing minutes are not invented."}
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
            "game_totals": [game_totals(g,predictions,lineups=snapshot.get('preflight',{}).get('lineups')) for g in snapshot['games'] if g['season_type']=='Regular Season'],
            "forecast_stage": snapshot.get("forecast_stage", "preliminary"),
            "coverage": {"roster_players": len(snapshot["roster"]["players"]),
                         "pool_rows": len(predictions), "by_status": dict(sorted(counts.items())),
                         "eligible_history_rows": len(eligible), "excluded_history_rows": sum(excluded.values()),
                         "complete_team_boxscores": len(complete),
                         "calibration": "Intervals are heuristic until prospective coverage is measured."}}
