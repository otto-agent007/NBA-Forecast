"""Cutoff-bound empirical residual intervals; no time-series coverage guarantee.

``model_key`` must fingerprint the entire model configuration and training
cutoff. Reconstruction rows additionally require ``reconstruction_origin`` and
``feature_as_of`` (at or before the target game). Their real capture timestamps
may be after the game: they are never relabelled as prospective forecasts.

Fitting uses only the supplied calibration partition. ``score_calibration``
scores later outcomes without modifying the fitted width. Callers own the
chronological partition and retain their explicitly labelled heuristic interval
whenever this module returns a fallback with ``interval=None``.
"""
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import math
from numbers import Real


PROSPECTIVE = "prospective"
RETROSPECTIVE = "retrospective_chronological_reconstruction"
_KINDS = {PROSPECTIVE, RETROSPECTIVE}
_CAVEAT = ("Observed empirical coverage only. Correlation and time dependence "
           "prevent an unconditional coverage guarantee for future games.")


def _time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an aware ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def _number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real):
        return False
    try:
        return math.isfinite(value) and (value > 0 if positive else value >= 0)
    except (TypeError, OverflowError, ValueError):
        return False


def _key(row):
    return json.dumps([row["game_id"], row.get("player_id")], separators=(",", ":"))


def _eligible(samples, model_key, cutoff, interval_family=None):
    """Return validated copies and exclusion counts; never mutate source rows.

    With ``interval_family``, rows from any model key whose sd was built the
    same way are pooled; without it, only the exact model key is accepted.
    """
    accepted, excluded, seen = [], Counter(), set()
    for row in samples:
        if not isinstance(row, Mapping):
            excluded["malformed"] += 1
            continue
        if interval_family is not None:
            if row.get("interval_family") != interval_family:
                excluded["interval_family_mismatch"] += 1
                continue
        elif row.get("model_key") != model_key:
            excluded["model_mismatch"] += 1
            continue
        if not isinstance(row.get("game_id"), str) or not row["game_id"].strip():
            excluded["malformed"] += 1
            continue
        if row.get("player_id") is not None and not isinstance(row["player_id"], str):
            excluded["malformed"] += 1
            continue
        kind = row.get("evidence_kind")
        if not isinstance(kind, str) or kind not in _KINDS:
            excluded["invalid_evidence_kind"] += 1
            continue
        try:
            game_time, actual_known_at = _time(row["game_time"]), _time(row["actual_known_at"])
            if game_time > cutoff or actual_known_at > cutoff:
                excluded["future_known"] += 1
                continue
            if actual_known_at <= game_time:
                excluded["invalid_actual_chronology"] += 1
                continue
            if kind == PROSPECTIVE:
                if _time(row["forecast_created_at"]) >= game_time:
                    excluded["not_pregame_forecast"] += 1
                    continue
            else:
                if not isinstance(row.get("reconstruction_origin"), str) or not row["reconstruction_origin"].strip():
                    excluded["missing_reconstruction_origin"] += 1
                    continue
                if _time(row["feature_as_of"]) > game_time:
                    excluded["future_features"] += 1
                    continue
                # Optional capture time is the actual reconstruction time, not a
                # fictional pregame forecast. Validate, but never backdate it.
                if "forecast_created_at" in row:
                    _time(row["forecast_created_at"])
        except (KeyError, TypeError, ValueError, OverflowError):
            excluded["malformed_timestamp"] += 1
            continue
        if not (_number(row.get("points_mean")) and _number(row.get("points_sd"), positive=True)
                and _number(row.get("actual_points"))):
            excluded["invalid_numeric"] += 1
            continue
        try:
            residual = abs(row["actual_points"] - row["points_mean"]) / row["points_sd"]
        except (OverflowError, ZeroDivisionError):
            residual = float("inf")
        if not math.isfinite(residual):
            excluded["invalid_numeric"] += 1
            continue
        key = _key(row)
        if key in seen:
            excluded["duplicate"] += 1
            continue
        seen.add(key)
        accepted.append({**row, "standardized_residual": residual,
                         "game_time": _iso(game_time), "actual_known_at": _iso(actual_known_at),
                         "sample_key": key})
    return accepted, dict(excluded)


def _label(kind):
    return "empirical retrospective" if kind == RETROSPECTIVE else "empirical prospective residuals"


def fit_calibration(samples, *, model_key, as_of, alpha=.2, minimum_samples=100, interval_family=None):
    """Fit a finite-sample absolute standardized residual quantile.

    Mixed evidence kinds are explicitly not ready: callers fit separate
    artifacts. The conformal rank is retained as an empirical convention,
    without assuming exchangeability for basketball time series. With
    ``interval_family`` the residuals pool across model keys that share it;
    the artifact is still installed for, and bound to, ``model_key``.
    """
    artifact = {
        "schema_version": 1, "status": "not_ready", "model_key": model_key,
        "training_cutoff": as_of, "alpha": alpha, "minimum_samples": minimum_samples,
        "sample_count": 0, "quantile": None, "quantile_rank": None,
        "evidence_kind": None, "label": "heuristic fallback required",
        "fitting_metrics": None, "exclusions": {}, "coverage_caveat": _CAVEAT,
        "interval_family": interval_family,
        "model_binding": ("model_key is the configuration this artifact is installed for; residuals pool "
                          "across model keys sharing interval_family" if interval_family else
                          "model_key fingerprints model configuration and its training cutoff"),
    }
    if (not isinstance(model_key, str) or not model_key.strip()
            or not _number(alpha, positive=True) or alpha >= 1
            or isinstance(minimum_samples, bool) or not isinstance(minimum_samples, int)
            or minimum_samples < 1):
        # Do not let nonfinite caller options leak into a JSON artifact.
        artifact.update(reason="invalid_parameters", alpha=None, minimum_samples=None,
                        model_key=model_key if isinstance(model_key, str) else None)
        return artifact
    try:
        cutoff = _time(as_of)
    except (TypeError, ValueError, OverflowError):
        artifact.update(reason="invalid_as_of", training_cutoff=None)
        return artifact
    artifact["training_cutoff"] = _iso(cutoff)
    try:
        rows, excluded = _eligible(iter(samples), model_key, cutoff, interval_family)
    except TypeError:
        artifact["reason"] = "malformed_samples"
        return artifact
    artifact.update(sample_count=len(rows), exclusions=excluded)
    if interval_family is not None:
        artifact["pooled_model_keys"] = sorted({str(row.get("model_key")) for row in rows})
    counts = Counter(row["evidence_kind"] for row in rows)
    artifact["evidence_counts"] = dict(counts)
    if len(counts) > 1:
        artifact["reason"] = "mixed_evidence_kinds"
        return artifact
    kind = next(iter(counts), None)
    artifact.update(evidence_kind=kind, label=_label(kind) if kind else "heuristic fallback required")
    if rows:
        artifact.update(
            earliest_game_time=min((row["game_time"] for row in rows), key=_time),
            latest_game_time=max((row["game_time"] for row in rows), key=_time),
            latest_actual_known_at=max((row["actual_known_at"] for row in rows), key=_time),
            sample_keys=sorted(row["sample_key"] for row in rows),
            reconstruction_origins=sorted({row["reconstruction_origin"] for row in rows
                                            if row["evidence_kind"] == RETROSPECTIVE}),
        )
        # Bind retained residual evidence, including honest capture provenance.
        canonical = json.dumps(sorted(rows, key=lambda row: row["sample_key"]),
                               sort_keys=True, separators=(",", ":"), default=str)
        artifact["residual_evidence_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    if len(rows) < minimum_samples:
        artifact["reason"] = "insufficient_samples"
        return artifact
    rank = math.ceil((len(rows) + 1) * (1 - alpha))
    artifact["quantile_rank"] = rank
    if rank > len(rows):
        artifact["reason"] = "finite_sample_rank_exceeds_sample"
        return artifact
    quantile = sorted(row["standardized_residual"] for row in rows)[rank - 1]
    artifact.update(status="ready", reason=None, quantile=quantile,
                    fitting_metrics={"sample_count": len(rows),
                                     "coverage": sum(row["standardized_residual"] <= quantile for row in rows) / len(rows),
                                     "partition": "fitting; not holdout"})
    return artifact


def _artifact_error(artifact, model_key, as_of):
    if not isinstance(artifact, Mapping) or artifact.get("status") != "ready":
        return "artifact_not_ready"
    if artifact.get("model_key") != model_key:
        return "model_mismatch"
    try:
        cutoff, now = _time(artifact["training_cutoff"]), _time(as_of)
        latest_known, latest_game = _time(artifact["latest_actual_known_at"]), _time(artifact["latest_game_time"])
        if cutoff > now or latest_known > cutoff or latest_game >= latest_known:
            return "future_known_artifact"
        count, minimum = artifact["sample_count"], artifact["minimum_samples"]
        alpha = artifact["alpha"]
        if (isinstance(count, bool) or not isinstance(count, int)
                or isinstance(minimum, bool) or not isinstance(minimum, int)
                or minimum < 1 or count < minimum
                or not _number(alpha, positive=True) or alpha >= 1
                or artifact["quantile_rank"] != math.ceil((count + 1) * (1 - alpha))
                or artifact["quantile_rank"] > count
                or len(set(artifact["sample_keys"])) != count
                or artifact["evidence_kind"] not in _KINDS
                or not isinstance(artifact["label"], str)
                or not _number(artifact["quantile"])):
            return "invalid_artifact"
    except (KeyError, ValueError, TypeError, OverflowError):
        return "invalid_artifact"
    return None


def calibrated_interval(mean, sd, artifact, *, model_key, as_of, interval_family=None):
    """Return an empirical interval and audit, or an explicit caller fallback.

    A pooled artifact applies only to an sd built the same way, so a caller
    that names its interval family is refused when the artifact's differs.
    """
    error = _artifact_error(artifact, model_key, as_of)
    if (not error and interval_family is not None and artifact.get("interval_family") is not None
            and artifact["interval_family"] != interval_family):
        error = "interval_family_mismatch"
    if not (_number(mean) and _number(sd, positive=True)):
        error = "invalid_forecast_mean_or_sd"
    if error:
        return {"status": "fallback", "interval": None, "reason": error,
                "audit": {"method": "caller_heuristic_required", "coverage_caveat": _CAVEAT}}
    width = sd * artifact["quantile"]
    lower, upper = max(0.0, mean - width), mean + width
    if not math.isfinite(width) or not math.isfinite(upper):
        return {"status": "fallback", "interval": None, "reason": "nonfinite_interval",
                "audit": {"method": "caller_heuristic_required", "coverage_caveat": _CAVEAT}}
    return {
        "status": "calibrated", "interval": [lower, upper], "reason": None,
        "audit": {"method": "absolute_standardized_residual_quantile",
                  "label": artifact["label"], "evidence_kind": artifact["evidence_kind"],
                  "model_key": model_key, "interval_family": artifact.get("interval_family"),
                  "training_cutoff": artifact["training_cutoff"],
                  "sample_count": artifact["sample_count"], "alpha": artifact["alpha"],
                  "quantile": artifact["quantile"], "coverage_caveat": _CAVEAT},
    }


def score_calibration(samples, artifact, *, model_key, as_of, interval_family=None):
    """Assess fixed widths on strictly later, separate holdout outcomes.

    The score's evidence kind describes its evaluation rows, independently of
    the artifact's fitting evidence. A mixed evaluation is rejected rather than
    combining retrospective and prospective coverage.
    """
    error = _artifact_error(artifact, model_key, as_of)
    if error:
        return {"status": "not_ready", "reason": error, "sample_count": 0}
    try:
        rows, excluded = _eligible(iter(samples), model_key, _time(as_of), interval_family)
    except TypeError:
        return {"status": "not_ready", "reason": "malformed_samples", "sample_count": 0}
    # Source finals can be captured today for games long past. Chronological
    # separation uses the last fitting game, not their honest archive cutoff.
    cutoff, training_keys = _time(artifact["latest_game_time"]), set(artifact["sample_keys"])
    evidence_cutoff = _time(artifact["training_cutoff"])
    holdout = []
    for row in rows:
        if row["sample_key"] in training_keys or _time(row["game_time"]) <= cutoff:
            excluded["not_separate_chronological_holdout"] = excluded.get("not_separate_chronological_holdout", 0) + 1
        elif row["evidence_kind"] == PROSPECTIVE and _time(row["forecast_created_at"]) < evidence_cutoff:
            excluded["artifact_not_available_at_forecast"] = excluded.get("artifact_not_available_at_forecast", 0) + 1
        else:
            holdout.append(row)
    counts = Counter(row["evidence_kind"] for row in holdout)
    result = {"status": "not_ready", "sample_count": len(holdout), "coverage": None,
              "mean_width": None, "exclusions": excluded, "evidence_counts": dict(counts),
              "partition": "untouched chronological holdout", "coverage_caveat": _CAVEAT,
              "artifact_evidence_kind": artifact["evidence_kind"]}
    if len(counts) > 1:
        result["reason"] = "mixed_evidence_kinds"
        return result
    if not holdout:
        result["reason"] = "no_eligible_holdout"
        return result
    covered, widths = [], []
    for row in holdout:
        interval = calibrated_interval(row["points_mean"], row["points_sd"], artifact,
                                       model_key=model_key, as_of=as_of)["interval"]
        if interval is None:
            result.update(reason="nonfinite_interval", sample_count=0)
            return result
        covered.append(interval[0] <= row["actual_points"] <= interval[1])
        widths.append(interval[1] - interval[0])
    kind = next(iter(counts))
    result.update(status="scored", reason=None, evidence_kind=kind,
                  label="prospectively assessed" if kind == PROSPECTIVE else "empirical retrospective holdout",
                  coverage=sum(covered) / len(covered),
                  mean_width=sum(width / len(widths) for width in widths))
    return result
