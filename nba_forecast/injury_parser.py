"""Conservative exact-identity parser with explicit complete-report assumptions."""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import re
import subprocess
import unicodedata
from .validation import parse_time
from .schedule import EASTERN

PARSER_VERSION='layout-exact-3'

STATUS = re.compile(r'\b(Available|Out|Questionable|Doubtful|Probable)\b')


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',text).casefold() if c.isalnum())


def pdf_text(path,expected_hash):
    path=Path(path)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected_hash:
        raise ValueError('injury PDF hash mismatch')
    try:
        result=subprocess.run(['pdftotext','-layout',str(path),'-'],capture_output=True,text=True,timeout=30,check=True)
    except (OSError,subprocess.SubprocessError) as exc:
        raise ValueError('Poppler pdftotext is required and must extract this PDF successfully') from exc
    if 'Injury Report:' not in result.stdout:raise ValueError('missing official injury report heading')
    return result.stdout


def parse_report(text,schedule,roster,evidence):
    names=defaultdict(list);teams={};games=defaultdict(list)
    for p in roster['players']:names[(p['team_id'],normalized(p['name']))].append(p['player_id'])
    for g in schedule['games']:
        for side in ('home','away'):
            name=g[side+'_name'];tid=g[side+'_team_id']
            if tid!='0':teams[normalized(name)]=tid
            if 'Clippers' in name:teams[normalized('LA Clippers')]=tid
        if g['start_time']:
            day=parse_time(g['start_time']).astimezone(EASTERN).date().isoformat()
            games[(day,str(g['away_tricode'])+'@'+str(g['home_tricode']))].append(g)
    day=matchup=team=None;rows=[];issues=[];coverage=set();dates=set()
    for line in text.splitlines():
        if 'Injury Report:' in line or 'Player Name' in line or re.search(r'Page \d+ of \d+',line):continue
        date=re.search(r'\b(\d{2}/\d{2}/\d{4})\b',line)
        if date:
            day=datetime.strptime(date[1],'%m/%d/%Y').date().isoformat();dates.add(day);team=None;matchup=None
            line=line[date.end():]
        match=re.search(r'\b([A-Z]{2,3}@[A-Z]{2,3})\b',line)
        if match:matchup=match[1];team=None;line=line[match.end():]
        line=re.sub(r'^\s*\d{2}:\d{2}\s*\(ET\)\s*','',line)
        tokens=[v.strip() for v in re.split(r'\s{2,}',line.strip()) if v.strip()]
        for token in tokens:
            if normalized(token) in teams:
                team=teams[normalized(token)];coverage.add((day,matchup,team))
        status=STATUS.search(line)
        if 'NOT YET SUBMITTED' in line.upper():
            issues.append(dict(kind='team_not_submitted',date=day,matchup=matchup,team_id=team));continue
        if not status:
            # A player-looking row cannot silently become an omission. Wrapped
            # or unknown statuses make complete-team inference unsafe.
            if any(',' in token for token in tokens):
                issues.append(dict(kind='unparsed_player_row',text=line.strip(),team_id=team,date=day,matchup=matchup))
            continue
        # A report row's player name is the final multi-space-delimited field
        # before Current Status. Wrapped reason lines cannot become player rows.
        before=line[:status.start()].strip();parts=re.split(r'\s{2,}',before)
        name=parts[-1] if parts else ''
        if ',' not in name:
            issues.append(dict(kind='unparsed_status_row',text=line.strip(),team_id=team,date=day,matchup=matchup));continue
        last,first=name.split(',',1);key=normalized(first+' '+last)
        ids=names.get((team,key),[]);candidates=games.get((day,matchup),[])
        candidates=[g for g in candidates if team in (g['home_team_id'],g['away_team_id'])]
        if len(ids)!=1 or len(candidates)!=1:
            issues.append(dict(kind='unmatched_identity_or_game',name=name,date=day,matchup=matchup,team_id=team));continue
        label=status[1]
        rows.append(dict(player_id=ids[0],team_id=team,game_id=candidates[0]['game_id'],reported_status=label,
                         status={'Available':'available','Out':'out'}.get(label,'questionable'),
                         source_name=name,source_line=line.strip(),**evidence))
    for (game_day,code),candidates in games.items():
        if game_day not in dates:continue
        for game in candidates:
            for tid in (game['home_team_id'],game['away_team_id']):
                if (game_day,code,tid) not in coverage:
                    issues.append(dict(kind='missing_team_coverage',game_id=game['game_id'],team_id=tid,date=game_day,matchup=code))
    if not dates:issues.append(dict(kind='missing_report_game_dates'))
    grouped=defaultdict(list)
    for row in rows:grouped[(row['game_id'],row['player_id'])].append(row)
    unique=[]
    for key,group in grouped.items():
        if len({r['reported_status'] for r in group})>1:
            issues.append(dict(kind='conflicting_player_status',game_id=key[0],player_id=key[1],team_id=group[0]['team_id']))
        else:unique.append(group[0])
    pages=[(int(a),int(b)) for a,b in re.findall(r'Page (\d+) of (\d+)',text)]
    pages_complete=bool(pages) and len({b for _,b in pages})==1 and {a for a,_ in pages}==set(range(1,pages[0][1]+1))
    complete_teams=[]
    headings=re.findall(r'Injury Report:\s*(\d{2}/\d{2}/\d{2})\s+(\d{2}:\d{2}\s*[AP]M)',text)
    heading_matches=bool(headings) and all(datetime.strptime(d+' '+t.replace(' ',''),'%m/%d/%y %I:%M%p').replace(tzinfo=EASTERN)==parse_time(evidence['published_at']) for d,t in headings)
    if pages_complete and heading_matches:
        for d,m,t in sorted(coverage,key=str):
            if not any(i.get('team_id') in (None,t) and i.get('date',d)==d and i.get('matchup',m)==m for i in issues):
                complete_teams.append(dict(date=d,matchup=m,team_id=t))
    return dict(schema_version=1,parser_version=PARSER_VERSION,pages_complete=pages_complete,heading_matches=heading_matches,complete_teams=complete_teams,evidence=evidence,rows=unique,issues=issues,
                team_coverage=[dict(date=d,matchup=m,team_id=t) for d,m,t in sorted(coverage,key=str)],
                policy='Exact name/team/game joins only. Omitted players remain unknown.')


def apply_report(snapshot,parsed,game_ids=None,*,allow_not_listed=False):
    out=deepcopy(snapshot);cutoff=parse_time(out['as_of']);evidence=parsed['evidence']
    if not parse_time(evidence['published_at'])<=parse_time(evidence['retrieved_at'])<=cutoff:
        raise ValueError('injury report evidence chronology invalid')
    if cutoff-parse_time(evidence['published_at'])>timedelta(hours=24):raise ValueError('injury report is stale')
    games={g['game_id']:g for g in out['games']};players={p['player_id']:p for p in out['roster']['players']}
    target=set(games) if game_ids is None else set(game_ids)
    if not target<=games.keys():raise ValueError('injury application targets unknown game')
    teams={games[gid][side] for gid in target for side in ('home_team_id','away_team_id')}
    for p in players.values():
        if p['team_id'] in teams:
            p['status']='unknown'
            for field in ('status_source_url','status_observed_at','status_published_at','status_game_id','reported_status','status_basis'):p.pop(field,None)
    for gid in target:
        g=games[gid];day=parse_time(g['start_time']).astimezone(EASTERN).date().isoformat()
        for tid in (g['home_team_id'],g['away_team_id']):
            if not any(c['date']==day and c['team_id']==tid for c in parsed['team_coverage']):
                out.setdefault('injury_coverage_warnings',[]).append(dict(game_id=gid,team_id=tid,kind='missing_team_coverage'))
    for row in parsed['rows']:
        if row['game_id'] not in target:continue
        p=players.get(row['player_id']);g=games[row['game_id']]
        if not p or p['team_id']!=row['team_id'] or p['team_id'] not in (g['home_team_id'],g['away_team_id']):
            raise ValueError('injury row does not match roster/game')
        p.update(status=row['status'],status_source_url=evidence['source_url'],status_observed_at=evidence['retrieved_at'],
                 status_published_at=evidence['published_at'],status_game_id=row['game_id'],reported_status=row['reported_status'],status_basis='explicit_report_status')
    if allow_not_listed:
        listed={(r['game_id'],r['player_id']) for r in parsed['rows']}
        for gid in target:
            g=games[gid];day=parse_time(g['start_time']).astimezone(EASTERN).date().isoformat()
            matchup=str(g.get('away_tricode'))+'@'+str(g.get('home_tricode'))
            for p in players.values():
                covered=any(c['date']==day and c['matchup']==matchup and c['team_id']==p['team_id'] for c in parsed.get('complete_teams',[]))
                if p['team_id'] in (g['home_team_id'],g['away_team_id']) and covered and (gid,p['player_id']) not in listed:
                    p.update(status='available',reported_status='Not listed',status_basis='not_listed_complete_report',
                             status_source_url=evidence['source_url'],status_observed_at=evidence['retrieved_at'],
                             status_published_at=evidence['published_at'],status_game_id=gid)
    out.setdefault('injury_extractions',[]).append(parsed)
    return out
