"""Read-only prospective residual collection and candidate interval fitting.

Select the latest live league prediction per game/player and exact model key
before opening any actuals. Withheld rows block older numeric projections.
Only genuine ledger creation times establish pregame provenance. No historical
input copies are loaded, forecasts rewritten, or model configuration activated.
"""
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from .calibration import PROSPECTIVE, fit_calibration
from .intervals import interval_family_of
from .validation import parse_time, validate_history


_POPULATION = "projected forecasts conditional on actual played status and positive observed minutes"


def _number(value, positive=False):
    try:
        return (not isinstance(value, bool) and isinstance(value, (float, int))
                and math.isfinite(value) and (value > 0 if positive else value >= 0))
    except OverflowError:
        return False


def _source_kind(prediction):
    components = prediction.get("components")
    if not isinstance(components, dict):
        return "unknown"
    uncertainty = components.get("uncertainty")
    if not isinstance(uncertainty, dict):
        return "unknown"
    calibration = uncertainty.get("calibration")
    if not isinstance(calibration, dict):
        return "unknown"
    if calibration.get("status") == "fallback":
        return "heuristic"
    if calibration.get("status") == "calibrated":
        audit = calibration.get("audit")
        evidence = audit.get("evidence_kind") if isinstance(audit, dict) else None
        if evidence in {PROSPECTIVE, "retrospective_chronological_reconstruction"}:
            return evidence
    return "unknown"


# What collect_samples reads from a selected prediction; ``components`` keeps only the
# uncertainty audit that interval_family_of and _source_kind read.
_FIELDS = ("status", "points_mean", "points_sd", "team_id", "start_time", "expected_minutes",
           "points_lower80", "points_upper80")
_CAUGHT = (ValueError, KeyError, TypeError, AttributeError)


def _compact(prediction):
    kept = {k: prediction[k] for k in _FIELDS if k in prediction}
    if "components" in prediction:
        components = prediction["components"]
        kept["components"] = ({"uncertainty": components["uncertainty"]} if "uncertainty" in components else {}) \
            if isinstance(components, dict) else components
    return kept


def run_ops(parsed):
    """What one live league run contributes to the selection, before the cutoff or model key is applied.

    ``parsed`` is a ``derived_cache.Parsed`` row. The fields follow the selector's
    own order: the timestamps, the creation-time and digest checks, the model key it
    is filtered on, then everything read after that filter.
    """
    row = parsed.row
    try:
        created, run_cutoff = parse_time(row["created_at"]), parse_time(row["as_of"])
    except _CAUGHT as exc:
        return dict(time_error=str(exc))
    try:
        if run_cutoff > created:
            raise ValueError("run cutoff follows its actual creation time")
        if parsed.digest("forecast") != row["forecast_sha256"]:
            raise ValueError("immutable forecast hash mismatch")
        forecast = parsed.forecast()
    except _CAUGHT as exc:
        return dict(time_error=None, pre_error=str(exc))
    try:
        model_key = dict(value=forecast.get("model_key"))
    except _CAUGHT as exc:
        model_key = dict(error=str(exc))
    post = dict(off_slate=0, not_pregame=0, error=None, forecast_sha256=row["forecast_sha256"])
    try:
        post["run_key"] = forecast.get("model_key") or forecast.get("model_version")
        if parse_time(forecast["as_of"]) != run_cutoff:
            raise ValueError("forecast and ledger cutoffs disagree")
        season = forecast["season"]
        if not isinstance(season, str):
            raise ValueError("forecast season is malformed")
        predictions = forecast["predictions"]
        if not isinstance(predictions, list):
            raise ValueError("forecast predictions must be a list")
        run_keys = set()
        entries = []
        for prediction in predictions:
            if prediction.get("game_id") is None:
                post["off_slate"] += 1
                continue
            gid, pid = prediction["game_id"], prediction["player_id"]
            if not all(isinstance(value, str) and value for value in (gid, pid, prediction["team_id"])):
                raise ValueError("forecast game/player/team identity is malformed")
            if Path(gid).name != gid or gid in {".", ".."}:
                raise ValueError("forecast game ID cannot identify an archive file")
            key = (gid, pid)
            if key in run_keys:
                raise ValueError("duplicate forecast game/player identity")
            run_keys.add(key)
            start = parse_time(prediction["start_time"])
            if created >= start:
                post["not_pregame"] += 1
                continue
            entries.append([gid, pid, _compact(prediction)])
        post.update(season=season, entries=entries)
    except _CAUGHT as exc:
        post["error"] = str(exc)
    return dict(time_error=None, pre_error=None, model_key=model_key, post=post)


def _selected_forecasts(db, model_key, cutoff, counts, excluded, errors, interval_family=None, direct=False):
    """Latest pregame prediction per game/player.

    With ``interval_family`` every live run whose predictions built their sd
    that way is eligible, whatever its model key; otherwise only runs of the
    exact ``model_key`` are.

    Each run's contribution (``run_ops``) is read from the derived sidecar, which
    opens the ledger read-only and extracts each run once in its lifetime;
    ``direct`` (an activation) reads and verifies every run's bytes on this call.
    """
    from contextlib import closing
    from .derived_cache import direct_rows, open_cache
    selected = {}
    if direct:
        runs = direct_rows(db, "prospective")
    else:
        with closing(open_cache(db)) as cache:
            runs = cache.rows("prospective")
    for run in runs:
        if run["mode"] != "live" or run["scope"] != "league":
            continue
        counts["live_runs"] += 1
        ops = run["value"]
        if ops["time_error"] is not None:
            errors.append(dict(run_id=run["run_id"], reason=ops["time_error"]))
            continue
        created = parse_time(run["created_at"])
        if created > cutoff:
            excluded["future_run"] += 1
            continue
        if ops["pre_error"] is not None:
            errors.append(dict(run_id=run["run_id"], reason=ops["pre_error"]))
            continue
        if interval_family is None:
            if "error" in ops["model_key"]:
                errors.append(dict(run_id=run["run_id"], reason=ops["model_key"]["error"]))
                continue
            if ops["model_key"]["value"] != model_key:
                excluded["model_mismatch_run"] += 1
                continue
        counts["model_runs"] += 1
        post = ops["post"]
        if post["off_slate"]:
            excluded["off_slate"] += post["off_slate"]
        if post["not_pregame"]:
            excluded["not_pregame"] += post["not_pregame"]
        if post["error"] is not None:
            errors.append(dict(run_id=run["run_id"], reason=post["error"]))
            continue
        rank = (created, run["insertion_order"])
        for gid, pid, prediction in post["entries"]:
            key = (gid, pid)
            if key not in selected or rank > selected[key][0]:
                # Do not filter status or numeric support until latest
                # identity is chosen, independently of every outcome.
                selected[key] = (rank, dict(run_id=run["run_id"], model_key=post["run_key"],
                    forecast_created_at=run["created_at"], season=post["season"],
                    forecast_sha256=post["forecast_sha256"], prediction=prediction))
    counts["selected_predictions"] = len(selected)
    return {key: entry[1] for key, entry in selected.items()}


def collect_samples(directory, model_key, as_of, interval_family=None, *, direct=False):
    """Return prospective samples, exclusion audit and saved-interval scores.

    With ``interval_family`` the samples pool across model keys sharing that
    family; each sample records the model key of the run it came from.

    The archive layout is ``forecast.sqlite`` and ``finals/<game_id>.json``.
    Duplicate or malformed final identities reject that entire game; an outcome
    not yet known at ``as_of`` is excluded without changing its source clock.
    Fitting and saved-interval assessment both condition on actual participation
    with positive observed minutes, matching retrospective residual calibration.
    """
    if not isinstance(model_key, str) or not model_key.strip():
        raise ValueError("model_key must be a complete configuration fingerprint")
    cutoff = parse_time(as_of)
    root = Path(directory)
    counts = Counter(live_runs=0, model_runs=0, selected_predictions=0,
                     projected_predictions=0, matched_samples=0, games=0, actual_files=0)
    excluded, errors = Counter(), []
    result = dict(samples=[], counts={}, exclusions={}, errors=errors, population=_POPULATION,
                  saved_interval_metrics={
                      "basis": "original saved pregame intervals; never newly fitted widths",
                      "assessment": "prospective observations of saved intervals, grouped by interval fitting source",
                      "population": _POPULATION,
                      "groups": {}})
    db = root / "forecast.sqlite"
    if not db.exists():
        excluded["missing_ledger"] += 1
        result.update(counts=dict(counts), exclusions=dict(excluded))
        return result
    try:
        selected = _selected_forecasts(db, model_key, cutoff, counts, excluded, errors, interval_family, direct)
    except sqlite3.Error as exc:
        errors.append(dict(path=str(db), reason=str(exc)))
        result.update(counts=dict(counts), exclusions=dict(excluded))
        return result
    # This phase cannot change the selector above. In particular, numeric/status
    # failures here never search for a more favorable or supported older run.
    projected = defaultdict(list)
    for (gid, pid), saved in sorted(selected.items()):
        prediction = saved["prediction"]
        status = prediction.get("status")
        if status != "projected":
            excluded["status_" + str(status)] += 1
            continue
        counts["projected_predictions"] += 1
        # Selection above is by recency alone; a latest prediction whose sd was
        # built another way is excluded here, never replaced by an older run.
        if interval_family is not None and interval_family_of(prediction) != interval_family:
            excluded["interval_family_mismatch"] += 1
            continue
        if not (_number(prediction.get("points_mean")) and _number(prediction.get("points_sd"), positive=True)):
            excluded["invalid_mean_or_sd"] += 1
            continue
        projected[gid].append((pid, saved))
    groups = defaultdict(list)
    for gid, predictions in sorted(projected.items()):
        path = root / "finals" / (gid + ".json")
        if not path.exists():
            excluded["missing_final"] += len(predictions)
            continue
        try:
            raw = path.read_bytes()
            finals = json.loads(raw)
            validate_history(finals)
            if not finals or any(row["game_id"] != gid for row in finals):
                raise ValueError("final archive game identity mismatch")
            # validate_history rejects duplicate IDs; no dictionary overwrite
            # may silently choose among conflicting outcomes.
            actuals = {row["player_id"]: row for row in finals}
            actuals_sha256 = hashlib.sha256(raw).hexdigest()
            counts["actual_files"] += 1
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(dict(game_id=gid, path=str(path), reason=str(exc)))
            excluded["invalid_actual_archive"] += len(predictions)
            continue
        for pid, saved in predictions:
            prediction, actual = saved["prediction"], actuals.get(pid)
            if actual is None:
                excluded["missing_actual_player"] += 1
                continue
            if (actual["team_id"] != prediction["team_id"]
                    or actual["season"] != saved["season"]
                    or parse_time(actual["game_time"]) != parse_time(prediction["start_time"])):
                excluded["actual_identity_mismatch"] += 1
                continue
            if parse_time(actual["known_at"]) > cutoff:
                excluded["future_known_actual"] += 1
                continue
            if actual["status"] != "played" or actual["minutes"] <= 0:
                excluded["actual_nonparticipation"] += 1
                continue
            try:
                residual = abs(actual["pts"] - prediction["points_mean"]) / prediction["points_sd"]
            except (OverflowError, ZeroDivisionError):
                residual = float("inf")
            if not math.isfinite(residual):
                excluded["nonfinite_standardized_residual"] += 1
                continue
            sample = dict(model_key=saved["model_key"] if interval_family else model_key,
                          interval_family=interval_family, game_id=gid, player_id=pid,
                          team_id=prediction["team_id"], points_mean=prediction["points_mean"],
                          points_sd=prediction["points_sd"], expected_minutes=prediction.get("expected_minutes"),
                          actual_points=actual["pts"],
                          game_time=prediction["start_time"], actual_known_at=actual["known_at"],
                          forecast_created_at=saved["forecast_created_at"], evidence_kind=PROSPECTIVE,
                          run_id=saved["run_id"], forecast_sha256=saved["forecast_sha256"],
                          actuals_sha256=actuals_sha256, actual_source_url=actual["source_url"],
                          actual_status=actual["status"])
            result["samples"].append(sample)
            lower, upper = prediction.get("points_lower80"), prediction.get("points_upper80")
            if (_number(lower) and _number(upper)
                    and lower <= prediction["points_mean"] <= upper):
                kind = _source_kind(prediction)
                groups[kind].append((lower <= actual["pts"] <= upper, upper - lower))
            else:
                excluded["invalid_saved_interval"] += 1
    for kind, observations in sorted(groups.items()):
        n = len(observations)
        result["saved_interval_metrics"]["groups"][kind] = {
            "sample_count": n, "coverage": sum(covered for covered, width in observations) / n,
            "mean_width": sum(width / n for covered, width in observations),
            "interval_source_kind": kind, "partition": "original saved prospective intervals",
        }
    counts["matched_samples"] = len(result["samples"])
    counts["games"] = len({row["game_id"] for row in result["samples"]})
    result.update(counts=dict(counts), exclusions=dict(excluded))
    return result


def fit_prospective(directory, model_key, as_of, minimum_samples=200, minimum_games=20, interval_family=None, *, direct=False):
    """Fit a candidate artifact; frozen saved-interval assessment stays separate.

    Player observations within a game are dependent. The minimum game count is
    an additional readiness guard, not an independence or coverage guarantee.
    This function performs no publication, activation, or forecast writes.
    """
    if isinstance(minimum_games, bool) or not isinstance(minimum_games, int) or minimum_games < 1:
        raise ValueError("minimum_games must be a positive integer")
    collection = collect_samples(directory, model_key, as_of, interval_family, direct=direct)
    artifact = fit_calibration(collection["samples"], model_key=model_key, as_of=as_of,
                               minimum_samples=minimum_samples, interval_family=interval_family)
    artifact.update(game_count=collection["counts"]["games"], minimum_games=minimum_games,
                    evidence_kind=PROSPECTIVE, population=_POPULATION,
                    collection_counts=collection["counts"], collection_exclusions=collection["exclusions"],
                    collection_errors=collection["errors"],
                    saved_interval_metrics=collection["saved_interval_metrics"],
                    assessment_policy="fitting_metrics describe fitted residuals only; saved_interval_metrics assess original frozen intervals separately")
    if artifact["game_count"] < minimum_games:
        artifact.update(status="not_ready", reason="insufficient_games", quantile=None)
    return artifact


def verify_prospective_sources(directory, artifact, as_of):
    """Fit ``artifact`` again from the ledger and final bytes read and verified on this call, never the derived sidecar.

    An activation installs only a fit this reproduces exactly; it differs only when
    the sidecar's facts no longer describe the ledger's bytes.
    """
    try:
        again = fit_prospective(directory, artifact["model_key"], as_of, minimum_samples=artifact["minimum_samples"],
                                minimum_games=artifact["minimum_games"], interval_family=artifact.get("interval_family"),
                                direct=True)
    except (KeyError, TypeError, ValueError):
        return False
    encode = lambda value: json.dumps(value, sort_keys=True, default=str)
    return encode(again) == encode(artifact)
