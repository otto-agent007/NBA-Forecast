"""Deterministic postgame evidence. Labels are descriptive, never model updates."""
import hashlib
import json
import re
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from .data import _fetch, boxscore_url, normalize_boxscore
from .shots import normalize_shots, playbyplay_url
from .schedule import atomic_json
from .validation import parse_time

VERSION = 'postgame-1.1'
# A blocked or incomplete feed is retried after five minutes, doubling to six hours.
SOURCE_RETRY_SECONDS = (300, 21600)


def classify_game(points):
    final = points[-1]['margin']
    direction = 1 if final >= 0 else -1
    lead = [p['margin'] * direction for p in points]
    sustained = [min(lead[i:]) for i in range(len(lead))]
    early = next((i for i,p in enumerate(points) if p['elapsed'] <= 720 and lead[i] >= 20 and sustained[i] >= 10), None)
    low = next((p['elapsed'] for i,p in enumerate(points) if 2160 <= p['elapsed'] < 2880 and lead[i] >= 20 and sustained[i] >= 15), None)
    if min(lead) <= -15:
        pattern='Comeback win'
    elif early is not None:
        pattern='Early runaway'
    elif abs(final) >= 20 and any(p['elapsed'] >= 1440 and lead[i] >= 20 and sustained[i] >= 15 for i,p in enumerate(points)):
        pattern='Late blowout'
    elif abs(final) <= 8:
        pattern='Competitive finish'
    else:
        pattern='Other game pattern'
    return dict(pattern=pattern,low_leverage_start=low,largest_home_lead=max(0,max(p['margin'] for p in points)),largest_away_lead=max(0,-min(p['margin'] for p in points)))


def player_feedback(prediction, actual):
    result=dict(points_error=None,minutes_error=None,fga_error=None,minutes_effect=None,shot_volume_effect=None,conversion_and_ft_effect=None)
    if not prediction or prediction.get('status') not in ('projected','out'):
        return result
    pm, pf, pp = (prediction.get(k) for k in ('expected_minutes','expected_fga','points_mean'))
    if any(v is None for v in (pm,pf,pp)):
        return result
    am,af,ap = (actual[k] for k in ('minutes','fga','pts'))
    result.update(points_error=ap-pp,minutes_error=am-pm,fga_error=af-pf)
    if pm > 0 and pf > 0:
        minutes=(am-pm)*pp/pm
        volume=(af-am*pf/pm)*pp/pf
        result.update(minutes_effect=minutes,shot_volume_effect=volume,conversion_and_ft_effect=ap-pp-minutes-volume)
    return result


def elapsed(action):
    match=re.fullmatch(r'PT(\d+)M([\d.]+)S',action['clock'])
    if not match:
        raise ValueError('Unrecognized game clock')
    period=action['period']
    remaining=int(match[1])*60+float(match[2])
    length=720 if period<=4 else 300
    if not isinstance(period,int) or period<1 or not 0<=remaining<=length:
        raise ValueError('Invalid period/clock')
    return (period-1)*720+length-remaining if period<=4 else 2880+(period-5)*300+length-remaining


def analyze_game(payload, boxscore, known_at, forecast=None):
    # Reuse strict final-game identity and all-player FGA/FGM/3PA/3PM reconciliation.
    verified=normalize_shots(payload,boxscore,known_at)
    g=boxscore['game']
    gid=g['gameId']
    actions=list(payload['game']['actions'])
    # The feed has labelled a regulation game's closing marker period 5 (0022600111).
    # A trailing game-end action past the final period that carries the final score adds nothing.
    closing=actions[-1] if actions else None
    if (closing and closing['actionType']=='game' and closing.get('subType')=='end' and closing['period']>g['period']
            and (int(closing['scoreHome']),int(closing['scoreAway']))==(g['homeTeam']['score'],g['awayTeam']['score'])):
        actions=actions[:-1]
    # The feed can re-send corrected actions after the game-end marker (0022600101 re-sent
    # a first-quarter layup and the quarter's end); those go back by game clock. Every
    # action before the marker keeps the strict chronology check below.
    marker=next((i for i,a in enumerate(actions) if a['actionType']=='game' and a.get('subType')=='end'),None)
    if marker is not None and marker<len(actions)-1:
        head=actions[:marker+1]
        for late in actions[marker+1:]:
            t=elapsed(late)
            head.insert(next((i for i,a in enumerate(head) if elapsed(a)>t or a is head[-1]),len(head)),late)
        actions=head
    rows=normalize_boxscore(boxscore, f'https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{gid}.json', known_at)
    end=2880+max(0,g['period']-4)*300
    points=[dict(elapsed=0,margin=0,home=0,away=0,period=1,clock='PT12M00.00S',action_number=None)]
    events=[]
    seen=set()
    previous=-1
    last_score=(0,0)
    periods=set()
    ft={str(g[s]['teamId']):0 for s in ('homeTeam','awayTeam')}
    players={r['player_id']:r for r in rows}
    # A period can start with a new lineup and no substitution event, so the last
    # recorded exit alone does not show that a player never came back.
    acted={}
    for a in actions:
        n=a['actionNumber']
        if n in seen:
            raise ValueError('Duplicate play-by-play event')
        seen.add(n)
        t=elapsed(a)
        if t<previous:
            raise ValueError('Nonchronological play-by-play')
        previous=t
        if a['actionType']=='period' and a.get('subType')=='end':
            periods.add(a['period'])
        h,w=int(a['scoreHome']),int(a['scoreAway'])
        if min(h,w)<0:
            raise ValueError('Invalid score')
        if (h,w)!=last_score or a['actionType']=='period':
            points.append(dict(elapsed=t,margin=h-w,home=h,away=w,period=a['period'],clock=a['clock'],action_number=n))
            last_score=(h,w)
        if str(a.get('personId')) in players:
            acted[str(a['personId'])]=t
        if a['actionType']=='freethrow' and a.get('shotResult')=='Made':
            ft[str(a['teamId'])]+=1
        if a['actionType']=='substitution':
            pid=str(a['personId'])
            if pid not in players or str(a['teamId'])!=players[pid]['team_id'] or a.get('subType') not in ('in','out'):
                raise ValueError('Invalid substitution identity')
            events.append(dict(elapsed=t,period=a['period'],clock=a['clock'],action_number=n,player_id=pid,name=players[pid]['name'],team_id=players[pid]['team_id'],direction=a['subType'],margin=h-w))
    if previous!=end or periods!=set(range(1,g['period']+1)) or last_score!=(g['homeTeam']['score'],g['awayTeam']['score']):
        raise ValueError('Incomplete game timeline or final score mismatch')
    for side in ('homeTeam','awayTeam'):
        if ft[str(g[side]['teamId'])]!=g[side]['statistics']['freeThrowsMade']:
            raise ValueError('Free throw reconciliation failed')
    points.append(dict(elapsed=end,margin=last_score[0]-last_score[1],home=last_score[0],away=last_score[1],period=g['period'],clock='PT00M00.00S',action_number=None))
    shape=classify_game(points)
    preds={p['player_id']:p for p in (forecast or {}).get('predictions',[]) if p['game_id']==gid}
    low=shape['low_leverage_start']
    takes=[]
    review=[]
    for row in rows:
        pid=row['player_id']
        pe=[e for e in events if e['player_id']==pid]
        last=pe[-1] if pe else None
        notes=[]
        if row['status']=='dnp':
            notes.append('Did not play. Cause requires sourced review.')
        if low is not None and last and last['direction']=='out' and 1440<=last['elapsed']<=end-120 and abs(last['margin'])>=20 and all(p['margin'] * (1 if last['margin']>0 else -1)>=15 for p in points if p['elapsed']>=last['elapsed']):
            notes.append('Final recorded exit in a sustained large-lead second-half window; possible blowout rest, not confirmed coaching intent.')
        if low is not None and any(e['direction']=='in' and e['elapsed']>=low for e in pe):
            notes.append('Entered during the sustained large-lead window; do not treat this alone as a larger next-game role.')
        feedback=player_feedback(preds.get(pid),row)
        if feedback['minutes_error'] is not None and abs(feedback['minutes_error'])>=5:
            notes.append('Minutes differed by at least five: review rotation, fouls and injury evidence before changing the baseline.')
        if not notes:
            notes.append('No automatic role change inferred. Review repeated minutes and opportunity evidence.')
        if len(notes)>1 or 'possible blowout' in notes[0] or 'Entered during' in notes[0]:
            review.append(dict(player_id=pid,name=row['name'],question='Does this game provide evidence of a persistent role change?',evidence=notes,status='needs_review',automatic_adjustment=False))
        takes.append(dict(**row,prediction=preds.get(pid),feedback=feedback,substitutions=pe,takeaways=notes,last_recorded_exit=last if last and last['direction']=='out' else None,
            last_action_elapsed=acted.get(pid)))
    first=next((p for p in points if p['period']==1 and abs(p['margin'])>=20),None)
    team_stats={str(g[side]['teamId']):{k:g[side]['statistics'].get(k) for k in ('fieldGoalsMade','fieldGoalsAttempted','threePointersMade','threePointersAttempted','freeThrowsMade','freeThrowsAttempted','turnoversTotal','reboundsOffensive','pointsFastBreak','pointsFromTurnovers','benchPoints')} for side in ('homeTeam','awayTeam')}
    return dict(team_stats=team_stats,schema_version=1,analysis_version=VERSION,game_id=gid,game_time=g['gameTimeUTC'],known_at=known_at,home=dict(id=str(g['homeTeam']['teamId']),code=g['homeTeam']['teamTricode'],score=g['homeTeam']['score']),away=dict(id=str(g['awayTeam']['teamId']),code=g['awayTeam']['teamTricode'],score=g['awayTeam']['score']),duration=end,timeline=points,substitutions=events,players=takes,review_queue=review,opening_large_lead=first,forecast_run_id=(forecast or {}).get('run_id'),**shape,coverage=dict(shots_reconciled=verified['coverage']['reconciled'],final_score_reconciled=True,rotation='Recorded substitution events only; complete on-court stints are not inferred.'),policy='All actual outcomes count. Descriptive thresholds are not win probabilities. No automatic model updates.')


def _archived(raw):
    """Latest archived LiveData box score and play-by-play provenance, keyed by (game, kind)."""
    sources={}
    for path in sorted(Path(raw).glob('*.provenance.json')):
        meta=json.loads(path.read_text())
        url=meta.get('source_url','')
        match=re.search(r'/liveData/(boxscore|playbyplay)/\1_(002\d{7})\.json$',url)
        if not match:
            continue
        kind,gid=match.groups()
        key=(gid,kind)
        if key not in sources or parse_time(meta['retrieved_at'])>parse_time(sources[key]['retrieved_at']):
            sources[key]=meta
    return sources


def _payload(raw,meta):
    data=(Path(raw)/(meta['sha256']+'.json')).read_bytes()
    if hashlib.sha256(data).hexdigest()!=meta['sha256']:
        raise ValueError('Archive hash mismatch')
    return json.loads(data)


def collect_sources(directory,schedule,now,*,max_games=1,budget_seconds=40):
    """Archive the LiveData box score and play-by-play of finished games for build_postgames.

    The shot sync completes every game from the official game page and never asks
    for these feeds. Bounded per tick (newest games first, then the oldest retry);
    a game is complete once its archived pair passes the postgame reconciliation,
    so a feed fetched before it was final is fetched again.
    """
    folder=Path(directory)
    raw=folder/'raw'
    path=folder/'postgame'/'sources.json'
    state=json.loads(path.read_text()) if path.exists() else {}
    # The saved calendar can still call a finished game scheduled; a cached final cannot.
    games=[g for g in schedule.get('games',[]) if g.get('start_time') and (folder/'finals'/(g['game_id']+'.json')).exists()]
    queue=[]
    for game in games:
        item=state.get(game['game_id'],{})
        if item.get('status')=='complete' or (item.get('next_retry_at') and parse_time(now)<parse_time(item['next_retry_at'])):
            continue
        attempted=item.get('attempts',0)>0
        queue.append(((1,parse_time(item['last_attempt_at']).timestamp()) if attempted else (0,-parse_time(game['start_time']).timestamp()),game['game_id']))
    started=time.monotonic()
    fetched=0
    errors=[]
    archived=_archived(raw) if queue else {}
    for _,gid in sorted(queue):
        item=state.get(gid,{})
        pair=[archived.get((gid,kind)) for kind in ('playbyplay','boxscore')]
        try:
            if all(pair) and not item.get('attempts'):
                payloads=[_payload(raw,meta) for meta in pair]
                known=max((meta['retrieved_at'] for meta in pair),key=parse_time)
            else:
                if fetched>=max_games or time.monotonic()-started>budget_seconds:
                    continue
                fetched+=1
                item['attempts']=item.get('attempts',0)+1
                item['last_attempt_at']=now
                responses=[_fetch(url,raw) for url in (playbyplay_url(gid),boxscore_url(gid))]
                payloads=[json.loads(body) for body,_ in responses]
                known=max((observed for _,observed in responses),key=parse_time)
            analyze_game(*payloads,known)
            state[gid]=dict(status='complete',attempts=item.get('attempts',0),verified_at=now,known_at=known)
        except (ValueError,KeyError,TypeError,OSError) as exc:
            # An archived pair that fails is fetched again on the next attempt.
            item.setdefault('attempts',1)
            item.setdefault('last_attempt_at',now)
            wait=min(SOURCE_RETRY_SECONDS[0]*2**(item['attempts']-1),SOURCE_RETRY_SECONDS[1])
            state[gid]=dict(item,status='pending',reason=str(exc),next_retry_at=(parse_time(now)+timedelta(seconds=wait)).isoformat())
            errors.append(dict(game_id=gid,error=str(exc)))
    atomic_json(path,state)
    complete=sum(state.get(g['game_id'],{}).get('status')=='complete' for g in games)
    return dict(final_games=len(games),complete=complete,pending=len(games)-complete,fetched=fetched,errors=errors)


def build_postgames(directory):
    """Read only hash-verified archives; no additional API calls or ledger writes."""
    from .dashboard import dashboard_data
    folder=Path(directory)
    sources=_archived(folder/'raw')
    errors=[]
    data=dashboard_data(folder)
    games={g['game_id']:g for g in data['games']}
    output=folder/'postgame'
    output.mkdir(exist_ok=True)
    count=0
    for gid,game in games.items():
        if (gid,'boxscore') not in sources or (gid,'playbyplay') not in sources:
            continue
        try:
            evidence=[sources[(gid,k)] for k in ('playbyplay','boxscore')]
            signature=hashlib.sha256(json.dumps(dict(version=VERSION,evidence=evidence,forecast=game.get('forecast')),sort_keys=True).encode()).hexdigest()
            saved=output/(gid+'.json')
            if saved.exists():
                old=json.loads(saved.read_text())
                if old.get('input_signature')==signature:
                    count+=1
                    continue
            payloads=[_payload(folder/'raw',m) for m in evidence]
            known=max((m['retrieved_at'] for m in evidence),key=parse_time)
            result=analyze_game(*payloads,known,game.get('forecast'))
            result['provenance']=evidence
            result['input_signature']=signature
            atomic_json(output/(gid+'.json'),result)
            count+=1
        except (ValueError,KeyError,TypeError,OSError) as exc:
            errors.append(dict(game_id=gid,error=str(exc)))
            atomic_json(output/(gid+'.json'),dict(game_id=gid,missing_reason=str(exc)))
    index=dict(generated_at=datetime.now(timezone.utc).isoformat(),analysis_version=VERSION,games=count,errors=errors)
    atomic_json(output/'index.json',index)
    return index
