"""Prepare and forecast saved snapshots on a data folder; print per-step timings and content digests.

Run once with main and once (cold, then warm) with the branch on the same folder; the digests must match.
Usage: PYTHONPATH=<repo> python3 tick_memo_prepare_identity.py <data-dir> <snapshot.json> [<snapshot.json> ...] [--clear-replay-cache]
Network is blocked. Digests cover the full prepared objects; preflight records its check times, so `preflight_stable` is its digest without them.
"""
import sys, json, time, hashlib, shutil
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent))
from tick_memo_offline import block_network, load_history
block_network()
from pathlib import Path
args = [a for a in sys.argv[1:] if not a.startswith('--')]
folder = Path(args[0]); snaps = args[1:]
if '--clear-replay-cache' in sys.argv:
    shutil.rmtree(folder/'cache'/'replay', ignore_errors=True)
pointer = json.loads((folder/'model-upgrade-active.json').read_text())
artifact = json.loads((folder/'model-artifacts'/(pointer['artifact_sha256']+'.json')).read_text())
import nba_forecast.runner_release as rr
rr.package_digest = lambda package: artifact.get('runner_package_sha256')
from nba_forecast.operations import _merge_finals
from nba_forecast.enhancements import load_active, prepare
from nba_forecast.shot_profiles import prepare_shot_profiles
from nba_forecast.model_versions import uses_refinements
from nba_forecast.profiles import eligible_history, build_profiles
from nba_forecast.preflight import run_preflight
from nba_forecast.model import forecast
from nba_forecast.schedule import canonical
D = lambda v: hashlib.sha256(canonical(v)).hexdigest()[:16]
TIME_KEYS = ('as_of', 'prepared_at', 'checked_at', 'retrieved_at', 'observed_at', 'generated_at', 'verified_at', 'computed_at')
def stable(value):
    """``value`` without wall-clock stamps (preflight records when it checked)."""
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if not any(t in k for t in TIME_KEYS)}
    if isinstance(value, list):
        return [stable(v) for v in value]
    if isinstance(value, str):
        # Error details name the data folder; main and the branch read differently named views of one copy.
        return value.replace(str(folder.resolve()), 'FOLDER').replace(str(folder), 'FOLDER')
    return value
T0 = time.monotonic()
history = _merge_finals(load_history(), folder/'finals', errors=[])
print('history rows', len(history), 'digest', D(history), '%.1fs' % (time.monotonic()-T0))
for path in snaps:
    snapshot = json.loads(Path(path).read_text())
    for k in ('model_enhancements', 'shot_profiles', 'role_context', 'shot_quality', 'preflight'):
        snapshot.pop(k, None)
    timings = {}
    def step(name, fn):
        w = time.monotonic(); v = fn(); timings[name] = round(time.monotonic()-w, 2); return v
    game = snapshot['games'][0]
    usable = step('eligible_history', lambda: eligible_history(snapshot, history))
    active, digest = load_active(folder, snapshot['as_of'])
    snapshot['model_enhancements'] = step('prepare', lambda: prepare(active, digest, game, snapshot['as_of'], directory=folder))
    rejected = []
    snapshot['shot_profiles'] = step('shot_profiles', lambda: prepare_shot_profiles(folder, snapshot, usable, rejected=rejected))
    if uses_refinements(snapshot):
        from nba_forecast.role_context import prepare_role_context
        from nba_forecast.shot_quality import prepare_shot_quality
        snapshot['role_context'] = step('role_context', lambda: prepare_role_context(folder, snapshot, usable, rejected=rejected))
        snapshot['shot_quality'] = step('shot_quality', lambda: prepare_shot_quality(folder, snapshot, usable, rejected=rejected))
    profiles = step('build_profiles', lambda: build_profiles(snapshot, usable))
    verification = step('preflight', lambda: run_preflight(snapshot, usable, profiles, folder))
    snapshot['preflight'] = verification
    profiles = step('build_profiles2', lambda: build_profiles(snapshot, usable))
    prediction = step('forecast', lambda: forecast(snapshot, usable))
    issues = [i.get("code") for i in verification.get("issues", [])]
    out = dict(issues=issues[:6], game=game['game_id'], preflight=verification['status'], rejected=len(rejected),
               digests={k: D(snapshot.get(k)) for k in ('model_enhancements', 'shot_profiles', 'role_context', 'shot_quality', 'preflight')},
               preflight_stable=D(stable(verification)), profiles=D(profiles), forecast=D(prediction), timings=timings, total=round(sum(timings.values()), 2))
    import os
    if os.environ.get('DUMP'):
        Path(os.environ['DUMP']).mkdir(parents=True, exist_ok=True)
        (Path(os.environ['DUMP'])/(game['game_id']+'.json')).write_text(json.dumps(stable(verification), sort_keys=True, indent=1))
    print(json.dumps(out, sort_keys=True))
