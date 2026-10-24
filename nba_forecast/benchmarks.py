"""Paired prospective comparisons using immutable inputs and outcome-blind selection."""
import json
import math
import sqlite3
from collections import defaultdict, deque
from contextlib import closing
from statistics import fmean
from .profiles import eligible_history
from .store import performance_report


def baseline_values(snapshot,history,prediction):
    rows=[r for r in eligible_history(snapshot,history) if r['player_id']==prediction['player_id']][-10:]
    return _baseline_values(rows,prediction)


def _baseline_values(rows,prediction):
    minutes=sum(r['minutes'] for r in rows)
    if not rows or minutes<=0 or prediction['expected_minutes'] is None:return None
    return dict(player_average=fmean(r['pts'] for r in rows),
                minutes_rate=prediction['expected_minutes']*sum(r['pts'] for r in rows)/minutes)


def paired_metrics(rows):
    out={'paired_n':len(rows)}
    for name in ('model','player_average','minutes_rate'):
        errors=[r[name]-r['actual'] for r in rows]
        out[name]={'mae':fmean(abs(e) for e in errors) if errors else None,
                   'rmse':math.sqrt(fmean(e*e for e in errors)) if errors else None,
                   'bias':fmean(errors) if errors else None}
    return out


def _run_pairs(conn,run_id,matched,parsed=None):
    """Release one run's full inputs before processing the next selected run.

    ``parsed`` holds at most one decoded history; runs sharing its digest reuse it.
    """
    run=conn.execute('SELECT snapshot_json,history_sha256,forecast_json FROM runs WHERE run_id=?',
                     (run_id,)).fetchone()
    parsed=parsed if parsed is not None else {}
    if run['history_sha256'] not in parsed:
        parsed.clear()
        encoded=conn.execute('SELECT history_json FROM runs WHERE run_id=?',(run_id,)).fetchone()[0]
        parsed[run['history_sha256']]=json.loads(encoded)
    snapshot=json.loads(run['snapshot_json']);history=parsed[run['history_sha256']]
    predictions={(p['game_id'],p['player_id']):p for p in json.loads(run['forecast_json'])['predictions']}
    recent=defaultdict(lambda:deque(maxlen=10))
    for row in eligible_history(snapshot,history):recent[row['player_id']].append(row)
    paired=[];excluded=0
    for item in matched:
        p=predictions[(item['game_id'],item['player_id'])]
        baseline=_baseline_values(recent[item['player_id']],p)
        if baseline is None:excluded+=1;continue
        paired.append(dict(model=p['points_mean'],actual=item['actual_points'],**baseline))
    return paired,excluded


def benchmark_report(db,aggregate=None):
    """``aggregate`` may be this ledger's current performance_report, already computed."""
    aggregate=aggregate if aggregate is not None else performance_report(db);groups=[]
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory=sqlite3.Row
        # Visit runs grouped by input history so each distinct history is decoded once.
        digests=dict(conn.execute('SELECT run_id,history_sha256 FROM runs').fetchall());parsed={}
        for group in aggregate['groups']:
            paired=[];excluded=0;by_run=defaultdict(list)
            for matched in group['matched']:
                # Out-only predictions are assessed by the ledger, not counted as
                # easy zeros against baselines that assume a role on the court.
                if matched['forecast_status']!='projected':continue
                by_run[matched['run_id']].append(matched)
            # Metrics use exactly rounded means, so visiting order cannot change them.
            for run_id,matched in sorted(by_run.items(),key=lambda item:digests.get(item[0],'')):
                values,unsupported=_run_pairs(conn,run_id,matched,parsed)
                paired.extend(values);excluded+=unsupported
            groups.append(dict(season=group['season'],model_version=group['model_version'],
                               metrics=paired_metrics(paired),unsupported_baselines=excluded))
    return dict(selection_policy=aggregate['selection_policy'],groups=groups,
                policy='Same selected projected player-games; last 10 eligible games including explicit DNPs. No historical backdating or automatic promotion.',
                interpretation='Observe prospective errors and coverage before changing constants; paired comparisons alone do not establish causality.')
