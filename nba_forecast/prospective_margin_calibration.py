"""Fit margin corrections only from immutable pregame forecasts and complete finals.

The correction is trained on earlier games and checked on later, separately
saved games. This is a validation gate, not a future coverage guarantee.
"""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from statistics import fmean
from zoneinfo import ZoneInfo

from .data import _NextDataParser
from .full_boxscore_sources import validate_complete_official_final
from .margins import METHOD as BASE_METHOD, SETTLEMENT, SUPPORT, _pmf, _sha
from .schedule import atomic_json
from .validation import parse_time,validate_history


METHOD='prospective_margin_location_scale_v1'
MIN_TRAIN=64
MIN_TRAIN_DAYS=8
MIN_VALIDATION=32
WINDOW_GAMES=112
EASTERN=ZoneInfo('America/New_York')
MAX_BRIER_REGRESSION=.01
SCALE_MIN=.7
SCALE_MAX=1.4
BIAS_MAX=8.


def _finite(value,positive=False):
    return (not isinstance(value,bool) and isinstance(value,(int,float))
            and math.isfinite(value) and (value>0 if positive else True))


def _empty():
    return dict(samples=[],counts=dict(live_runs=0,model_runs=0,selected_games=0,
        matched_games=0),exclusions={},errors=[])


def _verified_final_source(root,game_id,rows):
    """Require the archived official page and matching player/team provenance."""
    url=f'https://www.nba.com/game/{game_id}/box-score'
    played=[r for r in rows if r['status']=='played']
    digests={r.get('source_sha256') for r in played}
    if len(digests)!=1 or not re.fullmatch('[0-9a-f]{64}',next(iter(digests)) or ''):
        raise ValueError('Final lacks one official source digest')
    digest=next(iter(digests))
    source=root/'raw'/(digest+'.html')
    metadata=root/'raw'/(digest+'.provenance.json')
    if not source.exists() or not metadata.exists():
        raise FileNotFoundError('Original official box page is unavailable')
    raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=digest:
        raise ValueError('Official box page hash differs')
    meta=json.loads(metadata.read_text())
    if meta.get('source_url')!=url or meta.get('sha256')!=digest:
        raise ValueError('Official box archive metadata differs')
    retrieved=parse_time(meta['retrieved_at'])
    if not parse_time(rows[0]['game_time'])<retrieved:
        raise ValueError('Archived NBA page was captured before tipoff')
    parser=_NextDataParser()
    try:
        parser.feed(raw.decode('utf-8'))
        official=json.loads(''.join(parser.parts))['props']['pageProps']['game']
        if (official['gameId']!=game_id or official['gameStatus']!=3
                or parse_time(official['gameTimeUTC'])!=parse_time(rows[0]['game_time'])
                or official['period']!=rows[0]['game_period']):
            raise ValueError('Archived NBA final game identity differs')
        for side in ('home','away'):
            team=official[side+'Team']
            tid=str(team['teamId'])
            if (tid not in {r['team_id'] for r in rows}
                    or team['score']!=sum(r['pts'] for r in rows if r['team_id']==tid)):
                raise ValueError('Archived NBA final team score differs')
        if str(official['homeTeam']['teamId'])==str(official['awayTeam']['teamId']):
            raise ValueError('Archived NBA final matchup is invalid')
    except (KeyError,TypeError,UnicodeDecodeError,AttributeError,json.JSONDecodeError) as exc:
        raise ValueError(f'Archived NBA final page cannot be decoded: {exc}') from exc
    for row in rows:
        provenance=row.get('source_provenance',{}).get('nba_full_boxscore',{})
        team_provenance=row.get('team_boxscore_provenance',{})
        direct_official=(row.get('source_url')==url and row.get('source_sha256')==digest)
        team_source_valid=(
            (team_provenance.get('source_url')==url and team_provenance.get('source_sha256')==digest
             and team_provenance.get('game_id')==game_id and team_provenance.get('team_id')==row['team_id'])
            if team_provenance else direct_official)
        if ((row['status']=='played' and (row.get('source_url')!=url or row.get('source_sha256')!=digest))
                or (row['status']!='played' and (row['pts']!=0 or row['minutes']!=0))
                or provenance.get('source_url')!=url or provenance.get('sha256')!=digest
                or not parse_time(row['game_time'])<parse_time(provenance['retrieved_at'])<=parse_time(row['known_at'])
                or not team_source_valid
                or retrieved>parse_time(row['known_at'])):
            raise ValueError('Player/team official final provenance differs')
    for team in {r['team_id'] for r in rows}:
        members=[r for r in rows if r['team_id']==team]
        boxes=[r.get('team_boxscore') for r in members]
        if not isinstance(boxes[0],dict) or any(b!=boxes[0] for b in boxes):
            raise ValueError('Official team box is incomplete or inconsistent')
        if boxes[0].get('pts')!=sum(r['pts'] for r in members):
            raise ValueError('Official team and player final points differ')
    return digest


def collect_margin_samples(directory,model_key,as_of):
    """Select latest same-model pregame run before opening a single final file."""
    cutoff=parse_time(as_of)
    root=Path(directory)
    result=_empty()
    if not isinstance(model_key,str) or not model_key:
        raise ValueError('Model key is required')
    db=root/'forecast.sqlite'
    if not db.exists():
        return result
    chosen={}
    counts=Counter(result['counts'])
    excluded=Counter()
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        for row in conn.execute("SELECT rowid AS insertion_order,run_id,created_at,as_of,snapshot_json,"
                                "snapshot_sha256,forecast_json,forecast_sha256 FROM runs "
                                "WHERE mode='live' AND scope='league'"):
            counts['live_runs']+=1
            try:
                created=parse_time(row['created_at'])
                if created>cutoff:
                    excluded['future_run']+=1
                    continue
                if any(hashlib.sha256(row[k+'_json'].encode()).hexdigest()!=row[k+'_sha256']
                       for k in ('snapshot','forecast')):
                    raise ValueError('Immutable run hash differs')
                forecast=json.loads(row['forecast_json'])
                if forecast.get('model_key')!=model_key:
                    continue
                counts['model_runs']+=1
                snapshot=json.loads(row['snapshot_json'])
                if not parse_time(row['as_of'])==parse_time(forecast['as_of'])==parse_time(snapshot['as_of'])<=created:
                    raise ValueError('Run observation chronology differs')
                if forecast['season']!=snapshot['season']:
                    raise ValueError('Season identity differs')
                games=snapshot['games']
                distributions=forecast.get('margin_forecasts',[])
                if not isinstance(games,list) or not isinstance(distributions,list):
                    raise ValueError('Game or margin list is malformed')
                for game in games:
                    gid=game['game_id']
                    start=parse_time(game['start_time'])
                    if created>=start:
                        excluded['not_pregame']+=1
                        continue
                    if game.get('season_type')!='Regular Season':
                        continue
                    if not gid.startswith('002') or len(gid)!=10 or not gid.isdigit():
                        raise ValueError('Invalid NBA game ID')
                    candidates=[d for d in distributions if d.get('game_id')==gid]
                    if len(candidates)>1:
                        raise ValueError('Duplicate saved margin distribution')
                    item=dict(game=game,distribution=candidates[0] if candidates else None,
                        season=snapshot['season'],run_id=row['run_id'],created_at=row['created_at'],
                        forecast_sha256=row['forecast_sha256'])
                    rank=(created,row['insertion_order'])
                    if gid not in chosen or rank>chosen[gid][0]:
                        chosen[gid]=(rank,item)
            except (KeyError,ValueError,TypeError,OverflowError,AttributeError) as exc:
                result['errors'].append(dict(run_id=row['run_id'],reason=str(exc)))
    counts['selected_games']=len(chosen)
    for gid,(_,item) in sorted(chosen.items()):
        game=item['game']
        d=item['distribution']
        if not isinstance(d,dict) or d.get('status')!='estimated':
            excluded['latest_margin_unavailable']+=1
            continue
        try:
            if (d.get('model_key')!=model_key or any(d.get(k)!=game[k] for k in ('game_id','home_team_id','away_team_id'))
                    or d.get('settlement')!=SETTLEMENT or d.get('method')!=BASE_METHOD
                    or d.get('calibration_status') not in ('uncalibrated','prospective_validated')):
                raise ValueError('Saved margin identity or method differs')
            if d.get('pmf_sha256')!=_sha(d['pmf']):
                raise ValueError('Saved margin mass hash differs')
            if not math.isclose(math.fsum(r['probability'] for r in d['pmf']),1.,rel_tol=0.,abs_tol=1e-10):
                raise ValueError('Saved margin mass is incomplete')
            mean=d.get('uncalibrated_mean_margin',d.get('mean_margin'))
            sd=d.get('uncalibrated_standard_deviation',d.get('standard_deviation'))
            if not _finite(mean) or not _finite(sd,positive=True):
                raise ValueError('Invalid raw margin location or scale')
            path=root/'finals'/(gid+'.json')
            if not path.exists():
                excluded['missing_final']+=1
                continue
            raw=path.read_bytes()
            rows=json.loads(raw)
            validate_history(rows)
            if any(parse_time(r['known_at'])>cutoff for r in rows):
                excluded['future_known_final']+=1
                continue
            try:
                validate_complete_official_final(rows)
            except ValueError:
                excluded['incomplete_final']+=1
                continue
            try:
                _verified_final_source(root,gid,rows)
            except FileNotFoundError:
                excluded['unverified_final_source']+=1
                continue
            if ({r['team_id'] for r in rows}!={game['home_team_id'],game['away_team_id']}
                    or any(r['game_id']!=gid or r['season']!=item['season']
                           or parse_time(r['game_time'])!=parse_time(game['start_time'])
                           for r in rows)):
                raise ValueError('Final game, team, season, time or known-at identity differs')
            known=max((r['known_at'] for r in rows),key=parse_time)
            if parse_time(known)<=parse_time(game['start_time']):
                raise ValueError('Final was known before tipoff')
            scores={t:sum(r['pts'] for r in rows if r['team_id']==t)
                    for t in (game['home_team_id'],game['away_team_id'])}
            actual=scores[game['home_team_id']]-scores[game['away_team_id']]
            if actual==0:
                raise ValueError('Final score cannot be tied')
            result['samples'].append(dict(game_id=gid,model_key=model_key,
                home_team_id=game['home_team_id'],away_team_id=game['away_team_id'],
                game_time=game['start_time'],forecast_created_at=item['created_at'],
                actual_known_at=known,base_mean_margin=mean,base_standard_deviation=sd,
                actual_home_margin=actual,run_id=item['run_id'],
                forecast_sha256=item['forecast_sha256'],actuals_sha256=hashlib.sha256(raw).hexdigest()))
        except (KeyError,ValueError,TypeError,OverflowError,AttributeError) as exc:
            excluded['invalid_margin_or_final']+=1
            result['errors'].append(dict(game_id=gid,reason=str(exc)))
    result['samples'].sort(key=lambda r:(parse_time(r['game_time']),r['game_id']))
    counts['matched_games']=len(result['samples'])
    result.update(counts=dict(counts),exclusions=dict(excluded))
    return result


def _score(rows,bias,scale):
    brier=[]
    ranked=[]
    for row in rows:
        location=row['base_mean_margin']+bias
        sd=row['base_standard_deviation']*scale
        if abs(location)+3*sd>=SUPPORT:
            raise ValueError('Calibrated mass exceeds supported range')
        pmf=_pmf(location,sd)
        actual=row['actual_home_margin']
        win=math.fsum(r['probability'] for r in pmf if r['margin']>0)
        brier.append((win-(actual>0))**2)
        cdf=0.
        errors=[]
        mass={r['margin']:r['probability'] for r in pmf}
        for k in range(-SUPPORT,SUPPORT):
            cdf+=mass.get(k,0.)
            errors.append((cdf-(actual<=k))**2)
        ranked.append(math.fsum(errors))
    return dict(home_win_brier=fmean(brier),ranked_probability_score=fmean(ranked))


def _fit_window(rows,model_key,as_of):
    """Replay one fixed, nonoverlapping 112-game attempt from its exact samples."""
    value=dict(status='not_ready',reason='invalid_evidence_window')
    if not isinstance(rows,list) or len(rows)!=WINDOW_GAMES:
        return value
    try:
        cutoff=parse_time(as_of)
        ids=set()
        previous=None
        for row in rows:
            gid=row['game_id']
            game=parse_time(row['game_time'])
            forecast=parse_time(row['forecast_created_at'])
            known=parse_time(row['actual_known_at'])
            if (row['model_key']!=model_key or gid in ids or not re.fullmatch(r'002\d{7}',gid)
                    or not forecast<game<known<=cutoff or (previous is not None and game<previous)
                    or row['home_team_id']==row['away_team_id']
                    or type(row['actual_home_margin']) is not int or row['actual_home_margin']==0
                    or not _finite(row['base_mean_margin']) or not _finite(row['base_standard_deviation'],positive=True)
                    or any(not re.fullmatch('[0-9a-f]{64}',row[k]) for k in ('forecast_sha256','actuals_sha256'))):
                return value
            ids.add(gid)
            previous=game
        def day(row):
            return parse_time(row['game_time']).astimezone(EASTERN).date()
        boundary=None
        for index in range(MIN_TRAIN,WINDOW_GAMES-MIN_VALIDATION+1):
            if day(rows[index-1])>=day(rows[index]):
                continue
            if len({day(r) for r in rows[:index]})<MIN_TRAIN_DAYS:
                continue
            latest_train=max((r['actual_known_at'] for r in rows[:index]),key=parse_time)
            first_validation=min((r['forecast_created_at'] for r in rows[index:]),key=parse_time)
            if parse_time(latest_train)<parse_time(first_validation):
                boundary=index
                break
        if boundary is None:
            return dict(status='not_ready',reason='no_chronologically_separate_validation_window')
        train,validation=rows[:boundary],rows[boundary:]
        days={day(r) for r in train}
        latest=max((r['actual_known_at'] for r in train),key=parse_time)
        first=min((r['forecast_created_at'] for r in validation),key=parse_time)
        if parse_time(latest)>=parse_time(first):
            return dict(status='not_ready',reason='training_final_after_validation_forecast')
        residuals=[r['actual_home_margin']-r['base_mean_margin'] for r in train]
        weight=len(train)/(len(train)+40.)
        bias=max(-BIAS_MAX,min(BIAS_MAX,fmean(residuals)*weight))
        normalized=[(e-bias)/r['base_standard_deviation'] for e,r in zip(residuals,train)]
        scale=max(SCALE_MIN,min(SCALE_MAX,1.+(math.sqrt(fmean(z*z for z in normalized))-1.)*weight))
        result=dict(training_games=len(train),training_days=len(days),validation_games=len(validation),
            training_latest_actual_known_at=latest,validation_first_forecast_created_at=first,
            training_last_game_time=train[-1]['game_time'],validation_first_game_time=validation[0]['game_time'],
            location_bias=bias,scale_factor=scale,evidence_sha256=_sha(rows),
            training_source_sha256=_sha(train),validation_source_sha256=_sha(validation))
        try:
            baseline=_score(validation,0.,1.)
            candidate=_score(validation,bias,scale)
        except ValueError:
            return dict(status='not_ready',reason='validation_outside_support',**result)
        result['validation_metrics']=dict(baseline_home_win_brier=baseline['home_win_brier'],
            candidate_home_win_brier=candidate['home_win_brier'],
            baseline_ranked_probability_score=baseline['ranked_probability_score'],
            candidate_ranked_probability_score=candidate['ranked_probability_score'],
            selection='Frozen later games in one fixed 112-game window; promotion gate, not untouched future performance.')
        if (candidate['ranked_probability_score']>baseline['ranked_probability_score']
                or candidate['home_win_brier']>baseline['home_win_brier']+MAX_BRIER_REGRESSION):
            return dict(status='not_ready',reason='did_not_improve_later_games',**result)
        return dict(status='ready',reason=None,**result)
    except (KeyError,TypeError,ValueError,OverflowError,ZeroDivisionError):
        return value


def validate_margin_calibration(artifact,model_key,as_of):
    """Recompute fitted values and proper scores from frozen evidence bytes."""
    if not isinstance(artifact,dict):
        return False
    try:
        if (artifact['schema_version']!=1 or artifact['status']!='ready'
                or artifact['method']!=METHOD or artifact['model_key']!=model_key
                or type(artifact['window_index']) is not int or artifact['window_index']<0
                or parse_time(artifact['training_cutoff'])>parse_time(as_of)):
            return False
        rows=artifact['evidence_samples']
        expected=_fit_window(rows,model_key,artifact['training_cutoff'])
        if expected['status']!='ready':
            return False
        return all(artifact.get(k)==v for k,v in expected.items())
    except (KeyError,TypeError,ValueError,OverflowError):
        return False


def verify_margin_calibration_sources(directory,artifact):
    """Re-select the immutable ledger/final inputs before any promotion."""
    if not validate_margin_calibration(artifact,artifact.get('model_key'),artifact.get('training_cutoff')):
        return False
    collection=collect_margin_samples(directory,artifact['model_key'],artifact['training_cutoff'])
    if collection['errors']:
        return False
    index=artifact['window_index']
    rows=collection['samples'][index*WINDOW_GAMES:(index+1)*WINDOW_GAMES]
    return rows==artifact['evidence_samples'] and _sha(rows)==artifact['evidence_sha256']


def fit_prospective_margin(directory,model_key,as_of):
    """Fit one fixed window once; future games cannot retest the same window."""
    parse_time(as_of)
    collection=collect_margin_samples(directory,model_key,as_of)
    rows=collection['samples']
    result=dict(schema_version=1,status='not_ready',reason='insufficient_games',
        method=METHOD,model_key=model_key,training_cutoff=as_of,sample_count=len(rows),
        minimum_training_games=MIN_TRAIN,minimum_training_days=MIN_TRAIN_DAYS,
        minimum_validation_games=MIN_VALIDATION,training_games=0,training_days=0,
        validation_games=0,location_bias=None,scale_factor=None,evidence_sha256=None,
        collection_counts=collection['counts'],collection_exclusions=collection['exclusions'],
        collection_errors=collection['errors'],validation_metrics=None,
        caveat='Prospective saved-game validation is finite and cannot guarantee future coverage.')
    if collection['errors']:
        result['reason']='collection_errors'
        return result
    if len(rows)<WINDOW_GAMES:
        return result
    index=len(rows)//WINDOW_GAMES-1
    window=rows[index*WINDOW_GAMES:(index+1)*WINDOW_GAMES]
    root=Path(directory)/'margin-calibration-attempts'/hashlib.sha256(model_key.encode()).hexdigest()
    path=root/(str(index)+'.json')
    if path.exists():
        saved=json.loads(path.read_text())
        if saved.get('evidence_sha256')!=_sha(window):
            result['reason']='window_changed_after_first_attempt'
            return result
        return saved
    result.update(window_index=index,evidence_samples=window)
    result.update(_fit_window(window,model_key,as_of))
    atomic_json(path,result)
    return result
