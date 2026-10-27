"""Regularized observational rest/travel effects, fitted before held-out evaluation."""
import hashlib
import json
import math
from .validation import parse_time

FEATURES=('back_to_back','extra_rest','three_in_four','eastward_zones','westward_zones','away')


def context_features(context,*,home):
    missing=[]
    rest=context.get('rest_days')
    shift=context.get('timezone_shift_hours')
    if rest is None:
        missing.append('rest_days')
    if shift is None:
        missing.append('timezone_shift_hours')
    rest=max(0.,min(3.,float(rest))) if rest is not None else None
    shift=max(-12.,min(12.,float(shift))) if shift is not None else 0.
    return dict(back_to_back=float(bool(context.get('back_to_back',rest==0 if rest is not None else False))),
        extra_rest=max(0.,rest-1) if rest is not None else 0.,
        three_in_four=float(bool(context.get('three_in_four',False))),
        eastward_zones=max(0.,shift),westward_zones=max(0.,-shift),away=float(not home)),missing


def _solve(matrix,values):
    a=[list(row)+[value] for row,value in zip(matrix,values)]
    n=len(a)
    for i in range(n):
        pivot=max(range(i,n),key=lambda j:abs(a[j][i]))
        a[i],a[pivot]=a[pivot],a[i]
        if abs(a[i][i])<1e-12:
            raise ValueError('Context fit is singular')
        scale=a[i][i]
        a[i]=[x/scale for x in a[i]]
        for j in range(n):
            if i==j:
                continue
            scale=a[j][i]
            a[j]=[x-scale*y for x,y in zip(a[j],a[i])]
    return [row[-1] for row in a]


def fit_context(samples,*,as_of,ridge=10.,minimum_games=200):
    cutoff=parse_time(as_of)
    rows=[]
    seen=set()
    for sample in samples:
        if parse_time(sample['known_at'])>cutoff or parse_time(sample['game_time'])>=cutoff:
            continue
        key=(sample['game_id'],sample.get('team_id',''))
        if key in seen:
            raise ValueError('Duplicate context training observation')
        seen.add(key)
        values=[sample['baseline_points'],sample['actual_points']]+[sample['features'][k] for k in FEATURES]
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values):
            raise ValueError('Context fit requires finite numeric observations')
        if sample['baseline_points']<=0 or sample['actual_points']<=0:
            continue
        rows.append(sample)
    artifact=dict(schema_version=1,method='ridge_log_residual_context',training_cutoff=as_of,
        samples=len(rows),games=len({r['game_id'] for r in rows}),ridge=ridge,
        evidence_kind='retrospective_chronological_reconstruction',features=list(FEATURES),
        bounds=[.9,1.1],interpretation='Observational association; held-out validation and prospective monitoring remain necessary.')
    if artifact['games']<minimum_games:
        return dict(artifact,status='not_ready',coefficients={})
    if not math.isfinite(ridge) or ridge<=0:
        raise ValueError('Context ridge must be positive')
    rows.sort(key=lambda r:(parse_time(r['game_time']),r['game_id'],r.get('team_id','')))
    n=len(FEATURES)+1
    matrix=[[0.]*n for _ in range(n)]
    values=[0.]*n
    for row in rows:
        x=[1.]+[row['features'][k] for k in FEATURES]
        y=max(-.5,min(.5,math.log(row['actual_points']/row['baseline_points'])))
        for i in range(n):
            values[i]+=x[i]*y
            for j in range(n):
                matrix[i][j]+=x[i]*x[j]
    for i in range(n):
        matrix[i][i]+=ridge if i else 1e-8
    beta=_solve(matrix,values)
    artifact.update(status='ready',intercept=beta[0],coefficients=dict(zip(FEATURES,beta[1:])),
        event_cutoff=max((r['game_time'] for r in rows),key=parse_time),
        training_sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())
    return artifact


def context_adjustment(artifact,context,*,home,as_of):
    features,missing=context_features(context,home=home)
    audit=dict(factor=1.,features=features,missing=missing,status='neutral')
    if not artifact or artifact.get('status')!='ready':
        return dict(audit,reason='Insufficient fitted context evidence')
    if parse_time(artifact['training_cutoff'])>parse_time(as_of):
        return dict(audit,reason='Context fit is future-known')
    contributions={k:features[k]*artifact['coefficients'].get(k,0.) for k in FEATURES}
    value=artifact.get('intercept',0.)+sum(contributions.values())
    if not math.isfinite(value):
        raise ValueError('Invalid fitted context coefficients')
    raw=math.exp(max(-1.,min(1.,value)))
    low,high=artifact.get('bounds',[.9,1.1])
    return dict(audit,status='fitted',factor=min(high,max(low,raw)),unbounded_factor=raw,
        log_contributions=contributions,intercept=artifact.get('intercept',0.),
        training_cutoff=artifact['training_cutoff'],training_samples=artifact['samples'],
        evidence_kind=artifact['evidence_kind'])
