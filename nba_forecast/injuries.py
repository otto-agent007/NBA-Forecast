"""Official report capture and per-game T-minus-30 forecast checkpoints.

Publication times come from NBA PDF filenames in America/New_York. Reports
remain evidence requiring review: this module does not infer healthy status
from omission or extract potentially ambiguous player names from PDF layouts.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import re
from zoneinfo import ZoneInfo

from .data import _fetch
from .validation import parse_time

REPORT_PATTERN = r'https://ak-static\.cms\.nba\.com/referee/injury/Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2}_\d{2}[AP]M)\.pdf'
# Until 2025-12-21 the league published hourly and named each file by its hour
# alone (Injury-Report_2025-01-15_07PM.pdf); the file itself is stamped half
# past (07:30 PM). Only historical backfills meet these names.
LEGACY_REPORT_PATTERN = r'https://ak-static\.cms\.nba\.com/referee/injury/Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2}[AP]M)\.pdf'
LEGACY_LAST_DAY = '2025-12-21'
EASTERN = ZoneInfo('America/New_York')


def report_time(url):
    """Publication time (UTC) of an official report URL in either naming, or None."""
    match=re.fullmatch(REPORT_PATTERN,url)
    if match:
        local=datetime.strptime(match[1]+' '+match[2],'%Y-%m-%d %I_%M%p')
    else:
        match=re.fullmatch(LEGACY_REPORT_PATTERN,url)
        if not match or match[1]>LEGACY_LAST_DAY:
            return None
        local=datetime.strptime(match[1]+' '+match[2],'%Y-%m-%d %I%p')+timedelta(minutes=30)
    return local.replace(tzinfo=EASTERN).astimezone(timezone.utc)


def report_links(html, as_of):
    cutoff=parse_time(as_of)
    found={}
    for match in re.finditer(REPORT_PATTERN,html):
        local=datetime.strptime(match[1]+' '+match[2],'%Y-%m-%d %I_%M%p').replace(tzinfo=ZoneInfo('America/New_York'))
        published=local.astimezone(timezone.utc)
        if published <= cutoff:
            found[match[0]]={'source_url':match[0],'published_at':published.isoformat()}
    return sorted(found.values(),key=lambda r:(r['published_at'],r['source_url']))


def checkpoints(games, reports, checked_at):
    now=parse_time(checked_at)
    result=[]
    for game in games:
        start=parse_time(game['start_time'])
        checkpoint=start-timedelta(minutes=30)
        usable=[r for r in reports if parse_time(r['published_at']) <= min(now,checkpoint)]
        anchor=dict(max(usable,key=lambda r:parse_time(r['published_at']))) if usable else None
        latest=max((r for r in reports if parse_time(r['published_at'])<=now),
                   key=lambda r:parse_time(r['published_at']),default=None)
        age=(min(now,checkpoint)-parse_time(anchor['published_at'])).total_seconds()/60 if anchor else None
        result.append({'game_id':game['game_id'],'start_time':game['start_time'],
                       'checkpoint_at':checkpoint.isoformat(),
                       'phase':'started' if now>=start else 'preliminary' if now<checkpoint else 'final_review',
                       'anchor_report':anchor,'latest_report':dict(latest) if latest else None,
                       'report_status':'missing' if anchor is None else 'stale' if age>15 else 'captured_pending_review',
                       'later_report_available':bool(anchor and latest and parse_time(latest['published_at'])>parse_time(anchor['published_at'])),
                       'action':'Review both teams and resolve statuses; omission does not establish availability.'})
    return result


def fetch_injury_check(snapshot,cache_dir='data/raw',*,capture_cache=None):
    season=snapshot['season']
    if not re.fullmatch(r'20\d{2}-\d{2}',season):
        raise ValueError('invalid season')
    url=f'https://official.nba.com/nba-injury-report-{season}-season/'
    cache=capture_cache if capture_cache is not None else {}
    key=('index',season)
    entry=cache.get(key)
    if isinstance(entry,Exception):
        raise entry
    if entry:
        for game in snapshot['games']:
            target=parse_time(game['start_time'])-timedelta(minutes=30)
            if parse_time(entry[1])<target<=parse_time(snapshot.get('as_of',entry[1])):
                entry=None
                break
    if entry is None:
        try:
            raw,observed=_fetch(url,cache_dir)
            reports=report_links(raw.decode('utf-8'),observed)
            entry=(raw,observed,reports)
            cache[key]=entry
        except (ValueError,OSError) as exc:
            cache[key]=exc
            raise
    raw,observed,reports=entry
    checks=checkpoints(snapshot['games'],reports,observed)
    captured=cache.setdefault('pdfs',{})
    for check in checks:
        for name in ('anchor_report','latest_report'):
            report=check[name]
            if report is None:
                continue
            source=report['source_url']
            if source not in captured:
                try:
                    pdf,retrieved=_fetch(source,cache_dir)
                    if not pdf.startswith(b'%PDF-'):
                        raise ValueError('official injury report response is not a PDF')
                    captured[source]={'sha256':hashlib.sha256(pdf).hexdigest(),'retrieved_at':retrieved}
                except (ValueError,OSError) as exc:
                    captured[source]=exc
                    raise
            if isinstance(captured[source],Exception):
                raise captured[source]
            report.update(captured[source])
    return {'schema_version':1,'policy':'per-game-t-minus-30','index_url':url,
            'index_observed_at':observed,'index_sha256':hashlib.sha256(raw).hexdigest(),
            'checked_at':datetime.now(timezone.utc).isoformat(),'games':checks,
            'availability_applied':False}


def fetch_daily_report(season,cache_dir='data/raw'):
    """Capture the newest published official report, independently of a slate."""
    if not re.fullmatch(r'20\d{2}-\d{2}',season):
        raise ValueError('invalid season')
    url=f'https://official.nba.com/nba-injury-report-{season}-season/'
    raw,observed=_fetch(url,cache_dir)
    reports=report_links(raw.decode('utf-8'),observed)
    if not reports:
        raise ValueError('official injury index has no published report')
    report=dict(reports[-1])
    pdf,retrieved=_fetch(report['source_url'],cache_dir)
    if not pdf.startswith(b'%PDF-'):
        raise ValueError('official injury report response is not a PDF')
    report.update(sha256=hashlib.sha256(pdf).hexdigest(),retrieved_at=retrieved)
    return dict(schema_version=1,season=season,index_url=url,index_observed_at=observed,
                index_sha256=hashlib.sha256(raw).hexdigest(),checked_at=retrieved,report=report)


def validate_final_check(snapshot, check):
    """A final-stage run must carry a captured recent T-30 source for each game.

    This verifies report timing, not that a PDF covers both teams. An analyst
    must review the PDF and supply sourced availability; uncertain players still
    abstain. Later reports are retained separately in the evidence record.
    """
    try:
        cutoff=parse_time(snapshot['as_of'])
        checked=parse_time(check['checked_at'])
        if checked>cutoff:
            raise ValueError('injury check was learned after forecast cutoff')
        rows={r['game_id']:r for r in check['games']}
        if len(rows)!=len(check['games']) or set(rows)!={g['game_id'] for g in snapshot['games']}:
            raise ValueError('injury checkpoint games do not match forecast slate')
        for game in snapshot['games']:
            start=parse_time(game['start_time'])
            target=start-timedelta(minutes=30)
            row=rows[game['game_id']]
            if not target<=checked<=cutoff<start or parse_time(row['checkpoint_at'])!=target:
                raise ValueError('final run requires an actual check in the 30-minute pregame window')
            report=row['anchor_report']
            published=parse_time(report['published_at'])
            if not target-timedelta(minutes=15)<=published<=target:
                raise ValueError('missing or stale T-30 report')
            if not published<=parse_time(report['retrieved_at'])<=checked:
                raise ValueError('injury report retrieval chronology invalid')
            if not re.fullmatch('[a-f0-9]{64}',report['sha256']):
                raise ValueError('missing report hash')
            parsed=report_links(report['source_url'],snapshot['as_of'])
            if len(parsed)!=1 or parse_time(parsed[0]['published_at'])!=published:
                raise ValueError('report URL and publication time disagree')
    except (KeyError,TypeError) as exc:
        raise ValueError('incomplete final injury checkpoint') from exc
