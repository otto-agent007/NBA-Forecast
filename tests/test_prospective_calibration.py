"""Outcome-blind prospective residual collection from immutable ledger runs."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

try:
    from nba_forecast.prospective_calibration import collect_samples, fit_prospective
except ModuleNotFoundError:
    collect_samples = fit_prospective = None
from nba_forecast.store import _SCHEMA


MODEL = "model-config-sha256"
AS_OF = "2026-01-10T12:00:00Z"
START = "2026-01-05T20:00:00Z"


def prediction(pid="p", **changes):
    result = dict(player_id=pid, name=pid, game_id="g1", team_id="H", start_time=START,
                  status="projected", points_mean=20, points_sd=2,
                  points_lower80=17, points_upper80=23, components={})
    result.update(changes)
    return result


def actual(pid="p", **changes):
    result = dict(game_id="g1", player_id=pid, name=pid, team_id="H", opponent_id="A",
                  season="2025-26", season_type="Regular Season", game_time=START,
                  known_at="2026-01-05T23:00:00Z", source_url="https://example.test/final",
                  status="played", minutes=30, fgm=8, fga=15, fg3m=2, fg3a=5,
                  ftm=4, fta=4, tov=2, pts=22)
    result.update(changes)
    return result


class ProspectiveCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(collect_samples), "prospective collector must exist")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.db = self.directory / "forecast.sqlite"
        with sqlite3.connect(self.db) as conn:
            conn.executescript(_SCHEMA)
        (self.directory / "finals").mkdir()

    def run_row(self, run_id="r1", created="2026-01-05T19:00:00Z", rows=None,
                model_key=MODEL, mode="live", scope="league"):
        forecast = dict(model_key=model_key, season="2025-26", as_of="2026-01-05T18:00:00Z",
                        predictions=rows if rows is not None else [prediction()])
        encoded = json.dumps(forecast, allow_nan=True)
        # Exact production schema; deliberately non-JSON input histories prove
        # the reader never decodes irrelevant historical input copies.
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (run_id, created, forecast["as_of"], mode, scope,
                          "not-read-snapshot", "not-read-history", encoded, "x", "x",
                          hashlib.sha256(encoded.encode()).hexdigest(), "x"))

    def finals(self, rows=None, game_id="g1"):
        (self.directory / "finals" / (game_id + ".json")).write_text(json.dumps(rows if rows is not None else [actual()]))

    def collect(self):
        return collect_samples(self.directory, MODEL, AS_OF)

    def test_genuine_pregame_residual_preserves_capture_times_and_ledger_bytes(self):
        self.run_row()
        self.finals()
        before = self.db.read_bytes()
        result = self.collect()
        self.assertEqual(len(result["samples"]), 1)
        sample = result["samples"][0]
        self.assertEqual(sample["forecast_created_at"], "2026-01-05T19:00:00Z")
        self.assertEqual(sample["actual_known_at"], "2026-01-05T23:00:00Z")
        self.assertEqual(sample["actual_points"], 22)
        self.assertEqual(sample["evidence_kind"], "prospective")
        self.assertEqual(sample["model_key"], MODEL)
        self.assertEqual(sample["run_id"], "r1")
        self.assertEqual(self.db.read_bytes(), before)

    def test_latest_pregame_forecast_is_selected_before_reading_final(self):
        self.run_row(rows=[prediction(points_mean=22)])
        self.run_row("r2", "2026-01-05T19:30:00Z", [prediction(points_mean=5)])
        self.finals()
        sample = self.collect()["samples"][0]
        self.assertEqual(sample["points_mean"], 5)
        self.assertEqual(sample["run_id"], "r2")

    def test_latest_withheld_or_invalid_numeric_row_never_revives_earlier_projection(self):
        for status, sd in [("needs_review", None), ("out", 0), ("projected", 0)]:
            with self.subTest(status=status):
                pid = status
                self.run_row(status + "old", rows=[prediction(pid)])
                self.run_row(status + "new", "2026-01-05T19:30:00Z",
                             [prediction(pid, status=status, points_sd=sd)])
        self.finals([actual(pid) for pid in ("needs_review", "out", "projected")])
        result = self.collect()
        self.assertEqual(result["samples"], [])
        self.assertEqual(result["counts"]["selected_predictions"], 3)

    def test_tied_run_times_use_insertion_order(self):
        self.run_row("z", rows=[prediction(points_mean=22)])
        self.run_row("a", rows=[prediction(points_mean=5)])
        self.finals()
        self.assertEqual(self.collect()["samples"][0]["run_id"], "a")

    def test_nonlive_wrong_model_late_and_future_created_runs_are_excluded(self):
        self.run_row()
        self.run_row("replay", rows=[prediction(points_mean=1)], mode="replay")
        self.run_row("subset", rows=[prediction(points_mean=2)], scope="subset")
        self.run_row("wrong", rows=[prediction(points_mean=3)], model_key="other")
        self.run_row("late", START, [prediction(points_mean=4)])
        self.run_row("future", "2026-01-11T00:00:00Z", [prediction(points_mean=5)])
        self.finals()
        result = self.collect()
        self.assertEqual(len(result["samples"]), 1)
        self.assertEqual(result["samples"][0]["run_id"], "r1")

    def test_actual_identity_cutoff_or_duplicate_failure_cannot_be_fitted(self):
        self.run_row()
        for changes in [dict(team_id="A", opponent_id="H"), dict(game_time="2026-01-06T20:00:00Z"),
                        dict(season="2024-25"), dict(known_at="2026-01-11T00:00:00Z"),
                        dict(player_id="unknown")]:
            with self.subTest(changes=changes):
                self.finals([actual(**changes)])
                self.assertEqual(self.collect()["samples"], [])
        self.finals([actual(), actual()])
        result = self.collect()
        self.assertEqual(result["samples"], [])
        self.assertTrue(result["errors"])

    def test_actual_nonparticipation_is_excluded_from_conditional_calibration(self):
        self.run_row()
        for status in ("dnp", "played"):
            with self.subTest(status=status):
                row = actual(status=status)
                row.update({field: 0 for field in ("minutes", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "tov", "pts")})
                self.finals([row])
                result = self.collect()
                self.assertEqual(result["samples"], [])
                self.assertEqual(result["counts"]["games"], 0)
                self.assertEqual(result["exclusions"]["actual_nonparticipation"], 1)
                self.assertEqual(result["saved_interval_metrics"]["groups"], {})
                self.assertIn("positive observed minutes", result["population"])
                self.assertEqual(result["saved_interval_metrics"]["population"], result["population"])

    def test_easy_zero_dnp_outcomes_do_not_change_the_fitted_population(self):
        rows = [prediction(str(i)) for i in range(4)]
        nonparticipants = [prediction("dnp" + str(i), points_mean=0) for i in range(8)]
        self.run_row(rows=rows + nonparticipants)
        finals = [actual(str(i)) for i in range(4)]
        for i in range(8):
            row = actual("dnp" + str(i), status="dnp")
            row.update({field: 0 for field in ("minutes", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "tov", "pts")})
            finals.append(row)
        self.finals(finals)
        artifact = fit_prospective(self.directory, MODEL, AS_OF, minimum_samples=4, minimum_games=1)
        self.assertEqual(artifact["status"], "ready")
        self.assertEqual(artifact["sample_count"], 4)
        self.assertEqual(artifact["collection_exclusions"]["actual_nonparticipation"], 8)
        self.assertIn("positive observed minutes", artifact["population"])
        self.assertEqual(sum(group["sample_count"] for group in artifact["saved_interval_metrics"]["groups"].values()), 4)

    def test_saved_interval_assessment_is_separate_from_new_fit(self):
        calibration = dict(status="calibrated", audit=dict(evidence_kind="retrospective_chronological_reconstruction"))
        rows = [prediction(str(i), components=dict(uncertainty=dict(calibration=calibration))) for i in range(4)]
        self.run_row(rows=rows)
        self.finals([actual(str(i)) for i in range(4)])
        artifact = fit_prospective(self.directory, MODEL, AS_OF, minimum_samples=4, minimum_games=1)
        self.assertEqual(artifact["status"], "ready")
        self.assertEqual(artifact["quantile"], 1)
        groups = artifact["saved_interval_metrics"]["groups"]
        self.assertEqual(groups["retrospective_chronological_reconstruction"]["coverage"], 1)
        self.assertEqual(groups["retrospective_chronological_reconstruction"]["mean_width"], 6)
        self.assertIn("fitting", artifact["fitting_metrics"]["partition"])
        self.assertEqual(artifact["evidence_kind"], "prospective")

    def test_many_correlated_players_do_not_satisfy_minimum_games(self):
        self.run_row(rows=[prediction(str(i)) for i in range(4)])
        self.finals([actual(str(i)) for i in range(4)])
        artifact = fit_prospective(self.directory, MODEL, AS_OF, minimum_samples=4, minimum_games=2)
        self.assertEqual(artifact["status"], "not_ready")
        self.assertEqual(artifact["reason"], "insufficient_games")
        self.assertIsNone(artifact["quantile"])
        self.assertEqual(artifact["game_count"], 1)

    def test_residual_source_hash_is_stable_until_new_evidence_arrives(self):
        self.run_row(rows=[prediction(str(i)) for i in range(4)])
        self.finals([actual(str(i)) for i in range(4)])
        before = fit_prospective(self.directory, MODEL, AS_OF, minimum_samples=4, minimum_games=1)
        again = fit_prospective(self.directory, MODEL, "2026-01-10T13:00:00Z", minimum_samples=4, minimum_games=1)
        self.assertEqual(before["residual_evidence_sha256"], again["residual_evidence_sha256"])
        self.finals([actual(str(i), known_at="2026-01-06T23:00:00Z") for i in range(4)])
        after = fit_prospective(self.directory, MODEL, AS_OF, minimum_samples=4, minimum_games=1)
        self.assertNotEqual(before["residual_evidence_sha256"], after["residual_evidence_sha256"])

    def test_absent_ledger_does_not_create_a_database_or_final_directory(self):
        with tempfile.TemporaryDirectory() as missing:
            result = collect_samples(missing, MODEL, AS_OF)
            self.assertEqual(result["samples"], [])
            self.assertFalse((Path(missing) / "forecast.sqlite").exists())
            self.assertFalse((Path(missing) / "finals").exists())

    def test_invalid_actual_archive_records_errors_and_produces_no_partial_samples(self):
        self.run_row()
        (self.directory / "finals" / "g1.json").write_text("not JSON")
        result = self.collect()
        self.assertEqual(result["samples"], [])
        self.assertEqual(len(result["errors"]), 1)

    def test_nonfinite_standardized_residual_does_not_count_toward_readiness(self):
        self.run_row(rows=[prediction(points_mean=1e308, points_sd=1e-308)])
        self.finals()
        result = self.collect()
        self.assertEqual(result["samples"], [])
        self.assertEqual(result["counts"]["games"], 0)

    def test_unrecognized_saved_interval_audit_does_not_prevent_residual_collection(self):
        self.run_row(rows=[prediction(components=dict(uncertainty=None))])
        self.finals()
        result = self.collect()
        self.assertEqual(len(result["samples"]), 1)
        self.assertEqual(result["saved_interval_metrics"]["groups"]["unknown"]["sample_count"], 1)

    def test_saved_heuristic_and_empirical_sources_are_assessed_separately(self):
        empirical = dict(status="calibrated", audit=dict(evidence_kind="prospective"))
        heuristic = dict(status="fallback")
        self.run_row(rows=[prediction("e", components=dict(uncertainty=dict(calibration=empirical))),
                           prediction("h", points_lower80=0, points_upper80=40,
                                      components=dict(uncertainty=dict(calibration=heuristic)))])
        self.finals([actual("e"), actual("h")])
        groups = self.collect()["saved_interval_metrics"]["groups"]
        self.assertEqual(groups["prospective"]["mean_width"], 6)
        self.assertEqual(groups["heuristic"]["mean_width"], 40)


if __name__ == "__main__":
    unittest.main()
