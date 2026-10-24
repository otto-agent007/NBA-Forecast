"""Behavioral tests for the deterministic, evidence-only scoring baseline."""
import copy
import json
import math
import unittest

try:
    from nba_forecast.model import forecast
except ModuleNotFoundError:
    forecast = None


def player(pid="p", team="A", status="available"):
    result = {"player_id": pid, "name": pid, "team_id": team, "status": status}
    if status != "unknown":
        result.update(status_source_url="https://example.test/status",
                      status_observed_at="2026-01-10T11:00:00+00:00")
    return result


def snapshot(players=None):
    return {
        "schema_version": 1, "season": "2025-26",
        "as_of": "2026-01-10T12:00:00+00:00",
        "roster": {"observed_at": "2026-01-10T11:00:00+00:00",
                   "source_url": "https://example.test/roster", "complete": True,
                   "team_ids": ["A", "B", "C"], "players": players or [player()]},
        "games": [{"game_id": "next", "start_time": "2026-01-10T20:00:00+00:00",
                   "season_type": "Regular Season", "home_team_id": "A",
                   "away_team_id": "B"}],
    }


def row(i=0, pid="p", team="A", season="2024-25", minutes=30,
        fgm=6, fga=12, fg3m=2, fg3a=5, ftm=3, fta=4, **kw):
    r = {"game_id": f"g{i}", "season": season, "season_type": "Regular Season",
         "game_time": f"2025-03-{i % 28 + 1:02d}T20:00:00+00:00",
         "known_at": f"2025-03-{i % 28 + 1:02d}T23:00:00+00:00",
         "source_url": "https://example.test/box", "player_id": pid,
         "name": pid, "team_id": team, "opponent_id": "B" if team == "A" else "A",
         "minutes": minutes, "fgm": fgm, "fga": fga, "fg3m": fg3m,
         "fg3a": fg3a, "ftm": ftm, "fta": fta, "tov": 2,
         "pts": 2 * fgm + fg3m + ftm, "status": "played"}
    r.update(kw)
    return r


def current(i=0, **kw):
    kw.setdefault("season", "2025-26")
    kw.setdefault("game_time", f"2026-01-{i + 1:02d}T00:00:00Z")
    kw.setdefault("known_at", f"2026-01-{i + 1:02d}T03:00:00Z")
    return row(i=100 + i, **kw)


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(forecast), "nba_forecast.model.forecast must be implemented")

    def one(self, data, snap=None):
        return forecast(snap or snapshot(), data)["predictions"][0]

    def test_pool_includes_out_uncertain_cold_start_and_off_slate(self):
        snap = snapshot([player(), player("out", status="out"),
                         player("q", status="questionable"), player("u", status="unknown"),
                         player("new"), player("off", team="C")])
        result = forecast(snap, [row()])
        by_id = {p["player_id"]: p for p in result["predictions"]}
        self.assertEqual(set(by_id), {"p", "out", "q", "u", "new", "off"})
        self.assertEqual(by_id["p"]["status"], "projected")
        self.assertEqual(by_id["out"]["points_mean"], 0)
        self.assertEqual(by_id["out"]["points_sd"], 0)
        for pid in ("q", "u", "new"):
            self.assertEqual(by_id[pid]["status"], "needs_review")
            self.assertIsNone(by_id[pid]["points_mean"])
        self.assertEqual(by_id["off"]["status"], "no_game")
        self.assertIsNone(by_id["off"]["game_id"])
        self.assertEqual(result["coverage"]["roster_players"], 6)
        self.assertEqual(result["coverage"]["pool_rows"], 6)
        self.assertEqual(result["as_of"], snap["as_of"])

    def test_model_filters_late_known_future_preseason_and_older_season(self):
        base = [row()]
        excluded = [row(1, known_at="2026-01-10T12:00:01Z"),
                    row(2, game_time="2026-01-11T00:00:00Z"),
                    row(3, season_type="Preseason"), row(4, season="2023-24"),
                    row(5, season="2026-27")]
        baseline = self.one(base)
        filtered = self.one(base + excluded)
        self.assertEqual(filtered["points_mean"], baseline["points_mean"])
        self.assertEqual(filtered["history_games"], 1)
        self.assertEqual(filtered["components"]["history"]["excluded_rows"], 5)

    def test_timezone_cutoff_uses_instants(self):
        baseline = self.one([row()])
        filtered = self.one([row(), row(1, known_at="2026-01-10T08:00:01-04:00")])
        self.assertEqual(filtered["points_mean"], baseline["points_mean"])
        self.assertEqual(filtered["history_games"], 1)

    def test_one_bad_shooting_night_does_not_erase_prior_baseline(self):
        past = [row(i) for i in range(28)]
        before = self.one(past)
        after = self.one(past + [current(fgm=0, fg3m=0, ftm=0)])
        self.assertGreater(after["points_mean"], before["points_mean"] * .90)
        self.assertGreater(after["components"]["shooting"]["two_point"]["percentage"], .48)

    def test_tiny_prior_perfect_shooting_is_shrunk(self):
        p = self.one([row(fgm=1, fga=1, fg3m=0, fg3a=0, ftm=0, fta=0)])
        pct = p["components"]["shooting"]["two_point"]["percentage"]
        self.assertGreater(pct, .45)
        self.assertLess(pct, .65)
        self.assertLess(p["points_mean"], 1.4)

    def test_minutes_and_opportunities_respond_faster_than_shooting(self):
        past = [row(i) for i in range(28)]
        recent = [current(i, minutes=40, fgm=0, fga=24, fg3m=0, fg3a=10,
                          ftm=0, fta=8) for i in range(4)]
        before = self.one(past)
        after = self.one(past + recent)
        self.assertGreater(after["expected_minutes"], 35)
        self.assertGreater(after["expected_fga"], before["expected_fga"])
        self.assertGreater(after["components"]["shooting"]["two_point"]["percentage"], .35)
        self.assertIn("minutes_change", after["flags"])
        self.assertIn("opportunity_change", after["flags"])

    def test_role_changes_within_current_season_are_flagged_without_prior_history(self):
        data = [current(i, minutes=10, fga=2, fgm=1, fg3a=0, fg3m=0, fta=0, ftm=0)
                for i in range(6)]
        data += [current(i, minutes=35, fga=20, fgm=9, fg3a=0, fg3m=0, fta=4, ftm=3)
                 for i in range(6, 9)]
        p = self.one(data)
        self.assertIn("minutes_change", p["flags"])
        self.assertIn("opportunity_change", p["flags"])
        self.assertEqual(p["components"]["role_comparison"]["baseline"], "earlier_current_season")

    def test_trade_and_role_changes_enter_review_queue(self):
        result = forecast(snapshot(), [row(i, team="OLD", opponent_id="B") for i in range(12)])
        p = result["predictions"][0]
        self.assertEqual(p["status"], "projected")
        self.assertIn("team_change", p["flags"])
        self.assertTrue(any(q["player_id"] == "p" and "team_change" in q["flags"]
                            for q in result["review_queue"]))

    def test_dnp_is_known_zero_minutes_but_never_cold_start_production(self):
        dnp = row(minutes=0, fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0,
                  tov=0, status="dnp")
        p = self.one([dnp])
        self.assertEqual(p["status"], "needs_review")
        self.assertIsNone(p["points_mean"])
        played = [row()] + [current(i) for i in range(3)]
        with_dnp = self.one(played[:3] + [current(2, minutes=0, fgm=0, fga=0,
                              fg3m=0, fg3a=0, ftm=0, fta=0, tov=0, status="dnp")])
        # The DNP lowers the probability of playing, not the minutes when playing.
        without = self.one(played)
        self.assertGreater(with_dnp["expected_minutes"], 0)
        self.assertLess(with_dnp["participation_probability"], without["participation_probability"])

    def test_game_specific_sourced_adjustment_is_audited(self):
        snap = snapshot()
        adjustment = {"player_id": "p", "game_id": "next", "observed_at": snap["as_of"],
                      "source_url": "https://example.test/lineup", "reviewer": "human",
                      "reason": "Announced expanded role", "minutes_delta": 5,
                      "opportunity_multiplier": 1.2}
        snap["role_adjustments"] = [adjustment]
        past = [row(i) for i in range(12)]
        before = self.one(past)
        after = self.one(past, snap)
        # A sourced adjustment is applied to the role minutes exactly and is not raised to fill the budget.
        self.assertEqual(after["expected_minutes"], before["components"]["opportunities"]["minutes_before_adjustment"] + 5)
        self.assertGreater(after["expected_fga"], before["expected_fga"] * 1.3)
        self.assertEqual(after["components"]["manual_adjustment"], adjustment)
        self.assertIn("manual_role_adjustment", after["flags"])
        adjustment["game_id"] = "other-game"
        self.assertEqual(self.one(past, snap)["points_mean"], before["points_mean"])
        adjustment["game_id"] = "next"
        adjustment["observed_at"] = "2026-01-10T12:00:01Z"
        self.assertEqual(self.one(past, snap)["points_mean"], before["points_mean"])

    def test_excess_team_minutes_are_scaled_to_regulation_total(self):
        players = [player(f"p{i}") for i in range(8)]
        past = [row(i, pid=p["player_id"], minutes=45) for p in players for i in range(10)]
        projections = forecast(snapshot(players), past)["predictions"]
        # The budget constrains expected minutes (probability of playing times minutes when playing).
        self.assertLessEqual(sum(p["participation_probability"] * p["expected_minutes"] for p in projections), 240.00001)
        for p in projections:
            self.assertAlmostEqual(p["participation_probability"] * p["expected_minutes"], 30)
            self.assertIn("team_minutes_scaled", p["flags"])
            self.assertLess(p["components"]["rotation"]["scale"], 1)
            # Eight light shooters fall short of a team's usage, so the possession budget raises the rates.
            usage = p["components"]["team_usage"]["factor"]
            self.assertGreater(usage, 1)
            self.assertAlmostEqual(p["expected_fga"], p["expected_minutes"] * 12 / 45 * usage)

    def test_missing_rotation_minutes_are_flagged_not_invented(self):
        result = forecast(snapshot([player(), player("new")]), [row()])
        by_id = {p["player_id"]: p for p in result["predictions"]}
        self.assertIsNone(by_id["new"]["expected_minutes"])
        self.assertIn("team_minutes_unresolved", by_id["p"]["flags"])
        # Too few supported players to fill the budget: minutes are not invented.
        self.assertAlmostEqual(by_id["p"]["expected_minutes"], 30)
        self.assertAlmostEqual(by_id["p"]["components"]["rotation"]["unallocated_minutes"], 240 - 30 * by_id["p"]["participation_probability"])

    def complete_game(self, gid, b_allowed, partial=False):
        # Five 48-minute players per side establish complete 240-minute groups.
        data = []
        for team in ("B", "D"):
            for i in range(5):
                made = b_allowed // 10 if team == "D" else 10
                data.append(row(i=gid, pid=f"{team}{i}", team=team,
                                opponent_id="D" if team == "B" else "B",
                                minutes=48, fgm=made, fga=made + 10,
                                fg3m=0, fg3a=0, ftm=0, fta=0))
        return data[:-1] if partial else data

    def test_opponent_proxy_ignores_incomplete_team_boxscores(self):
        base = [row(i) for i in range(10)]
        before = self.one(base)
        after = self.one(base + self.complete_game(200, 200, partial=True))
        self.assertEqual(after["points_mean"], before["points_mean"])
        self.assertEqual(after["components"]["opponent"]["complete_games"], 0)
        self.assertEqual(after["components"]["opponent"]["factor"], 1)

    def test_complete_opponent_proxy_is_bounded_and_audited(self):
        base = [row(i) for i in range(10)]
        extra = [r for i in range(20) for r in self.complete_game(200 + i, 200)]
        before = self.one(base)
        after = self.one(base + extra)
        opp = after["components"]["opponent"]
        self.assertEqual(opp["complete_games"], 20)
        self.assertGreater(opp["factor"], 1)
        self.assertLessEqual(opp["factor"], 1.05)
        self.assertLessEqual(after["points_mean"], before["points_mean"] * 1.050001)
        self.assertIn("points_allowed_proxy", opp["method"])

    def test_variability_and_every_numeric_projection_are_finite(self):
        p = self.one([row(i, fgm=2 + i % 6, fg3m=0) for i in range(12)])
        self.assertGreater(p["points_sd"], 0)
        self.assertLess(p["points_lower80"], p["points_mean"])
        self.assertGreater(p["points_upper80"], p["points_mean"])
        self.assertIn("empirical_residual_variance", p["components"]["uncertainty"])
        self.assertIn("heuristic", p["components"]["uncertainty"]["method"])
        self.assertGreater(p["usage_proxy"], 0)
        for key in ("expected_minutes", "expected_fga", "expected_fta", "expected_tov",
                    "usage_proxy", "points_mean", "points_sd", "points_lower80", "points_upper80"):
            self.assertTrue(math.isfinite(p[key]), key)
        json.dumps(p, allow_nan=False)

    def test_review_queue_routes_data_and_reasoning_separately(self):
        result = forecast(snapshot([player(), player("q", status="questionable")]),
                          [row(team="OLD")])
        queue = {q["player_id"]: q for q in result["review_queue"]}
        self.assertEqual(queue["p"].get("tier"), "reasoning_review")
        self.assertEqual(queue["q"].get("tier"), "data_verification")
        self.assertIn(queue["p"]["priority"], ("high", "normal"))

    def test_late_known_status_or_roster_cannot_produce_projection(self):
        for field in ("status", "roster"):
            with self.subTest(field=field):
                snap = snapshot()
                if field == "status":
                    snap["roster"]["players"][0]["status_observed_at"] = "2026-01-10T12:00:01Z"
                else:
                    snap["roster"]["observed_at"] = "2026-01-10T12:00:01Z"
                p = self.one([row()], snap)
                self.assertEqual(p["status"], "needs_review")
                self.assertIsNone(p["points_mean"])

    def test_late_known_opponent_row_invalidates_complete_group(self):
        game = self.complete_game(200, 200)
        game[-1]["known_at"] = "2026-01-10T12:00:01Z"
        p = self.one([row()] + game)
        self.assertEqual(p["components"]["opponent"]["complete_games"], 0)
        self.assertEqual(p["components"]["opponent"]["factor"], 1)

    def test_overtime_normalizes_and_mismatched_minutes_are_rejected(self):
        game = self.complete_game(200, 200)
        for r in game:
            r["minutes"] = 53
        p = self.one([row()] + game)
        self.assertEqual(p["components"]["opponent"]["complete_games"], 1)
        self.assertAlmostEqual(p["components"]["opponent"]["points_allowed_per_48"], 200 * 48 / 53)
        for r in game:
            if r["team_id"] == "B":
                r["minutes"] = 48
        p = self.one([row()] + game)
        self.assertEqual(p["components"]["opponent"]["complete_games"], 0)

    def test_positive_minutes_without_scoring_opportunities_are_withheld(self):
        zero = current(fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0, tov=0)
        p = self.one([zero])
        self.assertEqual(p["status"], "needs_review")
        self.assertIn("no_scoring_opportunity_evidence", p["flags"])
        self.assertIsNone(p["points_mean"])
        self.assertIsNone(p["expected_fga"])
        self.assertIsNone(p["points_sd"])

    def test_current_only_history_supports_projection(self):
        p = self.one([current()])
        self.assertEqual(p["status"], "projected")
        self.assertGreater(p["points_mean"], 0)

    def test_absent_teammate_does_not_automatically_redistribute_opportunities(self):
        data = [row(), row(pid="teammate")]
        snap = snapshot([player(), player("teammate")])
        first = {p["player_id"]: p for p in forecast(snap, data)["predictions"]}["p"]
        snap["roster"]["players"][1]["status"] = "out"
        second = {p["player_id"]: p for p in forecast(snap, data)["predictions"]}["p"]
        self.assertEqual(first["expected_minutes"], second["expected_minutes"])
        self.assertEqual(first["points_mean"], second["points_mean"])

    def test_adjustment_does_not_create_support_or_override_status(self):
        for status in ("available", "out", "questionable"):
            with self.subTest(status=status):
                snap = snapshot([player(status=status)])
                snap["role_adjustments"] = [{"player_id": "p", "game_id": "next",
                    "observed_at": snap["as_of"], "source_url": "https://example.test/role",
                    "reviewer": "human", "reason": "Expanded role", "minutes_delta": 15,
                    "opportunity_multiplier": 1.5}]
                p = self.one([], snap)
                self.assertEqual(p["points_mean"], 0 if status == "out" else None)

    def test_uneven_rotation_never_exceeds_exact_regulation_budget(self):
        players = [player(f"p{i}") for i in range(11)]
        past = [row(i, pid=p["player_id"], minutes=44) for i, p in enumerate(players)]
        result = forecast(snapshot(players), past)
        self.assertLessEqual(sum((p["participation_probability"] or 0) * (p["expected_minutes"] or 0) for p in result["predictions"]), 240.0)

    def test_zero_minutes_adjustment_has_zero_scoring_uncertainty(self):
        snap = snapshot()
        snap["role_adjustments"] = [{"player_id": "p", "game_id": "next",
            "observed_at": snap["as_of"], "source_url": "https://example.test/role",
            "reviewer": "human", "reason": "Removed from rotation", "minutes_delta": -15,
            "opportunity_multiplier": 1.0}]
        data = [row(i, minutes=10, fgm=12 * (i % 2), fg3m=0) for i in range(12)]
        p = self.one(data, snap)
        self.assertEqual(p["expected_minutes"], 0)
        self.assertEqual(p["points_mean"], 0)
        self.assertEqual(p["points_sd"], 0)

    def test_excess_scoring_residuals_widen_uncertainty(self):
        variable = [row(i, fgm=12 * (i % 2), fg3m=0) for i in range(12)]
        steady = [row(i, fgm=6, fg3m=0) for i in range(12)]
        varied_result, steady_result = self.one(variable), self.one(steady)
        self.assertAlmostEqual(varied_result["points_mean"], steady_result["points_mean"])
        self.assertGreater(varied_result["points_sd"], steady_result["points_sd"])

    def test_forecast_is_pure_and_order_independent(self):
        snap = snapshot()
        past = [row(i) for i in range(10)]
        original = copy.deepcopy((snap, past))
        first = forecast(snap, past)
        self.assertEqual((snap, past), original)
        self.assertEqual(first, forecast(snap, list(reversed(past))))


if __name__ == "__main__":
    unittest.main()
