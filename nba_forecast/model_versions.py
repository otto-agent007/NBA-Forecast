"""Explicit opt-in versions for reproducible historical model execution."""

LEGACY_ENHANCED_VERSION = 'regular-season-baseline-0.5.0'
REFINED_VERSION = 'regular-season-baseline-0.6.0'
ENHANCED_VERSIONS = (LEGACY_ENHANCED_VERSION, REFINED_VERSION)


def uses_refinements(snapshot):
    return snapshot.get('model_enhancements', {}).get('model_version') == REFINED_VERSION
