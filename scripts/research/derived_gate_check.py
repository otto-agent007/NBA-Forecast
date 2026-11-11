"""On a production copy: the branch's interval-calibration fit (sidecar) and its activation gate (direct re-fit) agree,
and the margin sample collection through the sidecar equals the direct ledger read (PR #36 parameter family); what each costs.

    PYTHONPATH=<this checkout> python3 scripts/research/derived_gate_check.py <copy> <now-iso>

Writes only the branch's sidecar next to the copy's ledger; never point it at production.
"""
import json, sys, time
from nba_forecast.enhancements import load_active
from nba_forecast.intervals import INTERVAL_FAMILY
from nba_forecast.prospective_calibration import fit_prospective, verify_prospective_sources
from nba_forecast.prospective_margin_calibration import collect_margin_samples
from nba_forecast.margins import CALIBRATION_FAMILY
import nba_forecast.runner_release as rr
folder, now = sys.argv[1], sys.argv[2]
pointer = json.load(open(f'{folder}/model-upgrade-active.json'))
artifact = json.load(open(f'{folder}/model-artifacts/{pointer["artifact_sha256"]}.json'))
rr.package_digest = lambda package: artifact.get('runner_package_sha256')  # accept the copy's artifact from this tree
active, _ = load_active(folder, now)
t = time.monotonic(); fit = fit_prospective(folder, active['model_key'], now, interval_family=INTERVAL_FAMILY); t1 = time.monotonic()
ok = verify_prospective_sources(folder, fit, now) if fit['status'] == 'ready' else None; t2 = time.monotonic()
print(json.dumps(dict(status=fit['status'], samples=fit.get('sample_count'), games=fit.get('game_count'), gate_passes=ok,
                      fit_s=round(t1 - t, 1), gate_s=round(t2 - t1, 1))))
t = time.monotonic(); cached = collect_margin_samples(folder, CALIBRATION_FAMILY, now); t3 = time.monotonic()
direct = collect_margin_samples(folder, CALIBRATION_FAMILY, now, direct=True); t4 = time.monotonic()
print(json.dumps(dict(margin_family=CALIBRATION_FAMILY, samples=len(cached['samples']), counts=cached['counts'],
                      exclusions=cached['exclusions'], errors=len(cached['errors']))))
print(json.dumps(dict(margin_sidecar_equals_direct=cached == direct, sidecar_s=round(t3 - t, 1), direct_s=round(t4 - t3, 1))))
