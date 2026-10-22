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

PROJECTION_FIELDS = (
    'expected_minutes', 'expected_fga', 'expected_fta', 'expected_tov',
    'usage_proxy', 'points_mean', 'points_sd', 'points_lower80', 'points_upper80',
)

_SCHEMA = """
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


def _now():
    return datetime.now(timezone.utc)


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('ledger inputs must be finite JSON values') from exc


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _connect(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.executescript(_SCHEMA)
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
            if game and player['status'] in ('unknown', 'questionable') and status != 'needs_review':
                raise ValueError('uncertain availability cannot have a numeric forecast')
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
            if status in ('projected', 'out'):
                if not row['points_lower80'] <= row['points_mean'] <= row['points_upper80']:
                    raise ValueError('forecast interval must contain its mean')
                if row['expected_minutes'] > 60:
                    raise ValueError('expected minutes exceed supported forecast bound')
    except (KeyError, TypeError) as exc:
        raise ValueError(f'incomplete forecast: {exc}') from exc


def save_run(db_path, snapshot, history, forecast, mode='live', scope='league'):
    """Store independent immutable reruns, using the actual save-time clock.

    ``live`` requires a complete league roster, a cutoff within 48 hours, and
    creation strictly before every scheduled game. A supplied generated_at has
    no authority. Historical ``replay``/``demo`` runs remain visibly non-live.
    """
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
            conn.execute(
                'INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (run_id, created.isoformat(), snapshot['as_of'], mode, scope,
                 encoded['snapshot'], encoded['history'], encoded['forecast'],
                 _hash(encoded['snapshot']), _hash(encoded['history']),
                 _hash(encoded['forecast']), inputs_hash))
    finally:
        conn.close()
    return run_id


def _metrics(rows):
    count = len(rows)
    if not count:
        return dict(count=0, points_mae=None, points_rmse=None, mean_signed_error=None,
                    minutes_mae=None, interval80_coverage=None, interval80_mean_width=None,
                    interval_count=0)
    return dict(
        count=count,
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
        matched.append({
            **identity, 'forecast_status': status, 'actual_status': observed['status'],
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
        'scope': run['scope'], 'model_version': forecast['model_version'], 'live_eligible': live,
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
            run = conn.execute('SELECT * FROM runs WHERE run_id = ?', (run_id,)).fetchone()
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
            runs = conn.execute('SELECT rowid AS insertion_order, * FROM runs').fetchall()
            selected = {}
            off_slate_rows = non_live_runs = ineligible_rows = 0
            for run in runs:
                if run['mode'] != 'live' or run['scope'] != 'league':
                    non_live_runs += 1
                    continue
                forecast = json.loads(run['forecast_json'])
                created = parse_time(run['created_at'])
                rank = created, run['insertion_order']
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
                        selected[key] = rank, run, prediction

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
