"""The artifact production is running, exported for `backtest --production-path`, and how it differs from a dataset artifact.

    python3 scripts/research/export_production_artifact.py --directory data/operations/production \
        --against datasets/model-upgrade-2026-10-23/artifact.json.gz [--output datasets/production-artifact-<date>]

Reads `model-upgrade-active.json`, the active artifact it names under
`model-artifacts/` and, for a refined artifact, its parent, verifying each
archive hash; nothing in the directory is written. Prints, for every field
`backtest._production_forecast` reads (READ) and every other top-level key,
whether the active artifact, its parent and `--against` agree.

With `--output` it writes the active artifact without what binds it to one
release or to live outcomes (UNBOUND): the runner package digest is removed
and the prospective calibrations, fitted on live results the replay must not
read, are set to null. Everything the replay reads is kept as production has it.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

# What backtest._production_forecast takes from the artifact.
READ = ('model_version', 'model_key', 'known_at', 'parameters', 'context_model', 'excluded_history_game_ids')
# Removed (None) or nulled on export: enhancements._read binds a refined artifact to the
# release whose package digest it carries, and the calibrations are fitted on live outcomes.
UNBOUND = dict(runner_package_sha256=None, calibration='null', margin_calibration='null')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def archived(directory, sha):
    raw = (Path(directory) / 'model-artifacts' / (sha + '.json')).read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError('model artifact archive hash mismatch: ' + sha)
    return json.loads(raw)


def short(value):
    if isinstance(value, (dict, list)):
        return f'{type(value).__name__}[{len(value)}] sha {digest(value)[:12]}'
    text = json.dumps(value)
    return text if len(text) <= 70 else text[:67] + '...'


def differences(a, b, path=''):
    """Leaf paths where two JSON values differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for key in sorted(set(a) | set(b))
                for d in (differences(a[key], b[key], f'{path}.{key}') if key in a and key in b
                          else [(f'{path}.{key}', short(a.get(key, '<absent>')), short(b.get(key, '<absent>')))])]
    return [] if a == b else [(path, short(a), short(b))]


def report(name, artifact, other_name, other):
    print(f'== {name} against {other_name} ==')
    unequal = 0
    for key in (*READ, *sorted((set(artifact) | set(other)) - set(READ))):
        a, b = artifact.get(key, '<absent>'), other.get(key, '<absent>')
        same = canonical(a) == canonical(b)
        unequal += not same and key in READ
        print(f"  {'read by the replay' if key in READ else 'not read':18s} {key:26s} "
              + ('equal' if same else f'DIFFERS  {name}={short(a)}  {other_name}={short(b)}'))
        if not same and key in ('parameters', 'context_model'):
            for path, x, y in differences(a, b, key):
                print(f'      {path}: {x} | {y}')
    print(f'  fields the replay reads that differ: {unequal} of {len(READ)}')
    return unequal


def export(active):
    out = dict(active)
    for key, action in UNBOUND.items():
        if action is None:
            out.pop(key, None)
        elif key in out:
            out[key] = None
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--directory', required=True, help='Operations directory (read only)')
    parser.add_argument('--against', help='A dataset artifact (.json or .json.gz) to compare with the active one')
    parser.add_argument('--output', help='New dataset directory for artifact.json.gz and manifest.json')
    args = parser.parse_args()
    directory = Path(args.directory)
    pointer = json.loads((directory / 'model-upgrade-active.json').read_text())
    active = archived(directory, pointer['artifact_sha256'])
    print(f"active artifact {pointer['artifact_sha256']} activated {pointer['activated_at']}")
    print(f"  model_version {active['model_version']} model_key {active['model_key']} known_at {active['known_at']}")
    parent = None
    if active.get('parent_artifact_sha256'):
        parent = archived(directory, active['parent_artifact_sha256'])
        print(f"parent artifact {active['parent_artifact_sha256']} model_version {parent['model_version']} model_key {parent['model_key']}")
        report('active', active, 'parent', parent)
    if args.against:
        raw = Path(args.against).read_bytes()
        against = json.loads(gzip.decompress(raw) if args.against.endswith('.gz') else raw)
        print(f"{args.against}: canonical sha256 {digest(against)} model_version {against['model_version']}"
              + ('  (byte-identical to the parent)' if parent is not None and digest(against) == active['parent_artifact_sha256'] else ''))
        report('active', active, args.against, against)
    if args.output:
        out = Path(args.output)
        if out.exists() and any(out.iterdir()):
            raise SystemExit('output directory exists and is not empty')
        exported = export(active)
        changed = sorted(k for k in set(active) | set(exported) if canonical(active.get(k, '<absent>')) != canonical(exported.get(k, '<absent>')))
        if any(key in READ for key in changed):
            raise SystemExit('the export changed a field the replay reads')
        out.mkdir(parents=True, exist_ok=True)
        packed = gzip.compress(canonical(exported), mtime=0)
        (out / 'artifact.json.gz').write_bytes(packed)
        manifest = dict(schema_version=1, artifact_sha256=digest(exported), model_version=exported['model_version'],
                        model_key=exported['model_key'],
                        exported_from=dict(active_artifact_sha256=pointer['artifact_sha256'], activated_at=pointer['activated_at'],
                                           parent_artifact_sha256=active.get('parent_artifact_sha256'),
                                           runner_package_sha256=active.get('runner_package_sha256')),
                        changed_keys={key: ('removed' if key not in exported else 'set to null') for key in changed},
                        replay_reads={key: (exported[key] if not isinstance(exported[key], (dict, list)) else digest(exported[key]))
                                      for key in READ},
                        files={'artifact.json.gz': dict(sha256=hashlib.sha256(packed).hexdigest(), bytes=len(packed))},
                        purpose='backtest --production-path: the parameters, context model and model version production '
                                'runs, without the release binding or the live-fitted calibrations. Not installable.')
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        print(f"wrote {out / 'artifact.json.gz'} ({len(packed)} bytes), canonical sha256 {manifest['artifact_sha256']}; "
              f"changed against the active artifact: {manifest['changed_keys']}")


if __name__ == '__main__':
    main()
