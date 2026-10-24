"""Postgame source fallback, contemporary environment updates and calibration."""
from datetime import timedelta
import hashlib,json,sqlite3
from pathlib import Path
from .espn_actuals import fetch_espn_final,ActualsPending
from .schedule import atomic_json,canonical
from .validation import parse_time,validate_history


def environment_record(game,rows):
    teams=(game['home_team_id'],game['away_team_id']);stats={};periods=[]
    for team in teams:
        members=[r for r in rows if r['team_id']==team]
        if not members or any('game_period' not in r for r in members):raise ValueError('Explicit final period is required for environment duration')
        periods.extend(r['game_period'] for r in members)
        boxes=[r['team_boxscore'] for r in members]
        if any(b!=boxes[0] for b in boxes):raise ValueError('Final team statistics disagree')
        b=boxes[0];fields=('pts','fga','fta','oreb','total_turnovers','team_turnovers','tov')
        if any(type(b.get(k)) is not int or b[k]<0 for k in fields):raise ValueError('Missing final possession statistics')
        if b['total_turnovers']!=b['tov']+b['team_turnovers']:raise ValueError('Final turnover components disagree')
        stats[team]=dict(pts=b['pts'],fga=b['fga'],fta=b['fta'],oreb=b['oreb'],tov=b['total_turnovers'],
            player_tov=b['tov'],team_turnovers=b['team_turnovers'],total_turnovers=b['total_turnovers'])
    if len(set(periods))!=1 or type(periods[0]) is not int or periods[0]<4:raise ValueError('Final periods disagree')
    return dict(game_id=game['game_id'],game_time=game['start_time'],season=rows[0]['season'],
        home_team_id=teams[0],away_team_id=teams[1],team_stats=stats,game_minutes=48+5*(periods[0]-4),
        known_at=max((r['known_at'] for r in rows),key=parse_time),venue_timezone=rows[0].get('venue_timezone'),
        provenance=rows[0]['source_provenance'])


def _aliases(folder,game):
    db=folder/'forecast.sqlite'
    if not db.exists():return []
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as conn:
        row=conn.execute("SELECT snapshot_json FROM runs WHERE mode='live' AND json_extract(snapshot_json,'$.games[0].game_id')=? ORDER BY rowid DESC LIMIT 1",(game['game_id'],)).fetchone()
    if not row:return []
    snapshot=json.loads(row[0])
    if snapshot.get('preflight',{}).get('status')!='passed':return []
    return [a for r in snapshot.get('roster_reviews',[]) if r['game_id']==game['game_id'] for a in r.get('name_aliases',[])]


def sync_finished_games(directory,schedule,history,as_of):
    folder=Path(directory);now=parse_time(as_of);finals=folder/'finals';finals.mkdir(exist_ok=True)
    report=dict(imported=[],pending=[],pending_details=[],errors=[],environment_updates=[],environment_warnings=[])
    roster_path=folder/'roster.json'
    if not roster_path.exists():return dict(report,reason='No verified roster for outcome identities')
    roster=json.loads(roster_path.read_text())
    for game in schedule['games']:
        if not game.get('start_time') or game['status'] in ('postponed','tbd'):continue
        elapsed=now-parse_time(game['start_time'])
        if not timedelta(hours=2)<=elapsed<=timedelta(hours=48):continue
        target=finals/(game['game_id']+'.json')
        try:
            if target.exists():
                rows=json.loads(target.read_text());validate_history(rows)
            else:
                rows=fetch_espn_final(game,roster,folder/'references',history=history,name_aliases=_aliases(folder,game))
                validate_history(rows);atomic_json(target,rows);report['imported'].append(game['game_id'])
            if rows and rows[0].get('source_provenance'):
                try:
                    record=environment_record(game,rows);digest=hashlib.sha256(canonical(record)).hexdigest()
                    path=folder/'environment-finals/records'/(digest+'.json')
                    if not path.exists():atomic_json(path,record);report['environment_updates'].append(game['game_id'])
                except (ValueError,KeyError,TypeError) as exc:
                    report['environment_warnings'].append(dict(game_id=game['game_id'],error=str(exc)))
        except ActualsPending as exc:
            report['pending'].append(game['game_id'])
            report['pending_details'].append(dict(game_id=game['game_id'],reason=str(exc),evidence=getattr(exc,'details',None)))
        except (ValueError,OSError,KeyError,TypeError) as exc:
            report['errors'].append(dict(game_id=game['game_id'],error=str(exc)))
    return report
