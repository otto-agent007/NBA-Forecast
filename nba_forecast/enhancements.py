"""Hash-bound fitted model inputs and reproducible live environment preparation."""
import hashlib,json,re
from pathlib import Path
from .game_environment import build_environment
from .schedule import atomic_json,canonical
from .validation import parse_time
from .model_versions import ENHANCED_VERSIONS, REFINED_VERSION


def install_artifact(directory,artifact):
    raw=canonical(artifact);digest=hashlib.sha256(raw).hexdigest()
    path=Path(directory)/'model-artifacts'/(digest+'.json')
    if path.exists() and path.read_bytes()!=raw:raise ValueError('Model artifact archive differs')
    if not path.exists():atomic_json(path,artifact)
    return digest


def _read(directory,digest,as_of):
    if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Invalid model artifact SHA256')
    raw=(Path(directory)/'model-artifacts'/(digest+'.json')).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Model artifact archive hash mismatch')
    artifact=json.loads(raw);cutoff=parse_time(as_of);known=parse_time(artifact['known_at'])
    if artifact['model_version'] not in ENHANCED_VERSIONS or known>cutoff:
        raise ValueError('Unsupported or future-known model artifact')
    if artifact['model_version']==REFINED_VERSION:
        from .runner_release import package_digest
        if artifact.get('runner_package_sha256')!=package_digest(Path(__file__).parent):
            raise ValueError('Refined model artifact does not match the running code release')
    if any(parse_time(g['known_at'])>known for g in artifact['team_games']):raise ValueError('Model training used future-known team data')
    for key in ('context_model','calibration'):
        item=artifact.get(key)
        if item and parse_time(item['training_cutoff'])>known:raise ValueError('Fitted component is future-known')
    calibration=artifact.get('calibration')
    if calibration and calibration['model_key']!=artifact['model_key']:raise ValueError('Calibration model configuration differs')
    return artifact


def load_active(directory,as_of):
    pointer=Path(directory)/'model-upgrade-active.json'
    if not pointer.exists():return None,None
    active=json.loads(pointer.read_text())
    if parse_time(active['activated_at'])>parse_time(as_of):raise ValueError('Active model configuration is future-known')
    digest=active['artifact_sha256']
    return _read(directory,digest,as_of),digest


def _contemporary_games(directory,as_of):
    if directory is None:return [],[]
    cutoff=parse_time(as_of);chosen={}
    for path in (Path(directory)/'environment-finals/records').glob('*.json'):
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=path.stem:raise ValueError('Contemporary environment archive hash mismatch')
        record=json.loads(raw)
        if parse_time(record['known_at'])>cutoff:continue
        if parse_time(record['game_time'])>=parse_time(record['known_at']):raise ValueError('Contemporary final evidence chronology is invalid')
        previous=chosen.get(record['game_id'])
        if previous is None or (parse_time(record['known_at']),path.stem)>(parse_time(previous[0]['known_at']),previous[1]):chosen[record['game_id']]=(record,path.stem)
    values=[chosen[k] for k in sorted(chosen)]
    return [r for r,_ in values],[dict(game_id=r['game_id'],sha256=sha) for r,sha in values]


def prepare(artifact,digest,game,as_of,*,directory=None):
    if parse_time(artifact['known_at'])>parse_time(as_of):raise ValueError('Model fit is future-known')
    contemporary,sources=_contemporary_games(directory,as_of)
    records={r['game_id']:r for r in artifact['team_games']}
    for record in contemporary:
        previous=records.get(record['game_id'])
        if previous is None or parse_time(record['known_at'])>=parse_time(previous['known_at']):records[record['game_id']]=record
    environment=build_environment(list(records.values()),game,as_of,parameters=artifact['parameters'].get('environment_parameters'))
    result=dict(artifact_sha256=digest,prepared_at=as_of,known_at=artifact['known_at'],
        model_version=artifact['model_version'],model_key=artifact['model_key'],parameters=artifact['parameters'],
        context_model=artifact.get('context_model'),calibration=artifact.get('calibration'),environment=environment,
        excluded_history_game_ids=artifact.get('excluded_history_game_ids',[]),live_team_game_sources=sources)
    if artifact['model_version']==REFINED_VERSION:
        from .margins import prepare_margin_inputs
        result['margin_inputs']=prepare_margin_inputs(list(records.values()),as_of,artifact['model_key'])
    return result


def verify_enhancements(snapshot,directory):
    supplied=snapshot.get('model_enhancements')
    if not supplied:return
    if snapshot.get('research_reconstruction'):raise ValueError('Research reconstruction is ineligible for a live forecast')
    cutoff=parse_time(snapshot['as_of']);prepared=parse_time(supplied['prepared_at'])
    if not 0<=(cutoff-prepared).total_seconds()<=300:raise ValueError('Prepared model inputs are stale or future-known')
    if len(snapshot['games'])!=1:raise ValueError('Prepared model environment requires exactly one game')
    artifact=_read(directory,supplied['artifact_sha256'],snapshot['as_of'])
    expected=prepare(artifact,supplied['artifact_sha256'],snapshot['games'][0],supplied['prepared_at'],directory=directory)
    if canonical(supplied)!=canonical(expected):raise ValueError('Supplied fitted model inputs differ from the archive')
