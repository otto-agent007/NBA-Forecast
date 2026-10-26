"""Shared definitions for the heuristic 80% scoring interval.

The interval family names how ``points_sd`` is built. Calibration pools
standardized residuals across every model version that shares the family, so a
new model key refines the empirical width instead of restarting its sample.
"""

INTERVAL_FAMILY = "heuristic-binomial-excess-minutes-1"
LEGACY_METHOD = "uncalibrated_heuristic_binomial_plus_excess_residual_plus_minutes"

# Half-width of the 80% interval in sd units while no calibration artifact is
# ready. The normal 1.2816 covered 63% of 903 prospective played outcomes
# (October 22-23 2026, versions 0.2.1-0.4.2; per-version 80% quantiles 1.60 to
# 1.78); their pooled 80% quantile was 1.70. The 4,024-sample retrospective
# reconstruction gave 1.88. This is an empirical default, not a guarantee.
HEURISTIC_Z80 = 1.70
HEURISTIC_Z80_BASIS = ("pooled 80% quantile of |actual - mean| / sd over 903 prospective played "
                       "outcomes through 2026-10-23; a ready calibration artifact supersedes it")


def interval_family_of(prediction):
    """The interval family a saved prediction's sd belongs to, or None if unknown.

    Predictions saved before the family was recorded carry the heuristic method
    string instead; they built ``points_sd`` the same way.
    """
    try:
        uncertainty = prediction.get("components", {}).get("uncertainty") or {}
    except AttributeError:
        return None
    if not isinstance(uncertainty, dict):
        return None
    family = uncertainty.get("interval_family")
    if isinstance(family, str) and family:
        return family
    return INTERVAL_FAMILY if uncertainty.get("method") == LEGACY_METHOD else None
