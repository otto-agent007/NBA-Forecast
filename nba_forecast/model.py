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
by a bounded opponent points-allowed proxy and a symmetric home-court factor
(``_home_court``: flagged complete games shrunk toward a 2025-26 prior). Manual game-specific adjustments
alter minutes and attempt/turnover rates, with full source/reviewer audit.
Supported starters and high-minute rotation roles are preserved when excess
bench estimates push a team above 240 minutes. The core is scaled only if it
alone exceeds the budget. Deficits stay explicit. Individual minutes
are capped at 48: this v0.1 does not forecast overtime.

Intervals are uncalibrated heuristics: binomial shot variance plus shrunk excess
historical shot residual variance and minutes uncertainty, with the empirical
half-width in ``intervals.HEURISTIC_Z80`` until a calibration artifact fitted on
pooled prospective residuals replaces it. They are evaluated prospectively by
the ledger, not presented as established 80% coverage.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from math import sqrt
from statistics import fmean, pvariance
from .rotation import rotation_role
from .historical import season_prior
from .totals import RESIDUAL_MIN_PROJECTED, game_totals, short_bench
from .model_versions import uses_refinements
from .intervals import INTERVAL_FAMILY, HEURISTIC_Z80, HEURISTIC_Z80_BASIS, LEGACY_METHOD

MODEL_VERSION = "regular-season-baseline-0.4.8"
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
# Home court: home teams outscored visitors per 48 minutes by 1.45% over the
# first 30% of 2025-26 game dates (1.55% over the season, home win rate 55.5%).
# The prior is shrunk toward the observed ratio of flagged complete games before
# the cutoff. It is split symmetrically so the combined scoring level is unchanged.
HOME_COURT_PRIOR = 0.0145
HOME_COURT_PSEUDO_GAMES = 200
HOME_COURT_BOUNDS = (-0.02, 0.04)
# Participation: 2025-26 next-game play rates by role (average minutes when
# playing), toward which a player's own rate over the team's last ten games is
# shrunk by three pseudo-games. A reported status scales the role rate.
PARTICIPATION_PRIORS = ((10., .18), (20., .70), (28., .79), (float("inf"), .82))
PARTICIPATION_PSEUDO_GAMES = 3
PARTICIPATION_WINDOW = 10
# The window stays inside the current season: last season's final games say
# little about who dresses now. Even one listing counts: with three pseudo-games
# of prior, two DNPs take a newcomer from .79 to .47, which is what they mean.
PARTICIPATION_MIN_TEAM_GAMES = 1
# Factors on the role rate for a reported status, fitted on the 12:30 PM ET
# game-day reports of each season and checked on the other (2024-25 / 2025-26
# fits: probable 1.11 / 1.13, questionable .63 / .65, doubtful .040 / .048;
# raw play rates 89%, 49%, 3%). Probable plays like an untagged player. The
# ESPN-only labels keep .5.
STATUS_PARTICIPATION = {"probable": 1., "questionable": .64, "doubtful": .04, "day-to-day": .5, "game time decision": .5}
# When expected minutes fall short of 240, conditional minutes rise by at most
# these fractions (protected rotation, bench) and never past 48. Chosen on the
# last 250 games of 2025-26: larger caps over-projected starters' minutes, no
# fill left team totals short.
UPLIFT_CAP = (.10, .25)
# NBA rule: at most 13 players are active for a game, so a team's participation
# probabilities cannot sum past 13. Teams used at least ten players in three
# quarters of 2025-26 games (mean 10.8), so with ten or more projected players
# the probabilities are raised to sum to at least ten: the role priors were
# measured on everyone a box score lists, and the live roster has already lost
# its out players.
ACTIVE_LIST_MAX = 13
ACTIVE_LIST_MIN_EXPECTED = 10
# A team uses about the same number of possessions every night: in 2025-26
# FGA + 0.44*FTA + TOV per 240 team minutes averaged 112.6 with a spread of
# 2.5 between teams and 6.1 from game to game. Player rates learned in other
# roles (last season's primary option, a new signing's old team) do not add
# up to that on their own, so each team's expected usage is scaled to its own
# recency-weighted level, shrunk to the league, within these bounds. Minutes
# left to replacements are assumed to use possessions at the bench rate.
LEAGUE_USAGE_PER_240 = 112.6
USAGE_PRIOR_PSEUDO_GAMES = 10
USAGE_BOUNDS = (.85, 1.10)
REPLACEMENT_USAGE_PER_MINUTE = .38
# A reported-out player's absence is recorded for the margin's team rating
# (margins.absence_adjustment): his minutes per game for the team this season,
# and whether he played one of its last ten games (one who missed all ten is
# already absent from the team's recent results). Numbers stay zero either way.
ABSENCE_RECENT_TEAM_GAMES = 10


def _absence(current, team, team_games):
    """What a reported-out player's absence takes from his team, from its season so far."""
    played = [r for r in current if r["team_id"] == team and r["status"] == "played" and r["minutes"] > 0]
    recent = {gid for _, gid in team_games[-ABSENCE_RECENT_TEAM_GAMES:]}
    return {"team_games_played": len(played),
            "minutes_per_game": fmean(r["minutes"] for r in played) if played else None,
            "played_recent_team_game": any(r["game_id"] in recent for r in played),
            "recent_team_games": len(recent)}


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
        t=historical['totals']
        decay=.90**len(current)
        prior_weight=t['GP']*decay
        mass=prior_weight+sum(.90**age for age,_ in enumerate(reversed(current)))
        for field,key in (('minutes','MIN'),('fga','FGA'),('fg3a','FG3A'),('fta','FTA'),('tov','TOV')):
            totals[field]=t[key]*decay+sum(r[field]*(.90**age) for age,r in enumerate(reversed(current)))
    # Participation is assessed separately. Prior-year absences do not describe
    # a healthy player's rotation minutes. A new team supplies its own role;
    # older teams still inform per-minute production and shooting efficiency.
    # Role minutes are minutes when playing: participation is modeled separately,
    # so current-season DNPs no longer dilute them.
    role_previous=previous
    role_current=[r for r in current if r['status']=='played' and r['minutes']>0]
    current_team=[r for r in role_current if r['team_id']==team_id] if team_id else []
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
    if role_previous:
        role_weighted += [(r,role_prior_weight/len(role_previous)) for r in role_previous]
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
        "role_team_id":team_id,"role_minutes_policy":"Prior appearances and current-team role, minutes when playing; participation is modeled separately.",
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
            flags = {r.get("home") for r in teams[team]}
            complete.append({"team_id": team, "game_id": game_id,
                             "game_time": _time(teams[team][0]["game_time"]),
                             "points_allowed": sum(r["pts"] for r in teams[opponent]) * 48 / duration,
                             "usage_per_48": sum(r["fga"] + .44 * r["fta"] + r["tov"] for r in teams[team]) * 48 / duration,
                             "home": flags.pop() if len(flags) == 1 and flags != {None} else None})
    return complete


def _home_court(complete):
    """Symmetric home/away scoring factors from flagged complete games, shrunk to the prior."""
    sides = defaultdict(dict)
    for g in complete:
        if g.get("home") is not None:
            sides[g["game_id"]][g["home"]] = g
    pairs = [(d[True], d[False]) for d in sides.values() if True in d and False in d]
    n = len(pairs)
    # A team's scoring is the opposing entry's points allowed.
    observed = (fmean(a["points_allowed"] for h, a in pairs) / fmean(h["points_allowed"] for h, a in pairs) - 1) if n else None
    effect = ((observed or 0.0) * n + HOME_COURT_PRIOR * HOME_COURT_PSEUDO_GAMES) / (n + HOME_COURT_PSEUDO_GAMES)
    effect = min(HOME_COURT_BOUNDS[1], max(HOME_COURT_BOUNDS[0], effect))
    return {"method": "shrunk_home_away_scoring_ratio_per_48", "flagged_games": n, "observed_effect": observed,
            "prior_effect": HOME_COURT_PRIOR, "pseudo_games": HOME_COURT_PSEUDO_GAMES, "effect": effect,
            "home_factor": sqrt(1 + effect), "away_factor": 1 / sqrt(1 + effect), "bounds": list(HOME_COURT_BOUNDS),
            "basis": "pre-cutoff complete box scores with a home flag; the prior is 2025-26 warm-up evidence"}


def _team_usage_prior(team_id, complete):
    """The team's possessions used per 240 minutes: recency-weighted, shrunk to the league."""
    games = sorted((g for g in complete if g["team_id"] == team_id), key=lambda g: (g["game_time"], g["game_id"]))
    # usage_per_48 is a team-game total normalized to 48 game minutes: per 240 player minutes.
    league = fmean(g["usage_per_48"] for g in complete) if complete else LEAGUE_USAGE_PER_240
    weights = [0.90 ** age for age in range(len(games))]
    observed = (sum(g["usage_per_48"] * w for g, w in zip(reversed(games), weights)) / sum(weights)) if games else None
    shrinkage = len(games) / (len(games) + USAGE_PRIOR_PSEUDO_GAMES)
    usage = league + shrinkage * (observed - league) if games else league
    return {"method": "shrunk_recency_weighted_team_usage_per_240", "definition": "FGA + 0.44*FTA + TOV per 240 team minutes",
            "team_games": len(games), "observed_usage_per_240": observed, "league_usage_per_240": league,
            "pseudo_games": USAGE_PRIOR_PSEUDO_GAMES, "recency_decay_per_game": 0.90, "usage_per_240": usage}


def _usage_budget(projected_team, prior, reproject, locked, *, pace=1.0, stage="baseline", short=False):
    """Scale the open players' attempt rates so the team's expected usage meets its prior.

    ``reproject(p, scale)`` must recompute player ``p`` from its unscaled rates
    times ``scale``. Locked players keep their numbers and count as fixed usage.
    Raising needs a plausible rotation: eight projected players, or a short
    bench (``totals.short_bench``); lowering applies to any team.
    """
    if not projected_team:
        return None
    expected_minutes = sum(p["participation_probability"] * p["expected_minutes"] for p in projected_team)
    residual = max(0., 240. - expected_minutes)
    target = (prior["usage_per_240"] - residual * REPLACEMENT_USAGE_PER_MINUTE) * pace
    usage = {p["player_id"]: p["participation_probability"] * p["usage_proxy"] for p in projected_team}
    before = sum(usage.values())
    open_usage = sum(v for pid, v in usage.items() if pid not in locked)
    raw = (target - (before - open_usage)) / open_usage if open_usage > 0 else 1.
    factor = min(USAGE_BOUNDS[1], max(USAGE_BOUNDS[0], raw))
    if len(projected_team) < RESIDUAL_MIN_PROJECTED and not short:
        factor = min(1., factor)
    audit = {"method": "team_usage_budget", "stage": stage, "usage_prior": prior, "pace_factor": pace,
             "expected_minutes": expected_minutes, "residual_minutes": residual,
             "replacement_usage_per_minute": REPLACEMENT_USAGE_PER_MINUTE, "target": target, "before": before,
             "raw_factor": raw, "factor": factor, "bounds": list(USAGE_BOUNDS), "locked_player_ids": sorted(locked),
             "minimum_projected_to_raise": RESIDUAL_MIN_PROJECTED, "short_bench": short}
    if abs(factor - 1.) > 1e-9:
        for p in projected_team:
            if p["player_id"] in locked:
                continue
            reproject(p, factor)
            p["flags"] = [f for f in p["flags"] if f not in ("team_usage_scaled", "team_usage_raised")]
            p["flags"].append("team_usage_scaled" if factor < 1 else "team_usage_raised")
    audit["after"] = sum(p["participation_probability"] * p["usage_proxy"] for p in projected_team)
    for p in projected_team:
        p["components"]["team_usage"] = deepcopy(audit)
    return audit


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


def _participation(player, rows, team_games, role_minutes, unavailable=frozenset()):
    """Probability the player appears with positive minutes, from the team's last ten games.

    Team games before the player's first appearance with the team do not count
    against him; absence from a box score does. A player in none of the team's
    box scores is absent from every game since his last one for another team. Games his official injury report
    listed him out of (``unavailable``) do not count either way: the current
    report already says whether he is out, so the rate is his chance of playing
    when available, not a second count of the same injury. A reported
    questionable-type status scales the role rate. The result weights team
    totals and the 240-minute budget; player projections stay conditional on
    playing.
    """
    team = player["team_id"]
    appearances = {r["game_id"]: r for r in rows if r["team_id"] == team}
    if len(team_games) < PARTICIPATION_MIN_TEAM_GAMES:
        team_games = []
    first = next((i for i, (_, gid) in enumerate(team_games) if gid in appearances), None)
    if first is None:
        # Not in any of the team's box scores, not even as inactive (a G League assignee):
        # the team's games since his last game for another team count as absences.
        left = max((_time(r["game_time"]) for r in rows if r["team_id"] != team), default=None)
        first = next((i for i, (t, _) in enumerate(team_games) if left is None or t > left), None)
    since = [gid for _, gid in team_games[first:]] if first is not None else []
    window = [gid for gid in since if gid not in unavailable][-PARTICIPATION_WINDOW:]
    # Reported-out games inside the span the window reaches back over.
    excluded = [gid for gid in (since[since.index(window[0]):] if window else since) if gid in unavailable]
    played = sum(1 for gid in window if gid in appearances and appearances[gid]["status"] == "played" and appearances[gid]["minutes"] > 0)
    prior = next(rate for limit, rate in PARTICIPATION_PRIORS if role_minutes < limit)
    role_rate = (played + PARTICIPATION_PSEUDO_GAMES * prior) / (len(window) + PARTICIPATION_PSEUDO_GAMES)
    label = str(player.get("reported_status") or "").strip().lower()
    factor = STATUS_PARTICIPATION.get(label, .5) if player["status"] == "questionable" else 1.0
    return {"probability": min(1.0, max(0.0, role_rate * factor)), "role_rate": role_rate, "status_factor": factor,
            "reported_status": player.get("reported_status"), "played": played, "games": len(window),
            "window_game_ids": window, "reported_out_game_ids": excluded,
            "prior": prior, "pseudo_games": PARTICIPATION_PSEUDO_GAMES,
            "role_minutes": role_minutes, "method": "shrunk_recent_participation_rate",
            "basis": "played with positive minutes in the team's last ten current-season games since joining "
                     "(role prior alone before three team games); 2025-26 role priors; reported status scales the rate"}


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


def _project_numbers(prediction, minutes, rates, shooting, rows, minute_variance, factor, home_factor=1.0):
    """``factor`` is the opponent adjustment; ``home_factor`` the side's home-court factor."""
    fga, fg3a, fta, tov = (minutes * rates[k] for k in ("fga", "fg3a", "fta", "tov"))
    factor = factor * home_factor
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
                      points_lower80=max(0.0, points - HEURISTIC_Z80 * sd),
                      points_upper80=points + HEURISTIC_Z80 * sd)
    prediction["expected_fg3m"] = fg3a * shooting["three_point"]["percentage"]
    for stat, evidence in prediction["components"]["multistat"].items():
        rate = evidence["rate"]
        mean = minutes * rate if rate is not None else None
        prediction["expected_" + stat] = mean
        evidence["heuristic_sd"] = (sqrt(mean + rate ** 2 * minute_variance)
                                     if mean is not None and minutes > 0 else 0.0 if mean == 0 else None)
    prediction["components"]["scoring"] = {
        "expected_two_point_attempts": fga - fg3a, "expected_three_point_attempts": fg3a,
        "expected_free_throw_attempts": fta, "opponent_multiplier": factor / home_factor,
        "home_court_multiplier": home_factor, "points_before_opponent": points / factor,
        "usage_proxy_definition": "FGA + 0.44*FTA + TOV; opportunity count, not USG%",
    }
    prediction["components"]["uncertainty"] = {
        "method": LEGACY_METHOD, "interval_family": INTERVAL_FAMILY,
        "shot_variance": binomial, "empirical_residual_variance": empirical,
        "historical_shot_variance": historical_shot_variance,
        "shrunk_excess_residual_variance": excess * factor ** 2,
        "minutes_variance": minute_variance, "minutes_points_variance": minutes_component,
        "residual_exposure_scale": residual_exposure_scale,
        "residual_games": len(residuals), "heuristic_z80": HEURISTIC_Z80,
        "heuristic_z80_basis": HEURISTIC_Z80_BASIS,
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
        from .scoreline import reconcile
        result['margin_forecasts']=build_margin_forecasts(snapshot,result)
        # Team totals then agree with the published margin (scoreline.py).
        lineups=snapshot.get('preflight',{}).get('lineups')
        games={g['game_id']:g for g in snapshot['games']}
        for margin in result['margin_forecasts']:
            if margin['status']=='estimated':
                reconcile(result['predictions'],games[margin['game_id']],margin['mean_margin'],lineups=lineups)
        result['game_totals']=[game_totals(g,result['predictions'],lineups=lineups) for g in snapshot['games'] if g['season_type']=='Regular Season']
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
    home_court = _home_court(complete)
    team_games = defaultdict(set)
    for row in eligible:
        if row["season"] == season:
            team_games[row["team_id"]].add((_time(row["game_time"]), row["game_id"]))
    team_games = {team: sorted(items) for team, items in team_games.items()}
    games = {team: game for game in snapshot["games"] if game["season_type"] == "Regular Season"
             for team in (game["home_team_id"], game["away_team_id"])}
    usage_prior = {team: _team_usage_prior(team, complete) for team in games}
    adjustments = {(a["player_id"], a["game_id"]): a
                   for a in snapshot.get("role_adjustments", [])
                   if _time(a["observed_at"]) <= as_of}
    predictions, working = [], {}
    for player in sorted(snapshot["roster"]["players"], key=lambda p: (p["team_id"], p["player_id"])):
        p, entry = _project_player(player, snapshot, as_of, season, previous_season, accepted, excluded, games,
                                   team_games, complete, home_court, adjustments)
        predictions.append(p)
        if entry is not None:
            working[p["player_id"]] = entry
    for team in sorted(games):
        _allocate_team([p for p in predictions if p["team_id"] == team], working, usage_prior[team])
    for p in predictions:
        p["flags"] = sorted(set(p["flags"]))
    queue = _review_queue(predictions)
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


def _project_player(player, snapshot, as_of, season, previous_season, accepted, excluded, games, team_games, complete,
                    home_court, adjustments):
    """One roster player's prediction row, and the working inputs the team allocation needs when he is projected.

    Returns ``(prediction, None)`` for a player who gets no numbers (no game, late
    evidence, out, unresolved availability, no evidence) and ``(prediction,
    (minutes, rates, shooting, rows, variance, opponent factor, home factor,
    participation probability))`` for a projected one; the team pass finishes
    his minutes and numbers.
    """
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
         "participation_probability": None,
         "flags": [], "components": {"availability": {
             k: deepcopy(v) for k, v in player.items() if k.startswith("status")},
             "history": {"previous_season_games": len(previous),
                         "current_season_games": len(current), "played_games": len(played),
                         "dnp_games": sum(r["status"] == "dnp" for r in rows),
                         "excluded_rows": excluded[pid],
                         "game_ids": [r["game_id"] for r in rows]},
             "manual_adjustment": None}}
    p.update({field: None for field in NUMERIC_FIELDS})
    if not game:
        p.update(status="no_game", reason="Team has no regular-season game on this slate.")
        return p, None
    if (_time(snapshot["roster"]["observed_at"]) > as_of
            or (player.get("status_observed_at")
                and _time(player["status_observed_at"]) > as_of)):
        p["reason"] = "Roster or availability evidence was not known at the forecast cutoff."
        p["flags"].append("late_known_roster_or_status")
        return p, None
    if player["status"] == "out":
        p.update(status="out", reason="Sourced roster status is out.", participation_probability=0.0)
        p.update({field: 0.0 for field in NUMERIC_FIELDS})
        p["components"]["absence"] = _absence(current, team, team_games.get(team, []))
        return p, None
    if player["status"] == "unknown" or player.get("status_basis") == "roster_membership_review":
        p["reason"] = "Availability must be resolved before assigning minutes."
        p["flags"].append("availability_" + player["status"])
        if player.get('status_basis')=='roster_membership_review':
            p['reason']='Roster membership discrepancy is quarantined for source review; no numeric forecast.'
            p['flags'].append('roster_membership_review')
        return p, None
    if player["status"] == "questionable":
        # Projected conditional on playing, with a reduced participation probability.
        p["flags"].append("availability_questionable")
    if not played:
        p["reason"] = "No positive-minute current/previous regular-season evidence."
        p["flags"].append("cold_start")
        return p, None
    if not any(r["fga"] or r["fta"] for r in played):
        p["reason"] = "Positive minutes but no observed scoring opportunities; production remains unsupported."
        p["flags"].append("no_scoring_opportunity_evidence")
        return p, None
    p.update(status="projected", reason="Evidence-based baseline; review listed role and rotation flags.")
    if player["status"] == "questionable":
        p["reason"] = ("Conditional on playing: the official status is questionable, so the participation "
                       "probability is reduced. Review listed role and rotation flags.")
    elif player.get("status_basis") in ("not_listed_complete_report","not_listed_verified_empty_report"):
        p["flags"].append("availability_not_listed")
        p["reason"] = ("Conditional on participation: player was not listed in the complete injury report; "
                       "participation is not confirmed. Review listed role and rotation flags.")
        if player.get('status_basis')=='not_listed_verified_empty_report':
            p['reason']='Conditional on participation: a verified game-specific team report lists no injuries. Participation and starting lineup are not confirmed.'
    elif player.get("status_basis")=="espn_injury_list":
        p["flags"].append("availability_official_report_pending")
        p["reason"]=("Conditional on participation: the official injury report does not cover this team yet and "
                     "ESPN's injury list does not list this player. Review listed role and rotation flags.")
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
            return p, None
    if (any(r['team_id']==team for r in current) and
        not any(r['team_id']==team and r['minutes']>0 for r in previous+current) and
        not adjustments.get((pid,game['game_id']))):
        # Listed by the new team but yet to play for it: projected conditionally on
        # playing, from the earlier role, with those listings lowering the probability.
        p['reason']=('Conditional on playing: listed by the new team without a positive-minute appearance yet, '
                     'so the role comes from earlier evidence and the participation probability from the listings.')
        p['flags'].append('new_team_role_unobserved')
    participation = _participation(player, rows, team_games.get(team, []), minutes,
                                   frozenset((snapshot.get("reported_unavailable") or {}).get(pid, ())))
    p["participation_probability"] = participation["probability"]
    p["components"]["participation"] = participation
    if participation["probability"] < .5:
        p["flags"].append("participation_uncertain")
    shooting = _shooting(previous, current,historical)
    if uses_refinements(snapshot):
        from .shot_quality import adjust_shooting
        before={kind:detail['percentage'] for kind,detail in shooting.items()}
        shooting,quality=adjust_shooting(shooting,snapshot,pid)
        quality['baseline_percentages']=before
        quality['adjusted_percentages']={kind:detail['percentage'] for kind,detail in shooting.items()}
        p['components']['shot_quality']=quality
        if quality.get('scoring_changed'):
            p['flags'].append('location_shooting_adjustment')
    if historical:
        p['components']['historical_prior']=historical
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
    side = "home" if team == game["home_team_id"] else "away"
    p["components"].update(opportunities=opportunities, shooting=shooting, opponent=opponent,
                           home_court=dict(home_court, side=side, factor=home_court[side + "_factor"]))
    return p, (minutes, rates, shooting, rows, variance, opponent["factor"], home_court[side + "_factor"], participation["probability"])


def _allocate_team(team_predictions, working, usage_prior):
    """Finish one team: participation probabilities into the active-list range, the 240-minute budget, the usage budget.

    Mutates the predictions and ``working`` in place; every projected player
    receives his final minutes and numbers here.
    """
    projected_team = [p for p in team_predictions if p["status"] == "projected"]
    # At most 13 players dress, and a full team uses at least ten: scale the
    # team's probabilities into that range. Raising never passes 1 and never
    # touches a player whose reported status already lowered his probability.
    # A short bench (``totals.short_bench``: fewer than eight projected, every
    # other roster player explicitly out) has to play everyone available.
    short = short_bench(team_predictions)
    active_sum = sum(working[p["player_id"]][7] for p in projected_team)
    goal = (ACTIVE_LIST_MAX if active_sum > ACTIVE_LIST_MAX else
            ACTIVE_LIST_MIN_EXPECTED if active_sum < ACTIVE_LIST_MIN_EXPECTED and len(projected_team) >= ACTIVE_LIST_MIN_EXPECTED else
            len(projected_team) if short and active_sum < len(projected_team) else None)
    if goal is not None and active_sum:
        for _ in range(3):
            current_sum = sum(working[p["player_id"]][7] for p in projected_team)
            movable = [p for p in projected_team if (goal < current_sum) or
                       (working[p["player_id"]][7] < 1 - 1e-9 and p["components"]["participation"]["status_factor"] >= 1)]
            movable_sum = sum(working[p["player_id"]][7] for p in movable)
            if abs(current_sum - goal) < 1e-9 or not movable_sum:
                break
            ratio = 1 + (goal - current_sum) / movable_sum
            for p in movable:
                items = list(working[p["player_id"]])
                items[7] = min(1.0, items[7] * ratio)
                working[p["player_id"]] = tuple(items)
        for p in projected_team:
            p["participation_probability"] = working[p["player_id"]][7]
            p["components"]["participation"].update(probability=working[p["player_id"]][7], team_probability_sum_before=active_sum,
                                                      team_probability_goal=goal, team_probability_sum_after=sum(working[x["player_id"]][7] for x in projected_team))
    # The 240-minute budget constrains expected minutes: participation
    # probability times the minutes a player gets when he plays.
    expected = {p["player_id"]: working[p["player_id"]][7] * working[p["player_id"]][0] for p in projected_team}
    total = sum(expected.values())
    core={p['player_id'] for p in projected_team if
          p['components'].get('current_team_rotation',{}).get('observations',0)>0 and
          (p['components']['current_team_rotation'].get('last_game_started') is True or
           (working[p['player_id']][0]>=28 and (p['components']['current_team_rotation'].get('recent_minutes') or 0)>=24))}
    core_total=sum(expected[pid] for pid in core)
    core_scale=min(1.,240./core_total) if core_total else 1.
    bench_total=total-core_total
    bench_scale=min(1.,max(0.,240.-core_total*core_scale)/bench_total) if bench_total else 1.
    target={pid:working[pid][0]*(core_scale if pid in core else bench_scale) for pid in expected}
    # Expected minutes below 240 are still played by someone. Supported
    # players' conditional minutes rise proportionally to fill the budget,
    # by at most a quarter and never past 48; any rest stays an explicit deficit.
    locked={p['player_id'] for p in projected_team if p['components'].get('manual_adjustment')}
    # Filling needs a plausible rotation; a handful of supported players is left
    # as a deficit, unless the bench is short: then the shortfall is forced on
    # everyone available, shared equally up to 48 each, not in proportion to
    # roles sized for a full roster. The eight replayed seven-player team-games
    # averaged 42.6, 40.0, 39.0, 35.7, 33.9, 27.5 and 21.3 minutes by rank.
    for _ in range((len(target) if short else 4) if len(projected_team) >= 8 or short else 0):
        shortfall=240.-sum(working[pid][7]*target[pid] for pid in target)
        ceiling={pid:48. if short else min(48.,(1.+UPLIFT_CAP[0 if pid in core else 1])*working[pid][0]) for pid in target}
        open_ids=[pid for pid in target if pid not in locked and target[pid]>0 and working[pid][7]>0 and target[pid]<ceiling[pid]-1e-9]
        if shortfall<=1e-9 or not open_ids:
            break
        if short:
            share=shortfall/sum(working[pid][7] for pid in open_ids)
            for pid in open_ids:
                target[pid]=min(ceiling[pid],target[pid]+share)
            continue
        factor=1.+shortfall/sum(working[pid][7]*target[pid] for pid in open_ids)
        for pid in open_ids:
            target[pid]=min(ceiling[pid],target[pid]*factor)
    deficit=max(0.,240.-sum(working[pid][7]*target[pid] for pid in target))
    assigned_minutes = 0.0
    for p in team_predictions:
        pid=p['player_id']
        scale=(target[pid]/working[pid][0] if working[pid][0] else 1.) if pid in target else 1.
        p["components"]["rotation"] = {"team_minutes_before_scaling": total,
            "regulation_budget": 240.0, "scale": scale, "unallocated_minutes": deficit, "short_bench": short,
            "protected_rotation_player_ids":sorted(core),"protected_rotation_minutes":core_total,
            "budget_basis": "expected minutes: participation probability times minutes when playing",
            "method": "Preserve supported starters and high-minute rotation roles; scale excess bench estimates first. "
                      "A shortfall raises supported players' conditional minutes by at most a quarter; the rest is an explicit deficit. "
                      "A short bench (every other roster player explicitly out) plays everyone and shares the shortfall equally, up to 48 each."}
        if deficit > 1e-8:
            p["flags"].append("team_minutes_unresolved")
        if p["status"] != "projected":
            continue
        minutes, rates, shooting, rows, variance, factor, side_factor, probability = working[pid]
        if scale < 1:
            p["flags"].append("team_minutes_scaled")
        elif scale > 1 + 1e-9:
            p["flags"].append("team_minutes_raised")
        # Reserving the remaining budget also prevents floating-point sums
        # such as 240.00000000000003 from exceeding the exact output cap.
        room = max(0.0, 240.0 - assigned_minutes)
        allocated = min(target[pid], room / probability if probability else target[pid])
        assigned_minutes += probability * allocated
        effective_scale = allocated / minutes if minutes else 1.0
        _project_numbers(p, allocated, rates, shooting, rows, variance * effective_scale ** 2, factor, side_factor)
    output_total = sum(working[p["player_id"]][7] * p["expected_minutes"] for p in projected_team)
    if output_total > 240.0:
        # Python's compensated sum can differ from the running allocation
        # sum. Correct its residual by a negligible one-billionth-minute margin.
        last = projected_team[-1]
        minutes, rates, shooting, rows, variance, factor, side_factor, probability = working[last["player_id"]]
        excess = (output_total - 240.0) / probability if probability else output_total - 240.0
        allocated = max(0.0, last["expected_minutes"] - excess - 1e-9)
        effective_scale = allocated / minutes if minutes else 1.0
        _project_numbers(last, allocated, rates, shooting, rows, variance * effective_scale ** 2, factor, side_factor)
    # Then hold the team to the possessions it actually uses.
    def reproject(p, scale):
        minutes, rates, shooting, rows, variance, factor, side_factor, probability = working[p["player_id"]]
        effective_scale = p["expected_minutes"] / minutes if minutes else 1.
        _project_numbers(p, p["expected_minutes"], {k: v * scale for k, v in rates.items()}, shooting, rows,
                         variance * effective_scale ** 2, factor, side_factor)
    _usage_budget(projected_team, usage_prior, reproject, locked, short=short)


def _review_queue(predictions):
    """Players whose row needs a human look, by tier and priority."""
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
    return queue
