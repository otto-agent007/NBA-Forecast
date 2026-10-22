"""Paired prospective comparisons using immutable inputs and outcome-blind selection."""
import json
import math
import sqlite3
from statistics import fmean
from .profiles import eligible_history
from .store import performance_report


def baseline_values(snapshot,history,prediction):
    rows=[r for r in eligible_history(snapshot,history) if r['player_id']==prediction['player_id']][-10:]
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


def benchmark_report(db):
    aggregate=performance_report(db);groups=[];cache={}
    with sqlite3.connect(db) as conn:
        conn.row_factory=sqlite3.Row
        for group in aggregate['groups']:
            paired=[];excluded=0
            for matched in group['matched']:
                # Out-only predictions are assessed by the ledger, not counted as
                # easy zeros against baselines that assume a role on the court.
                if matched['forecast_status']!='projected':continue
                rid=matched['run_id']
                if rid not in cache:
                    run=conn.execute('SELECT * FROM runs WHERE run_id=?',(rid,)).fetchone()
                    snapshot=json.loads(run['snapshot_json']);history=json.loads(run['history_json'])
                    predictions=json.loads(run['forecast_json'])['predictions']
                    cache[rid]=(snapshot,history,{(p['game_id'],p['player_id']):p for p in predictions})
                snapshot,history,predictions=cache[rid]
                p=predictions[(matched['game_id'],matched['player_id'])];b=baseline_values(snapshot,history,p)
                if b is None:excluded+=1;continue
                paired.append(dict(model=p['points_mean'],actual=matched['actual_points'],**b))
            groups.append(dict(season=group['season'],model_version=group['model_version'],
                               metrics=paired_metrics(paired),unsupported_baselines=excluded))
    return dict(selection_policy=aggregate['selection_policy'],groups=groups,
                policy='Same selected projected player-games; last 10 eligible games including explicit DNPs. No historical backdating or automatic promotion.',
                interpretation='Observe prospective errors and coverage before changing constants; paired comparisons alone do not establish causality.')
