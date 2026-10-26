"""Prepare an immutable new model configuration without activating it."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .enhancements import load_active, install_artifact, _read
from .margins import METHOD as MARGIN_METHOD, PARAMETERS as MARGIN_PARAMETERS
from .model_versions import LEGACY_ENHANCED_VERSION, REFINED_VERSION
from .role_context import PARSER_VERSION
from .runner_release import package_digest
from .schedule import atomic_json, canonical
from .shot_quality import VERSION as SHOT_VERSION, PARAMETERS as SHOT_PARAMETERS
from .validation import parse_time


def build_artifact(base, parent_digest, as_of):
    if base.get('model_version')!=LEGACY_ENHANCED_VERSION:
        raise ValueError('Refinement upgrade requires the established 0.5.0 base artifact')
    if parse_time(base['known_at'])>parse_time(as_of):raise ValueError('Base model is future-known')
    if not isinstance(parent_digest,str) or not re.fullmatch('[a-f0-9]{64}',parent_digest):
        raise ValueError('Parent artifact hash is invalid')
    configuration=dict(model_version=REFINED_VERSION, parent_artifact_sha256=parent_digest,
        parent_model_key=base['model_key'], runner_package_sha256=package_digest(Path(__file__).parent),
        parameters=deepcopy(base['parameters']),
        refinements=dict(role_context=PARSER_VERSION,
            shot_quality=dict(version=SHOT_VERSION,parameters=deepcopy(SHOT_PARAMETERS)),
            margins=dict(method=MARGIN_METHOD,parameters=deepcopy(MARGIN_PARAMETERS))))
    artifact=deepcopy(base)
    artifact.update(configuration,known_at=as_of,calibration=None,
        model_key=hashlib.sha256(canonical(configuration)).hexdigest(),
        validation_report=dict(scope='New injury-role, court-location and margin model; prospective accuracy not established.',
            status='prospective_validation_pending',parent_model_key=base['model_key'],
            inherited_calibration=False,refinements=deepcopy(configuration['refinements'])))
    return artifact


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True)
    parser.add_argument('--output',required=True,help='New reviewable artifact file; activation is separate')
    parser.add_argument('--base-artifact',help='Archived 0.5.0 parent SHA256 when preparing another reviewed code release')
    args=parser.parse_args()
    output=Path(args.output)
    if output.exists():parser.error('Output already exists; preserve the prior candidate')
    now=datetime.now(timezone.utc).isoformat()
    if args.base_artifact:
        digest=args.base_artifact;base=_read(args.directory,digest,now)
    else:
        base,digest=load_active(args.directory,now)
        if base and base['model_version']==REFINED_VERSION:
            digest=base['parent_artifact_sha256'];base=_read(args.directory,digest,now)
    if base is None:parser.error('No active base model')
    artifact=build_artifact(base,digest,now)
    installed=install_artifact(args.directory,artifact)
    atomic_json(output,artifact)
    print(json.dumps(dict(model_version=REFINED_VERSION,artifact_sha256=installed,
        model_key=artifact['model_key'],runner_package_sha256=artifact['runner_package_sha256'],activated=False)))


if __name__=='__main__':main()
