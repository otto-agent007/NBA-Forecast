"""Behavioral tests for the immutable forecast ledger."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from nba_forecast import store

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def inputs():
    players = [dict(player_id=f"P{i}", name=f"Player {i}", team_id=f"T{i}",
                    status="available", status_source_url="https://example.test/status",
                    status_observed_at="2026-10-22T10:00:00Z") for i in range(1, 31)]
    snapshot = {
        "schema_version": 1, "season": "2026-27", "as_of": "2026-10-22T11:00:00Z",
        "roster": {"observed_at": "2026-10-22T10:00:00Z",
                   "source_url": "https://example.test/roster", "complete": True,
                   "team_ids": [f"T{i}" for i in range(1, 31)], "players": players},
        "games": [{"game_id": "G1", "start_time": "2026-10-22T18:00:00Z",
                   "season_type": "Regular Season", "home_team_id": "T1",
                   "away_team_id": "T2"}],
    }
    predictions = []
    for i, player in enumerate(players, 1):
        on_slate = i <= 2
        row = {key: player[key] for key in ("player_id", "name", "team_id")}
        row.update(game_id="G1" if on_slate else None,
                   start_time="2026-10-22T18:00:00Z" if on_slate else None,
                   status="projected" if on_slate else "no_game", reason="baseline",
                   history_games=5 if on_slate else 0, flags=[], components={})
        row.update({key: None for key in (
            "expected_minutes", "expected_fga", "expected_fta", "expected_tov",
            "usage_proxy", "points_mean", "points_sd", "points_lower80", "points_upper80")})
        if on_slate:
            row.update(expected_minutes=30 if i == 1 else 20, expected_fga=10,
                       expected_fta=3, expected_tov=1, usage_proxy=.2,
                       points_mean=20 if i == 1 else 10, points_sd=4,
                       points_lower80=15 if i == 1 else 5,
                       points_upper80=25 if i == 1 else 15)
        predictions.append(row)
    forecast = {"model_version": "test-v1", "as_of": snapshot["as_of"],
                "season": snapshot["season"], "predictions": predictions,
                "review_queue": [], "coverage": {}}
    return snapshot, [], forecast


def actual(player="P1", status="played"):
    team, opponent = ("T1", "T2") if player == "P1" else ("T2", "T1")
    row = {"game_id": "G1", "season": "2026-27", "season_type": "Regular Season",
           "game_time": "2026-10-22T18:00:00Z", "known_at": "2026-10-22T21:00:00Z",
           "source_url": "https://example.test/final", "player_id": player,
           "name": "Player 1" if player == "P1" else "Player 2", "team_id": team,
           "opponent_id": opponent, "minutes": 32, "fgm": 10, "fga": 20,
           "fg3m": 2, "fg3a": 5, "ftm": 2, "fta": 3, "tov": 2, "pts": 24,
           "status": status}
    if status == "dnp":
        row.update({key: 0 for key in ("minutes", "fgm", "fga", "fg3m", "fg3a",
                                      "ftm", "fta", "tov", "pts")})
    return row


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "ledger.sqlite"
        self.snapshot, self.history, self.forecast = inputs()
        self.clock = patch("nba_forecast.store._now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def save(self, **kwargs):
        return store.save_run(self.db, self.snapshot, self.history, self.forecast, **kwargs)

    def evaluate(self, run_id, actuals):
        with patch("nba_forecast.store._now", return_value=LATER):
            return store.evaluate_run(self.db, run_id, actuals)

    def rows(self, table):
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]

    def test_saves_clock_full_inputs_and_hashes_with_independent_reruns(self):
        self.forecast["generated_at"] = "2001-01-01T00:00:00Z"
        first, second = self.save(), self.save()
        self.assertNotEqual(first, second)
        rows = self.rows("runs")
        self.assertEqual(len(rows), 2)
        self.assertEqual(datetime.fromisoformat(rows[0]["created_at"]), NOW)
        self.assertEqual(rows[0]["as_of"], self.snapshot["as_of"])
        for name, obj in (("snapshot", self.snapshot), ("history", self.history),
                          ("forecast", self.forecast)):
            encoded = json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)
            self.assertEqual(rows[0][f"{name}_json"], encoded)
            self.assertEqual(rows[0][f"{name}_sha256"], hashlib.sha256(encoded.encode()).hexdigest())

    def test_database_prevents_snapshot_mutation_and_deletion(self):
        self.save()
        with sqlite3.connect(self.db) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE runs SET forecast_json = '{}' ")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM runs")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT OR REPLACE INTO runs SELECT * FROM runs")

    def test_live_rejects_started_games_despite_asserted_generation_time(self):
        self.forecast["generated_at"] = "2026-10-22T11:00:00Z"
        with patch("nba_forecast.store._now", return_value=LATER):
            with self.assertRaises(ValueError):
                self.save()

    def test_live_rejects_future_or_stale_cutoff(self):
        for cutoff in ("2026-10-22T13:00:00Z", "2026-10-19T11:00:00Z"):
            with self.subTest(cutoff=cutoff):
                self.snapshot["as_of"] = self.forecast["as_of"] = cutoff
                self.snapshot["roster"]["observed_at"] = cutoff
                with self.assertRaises(ValueError):
                    self.save()

    def test_live_requires_league_scope_and_complete_teams(self):
        with self.assertRaises(ValueError):
            self.save(scope="subset")
        self.snapshot["roster"]["team_ids"].pop()
        with self.assertRaises(ValueError):
            self.save()

    def test_historical_replay_excluded_from_live_counts(self):
        with patch("nba_forecast.store._now", return_value=LATER):
            run_id = self.save(mode="replay")
        report = self.evaluate(run_id, [actual()])
        self.assertFalse(report["live_eligible"])
        self.assertEqual(report["mode"], "replay")
        self.assertEqual(report["sample_counts"]["live"], 0)
        self.assertEqual(report["sample_counts"]["replay"], 1)

    def test_missing_final_is_pending_and_final_dnp_is_zero(self):
        run_id = self.save()
        report = self.evaluate(run_id, [actual()])
        self.assertEqual(report["sample_counts"]["evaluated"], 1)
        self.assertEqual(report["sample_counts"]["pending"], 1)
        self.assertEqual(report["sample_counts"]["excluded"], 28)
        self.assertEqual(report["metrics"]["points_mae"], 4)
        report = self.evaluate(run_id, [actual("P2", "dnp")])
        self.assertEqual(report["sample_counts"]["pending"], 0)
        self.assertEqual(report["sample_counts"]["evaluated"], 2)
        self.assertEqual(report["metrics"]["points_mae"], 7)
        self.assertAlmostEqual(report["metrics"]["points_rmse"], 58 ** .5)
        self.assertEqual(report["metrics"]["mean_signed_error"], 3)
        self.assertEqual(report["metrics"]["minutes_mae"], 11)
        self.assertEqual(report["metrics"]["interval80_coverage"], .5)
        self.assertEqual(report["metrics"]["interval80_mean_width"], 10)
        self.assertEqual(report["actual_status_groups"]["dnp"]["count"], 1)

    def test_identical_actuals_are_idempotent_and_corrections_append(self):
        run_id = self.save()
        original = self.rows("runs")[0]["forecast_json"]
        finals = [actual(), actual("P2", "dnp")]
        first = self.evaluate(run_id, finals)
        same = self.evaluate(run_id, list(reversed(finals)))
        self.assertEqual(first, same)
        self.assertEqual(len(self.rows("evaluations")), 1)
        corrected = deepcopy(finals[0])
        corrected.update(pts=26, fgm=11)
        changed = self.evaluate(run_id, [corrected])
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(len(self.rows("evaluations")), 2)
        self.assertEqual(self.rows("runs")[0]["forecast_json"], original)
        with sqlite3.connect(self.db) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM evaluations")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT OR REPLACE INTO evaluations SELECT * FROM evaluations")

    def test_invalid_correction_rolls_back_entire_batch(self):
        run_id = self.save()
        first = self.evaluate(run_id, [actual()])
        invalid = actual("P2")
        invalid["pts"] = -1
        correction = actual()
        correction.update(fgm=11, pts=26)
        with self.assertRaises(ValueError):
            self.evaluate(run_id, [correction, invalid])
        self.assertEqual(self.evaluate(run_id, [actual()]), first)
        self.assertEqual(len(self.rows("evaluations")), 1)

    def test_actuals_match_identity_and_final_contract_strictly(self):
        run_id = self.save()
        for field, value in (("game_id", "wrong"), ("player_id", "wrong"),
                             ("team_id", "T2"), ("opponent_id", "T3"),
                             ("season", "2025-26"), ("season_type", "Playoffs"),
                             ("status", "unknown"),
                             ("game_time", "2026-10-21T18:00:00Z"),
                             ("known_at", "2026-10-24T00:00:00Z")):
            with self.subTest(field=field):
                bad = actual()
                bad[field] = value
                with self.assertRaises(ValueError):
                    self.evaluate(run_id, [bad])
        self.assertEqual(len(self.rows("evaluations")), 0)

    def test_out_forecast_for_player_who_plays_is_an_error(self):
        self.snapshot["roster"]["players"][0]["status"] = "out"
        row = self.forecast["predictions"][0]
        row["status"] = "out"
        for key in ("expected_minutes", "expected_fga", "expected_fta", "expected_tov",
                    "usage_proxy", "points_mean", "points_sd", "points_lower80", "points_upper80"):
            row[key] = 0
        report = self.evaluate(self.save(), [actual()])
        self.assertEqual(report["metrics"]["points_mae"], 24)
        self.assertEqual(report["status_groups"]["out"]["count"], 1)
        self.assertIn("out_player_played", {item["code"] for item in report["diagnostics"]})

    def test_unsupported_predictions_are_explicitly_excluded(self):
        self.snapshot["roster"]["players"][0]["status"] = "unknown"
        row = self.forecast["predictions"][0]
        row["status"] = "needs_review"
        for key in ("expected_minutes", "expected_fga", "expected_fta", "expected_tov",
                    "usage_proxy", "points_mean", "points_sd", "points_lower80", "points_upper80"):
            row[key] = None
        report = self.evaluate(self.save(), [actual()])
        self.assertEqual(report["sample_counts"]["evaluated"], 0)
        self.assertEqual(report["sample_counts"]["excluded"], 29)
        self.assertIsNone(report["metrics"]["points_mae"])
        self.assertTrue(any(row["player_id"] == "P1" for row in report["excluded"]))

    def test_diagnostics_explain_calibration_bias_and_minutes(self):
        report = self.evaluate(self.save(), [actual(), actual("P2", "dnp")])
        codes = {item["code"] for item in report["diagnostics"]}
        self.assertTrue({"low_sample", "calibration_low_sample", "undercoverage",
                         "systematic_overprediction", "minutes_driver"} <= codes)
        matched = report["matched"][0]
        self.assertAlmostEqual(matched["minutes_component"] + matched["rate_component"],
                               matched["points_error"])

    def test_save_rejects_tampered_forecasts(self):
        for change in (lambda f: f.update(as_of="2026-10-21T11:00:00Z"),
                       lambda f: f["predictions"][0].update(team_id="T3"),
                       lambda f: f["predictions"].pop(),
                       lambda f: f["predictions"][0].update(points_mean=float("nan")),
                       lambda f: f["predictions"][0].update(points_lower80=30)):
            with self.subTest(change=change):
                self.forecast = inputs()[2]
                change(self.forecast)
                with self.assertRaises(ValueError):
                    self.save()


    def test_aggregate_uses_latest_pregame_run_without_duplicate_samples(self):
        first = self.save()
        self.evaluate(first, [actual(), actual("P2", "dnp")])
        self.forecast["predictions"][0].update(points_mean=30, points_upper80=35)
        self.forecast["generated_at"] = "2001-01-01T00:00:00Z"
        with patch("nba_forecast.store._now", return_value=NOW + timedelta(hours=1)):
            latest = self.save()
        self.evaluate(latest, [actual(), actual("P2", "dnp")])
        report = store.performance_report(self.db)
        self.assertEqual(report["sample_counts"]["evaluated"], 2)
        group = report["groups"][0]
        self.assertEqual(group["metrics"]["points_mae"], 8)
        self.assertEqual(group["sample_counts"]["predictions"], 2)
        self.assertEqual({row["run_id"] for row in group["selected_forecasts"]}, {latest})
        self.assertEqual(report["off_slate_rows_ignored"], 56)

    def test_aggregate_selects_before_evaluation_and_never_falls_back(self):
        first = self.save()
        self.evaluate(first, [actual()])
        # A timestamp tie is resolved by insertion order, not by evaluations or UUID.
        latest = self.save()
        report = store.performance_report(self.db)
        group = report["groups"][0]
        self.assertEqual(group["sample_counts"]["evaluated"], 0)
        self.assertEqual(group["sample_counts"]["pending"], 2)
        self.assertIsNone(group["metrics"]["points_mae"])
        self.assertEqual({row["run_id"] for row in group["selected_forecasts"]}, {latest})

    def test_aggregate_latest_unsupported_status_does_not_revive_old_projection(self):
        self.evaluate(self.save(), [actual()])
        self.snapshot["roster"]["players"][0]["status"] = "unknown"
        row = self.forecast["predictions"][0]
        row["status"] = "needs_review"
        for key in store.PROJECTION_FIELDS:
            row[key] = None
        latest = self.save()
        self.evaluate(latest, [actual(), actual("P2", "dnp")])
        group = store.performance_report(self.db)["groups"][0]
        self.assertEqual(group["sample_counts"]["evaluated"], 1)
        self.assertEqual(group["sample_counts"]["excluded"], 1)
        self.assertEqual(group["metrics"]["points_mae"], 10)
        self.assertEqual(group["excluded"][0]["player_id"], "P1")
        self.assertEqual(group["excluded"][0]["reason"], "needs_review")

    def test_aggregate_ignores_non_live_and_keeps_model_versions_separate(self):
        self.evaluate(self.save(), [actual()])
        self.forecast["predictions"][0].update(points_mean=30, points_upper80=35)
        for mode, scope in (("replay", "league"), ("demo", "league"), ("demo", "subset")):
            run_id = self.save(mode=mode, scope=scope)
            self.evaluate(run_id, [actual()])
        self.forecast["model_version"] = "test-v2"
        self.evaluate(self.save(), [actual()])
        report = store.performance_report(self.db)
        groups = {group["model_version"]: group for group in report["groups"]}
        self.assertEqual(set(groups), {"test-v1", "test-v2"})
        self.assertEqual(groups["test-v1"]["metrics"]["points_mae"], 4)
        self.assertEqual(groups["test-v2"]["metrics"]["points_mae"], 6)
        self.assertEqual(report["non_live_runs_ignored"], 3)
        self.assertEqual(report["sample_counts"]["evaluated"], 2)

    def test_aggregate_uses_latest_correction_revision(self):
        run_id = self.save()
        self.evaluate(run_id, [actual()])
        correction = actual()
        correction.update(fgm=11, pts=26)
        self.evaluate(run_id, [correction])
        group = store.performance_report(self.db)["groups"][0]
        self.assertEqual(group["metrics"]["points_mae"], 6)
        self.assertEqual(group["sample_counts"]["evaluated"], 1)
        self.assertEqual(group["matched"][0]["evaluation_revision"], 2)

    def test_unknown_run_is_not_an_empty_success(self):
        self.save()
        with self.assertRaises(ValueError):
            self.evaluate("missing", [])


if __name__ == "__main__":
    unittest.main()
