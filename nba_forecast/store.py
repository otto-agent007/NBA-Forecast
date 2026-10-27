"""Append-only SQLite forecast and final-result ledger.

Per-run evaluations preserve every correction. Season performance uses the
latest pregame run per model/game/player, selected before reading evaluations,
to prevent duplicate samples and favorable-rerun selection leakage.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import sqlite3
import uuid

from .validation import parse_time, validate_history, validate_inputs
from .totals import game_totals
from .boxscore_model import validate_boxscore, PERCENTAGES, VERSION as BOX_MODEL_VERSION
from .model_versions import uses_refinements, REFINED_VERSION

PROJECTION_FIELDS = (
    'expected_minutes', 'expected_fga', 'expected_fta', 'expected_tov',
    'usage_proxy', 'points_mean', 'points_sd', 'points_lower80', 'points_upper80',
)

OPTIONAL_PROJECTION_FIELDS = ('expected_reb', 'expected_ast', 'expected_fg3m', 'expected_stl', 'expected_blk')
STAT_PROJECTIONS = {'reb': 'expected_reb', 'ast': 'expected_ast', 'fg3m': 'expected_fg3m',
                    'stl': 'expected_stl', 'blk': 'expected_blk', 'tov': 'expected_tov'}
STAT_PROJECTIONS.update({k:'expected_'+k for k in ('fgm','fga','fg3a','ftm','fta','oreb','dreb','pf','plus_minus',*PERCENTAGES)})

# Original single-table layout. Ledgers created before history deduplication
# keep it until ``dedupe_histories`` rebuilds them; readers see the same rows.
_LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    as_of TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('live', 'replay', 'demo')),
    scope TEXT NOT NULL CHECK (scope IN ('league', 'subset')),
    snapshot_json TEXT NOT NULL,
    history_json TEXT NOT NULL,
    forecast_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    history_sha256 TEXT NOT NULL,
    forecast_sha256 TEXT NOT NULL,
    inputs_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluations (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    revision INTEGER NOT NULL,
    evaluated_at TEXT NOT NULL,
    actuals_json TEXT NOT NULL,
    actuals_sha256 TEXT NOT NULL,
    report_json TEXT NOT NULL,
    PRIMARY KEY (run_id, revision)
);
CREATE TRIGGER IF NOT EXISTS runs_no_replace
BEFORE INSERT ON runs WHEN EXISTS (SELECT 1 FROM runs WHERE run_id = NEW.run_id)
BEGIN SELECT RAISE(ABORT, 'forecast runs cannot be replaced'); END;
CREATE TRIGGER IF NOT EXISTS evaluations_no_replace
BEFORE INSERT ON evaluations WHEN EXISTS (
    SELECT 1 FROM evaluations WHERE run_id = NEW.run_id AND revision = NEW.revision
) BEGIN SELECT RAISE(ABORT, 'evaluation revisions cannot be replaced'); END;
CREATE TRIGGER IF NOT EXISTS runs_no_update
BEFORE UPDATE ON runs BEGIN SELECT RAISE(ABORT, 'forecast runs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS runs_no_delete
BEFORE DELETE ON runs BEGIN SELECT RAISE(ABORT, 'forecast runs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS evaluations_no_update
BEFORE UPDATE ON evaluations BEGIN SELECT RAISE(ABORT, 'evaluation revisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS evaluations_no_delete
BEFORE DELETE ON evaluations BEGIN SELECT RAISE(ABORT, 'evaluation revisions are immutable'); END;
"""

# Every run used to embed its own copy of the (large, usually identical) input
# history. Each distinct history is now stored once, keyed by its digest. The
# ``runs`` view still presents the complete logical row, including
# history_json and the insertion-order rowid, so readers are unchanged. Trigger
# names match the legacy layout so older code's IF NOT EXISTS setup is a no-op.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS histories (
    history_sha256 TEXT PRIMARY KEY,
    history_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_records (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    as_of TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('live', 'replay', 'demo')),
    scope TEXT NOT NULL CHECK (scope IN ('league', 'subset')),
    snapshot_json TEXT NOT NULL,
    history_sha256 TEXT NOT NULL REFERENCES histories(history_sha256),
    forecast_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    forecast_sha256 TEXT NOT NULL,
    inputs_sha256 TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS runs AS
SELECT r.run_id AS run_id, r.created_at AS created_at, r.as_of AS as_of, r.mode AS mode,
       r.scope AS scope, r.snapshot_json AS snapshot_json, h.history_json AS history_json,
       r.forecast_json AS forecast_json, r.snapshot_sha256 AS snapshot_sha256,
       r.history_sha256 AS history_sha256, r.forecast_sha256 AS forecast_sha256,
       r.inputs_sha256 AS inputs_sha256, r.rowid AS rowid
FROM run_records r JOIN histories h ON h.history_sha256 = r.history_sha256;
CREATE TABLE IF NOT EXISTS evaluations (
    run_id TEXT NOT NULL REFERENCES run_records(run_id),
    revision INTEGER NOT NULL,
    evaluated_at TEXT NOT NULL,
    actuals_json TEXT NOT NULL,
    actuals_sha256 TEXT NOT NULL,
    report_json TEXT NOT NULL,
    PRIMARY KEY (run_id, revision)
);
CREATE TRIGGER IF NOT EXISTS runs_insert INSTEAD OF INSERT ON runs BEGIN
    SELECT RAISE(ABORT, 'input history differs from the copy stored under its digest')
    WHERE EXISTS (SELECT 1 FROM histories WHERE history_sha256 = NEW.history_sha256
                  AND history_json IS NOT NEW.history_json);
    INSERT INTO histories (history_sha256, history_json)
    SELECT NEW.history_sha256, NEW.history_json
    WHERE NOT EXISTS (SELECT 1 FROM histories WHERE history_sha256 = NEW.history_sha256);
    INSERT INTO run_records (run_id, created_at, as_of, mode, scope, snapshot_json, history_sha256,
                             forecast_json, snapshot_sha256, forecast_sha256, inputs_sha256)
    VALUES (NEW.run_id, NEW.created_at, NEW.as_of, NEW.mode, NEW.scope, NEW.snapshot_json,
            NEW.history_sha256, NEW.forecast_json, NEW.snapshot_sha256, NEW.forecast_sha256,
            NEW.inputs_sha256);
END;
CREATE TRIGGER IF NOT EXISTS runs_view_no_update
INSTEAD OF UPDATE ON runs BEGIN SELECT RAISE(ABORT, 'forecast runs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS runs_view_no_delete
INSTEAD OF DELETE ON runs BEGIN SELECT RAISE(ABORT, 'forecast runs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS runs_no_replace
BEFORE INSERT ON run_records WHEN EXISTS (SELECT 1 FROM run_records WHERE run_id = NEW.run_id)
BEGIN SELECT RAISE(ABORT, 'forecast runs cannot be replaced'); END;
CREATE TRIGGER IF NOT EXISTS runs_no_update
BEFORE UPDATE ON run_records BEGIN SELECT RAISE(ABORT, 'forecast runs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS runs_no_delete
BEFORE DELETE ON run_records BEGIN SELECT RAISE(ABORT, 'forecast runs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS histories_no_replace
BEFORE INSERT ON histories WHEN EXISTS (SELECT 1 FROM histories WHERE history_sha256 = NEW.history_sha256)
BEGIN SELECT RAISE(ABORT, 'input histories cannot be replaced'); END;
CREATE TRIGGER IF NOT EXISTS histories_no_update
BEFORE UPDATE ON histories BEGIN SELECT RAISE(ABORT, 'input histories are immutable'); END;
CREATE TRIGGER IF NOT EXISTS histories_no_delete
BEFORE DELETE ON histories BEGIN SELECT RAISE(ABORT, 'input histories are immutable'); END;
CREATE TRIGGER IF NOT EXISTS evaluations_no_replace
BEFORE INSERT ON evaluations WHEN EXISTS (
    SELECT 1 FROM evaluations WHERE run_id = NEW.run_id AND revision = NEW.revision
) BEGIN SELECT RAISE(ABORT, 'evaluation revisions cannot be replaced'); END;
CREATE TRIGGER IF NOT EXISTS evaluations_no_update
BEFORE UPDATE ON evaluations BEGIN SELECT RAISE(ABORT, 'evaluation revisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS evaluations_no_delete
BEFORE DELETE ON evaluations BEGIN SELECT RAISE(ABORT, 'evaluation revisions are immutable'); END;
"""

_RUN_COLUMNS = ('run_id', 'created_at', 'as_of', 'mode', 'scope', 'snapshot_json', 'history_json',
                'forecast_json', 'snapshot_sha256', 'history_sha256', 'forecast_sha256', 'inputs_sha256')


def _now():
    return datetime.now(timezone.utc)


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('ledger inputs must be finite JSON values') from exc


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _is_legacy(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'").fetchone() is not None


def _connect(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.executescript(_LEGACY_SCHEMA if _is_legacy(conn) else _SCHEMA)
    return conn


def _validate_forecast(snapshot, forecast):
    """A caller cannot label unrelated or unsupported projections as this run."""
    try:
        if forecast.get('forecast_stage', 'preliminary') != snapshot.get('forecast_stage', 'preliminary'):
            raise ValueError('forecast stage does not match snapshot evidence')
        if (not isinstance(forecast['model_version'], str) or not forecast['model_version'] or
                forecast['as_of'] != snapshot['as_of'] or forecast['season'] != snapshot['season']):
            raise ValueError('forecast version, cutoff, or season does not match snapshot')
        if not isinstance(forecast['review_queue'], list) or not isinstance(forecast['coverage'], dict):
            raise ValueError('forecast review_queue/coverage are invalid')
        roster = {p['player_id']: p for p in snapshot['roster']['players']}
        slate = {team: game for game in snapshot['games']
                 for team in (game['home_team_id'], game['away_team_id'])}
        rows = forecast['predictions']
        if not isinstance(rows, list) or len(rows) != len(roster):
            raise ValueError('forecast must contain every roster player exactly once')
        seen = set()
        for row in rows:
            player_id = row['player_id']
            if forecast.get('boxscore_model_version') and (forecast['boxscore_model_version']!=BOX_MODEL_VERSION or row.get('boxscore_model_version')!=forecast['boxscore_model_version']):
                raise ValueError('Full-box model versions disagree')
            if row.get('boxscore_model_version') and not forecast.get('boxscore_model_version'):
                raise ValueError('Forecast lacks its full-box model version')
            if player_id not in roster or player_id in seen:
                raise ValueError('forecast contains duplicate or unknown player')
            seen.add(player_id)
            player = roster[player_id]
            if any(row[key] != player[key] for key in ('name', 'team_id')):
                raise ValueError('forecast player identity does not match roster')
            game = slate.get(player['team_id'])
            if (row['game_id'] != (game['game_id'] if game else None) or
                    row['start_time'] != (game['start_time'] if game else None)):
                raise ValueError('forecast game identity does not match schedule')
            status = row['status']
            if status not in ('projected', 'out', 'needs_review', 'no_game'):
                raise ValueError('invalid forecast status')
            if (game is None) != (status == 'no_game'):
                raise ValueError('off-slate players must be labeled no_game')
            if game and player['status'] == 'out' and status != 'out':
                raise ValueError('out roster player must have out forecast')
            if game and player['status'] == 'unknown' and status != 'needs_review':
                raise ValueError('unknown availability cannot have a numeric forecast')
            probability = row.get('participation_probability')
            if probability is not None and (isinstance(probability, bool) or not isinstance(probability, (int, float))
                                            or not math.isfinite(probability) or not 0 <= probability <= 1):
                raise ValueError('participation probability must be in [0, 1]')
            if game and player['status'] == 'questionable':
                if status == 'projected' and not (probability is not None and probability < 1):
                    raise ValueError('a questionable player needs a participation probability below one')
                if status not in ('projected', 'needs_review'):
                    raise ValueError('questionable availability must be projected conditionally or withheld')
            if status in ('no_game', 'needs_review') and probability is not None:
                raise ValueError('unsupported forecasts have no participation probability')
            if status == 'out' and probability not in (None, 0, 0.0):
                raise ValueError('out forecasts have zero participation probability')
            if status == 'out' and player['status'] != 'out':
                raise ValueError('out forecast requires sourced out roster status')
            if (not isinstance(row['reason'], str) or not isinstance(row['flags'], list) or
                    not isinstance(row['components'], dict) or isinstance(row['history_games'], bool) or
                    not isinstance(row['history_games'], int) or row['history_games'] < 0):
                raise ValueError('invalid forecast audit fields')
            for key in PROJECTION_FIELDS:
                value = row[key]
                if status in ('no_game', 'needs_review'):
                    if value is not None:
                        raise ValueError('unsupported forecasts must have null projections')
                elif (isinstance(value, bool) or not isinstance(value, (int, float)) or
                      not math.isfinite(value) or value < 0):
                    raise ValueError('forecast projections must be finite nonnegative numbers')
                elif status == 'out' and value != 0:
                    raise ValueError('out projections must be zero')
            for key in OPTIONAL_PROJECTION_FIELDS:
                if key not in row:  # v0.1 immutable ledgers remain valid.
                    continue
                value = row[key]
                if status in ('no_game', 'needs_review') and value is not None:
                    raise ValueError('unsupported optional projections must be null')
                if status == 'out' and value != 0:
                    raise ValueError('out optional projections must be zero')
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                          or not math.isfinite(value) or value < 0):
                    raise ValueError('optional projections must be finite nonnegative numbers or null')
            validate_boxscore(row)
            if status in ('projected', 'out'):
                if not row['points_lower80'] <= row['points_mean'] <= row['points_upper80']:
                    raise ValueError('forecast interval must contain its mean')
                if row['expected_minutes'] > 60:
                    raise ValueError('expected minutes exceed supported forecast bound')
        if 'game_totals' in forecast:
            versions={t.get('schema_version',1) for t in forecast['game_totals']}
            if len(versions)>1:
                raise ValueError('forecast totals mix schema versions')
            version=next(iter(versions),2)
            expected=[game_totals(g,rows,schema_version=version,lineups=snapshot.get('preflight',{}).get('lineups')) for g in snapshot['games'] if g['season_type']=='Regular Season']
            if _canonical(forecast['game_totals'])!=_canonical(expected):
                raise ValueError('forecast totals disagree with player projections')
        if uses_refinements(snapshot):
            from .margins import build_margin_forecasts
            if forecast['model_version']!=REFINED_VERSION or forecast.get('model_key')!=snapshot['model_enhancements']['model_key']:
                raise ValueError('Refined model configuration does not match its snapshot')
            if _canonical(forecast.get('margin_forecasts'))!=_canonical(build_margin_forecasts(snapshot,forecast)):
                raise ValueError('Forecast margins disagree with the frozen model inputs and player totals')
        elif 'margin_forecasts' in forecast:
            raise ValueError('Margin distributions require a matching refined model snapshot')
    except (KeyError, TypeError) as exc:
        raise ValueError(f'incomplete forecast: {exc}') from exc


def save_run(db_path, snapshot, history, forecast, mode='live', scope='league'):
    """Store independent immutable reruns, using the actual save-time clock.

    ``live`` requires a complete league roster, a cutoff within 48 hours, and
    creation strictly before every scheduled game. A supplied generated_at has
    no authority. Historical ``replay``/``demo`` runs remain visibly non-live.
    """
    if mode=='live' and snapshot.get('research_reconstruction'):
        raise ValueError('Research reconstruction cannot be saved as a live forecast')
    if mode not in ('live', 'replay', 'demo'):
        raise ValueError('mode must be live, replay, or demo')
    if mode == 'live' and scope != 'league':
        raise ValueError('live runs require league scope')
    encoded = {name: _canonical(value) for name, value in (
        ('snapshot', snapshot), ('history', history), ('forecast', forecast))}
    # Validate exactly the JSON that will be stored, independent of caller mutation.
    snapshot, history, forecast = (json.loads(encoded[key]) for key in ('snapshot', 'history', 'forecast'))
    validate_inputs(snapshot, history, scope=scope)
    _validate_forecast(snapshot, forecast)
    inputs_hash = _hash(_canonical({'snapshot': snapshot, 'history': history}))
    run_id = str(uuid.uuid4())
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            created = _now()
            cutoff = parse_time(snapshot['as_of'])
            if mode == 'live':
                if not created - timedelta(hours=48) <= cutoff <= created:
                    raise ValueError('live as_of must be within 48 hours before actual save time')
                if any(parse_time(game['start_time']) <= created for game in snapshot['games']):
                    raise ValueError('live run must be saved before every tipoff')
            # Named columns work for both the legacy table and the deduplicated view.
            conn.execute(
                f'INSERT INTO runs ({", ".join(_RUN_COLUMNS)}) VALUES ({", ".join(["?"] * len(_RUN_COLUMNS))})',
                (run_id, created.isoformat(), snapshot['as_of'], mode, scope,
                 encoded['snapshot'], encoded['history'], encoded['forecast'],
                 _hash(encoded['snapshot']), _hash(encoded['history']),
                 _hash(encoded['forecast']), inputs_hash))
    finally:
        conn.close()
    return run_id


def _metrics(rows):
    count = len(rows)
    per_stat = {}
    for stat in STAT_PROJECTIONS:
        errors = [r.get('stat_errors', {}).get(stat) for r in rows]
        errors = [e for e in errors if e is not None]
        per_stat[stat] = {'count': len(errors),
                          'mae': sum(abs(e) for e in errors) / len(errors) if errors else None,
                          'mean_signed_error': sum(errors) / len(errors) if errors else None}
    scored = [r for r in rows if r.get('participation_probability') is not None and r.get('played') is not None]
    participation = dict(count=len(scored),
                         brier=sum((r['participation_probability'] - r['played']) ** 2 for r in scored) / len(scored) if scored else None,
                         mean_probability=sum(r['participation_probability'] for r in scored) / len(scored) if scored else None,
                         played_share=sum(r['played'] for r in scored) / len(scored) if scored else None)
    if not count:
        return dict(count=0, points_mae=None, points_rmse=None, mean_signed_error=None,
                    minutes_mae=None, interval80_coverage=None, interval80_mean_width=None,
                    interval_count=0, per_stat=per_stat, participation=participation)
    return dict(
        count=count, per_stat=per_stat, participation=participation,
        points_mae=sum(abs(r['points_error']) for r in rows) / count,
        points_rmse=math.sqrt(sum(r['points_error'] ** 2 for r in rows) / count),
        mean_signed_error=sum(r['points_error'] for r in rows) / count,
        minutes_mae=sum(abs(r['minutes_error']) for r in rows) / count,
        interval80_coverage=sum(r['interval80_contains_actual'] for r in rows) / count,
        interval80_mean_width=sum(r['interval80_width'] for r in rows) / count,
        interval_count=count,
    )


def _diagnostics(matched, metrics):
    flags = []

    def flag(code, message):
        flags.append({'code': code, 'message': message})

    count = len(matched)
    if count < 30:
        flag('low_sample', f'Only {count} matched finals; error and bias estimates are unstable.')
    if count < 100:
        flag('calibration_low_sample', 'Fewer than 100 matched finals; do not claim calibrated 80% intervals.')
    if not count:
        return flags
    if metrics['interval80_coverage'] < .7:
        flag('undercoverage', 'Observed 80% interval coverage is below 70%; inspect missed statuses and interval widths.')
    bias = metrics['mean_signed_error']
    if abs(bias) >= 2:
        direction = 'overprediction' if bias > 0 else 'underprediction'
        flag('systematic_' + direction,
             f'Mean forecast-minus-actual error is {bias:.2f} points; inspect minutes and scoring rates before adjusting.')
    comparable = [r for r in matched if r['minutes_component'] is not None]
    if comparable:
        minutes = sum(abs(r['minutes_component']) for r in comparable)
        rate = sum(abs(r['rate_component']) for r in comparable)
        if minutes > rate:
            flag('minutes_driver', 'Minutes differences contribute more absolute point error than scoring-rate differences; review rotation/status evidence.')
        elif rate > minutes:
            flag('scoring_rate_driver', 'Scoring-rate differences contribute more absolute point error than minutes differences; inspect opportunities and shooting variance.')
    if any(r['forecast_status'] == 'out' and r['actual_status'] == 'played' for r in matched):
        flag('out_player_played', 'A player forecast out played; audit the availability evidence and its timestamp.')
    return flags


def _report(run, forecast, actuals, revision, evaluated_at):
    by_key = {(a['game_id'], a['player_id']): a for a in actuals}
    matched, pending, excluded = [], [], []
    for prediction in forecast['predictions']:
        identity = {key: prediction[key] for key in ('game_id', 'player_id', 'team_id')}
        status = prediction['status']
        if status in ('no_game', 'needs_review'):
            excluded.append({**identity, 'reason': status})
            continue
        observed = by_key.get((prediction['game_id'], prediction['player_id']))
        if observed is None:
            pending.append({**identity, 'forecast_status': status})
            continue
        predicted_points, predicted_minutes = prediction['points_mean'], prediction['expected_minutes']
        points, minutes = observed['pts'], observed['minutes']
        minutes_component = rate_component = None
        if predicted_minutes > 0:
            predicted_rate = predicted_points / predicted_minutes
            minutes_component = (predicted_minutes - minutes) * predicted_rate
            # This algebra remains defined for a final DNP (zero actual minutes).
            rate_component = minutes * predicted_rate - points
        stat_errors, stat_feedback = {}, {}
        for stat, field in STAT_PROJECTIONS.items():
            predicted, actual = prediction.get(field), observed.get(stat)
            if stat in PERCENTAGES and actual is None:
                made,attempted=PERCENTAGES[stat]
                if observed.get(attempted) and observed.get(made) is not None:
                    actual=observed[made]/observed[attempted]
            error = predicted - actual if predicted is not None and actual is not None else None
            stat_errors[stat] = error
            rate = predicted / predicted_minutes if predicted is not None and predicted_minutes > 0 and stat not in PERCENTAGES else None
            stat_feedback[stat] = {
                'predicted': predicted, 'actual': actual, 'error': error,
                'minutes_component': (predicted_minutes - minutes) * rate if rate is not None and error is not None else None,
                'rate_component': minutes * rate - actual if rate is not None and error is not None else None,
            }
        matched.append({
            'stat_errors': stat_errors, 'stat_feedback': stat_feedback,
            **identity, 'forecast_status': status, 'actual_status': observed['status'],
            'participation_probability': prediction.get('participation_probability'),
            'played': observed['status'] == 'played' and minutes > 0,
            'predicted_points': predicted_points, 'actual_points': points,
            'predicted_minutes': predicted_minutes, 'actual_minutes': minutes,
            'points_error': predicted_points - points, 'minutes_error': predicted_minutes - minutes,
            'minutes_component': minutes_component, 'rate_component': rate_component,
            'interval80_contains_actual': prediction['points_lower80'] <= points <= prediction['points_upper80'],
            'interval80_width': prediction['points_upper80'] - prediction['points_lower80'],
        })
    metrics = _metrics(matched)
    forecast_groups, actual_groups = defaultdict(list), defaultdict(list)
    for row in matched:
        forecast_groups[row['forecast_status']].append(row)
        actual_groups[row['actual_status']].append(row)
    live = run['mode'] == 'live' and run['scope'] == 'league'
    return {
        'run_id': run['run_id'], 'revision': revision, 'evaluated_at': evaluated_at,
        'created_at': run['created_at'], 'as_of': run['as_of'], 'mode': run['mode'],
        'scope': run['scope'], 'model_version': forecast['model_version'],
        'boxscore_model_version':forecast.get('boxscore_model_version'), 'live_eligible': live,
        'evaluation_label': 'LIVE — per-run prospective results' if live else 'NON-LIVE — replay/demo results',
        'signed_error_convention': 'forecast minus actual; positive means overprediction',
        'aggregation_policy': 'Per-run only. Do not select favorable reruns after outcomes are known.',
        'sample_counts': {'predictions': len(forecast['predictions']), 'evaluated': len(matched),
                          'pending': len(pending), 'excluded': len(excluded), 'final_rows': len(actuals),
                          'live': len(matched) if live else 0,
                          'replay': 0 if live else len(matched)},
        'metrics': metrics,
        'status_groups': {key: _metrics(value) for key, value in sorted(forecast_groups.items())},
        'actual_status_groups': {key: _metrics(value) for key, value in sorted(actual_groups.items())},
        'diagnostics': _diagnostics(matched, metrics),
        'matched': matched, 'pending': pending, 'excluded': excluded,
    }


def evaluate_run(db_path, run_id, actuals):
    """Append a revision after matching validated finals, or return an exact retry.

    Partial batches accumulate. Corrections replace only that observation in a
    new revision; the original prediction and all older revisions remain intact.
    Ingestion verifies final status; a manually normalized row asserts that same
    contract. Missing rows are pending, never an inferred zero-point DNP.
    """
    actuals = json.loads(_canonical(actuals))
    validate_history(actuals)
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            # Never the run's input history: scoring needs only the frozen forecast.
            run = conn.execute(
                'SELECT run_id, created_at, as_of, mode, scope, snapshot_json, forecast_json '
                'FROM runs WHERE run_id = ?', (run_id,)).fetchone()
            if run is None:
                raise ValueError(f'unknown forecast run: {run_id}')
            snapshot, forecast = json.loads(run['snapshot_json']), json.loads(run['forecast_json'])
            predictions = {(p['game_id'], p['player_id']): p for p in forecast['predictions']}
            games = {g['game_id']: g for g in snapshot['games']}
            now = _now()
            for row in actuals:
                prediction = predictions.get((row['game_id'], row['player_id']))
                game = games.get(row['game_id'])
                if prediction is None or game is None or row['team_id'] != prediction['team_id']:
                    raise ValueError('actual does not match forecast game/player/team')
                opponent = game['away_team_id'] if row['team_id'] == game['home_team_id'] else game['home_team_id']
                if row['opponent_id'] != opponent or row['season'] != snapshot['season']:
                    raise ValueError('actual season/opponent does not match forecast')
                if parse_time(row['game_time']) != parse_time(game['start_time']):
                    raise ValueError('actual game time does not match saved schedule')
                if parse_time(row['known_at']) > now or parse_time(row['game_time']) >= now:
                    raise ValueError('actuals cannot contain future-known or unplayed games')
            latest = conn.execute(
                'SELECT * FROM evaluations WHERE run_id = ? ORDER BY revision DESC LIMIT 1', (run_id,)).fetchone()
            previous = json.loads(latest['actuals_json']) if latest else []
            combined = {(row['game_id'], row['player_id']): row for row in previous}
            for row in actuals:
                key = row['game_id'], row['player_id']
                if key in combined and parse_time(row['known_at']) < parse_time(combined[key]['known_at']):
                    raise ValueError('a correction cannot replace newer evidence with an older observation')
                combined[key] = row
            finals = [combined[key] for key in sorted(combined)]
            validate_history(finals)
            encoded = _canonical(finals)
            actuals_hash = _hash(encoded)
            if latest and latest['actuals_sha256'] == actuals_hash:
                return json.loads(latest['report_json'])
            revision = latest['revision'] + 1 if latest else 1
            report = _report(run, forecast, finals, revision, now.isoformat())
            conn.execute('INSERT INTO evaluations VALUES (?, ?, ?, ?, ?, ?)',
                         (run_id, revision, now.isoformat(), encoded, actuals_hash, _canonical(report)))
            return report
    finally:
        conn.close()


def performance_report(db_path):
    """Aggregate prospective feedback with a fixed, outcome-blind selector.

    Select the latest saved live forecast for each season/model/game/player
    before inspecting evaluation availability. A newer pending or unsupported
    forecast therefore cannot fall back to an older, favorable evaluated run.
    Exact clock ties use insertion order. Latest final-result corrections apply
    only after forecast selection. Off-slate pool rows have no outcome target.
    """
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute('BEGIN')
            runs = conn.execute(
                'SELECT rowid AS insertion_order,run_id,created_at,mode,scope,forecast_json FROM runs')
            selected = {}
            off_slate_rows = non_live_runs = ineligible_rows = 0
            for run in runs:
                if run['mode'] != 'live' or run['scope'] != 'league':
                    non_live_runs += 1
                    continue
                forecast = json.loads(run['forecast_json'])
                created = parse_time(run['created_at'])
                rank = created, run['insertion_order']
                identity = {'run_id': run['run_id'], 'created_at': run['created_at']}
                for prediction in forecast['predictions']:
                    if prediction['game_id'] is None:
                        off_slate_rows += 1
                        continue
                    if created >= parse_time(prediction['start_time']):
                        ineligible_rows += 1
                        continue
                    key = (forecast['season'], forecast['model_version'],
                           prediction['game_id'], prediction['player_id'])
                    if key not in selected or rank > selected[key][0]:
                        # Selection needs identity/status only, not another copy
                        # of every input or each prediction's component audit.
                        selected[key] = rank, identity, {
                            'team_id': prediction['team_id'], 'status': prediction['status']}

            # Only now inspect final-result revisions; none participate in selection.
            evaluations = {}
            for _, run, _ in selected.values():
                if run['run_id'] in evaluations:
                    continue
                latest = conn.execute(
                    'SELECT * FROM evaluations WHERE run_id = ? ORDER BY revision DESC LIMIT 1',
                    (run['run_id'],)).fetchone()
                if latest:
                    report = json.loads(latest['report_json'])
                    matched = {(r['game_id'], r['player_id']): r for r in report['matched']}
                    evaluations[run['run_id']] = latest['revision'], matched
                else:
                    evaluations[run['run_id']] = None, {}

            groups = {}
            for key in sorted(selected):
                season, model_version, game_id, player_id = key
                _, run, prediction = selected[key]
                group = groups.setdefault((season, model_version), {
                    'season': season, 'model_version': model_version,
                    'selected_forecasts': [], 'matched': [], 'pending': [], 'excluded': [],
                })
                revision, results = evaluations[run['run_id']]
                identity = {'game_id': game_id, 'player_id': player_id,
                            'team_id': prediction['team_id'], 'run_id': run['run_id'],
                            'created_at': run['created_at'], 'evaluation_revision': revision}
                status = prediction['status']
                group['selected_forecasts'].append({**identity, 'forecast_status': status})
                if status in ('needs_review', 'no_game'):
                    group['excluded'].append({**identity, 'reason': status})
                elif (game_id, player_id) in results:
                    group['matched'].append({**results[(game_id, player_id)], **identity})
                else:
                    group['pending'].append({**identity, 'forecast_status': status})

            totals = dict(predictions=0, evaluated=0, pending=0, excluded=0, live=0, replay=0)
            for group in groups.values():
                matched = group['matched']
                group['sample_counts'] = {
                    'predictions': len(group['selected_forecasts']), 'evaluated': len(matched),
                    'pending': len(group['pending']), 'excluded': len(group['excluded']),
                    'live': len(matched), 'replay': 0,
                }
                group['metrics'] = _metrics(matched)
                group['diagnostics'] = _diagnostics(matched, group['metrics'])
                forecast_groups, actual_groups = defaultdict(list), defaultdict(list)
                for row in matched:
                    forecast_groups[row['forecast_status']].append(row)
                    actual_groups[row['actual_status']].append(row)
                group['status_groups'] = {k: _metrics(v) for k, v in sorted(forecast_groups.items())}
                group['actual_status_groups'] = {k: _metrics(v) for k, v in sorted(actual_groups.items())}
                for key, value in group['sample_counts'].items():
                    totals[key] += value
            return {
                'generated_at': _now().isoformat(),
                'evaluation_label': 'LIVE — prospective season feedback',
                'selection_policy': (
                    'Latest actual created_at before tipoff per season/model_version/game_id/player_id; '
                    'equal-clock ties use insertion order. Select before reading evaluations; '
                    'use the selected run\'s latest evaluation revision. No fallback to older runs.'),
                'signed_error_convention': 'forecast minus actual; positive means overprediction',
                'sample_counts': totals,
                'non_live_runs_ignored': non_live_runs,
                'off_slate_rows_ignored': off_slate_rows,
                'ineligible_rows_ignored': ineligible_rows,
                'groups': [groups[key] for key in sorted(groups)],
            }
    finally:
        conn.close()


def _row_digest(conn, query):
    """Order-sensitive digest of every value, keeping SQLite types distinct."""
    digest, count = hashlib.sha256(), 0
    for row in conn.execute(query):
        for value in row:
            data = b'' if value is None else str(value).encode('utf-8')
            digest.update(type(value).__name__.encode() + len(data).to_bytes(8, 'big') + data)
        count += 1
    return count, digest.hexdigest()


def dedupe_histories(db_path, backup_suffix='.pre-history-dedupe'):
    """Rebuild a legacy ledger so each distinct input history is stored once.

    The rebuilt file is verified before it replaces the original: every run,
    including its insertion-order rowid, and every evaluation revision must read
    back value-for-value through the new ``runs`` view. The original file stays
    beside it (a hard link, so no copy) until the owner deletes it. Stop ticks
    and other writers first; this holds the write lock while it runs.
    """
    import os
    from pathlib import Path
    db = Path(db_path)
    backup, staging = db.with_name(db.name + backup_suffix), db.with_name(db.name + '.dedupe-tmp')
    for suffix in ('-wal', '-journal'):
        if db.with_name(db.name + suffix).exists():
            raise ValueError(f'{db.name}{suffix} exists; recover the open transaction first')
    if backup.exists():
        raise ValueError(f'{backup} already exists')
    source = sqlite3.connect(db, timeout=30)
    try:
        if not _is_legacy(source):
            raise ValueError('ledger histories are already stored once')
        source.execute('BEGIN IMMEDIATE')  # No other writer can commit until the swap.
        staging.unlink(missing_ok=True)
        target = sqlite3.connect(staging)
        try:
            target.execute('PRAGMA foreign_keys = ON')
            target.executescript(_SCHEMA)
            stored, mismatched = {}, 0
            with target:
                rows = source.execute(f'SELECT rowid, {", ".join(_RUN_COLUMNS)} FROM runs ORDER BY rowid')
                for row in rows:
                    run = dict(zip(('rowid', *_RUN_COLUMNS), row))
                    content = _hash(run['history_json'])
                    mismatched += content != run['history_sha256']
                    if run['history_sha256'] not in stored:
                        target.execute('INSERT INTO histories (history_sha256, history_json) VALUES (?, ?)',
                                       (run['history_sha256'], run['history_json']))
                        stored[run['history_sha256']] = content
                    elif stored[run['history_sha256']] != content:
                        raise ValueError(f'run {run["run_id"]} shares a history digest with different content')
                    columns = ('rowid', *(c for c in _RUN_COLUMNS if c != 'history_json'))
                    target.execute(f'INSERT INTO run_records ({", ".join(columns)}) '
                                   f'VALUES ({", ".join(["?"] * len(columns))})',
                                   tuple(run[c] for c in columns))
                eval_columns = 'rowid, run_id, revision, evaluated_at, actuals_json, actuals_sha256, report_json'
                for row in source.execute(f'SELECT {eval_columns} FROM evaluations ORDER BY rowid'):
                    target.execute(f'INSERT INTO evaluations ({eval_columns}) VALUES (?, ?, ?, ?, ?, ?, ?)', row)
            run_query = f'SELECT rowid, {", ".join(_RUN_COLUMNS)} FROM runs ORDER BY rowid'
            eval_query = f'SELECT {eval_columns} FROM evaluations ORDER BY rowid'
            runs, evaluations = _row_digest(source, run_query), _row_digest(source, eval_query)
            if (runs, evaluations) != (_row_digest(target, run_query), _row_digest(target, eval_query)):
                raise ValueError('rebuilt ledger does not read back identically; original left in place')
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or \
                    target.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('rebuilt ledger failed SQLite integrity checks; original left in place')
        finally:
            target.close()
        with open(staging, 'rb') as stream:
            os.fsync(stream.fileno())
        os.chmod(staging, db.stat().st_mode & 0o777)
        before = db.stat().st_size
        os.link(db, backup)
        os.replace(staging, db)
        directory = os.open(db.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    finally:
        source.close()
    return dict(runs=runs[0], evaluations=evaluations[0], histories=len(stored),
                history_digest_mismatches=mismatched, bytes_before=before,
                bytes_after=db.stat().st_size, backup=str(backup))
