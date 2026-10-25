"""Empirical widths must use eligible residuals, never holdout outcomes."""
import copy
import json
import unittest

try:
    from nba_forecast.calibration import (
        fit_calibration, calibrated_interval, score_calibration,
    )
except ModuleNotFoundError:
    fit_calibration = calibrated_interval = score_calibration = None


MODEL = "sha256:configuration-v1"
CUTOFF = "2026-01-10T12:00:00Z"


def residual(index, error=1, **changes):
    row = {
        "model_key": MODEL, "game_id": f"g{index}", "player_id": "p",
        "points_mean": 20, "points_sd": 2, "actual_points": 20 + 2 * error,
        "game_time": "2026-01-05T20:00:00Z",
        "actual_known_at": "2026-01-05T23:00:00Z",
        "forecast_created_at": "2026-01-05T19:00:00Z",
        "evidence_kind": "prospective",
    }
    row.update(changes)
    return row


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(fit_calibration), "calibration helper must exist")

    def fit(self, rows=None, **options):
        return fit_calibration(rows if rows is not None else [residual(i, i + 1) for i in range(9)],
                               model_key=MODEL, as_of=CUTOFF, minimum_samples=3, **options)

    def test_finite_sample_rank_and_width_are_empirical(self):
        artifact = self.fit()
        self.assertEqual(artifact["status"], "ready")
        self.assertEqual(artifact["quantile"], 8)
        self.assertEqual(artifact["quantile_rank"], 8)
        self.assertEqual(artifact["sample_count"], 9)
        self.assertAlmostEqual(artifact["fitting_metrics"]["coverage"], 8 / 9)
        # Each tail at alpha/2: signed residuals 1..9 give ranks 1 and 9, so the
        # interval sits entirely above the mean, as the outcomes did.
        self.assertEqual((artifact["lower_quantile"], artifact["upper_quantile"]), (1, 9))
        self.assertEqual((artifact["lower_rank"], artifact["upper_rank"]), (1, 9))
        interval = calibrated_interval(20, 2, artifact, model_key=MODEL, as_of=CUTOFF)
        self.assertEqual(interval["status"], "calibrated")
        self.assertEqual(interval["interval"], [20, 38])
        self.assertEqual(interval["audit"]["method"], "two_tailed_signed_standardized_residual_quantiles")
        self.assertIn("time", interval["audit"]["coverage_caveat"])
        legacy = {k: v for k, v in artifact.items() if k not in ("lower_quantile", "upper_quantile", "lower_rank", "upper_rank")}
        self.assertEqual(calibrated_interval(20, 2, legacy, model_key=MODEL, as_of=CUTOFF)["interval"], [4, 36])
        tampered = dict(artifact, lower_quantile=10)
        self.assertEqual(calibrated_interval(20, 2, tampered, model_key=MODEL, as_of=CUTOFF)["status"], "fallback")
        json.dumps(artifact, allow_nan=False)

    def test_minute_buckets_get_their_own_tails_once_they_have_enough_outcomes(self):
        # Bench rows miss high, starters miss low; a sparse bucket falls back to the pool.
        rows = [residual(i, error=e, expected_minutes=6) for i, e in enumerate([0, 1, 2, 3, 4, 5, 6, 7, 8])]
        rows += [residual(100 + i, error=-e, expected_minutes=34) for i, e in enumerate([0, 1, 2, 3, 4, 5, 6, 7, 8])]
        rows += [residual(200, error=0, expected_minutes=24)]
        artifact = fit_calibration(rows, model_key=MODEL, as_of=CUTOFF, minimum_samples=5)
        self.assertEqual(sorted(artifact["minute_buckets"]), ["0-10", "30+"])
        self.assertEqual((artifact["minute_buckets"]["0-10"]["lower_quantile"],
                          artifact["minute_buckets"]["0-10"]["upper_quantile"]), (0, 8))
        bench = calibrated_interval(10, 2, artifact, model_key=MODEL, as_of=CUTOFF, expected_minutes=6)
        starter = calibrated_interval(30, 2, artifact, model_key=MODEL, as_of=CUTOFF, expected_minutes=34)
        pooled = calibrated_interval(20, 2, artifact, model_key=MODEL, as_of=CUTOFF, expected_minutes=24)
        self.assertEqual(bench["interval"], [10, 26])
        self.assertEqual(starter["interval"], [14, 30])
        self.assertEqual((bench["audit"]["minute_bucket"], pooled["audit"]["minute_bucket"]), ("0-10", None))
        self.assertEqual(pooled["interval"], [20 + 2 * artifact["lower_quantile"], 20 + 2 * artifact["upper_quantile"]])
        broken = json.loads(json.dumps(artifact))
        broken["minute_buckets"]["0-10"]["lower_rank"] = 2
        self.assertEqual(calibrated_interval(10, 2, broken, model_key=MODEL, as_of=CUTOFF)["status"], "fallback")

    def test_abs_residuals_and_nonnegative_bounds(self):
        artifact = self.fit([residual(0, -4), residual(1, 2), residual(2, 1), residual(3, 3)])
        self.assertEqual(artifact["quantile"], 4)
        self.assertEqual(calibrated_interval(1, 2, artifact, model_key=MODEL,
                                             as_of=CUTOFF)["interval"], [0, 9])

    def test_no_gaussian_width_substitution_for_wide_or_narrow_evidence(self):
        for error, expected in [(0.1, [19.8, 20.2]), (5, [10, 30])]:
            with self.subTest(error=error):
                artifact = self.fit([residual(i, error) for i in range(4)])
                result = calibrated_interval(20, 2, artifact, model_key=MODEL, as_of=CUTOFF)
                for actual, wanted in zip(result["interval"], expected):
                    self.assertAlmostEqual(actual, wanted)

    def test_sparse_or_unattainable_quantile_is_explicit_fallback(self):
        artifact = fit_calibration([residual(0)], model_key=MODEL, as_of=CUTOFF)
        self.assertEqual(artifact["status"], "not_ready")
        self.assertIsNone(artifact["quantile"])
        result = calibrated_interval(20, 2, artifact, model_key=MODEL, as_of=CUTOFF)
        self.assertEqual(result["status"], "fallback")
        self.assertIsNone(result["interval"])
        artifact = self.fit([residual(i) for i in range(3)], alpha=.01)
        self.assertEqual(artifact["status"], "not_ready")

    def test_time_leaks_and_model_mismatch_are_excluded(self):
        invalid = [
            residual(20, actual_known_at="2026-01-11T00:00:00Z"),
            residual(21, game_time="2026-01-11T00:00:00Z"),
            residual(22, actual_known_at="2026-01-05T19:59:59Z"),
            residual(23, forecast_created_at="2026-01-05T20:00:00Z"),
            residual(24, model_key="different-config"),
            residual(25, actual_known_at="2026-01-05T23:00:00"),
        ]
        artifact = self.fit([residual(i) for i in range(4)] + invalid)
        self.assertEqual(artifact["sample_count"], 4)
        self.assertEqual(sum(artifact["exclusions"].values()), 6)
        self.assertEqual(artifact["quantile"], 1)

    def test_equivalent_timezone_instants_and_cutoff_boundary(self):
        rows = [residual(i, actual_known_at="2026-01-10T04:00:00-08:00") for i in range(4)]
        self.assertEqual(self.fit(rows)["sample_count"], 4)

    def test_duplicate_game_player_does_not_inflate_evidence(self):
        rows = [residual(i) for i in range(4)]
        artifact = self.fit(rows + [copy.deepcopy(rows[0]), residual(0, player_id="other")])
        self.assertEqual(artifact["sample_count"], 5)
        self.assertEqual(artifact["exclusions"]["duplicate"], 1)

    def test_invalid_numeric_rows_cannot_poison_artifact(self):
        bad = []
        for field in ["points_mean", "points_sd", "actual_points"]:
            for value in [None, float("nan"), float("inf"), -1, True, "20"]:
                bad.append(residual(len(bad) + 20, **{field: value}))
        bad.extend([residual(100, points_sd=0), None, {"model_key": MODEL}])
        artifact = self.fit([residual(i) for i in range(4)] + bad)
        self.assertEqual(artifact["sample_count"], 4)
        self.assertEqual(sum(artifact["exclusions"].values()), len(bad))
        json.dumps(artifact, allow_nan=False)

    def test_invalid_forecast_mean_or_sd_returns_explicit_fallback(self):
        artifact = self.fit()
        for mean, sd in [(float("nan"), 2), (20, 0), (20, -1), (-1, 2),
                         (20, float("inf")), (True, 2), (20, "2")]:
            with self.subTest(mean=mean, sd=sd):
                result = calibrated_interval(mean, sd, artifact, model_key=MODEL, as_of=CUTOFF)
                self.assertEqual(result["status"], "fallback")
                self.assertIsNone(result["interval"])

    def test_artifact_model_or_future_cutoff_cannot_be_used(self):
        artifact = self.fit()
        for key, as_of in [("other", CUTOFF), (MODEL, "2026-01-09T00:00:00Z")]:
            result = calibrated_interval(20, 2, artifact, model_key=key, as_of=as_of)
            self.assertEqual(result["status"], "fallback")
        artifact["latest_actual_known_at"] = "2026-01-11T00:00:00Z"
        self.assertEqual(calibrated_interval(20, 2, artifact, model_key=MODEL,
                                             as_of=CUTOFF)["status"], "fallback")

    def test_reconstruction_requires_origin_and_feature_cutoff(self):
        rows = [residual(i, evidence_kind="retrospective_chronological_reconstruction",
                         reconstruction_origin="archived-final chronological replay",
                         feature_as_of="2026-01-05T19:00:00Z",
                         forecast_created_at="2026-01-10T00:00:00Z") for i in range(4)]
        original = copy.deepcopy(rows)
        artifact = self.fit(rows)
        self.assertEqual(artifact["status"], "ready")
        self.assertEqual(artifact["label"], "empirical retrospective")
        self.assertEqual(rows, original)
        self.assertEqual(artifact["latest_actual_known_at"], "2026-01-05T23:00:00Z")
        del rows[0]["reconstruction_origin"]
        rows[1]["feature_as_of"] = "2026-01-05T21:00:00Z"
        self.assertEqual(self.fit(rows)["status"], "not_ready")

    def test_mixed_reconstruction_and_prospective_evidence_is_not_pooled(self):
        rows = [residual(i) for i in range(4)]
        rows.append(residual(20, evidence_kind="retrospective_chronological_reconstruction",
                             reconstruction_origin="chronological replay",
                             feature_as_of="2026-01-05T19:00:00Z"))
        artifact = self.fit(rows)
        self.assertEqual(artifact["status"], "not_ready")
        self.assertEqual(artifact["reason"], "mixed_evidence_kinds")

    def test_holdout_scoring_does_not_refit_or_count_training_rows(self):
        artifact = self.fit([residual(i) for i in range(4)])
        untouched = copy.deepcopy(artifact)
        holdout = [residual(100, 1, game_time="2026-01-11T20:00:00Z",
                            forecast_created_at="2026-01-11T19:00:00Z",
                            actual_known_at="2026-01-11T23:00:00Z"),
                   residual(101, 5, game_time="2026-01-12T20:00:00Z",
                            forecast_created_at="2026-01-12T19:00:00Z",
                            actual_known_at="2026-01-12T23:00:00Z")]
        result = score_calibration(holdout + [residual(0)], artifact,
                                   model_key=MODEL, as_of="2026-01-13T00:00:00Z")
        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["coverage"], .5)
        self.assertEqual(result["mean_width"], 4)
        self.assertEqual(artifact, untouched)
        self.assertEqual(artifact["fitting_metrics"]["coverage"], 1)

    def test_reconstruction_holdout_is_later_than_fit_games_not_archive_capture(self):
        rows = [residual(i, evidence_kind="retrospective_chronological_reconstruction",
                         reconstruction_origin="chronological replay",
                         feature_as_of="2026-01-05T20:00:00Z",
                         actual_known_at="2026-01-10T10:00:00Z") for i in range(4)]
        artifact = self.fit(rows)
        heldout = residual(99, evidence_kind="retrospective_chronological_reconstruction",
                           reconstruction_origin="chronological replay",
                           feature_as_of="2026-01-06T20:00:00Z",
                           game_time="2026-01-06T20:00:00Z",
                           actual_known_at="2026-01-10T10:00:00Z")
        result = score_calibration([heldout], artifact, model_key=MODEL, as_of=CUTOFF)
        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["coverage"], 1)
        self.assertEqual(result["label"], "empirical retrospective holdout")

    def test_latest_known_metadata_compares_actual_instants_with_subseconds(self):
        rows = [residual(i, actual_known_at="2026-01-05T23:00:00Z") for i in range(4)]
        rows[0]["actual_known_at"] = "2026-01-05T23:00:00.500Z"
        artifact = self.fit(rows)
        self.assertEqual(artifact["latest_actual_known_at"], "2026-01-05T23:00:00.500000Z")

    def test_malformed_artifact_does_not_raise_in_forecast_path(self):
        artifact = self.fit()
        del artifact["label"]
        result = calibrated_interval(20, 2, artifact, model_key=MODEL, as_of=CUTOFF)
        self.assertEqual(result["status"], "fallback")

    def test_invalid_fit_options_and_holdout_samples_fail_safely(self):
        for options in [{"alpha": float("nan")}, {"alpha": 0}, {"minimum_samples": 0}]:
            artifact = fit_calibration([], model_key=MODEL, as_of=CUTOFF, **options)
            self.assertEqual(artifact["status"], "not_ready")
            json.dumps(artifact, allow_nan=False)
        result = score_calibration(None, self.fit(), model_key=MODEL, as_of=CUTOFF)
        self.assertEqual(result["status"], "not_ready")

    def test_prospective_coverage_requires_artifact_available_at_forecast_time(self):
        artifact = self.fit()
        heldout = residual(99, game_time="2026-01-06T20:00:00Z",
                           forecast_created_at="2026-01-06T19:00:00Z",
                           actual_known_at="2026-01-06T23:00:00Z")
        result = score_calibration([heldout], artifact, model_key=MODEL, as_of=CUTOFF)
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["sample_count"], 0)
        self.assertEqual(result["exclusions"]["artifact_not_available_at_forecast"], 1)


if __name__ == "__main__":
    unittest.main()
