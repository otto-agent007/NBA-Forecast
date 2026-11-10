"""News timing: when the line moves before tip, did the forecast see the news, and did it move as much?

Three steps, all read-only on a COPY of the production directory (never production):

``extract``  reads every pregame run of every finished game from the copy's ledger
             (``run_records`` by ``run_id`` only, rows in ledger order, throttled to a
             third of the time on the disk) and writes one small JSON line per
             (run, game): created_at/as_of, the published margin (the market report's
             basis: the margin forecast's ``mean_margin`` when estimated, else the
             player sum), its parts, the injury report edition the run applied
             (publication and capture time), each player's status as applied and its
             source, the participation probabilities, the projected lineups and the
             ESPN fallbacks. It resumes from an existing output. Heavy: run it through
             the I/O guard (flock + MemoryHigh scope + guard.py).

``prepare``  indexes the official injury report editions we captured (first
             ``retrieved_at`` over ``injuries/games/*/records``, the daily captures and the
             runs' own evidence), parses each ``raw/<sha>.pdf`` with the production parser,
             and reads the ESPN injury lists in the summary pages the line captures archived
             (``references/raw/<sha>.json``; a page is archived when a line changed, so an
             ESPN change's capture time is an upper bound; ESPN's own entry date is kept).

``analyze``  joins those with the line captures (``market/game-lines``, recovered closes
             excluded; ``market/kalshi`` implied margins beside them) and the final box
             scores, and prints every table. The forecast path uses the runs that published
             an estimated margin forecast; runs that fell back to the player sum (broken
             totals) are counted and set aside (``--all-runs`` keeps them).

Times: all UTC. A report edition's publication time is the time in its file name
(``published_at``); our capture time is the first ``retrieved_at`` of that edition in any
of our check records. A run applied an edition when the edition is its snapshot's parsed
extraction evidence. A player's first listing for a game is compared with what our latest
earlier run assumed for him. Lines: the spread at a time T is the mean over providers of
each provider's last capture at or before T (``market_lines.closing_lines`` rule at T);
captures are appended when a line changes, every 15 minutes within a day of tip.

± is ONE standard error clustered by Eastern game date (``backtest._mean_se``; slopes: CR1
clustered by date) unless a table says otherwise.

    python3 scripts/research/news_timing.py extract --copy COPY --output DIR/news-runs.jsonl
    python3 scripts/research/news_timing.py prepare --copy COPY --runs DIR/news-runs.jsonl --out DIR
    python3 scripts/research/news_timing.py analyze --copy COPY --folder DIR [--reference main-rebuilt.json]
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_replay as LR  # noqa: E402
from nba_forecast.validation import parse_time  # noqa: E402

TOOL = 'news-timing-1'


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# ---------------------------------------------------------------- extract

def published_margin(forecast, game):
    """The margin the market report counts as published for this game (market_lines.market_report)."""
    gid = game['game_id']
    mf = next((m for m in forecast.get('margin_forecasts') or [] if m.get('game_id') == gid), None) or {}
    totals = next((t for t in forecast.get('game_totals') or [] if t.get('game_id') == gid), None) or {}
    teams = {t.get('side') or ('home' if t['team_id'] == game['home_team_id'] else 'away'): t for t in totals.get('teams', [])}
    home = (teams.get('home') or {}).get('points_mean')
    away = (teams.get('away') or {}).get('points_mean')
    saved = mf.get('player_sum_margin')
    player_sum = saved if _finite(saved) else (home - away if _finite(home) and _finite(away) else None)
    published = mf.get('mean_margin') if mf.get('status') == 'estimated' else None
    basis = 'margin_forecast' if _finite(published) else 'player_sum'
    side = {s: {k: t.get(k) for k in ('points_mean', 'expected_players', 'out_players', 'questionable_players', 'projected_players')}
            for s, t in teams.items()}
    return dict(published=published if basis == 'margin_forecast' else player_sum, basis=basis, player_sum=player_sum,
                team_rating_margin=mf.get('team_rating_margin'), absence_rating_adjustment=mf.get('absence_rating_adjustment'),
                absent_regular_minutes=mf.get('absent_regular_minutes'), status=mf.get('status'), method=mf.get('method'),
                rating_weight=mf.get('rating_weight'), mean_basis=mf.get('mean_basis'), teams=side)


def _mentions(entry, keys):
    text = json.dumps(entry)
    return any(k in text for k in keys)


def extract_game(run, snapshot, forecast, game, stage):
    gid, teams = game['game_id'], {game['home_team_id'], game['away_team_id']}
    sgame = next((g for g in snapshot.get('games') or [] if g.get('game_id') == gid), None)
    check = snapshot.get('injury_check') or {}
    check_game = next((g for g in check.get('games') or [] if g.get('game_id') == gid), None)
    extractions = []
    for e in snapshot.get('injury_extractions') or []:
        extractions.append(dict(evidence=e.get('evidence'), parser_version=e.get('parser_version'),
                                complete_teams=[t for t in e.get('complete_teams') or [] if _mentions(t, sorted(teams))],
                                rows=[{k: r.get(k) for k in ('game_id', 'team_id', 'player_id', 'status', 'reported_status', 'source_name')}
                                      for r in e.get('rows') or [] if r.get('game_id') == gid or r.get('team_id') in teams]))
    roster = [{k: v for k, v in p.items() if k != 'bio'} for p in (snapshot.get('roster') or {}).get('players') or [] if p.get('team_id') in teams]
    preds = [dict(player_id=p.get('player_id'), name=p.get('name'), team_id=p.get('team_id'), status=p.get('status'),
                  participation_probability=p.get('participation_probability'), expected_minutes=p.get('expected_minutes'),
                  points_mean=p.get('points_mean'), flags=p.get('flags'), history_games=p.get('history_games'))
             for p in forecast.get('predictions') or [] if p.get('team_id') in teams]
    pre = snapshot.get('preflight') or {}
    keys = [gid] + sorted(teams)
    return dict(
        tool=TOOL, run_id=run['run_id'], game_id=gid, stage=stage, created_at=run['created_at'], as_of=run['as_of'],
        rowid=run['insertion_order'], model_version=forecast.get('model_version'), model_key=forecast.get('model_key'),
        start_time=(sgame or game).get('start_time'), home_team_id=game['home_team_id'], away_team_id=game['away_team_id'],
        margin=published_margin(forecast, game),
        injury_check=dict(checked_at=check.get('checked_at'), availability_applied=check.get('availability_applied'), game=check_game),
        extractions=extractions, roster=roster, predictions=preds,
        preflight=dict(checked_at=pre.get('checked_at'), status=pre.get('status'),
                       lineups={t: v for t, v in (pre.get('lineups') or {}).items() if t in teams},
                       sources=pre.get('sources'), issues=pre.get('issues'), warnings=pre.get('warnings'), notes=pre.get('notes')),
        espn_injury_fallbacks=[e for e in snapshot.get('espn_injury_fallbacks') or [] if _mentions(e, keys)],
        availability_supplements=[e for e in snapshot.get('availability_supplements') or [] if _mentions(e, keys)],
        roster_reviews=[e for e in snapshot.get('roster_reviews') or [] if _mentions(e, keys)],
        roster_adjudications=[e for e in snapshot.get('roster_adjudications') or [] if _mentions(e, keys)])


def load_schedule(copy):
    return json.loads((Path(copy) / 'schedule' / 'latest.json').read_text())


def cmd_extract(args):
    copy = Path(args.copy)
    if 'operations/production' in str(copy.resolve()):
        raise SystemExit('Refusing to read production; point --copy at a copy.')
    schedule = load_schedule(copy)
    games = {g['game_id']: g for g in LR.finished_games(schedule, copy / 'finals')}
    jobs = json.loads((copy / 'state.json').read_text())['jobs']
    by_game, unreadable = LR.index_jobs(jobs)
    ledger = LR.Ledger(copy / 'forecast.sqlite', immutable=True)
    throttle = LR.Throttle(share=args.share)
    out = Path(args.output)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done.add(rec['run_id'])
    # Candidates: live league pregame runs of each finished game (live_replay.eligible).
    wanted = defaultdict(list)  # run_id -> [(game, stage)]
    meta_io = 0.
    meta_path = out.with_name(out.stem + '-meta.json')  # run metadata, read once (4,000 rows by key)
    metas = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    for gid, game in games.items():
        cands = []
        for c in by_game.get(gid, []):
            if c['run_id'] not in metas:
                before = ledger.io_seconds
                metas[c['run_id']] = ledger.meta(c['run_id'])
                meta_io += ledger.io_seconds - before
            meta = metas[c['run_id']]
            if meta is None:
                continue
            cands.append(dict(c, **meta))
        for c in LR.eligible(game, cands):
            wanted[c['run_id']].append((game, c['stage'], c['insertion_order']))
    meta_path.write_text(json.dumps(metas))
    throttle.rest(meta_io)
    order = sorted(wanted, key=lambda r: wanted[r][0][2])
    todo = [r for r in order if r not in done][:args.limit]
    print(f'{len(games)} finished games; {len(order)} distinct pregame runs; {len(done)} already extracted; {len(todo)} to read; '
          f'{unreadable} unreadable job keys', flush=True)
    began = time.monotonic()
    with out.open('a') as fh:
        for i, run_id in enumerate(todo):
            before = ledger.io_seconds
            run = ledger.run(run_id)
            io = ledger.io_seconds - before
            snapshot, forecast = json.loads(run['snapshot_json']), json.loads(run['forecast_json'])
            del run['snapshot_json'], run['forecast_json']
            for game, stage, _ in wanted[run_id]:
                fh.write(json.dumps(extract_game(run, snapshot, forecast, game, stage), separators=(',', ':')) + '\n')
            fh.flush()
            del snapshot, forecast
            throttle.rest(io)
            if i % 100 == 0 or i == len(todo) - 1:
                print(f'{i + 1}/{len(todo)} runs, {time.monotonic() - began:.0f} s, read {ledger.io_seconds:.0f} s, slept {throttle.slept:.0f} s', flush=True)
            if args.stop_at and datetime.now(timezone.utc) >= parse_time(args.stop_at):
                print('Stopping at --stop-at; rerun to resume.', flush=True)
                break


# ---------------------------------------------------------------- prepare (report editions, ESPN lists)

def load_runs(path):
    """The extracted (run, game) records, by game, in creation order (ledger row order breaks ties)."""
    by_game = defaultdict(list)
    for line in Path(path).read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            by_game[rec['game_id']].append(rec)
    for recs in by_game.values():
        recs.sort(key=lambda r: (parse_time(r['created_at']), r['rowid']))
    return dict(by_game)


def roster_union(runs):
    """Every (player, team, name) any extracted run's roster carried, for joining report and ESPN names."""
    seen = {}
    for recs in runs.values():
        for rec in recs:
            for p in rec['roster']:
                seen[(p['player_id'], p['team_id'])] = dict(player_id=p['player_id'], team_id=p['team_id'], name=p['name'])
    return dict(players=sorted(seen.values(), key=lambda p: (p['team_id'], p['player_id'])))


def _report_evidence(record, gid):
    for g in record.get('games') or []:
        if g.get('game_id') != gid:
            continue
        for key in ('anchor_report', 'latest_report'):
            if isinstance(g.get(key), dict) and g[key].get('sha256'):
                yield g[key]


def cmd_prepare(args):
    """Index the captured report editions (first capture time each), parse them, and read the ESPN injury lists
    of the summary pages the line captures archived. Writes ``editions.json`` and ``espn.json`` into --out."""
    from nba_forecast.injury_parser import parse_report, pdf_text, normalized, person_key
    copy, outdir = Path(args.copy), Path(args.out)
    schedule = load_schedule(copy)
    games = {g['game_id']: g for g in LR.finished_games(schedule, copy / 'finals')}
    if args.games:
        games = {k: v for k, v in games.items() if k in set(args.games.split(','))}
    runs = load_runs(args.runs)
    roster = roster_union(runs)
    runs = {k: v for k, v in runs.items() if k in games}
    editions = {}  # sha -> evidence with the first capture time across all of our checks

    def note(ev, checked_at=None):
        e = editions.setdefault(ev['sha256'], dict(sha256=ev['sha256'], published_at=ev['published_at'], source_url=ev.get('source_url'),
                                                   first_retrieved_at=ev['retrieved_at'], retrieved=set()))
        e['retrieved'].add(ev['retrieved_at'])
        if parse_time(ev['retrieved_at']) < parse_time(e['first_retrieved_at']):
            e['first_retrieved_at'] = ev['retrieved_at']

    root = copy / 'injuries' / 'games'
    files = 0
    for gid in sorted(os.listdir(root)):
        if args.games and gid not in games:
            continue
        folder = root / gid
        paths = [folder / 'final.json', folder / 'latest.json'] + sorted((folder / 'records').glob('*.json'))
        for path in paths:
            if not path.exists():
                continue
            files += 1
            rec = json.loads(path.read_text())
            for ev in _report_evidence(rec, gid):
                note(ev)
    for path in ([] if args.games else sorted((copy / 'injuries' / 'daily').glob('*.json'))):
        rec = json.loads(path.read_text())
        if isinstance(rec.get('report'), dict) and rec['report'].get('sha256'):
            note(rec['report'])
    # Editions a run applied are captures too (a run's snapshot keeps its evidence).
    for recs in runs.values():
        for rec in recs:
            for ex in rec['extractions']:
                if ex.get('evidence') and ex['evidence'].get('sha256'):
                    note(ex['evidence'])
    parsed, failed = {}, Counter()
    for sha, e in sorted(editions.items(), key=lambda kv: kv[1]['published_at']):
        path = copy / 'raw' / (sha + '.pdf')
        if not path.exists():
            failed['pdf_missing'] += 1
            continue
        try:
            text = pdf_text(path, sha)
            rep = parse_report(text, schedule, roster, dict(published_at=e['published_at'], retrieved_at=e['first_retrieved_at'],
                                                            sha256=sha, source_url=e['source_url']))
        except (ValueError, OSError) as exc:
            failed[type(exc).__name__ + ': ' + str(exc)[:60]] += 1
            continue
        parsed[sha] = dict(rows=[{k: r[k] for k in ('game_id', 'team_id', 'player_id', 'status', 'reported_status', 'source_name')}
                                 for r in rep['rows'] if r['game_id'] in games],
                           complete_teams=rep['complete_teams'], team_coverage=rep['team_coverage'],
                           unmatched=[i for i in rep['issues'] if i.get('kind') == 'unmatched_identity_or_game'],
                           pages_complete=rep['pages_complete'], heading_matches=rep['heading_matches'])
    for e in editions.values():
        e['retrieved'] = sorted(e['retrieved'])
        e.update(parsed.get(e['sha256']) or dict(parse_failed=True))
    (outdir / 'editions.json').write_text(json.dumps(dict(tool=TOOL, record_files=files, failed=dict(failed),
                                                          editions=sorted(editions.values(), key=lambda e: e['published_at']))))
    print(f'{len(editions)} report editions from {files} check records; parsed {len(parsed)}; failures {dict(failed)}', flush=True)

    # ESPN injury lists in the summary pages each line capture archived (captures are appended when a line changes).
    by_team_name = {}
    for p in roster['players']:
        by_team_name.setdefault((p['team_id'], normalized(p['name'])), set()).add(p['player_id'])
        by_team_name.setdefault((p['team_id'], 'k:' + person_key(p['name'])), set()).add(p['player_id'])
    espn, missing, cache = {}, 0, {}
    for gid, game in sorted(games.items()):
        path = copy / 'market' / 'game-lines' / (gid + '.jsonl')
        if not path.exists():
            continue
        pages = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get('recovered_close') or parse_time(r['retrieved_at']) >= parse_time(game['start_time']):
                continue
            raw = copy / 'references' / 'raw' / (r['sha256'] + '.json')
            if not raw.exists():
                missing += 1
                continue
            if r['sha256'] not in cache:
                payload = json.loads(raw.read_bytes())
                comp = ((payload.get('header') or {}).get('competitions') or [{}])[0]
                sides = {str((c.get('team') or {}).get('id')): c.get('homeAway') for c in comp.get('competitors') or []}
                entries = []
                for team in payload.get('injuries') or []:
                    side = sides.get(str((team.get('team') or {}).get('id')))
                    tid = game['home_team_id'] if side == 'home' else game['away_team_id'] if side == 'away' else None
                    for inj in team.get('injuries') or []:
                        name = (inj.get('athlete') or {}).get('displayName') or ''
                        ids = by_team_name.get((tid, normalized(name))) or by_team_name.get((tid, 'k:' + person_key(name))) or set()
                        entries.append(dict(team_id=tid, name=name, player_id=next(iter(ids)) if len(ids) == 1 else None,
                                            status=inj.get('status'), date=inj.get('date'),
                                            type=(inj.get('type') or {}).get('description')))
                cache[r['sha256']] = entries
            pages.append(dict(retrieved_at=r['retrieved_at'], sha256=r['sha256'], entries=cache[r['sha256']]))
        espn[gid] = sorted(pages, key=lambda p: p['retrieved_at'])
    (outdir / 'espn.json').write_text(json.dumps(dict(tool=TOOL, missing_pages=missing, games=espn)))
    print(f'ESPN lists: {sum(len(v) for v in espn.values())} archived summary pages over {len(espn)} games; {missing} missing', flush=True)


# ---------------------------------------------------------------- analyze

SEVERITY = {'available': 0., 'not listed': 0., 'active': 0., 'probable': .25, 'questionable': .5, 'day-to-day': .5,
            'doubtful': .75, 'out': 1., 'suspension': 1.}
MATERIAL = 1.5           # points of line move within the last six hours
EVENT_FLOOR = 8.         # |status severity change| x player's projected minutes, to count as an availability event
POINTS = (('T-6h', 360), ('T-2h', 120), ('T-60', 60), ('T-30', 30))


def _sev(label):
    return SEVERITY.get(str(label or '').strip().lower())


def _minutes(td):
    return td.total_seconds() / 60.


def median(values):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def quartiles(values):
    v = sorted(x for x in values if x is not None)
    if not v:
        return (None, None, None)
    q = lambda f: v[min(len(v) - 1, int(round(f * (len(v) - 1))))]
    return (q(.25), q(.5), q(.75))


def fmt(x, d=1):
    return '   -' if x is None else f'{x:.{d}f}'


def clustered_slope(xs, ys, dates):
    """OLS slope of y on x with intercept; one standard error clustered by date (CR1, G/(G-1))."""
    n = len(xs)
    if n < 3:
        return None, None, n
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None, None, n
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    groups = defaultdict(float)
    for x, y, d in zip(xs, ys, dates):
        groups[d] += (x - mx) * (y - a - b * x)
    g = len(groups)
    var = sum(v * v for v in groups.values()) / sxx ** 2 * (g / (g - 1) if g > 1 else 1.)
    return b, math.sqrt(var), n


class Lines:
    """One game's line captures: the mean over providers of each provider's last capture at or before a time."""

    def __init__(self, records, tip):
        self.tip = tip
        self.records = sorted((r for r in records if not r.get('recovered_close') and parse_time(r['retrieved_at']) < tip),
                              key=lambda r: parse_time(r['retrieved_at']))
        self.recovered = sum(1 for r in records if r.get('recovered_close'))

    @staticmethod
    def _spread(r):
        sp = ((r.get('lines') or {}).get('spread') or {}).get('home') or {}
        v = sp.get('current_line')
        return v if _finite(v) else None

    def margin_at(self, when):
        latest = {}
        for r in self.records:
            if parse_time(r['retrieved_at']) <= when and self._spread(r) is not None:
                latest[r['provider_id']] = r
        if not latest:
            return None
        return -sum(self._spread(r) for r in latest.values()) / len(latest)

    def series(self):
        """(time, home margin) after each capture."""
        out, latest = [], {}
        for r in self.records:
            if self._spread(r) is None:
                continue
            latest[r['provider_id']] = r
            out.append((parse_time(r['retrieved_at']), -sum(self._spread(x) for x in latest.values()) / len(latest)))
        return out

    def open_margin(self):
        if not self.records:
            return None
        sp = ((self.records[0].get('lines') or {}).get('spread') or {}).get('home') or {}
        return -sp['open_line'] if _finite(sp.get('open_line')) else None


class Kalshi:
    def __init__(self, records, tip):
        self.records = sorted((r for r in records if not any('recover' in k for k in r) and _finite(r.get('implied_home_margin'))
                               and parse_time(r['captured_at']) < tip), key=lambda r: parse_time(r['captured_at']))

    def margin_at(self, when):
        v = None
        for r in self.records:
            if parse_time(r['captured_at']) <= when:
                v = r['implied_home_margin']
        return v


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def applied_edition(rec):
    """The publication time and hash of the latest official edition a run applied (its parsed extraction evidence)."""
    best = None
    for ex in rec['extractions']:
        ev = ex.get('evidence') or {}
        if ev.get('published_at') and (best is None or parse_time(ev['published_at']) > parse_time(best['published_at'])):
            best = ev
    return best


def forecast_at(runs, when):
    last = None
    for r in runs:
        if parse_time(r['created_at']) <= when:
            last = r
        else:
            break
    return last


def player_minutes(runs):
    """Each player's largest projected minutes in any of the game's runs (his role when he plays)."""
    best = {}
    for r in runs:
        for p in r['predictions']:
            m = p.get('expected_minutes')
            if _finite(m):
                best[p['player_id']] = max(best.get(p['player_id'], 0.), m)
    return best


def _run_label(run, player_id):
    """What a run assumed for a player, as a report label: its roster status (an unlisted available player is 'Not listed')."""
    p = next((x for x in run['roster'] if x['player_id'] == player_id), None)
    if p is None:
        return None
    if p.get('status') == 'out':
        return 'Out'
    if p.get('status') == 'questionable':
        return p.get('reported_status') if _sev(p.get('reported_status')) not in (None, 0.) else 'Questionable'
    return 'Not listed'


def official_events(game, editions, minutes, runs=()):
    """Status changes between consecutive captured editions covering this game, per player of either team.

    A player's first listing for the game has no earlier edition to compare with (his team had not
    submitted, or was not read completely); its 'before' is what our latest run created before that
    edition's publication assumed for him (``_run_label``), so a listing we already assumed is no event.
    """
    gid, teams = game['game_id'], (game['home_team_id'], game['away_team_id'])
    day = LR._eastern(game['start_time'])
    seq = []
    for e in editions:
        if e.get('parse_failed'):
            continue
        # 'Not listed' is inferred only for a team the parser read completely (complete_teams), as production does.
        covered = {c['team_id'] for c in e.get('complete_teams') or [] if c.get('date') == day and c['team_id'] in teams}
        rows = {r['player_id']: r for r in e['rows'] if r['game_id'] == gid}
        if not covered and not rows:
            continue
        seq.append((e, covered, rows))
    seq.sort(key=lambda t: parse_time(t[0]['published_at']))
    state, events, names = {}, [], {}
    players = set()
    for _, _, rows in seq:
        players.update((r['player_id'], r['team_id']) for r in rows.values())
    for e, covered, rows in seq:
        for pid, tid in sorted(players):
            if pid in rows:
                label = rows[pid]['reported_status']
                names[pid] = rows[pid]['source_name']
            elif tid in covered:
                label = 'Not listed'
            else:
                continue
            prev = state.get(pid)
            before, first = (prev[0] if prev else None), prev is None
            if first:
                run = forecast_at(runs, parse_time(e['published_at']))
                before = _run_label(run, pid) if run else None
            if before is not None and before != label and not (_sev(before) == _sev(label) == 0.):
                d = (_sev(label) or 0.) - (_sev(before) or 0.)
                mins = minutes.get(pid, 0.)
                sign = -1 if tid == game['home_team_id'] else 1
                events.append(dict(source='official', player_id=pid, name=names.get(pid, pid), team_id=tid, before=before, after=label,
                                   first_listing=first, published_at=e['published_at'], captured_at=e['first_retrieved_at'],
                                   sha256=e['sha256'], minutes=mins, impact=sign * d * mins))
            state[pid] = (label, e)
    return events, seq


def espn_events(game, pages, minutes):
    """ESPN injury-list status changes between consecutive archived summary pages (captured when a line changed).

    A player absent from a page is 'Active'. The first page sets the state. The change's publication time is
    ESPN's own date on the entry (absent when a player leaves the list: then the page's capture time).
    """
    state, events = None, []
    for page in pages:
        now = {(en['player_id'] or ('name:' + en['name'])): en for en in page['entries']}
        if state is not None:
            for key in sorted(set(state) | set(now)):
                before, after = state.get(key), now.get(key)
                b = before['status'] if before else 'Active'
                a = after['status'] if after else 'Active'
                if a == b:
                    continue
                en = after or before
                d = (_sev(a) or 0.) - (_sev(b) or 0.)
                mins = minutes.get(en['player_id'], 0.) if en['player_id'] else 0.
                sign = -1 if en['team_id'] == game['home_team_id'] else 1
                events.append(dict(source='espn', player_id=en['player_id'], name=en['name'], team_id=en['team_id'], before=b, after=a,
                                   published_at=(after or {}).get('date') or page['retrieved_at'], captured_at=page['retrieved_at'],
                                   minutes=mins, impact=sign * d * mins))
        state = now
    return events


def final_status(run, player_id):
    """The status, report label and participation probability the run used for a player."""
    if not run or not player_id:
        return None
    ros = next((p for p in run['roster'] if p['player_id'] == player_id), None) or {}
    pred = next((p for p in run['predictions'] if p['player_id'] == player_id), None) or {}
    prob = 0. if pred.get('status') == 'out' else pred.get('participation_probability')
    return dict(status=ros.get('status'), reported=ros.get('reported_status'), basis=ros.get('status_basis'),
                published=ros.get('status_published_at'), prob=prob)


def first_applying_run(runs, event):
    """The first run created at or after our capture of the event whose applied edition is at least as new."""
    if event['source'] != 'official':
        return None
    pub = parse_time(event['published_at'])
    for r in runs:
        ed = applied_edition(r)
        if ed and parse_time(ed['published_at']) >= pub:
            return r
    return None


def cmd_analyze(args):
    copy, folder = Path(args.copy), Path(args.folder)
    schedule = load_schedule(copy)
    games = {g['game_id']: g for g in LR.finished_games(schedule, copy / 'finals')}
    runs = load_runs(folder / 'news-runs.jsonl')
    editions = json.loads((folder / 'editions.json').read_text())
    espn = json.loads((folder / 'espn.json').read_text())['games']
    ed_list = editions['editions']
    print(f'Data: {len(games)} finished games in the copy; {len(runs)} with pregame runs ({sum(len(v) for v in runs.values())} run-games); '
          f'{len(ed_list)} captured official editions ({sum(1 for e in ed_list if e.get("parse_failed"))} not parsed); '
          f'ESPN pages archived for {len(espn)} games')
    rows, skipped = [], Counter()
    for gid, game in sorted(games.items(), key=lambda kv: kv[1]['start_time']):
        tip = parse_time(game['start_time'])
        lines = Lines(read_jsonl(copy / 'market' / 'game-lines' / (gid + '.jsonl')), tip)
        kalshi = Kalshi(read_jsonl(copy / 'market' / 'kalshi' / (gid + '.jsonl')), tip)
        all_runs = runs.get(gid, [])
        # The forecast path uses runs that published an estimated margin forecast; a run that fell back to the
        # player sum (margin forecast unavailable: broken team totals, early-season gaps) is counted and set aside.
        grun = all_runs if args.all_runs else [x for x in all_runs if x['margin']['basis'] == 'margin_forecast']
        skipped['fallback'] += len(all_runs) - len(grun)
        if not lines.records or not grun:
            skipped['games without line captures or estimated runs'] += 1
            continue
        day = LR._eastern(game['start_time'])
        firsts = [t for t, _ in lines.series() if LR._eastern(t.isoformat()) == day]
        pts = dict(open=lines.open_margin(), day_first=lines.margin_at(firsts[0]) if firsts else None,
                   close=lines.margin_at(tip - timedelta(seconds=1)))
        kpts = dict(close=kalshi.margin_at(tip - timedelta(seconds=1)))
        fpts, fall, starts = {}, {}, {}
        usable_all = [x for x in all_runs if x['margin']['published'] is not None]
        for label, mins in POINTS:
            starts[label] = tip - timedelta(minutes=mins)
            starts[label + '-30'] = starts[label] - timedelta(minutes=30)
            when = tip - timedelta(minutes=mins)
            pts[label] = lines.margin_at(when)
            kpts[label] = kalshi.margin_at(when)
            r = forecast_at(grun, when)
            fpts[label] = r['margin']['published'] if r else None
            re_ = forecast_at(grun, when - timedelta(minutes=30))
            fpts[label + '-30'] = re_['margin']['published'] if re_ else None
            ra = forecast_at(usable_all, when)
            fall[label] = ra['margin']['published'] if ra else None
        final = forecast_at(grun, tip)
        fa = forecast_at(usable_all, tip)
        fall['close'] = fa['margin']['published'] if fa else None
        fpts['close'] = final['margin']['published'] if final else None
        fpts['first'] = grun[0]['margin']['published']
        starts['first'] = parse_time(grun[0]['created_at'])
        pts['first'] = lines.margin_at(starts['first'])
        kpts['first'] = kalshi.margin_at(starts['first'])
        minutes = player_minutes(all_runs)
        off, seq = official_events(game, ed_list, minutes, all_runs)
        esp = espn_events(game, espn.get(gid, []), minutes)
        rows.append(dict(game=game, tip=tip, day=day, lines=lines, kalshi=kalshi, runs=grun, pts=pts, kpts=kpts, fpts=fpts, fall=fall,
                         starts=starts, final=final, minutes=minutes, official=off, espn=esp, seq=seq))
    print(f'Runs set aside from the forecast path (published the player-sum fallback, no estimated margin forecast): '
          f'{skipped["fallback"]} of {sum(len(v) for v in runs.values())} run-games' + ('' if not args.all_runs else ' (--all-runs: none)'))
    ref = None
    if args.reference:
        ref = {g['game_id'] for g in json.loads(Path(args.reference).read_text())['games'] if g.get('closes') and not g.get('error')}
    print(f'Games with line captures and pregame runs: {len(rows)}' +
          (f'; {sum(1 for r in rows if r["game"]["game_id"] in ref)} of them among the live replay reference\'s {len(ref)}' if ref else ''))

    # ---- 1. line moves at fixed points
    print('\n== 1. Spread (home margin = -spread) at fixed points; moves to the close ==')
    print(f'{"from":>10} {"n":>4} {"mean|mv|":>8} {"med|mv|":>8} {"=0":>4} {"0.5-1":>6} {"1.5-2.5":>8} {">=3":>4}  (DK capture; Kalshi n, mean|mv|)')
    for label in ('open', 'day_first', 'T-6h', 'T-2h', 'T-60', 'T-30'):
        mv = [r['pts']['close'] - r['pts'][label] for r in rows if r['pts'].get(label) is not None and r['pts']['close'] is not None]
        km = [r['kpts']['close'] - r['kpts'][label] for r in rows if r['kpts'].get(label) is not None and r['kpts'].get('close') is not None]
        a = [abs(x) for x in mv]
        print(f'{label:>10} {len(a):4d} {fmt(sum(a) / len(a) if a else None, 2):>8} {fmt(median(a), 2):>8} {sum(1 for x in a if x < .25):4d} '
              f'{sum(1 for x in a if .25 <= x < 1.25):6d} {sum(1 for x in a if 1.25 <= x < 2.75):8d} {sum(1 for x in a if x >= 2.75):4d}'
              f'   Kalshi n={len(km)} mean|mv|={fmt(sum(abs(x) for x in km) / len(km) if km else None, 2)}')
    material = [r for r in rows if r['pts']['T-6h'] is not None and r['pts']['close'] is not None
                and abs(r['pts']['close'] - r['pts']['T-6h']) >= MATERIAL]
    with6 = [r for r in rows if r['pts']['T-6h'] is not None and r['pts']['close'] is not None]
    print(f'Material moves (|close - T-6h| >= {MATERIAL}): {len(material)} of {len(with6)} games with a T-6h line'
          + (f' ({sum(1 for r in material if r["game"]["game_id"] in ref)} of {sum(1 for r in with6 if r["game"]["game_id"] in ref)} '
             'among the reference games)' if ref else ''))

    print('\nOur published margin against the DK line at each point (same games at each point; ± one SE clustered by Eastern date):')
    print(f'{"point":>8} {"n":>4} {"mean |ours - line|":>20} {"mean ours - line":>18}')
    for label in ('first', 'T-6h', 'T-2h', 'T-60', 'T-30', 'close'):
        sel = [r for r in rows if r['pts'].get(label) is not None and r['fpts'].get(label) is not None]
        a, ase = LR.mean_se([abs(r['fpts'][label] - r['pts'][label]) for r in sel], [r['day'] for r in sel])
        d, dse = LR.mean_se([r['fpts'][label] - r['pts'][label] for r in sel], [r['day'] for r in sel])
        print(f'{label:>8} {len(sel):4d} {fmt(a, 2):>12} ± {fmt(ase, 2):<5} {fmt(d, 2):>10} ± {fmt(dse, 2)}')
    both = [r for r in rows if all(r['pts'].get(k) is not None and r['fpts'].get(k) is not None for k in ('first', 'T-6h', 'close'))]
    for label in ('first', 'T-6h', 'close'):
        a, ase = LR.mean_se([abs(r['fpts'][label] - r['pts'][label]) for r in both], [r['day'] for r in both])
        print(f'  paired on the {len(both)} games with all three of first/T-6h/close: {label:>6} mean |ours - line| {fmt(a, 2)} ± {fmt(ase, 2)}')
    # Does the day's movement carry information? Error against the final score at each point, ours and the line's.
    for r in rows:
        box = json.loads((copy / 'finals' / (r['game']['game_id'] + '.json')).read_text())
        r['actual'] = (sum(b['pts'] for b in box if b['team_id'] == r['game']['home_team_id'])
                       - sum(b['pts'] for b in box if b['team_id'] == r['game']['away_team_id']))
    print(f'\nAbsolute error against the final margin, paired on the {len(both)} games (± one SE clustered by Eastern date):')
    for label in ('first', 'T-6h', 'close'):
        o, ose = LR.mean_se([abs(r['actual'] - r['fpts'][label]) for r in both], [r['day'] for r in both])
        m, mse = LR.mean_se([abs(r['actual'] - r['pts'][label]) for r in both], [r['day'] for r in both])
        d, dse = LR.mean_se([abs(r['actual'] - r['fpts'][label]) - abs(r['actual'] - r['pts'][label]) for r in both], [r['day'] for r in both])
        print(f'  {label:>6}: ours {fmt(o, 2)} ± {fmt(ose, 2)}   line {fmt(m, 2)} ± {fmt(mse, 2)}   ours - line {d:+.2f} ± {fmt(dse, 2)}')
    for who, key in (('ours', 'fpts'), ('line', 'pts')):
        d, dse = LR.mean_se([abs(r['actual'] - r[key]['close']) - abs(r['actual'] - r[key]['first']) for r in both], [r['day'] for r in both])
        print(f'  {who}: error at close minus error at first run {d:+.2f} ± {fmt(dse, 2)} (negative: the day\'s moves helped)')

    # ---- 2. timing chain per material move
    print('\n== 2. Material moves: news in our evidence, our forecast path, timing chain ==')
    classes, lags = Counter(), defaultdict(list)
    detail = []
    for r in material:
        tip, start = r['tip'], r['tip'] - timedelta(hours=6)
        m0, m1 = r['pts']['T-6h'], r['pts']['close']
        move = m1 - m0
        sign = 1 if move > 0 else -1
        mt = next((t for t, v in r['lines'].series() if t > start and sign * (v - m0) >= MATERIAL), None)
        k0 = r['kpts'].get('T-6h')
        kt = None
        if k0 is not None:
            kt = next((parse_time(k['captured_at']) for k in r['kalshi'].records if parse_time(k['captured_at']) > start
                       and sign * (k['implied_home_margin'] - k0) >= .5 * abs(move)), None)
        f0 = r['fpts']['T-6h'] if r['fpts']['T-6h'] is not None else r['runs'][0]['margin']['published']
        f1 = r['fpts']['close']
        fmove = None if f1 is None or f0 is None else f1 - f0
        lo, hi = start - timedelta(hours=1), (mt or tip) + timedelta(minutes=30)
        cands = [e for e in r['official'] + r['espn'] if lo <= parse_time(e['published_at']) <= min(hi, tip)
                 and abs(e['impact']) >= EVENT_FLOOR and e['impact'] * sign > 0]
        opposite = [e for e in r['official'] + r['espn'] if lo <= parse_time(e['published_at']) <= tip
                    and abs(e['impact']) >= EVENT_FLOOR and e['impact'] * sign < 0]
        captured = [e for e in cands if parse_time(e['captured_at']) < tip]
        if captured:
            # Our move is measured from before we captured the news, if that was before T-6h (the line can lag our capture).
            first_cap = min(parse_time(e['captured_at']) for e in captured)
            if first_cap < start:
                r0 = forecast_at(r['runs'], first_cap - timedelta(seconds=1))
                f0 = r0['margin']['published'] if r0 else f0
                fmove = None if f1 is None or f0 is None else f1 - f0
        if not captured:
            cls = 'c'
        elif fmove is not None and fmove * sign >= .25 * abs(move):
            cls = 'a'
        else:
            cls = 'b'
        classes[cls] += 1
        main = max(captured, key=lambda e: abs(e['impact'])) if captured else None
        chain = {}
        if main:
            pub, cap = parse_time(main['published_at']), parse_time(main['captured_at'])
            fr = first_applying_run(r['runs'], main)
            prev = None
            if fr:
                i = r['runs'].index(fr)
                prev = r['runs'][i - 1] if i > 0 else None
            step = (fr['margin']['published'] - prev['margin']['published']) if fr and prev and fr['margin']['published'] is not None \
                and prev['margin']['published'] is not None else None
            chain = dict(capture_lag=_minutes(cap - pub), line_minus_pub=_minutes(mt - pub) if mt else None,
                         kalshi_minus_pub=_minutes(kt - pub) if kt else None,
                         run_lag=_minutes(parse_time(fr['created_at']) - cap) if fr else None,
                         run_minus_line=_minutes(parse_time(fr['created_at']) - mt) if fr and mt else None,
                         pub_before_tip=_minutes(tip - pub))
            for k, v in chain.items():
                if v is not None:
                    lags[(cls, k)].append(v)
                    lags[('all', k)].append(v)
            chain['step'] = step
            chain['step_share'] = step / move if step is not None else None
        fs = final_status(r['final'], main['player_id']) if main else None
        # Did the final run carry the event's new status? (official: the label; ESPN: an out/questionable status or its probability)
        if main and fs:
            known = [e for e in r['official'] + r['espn'] if e['player_id'] == main['player_id']
                     and parse_time(e['captured_at']) <= parse_time(r['final']['created_at'])]
            latest = max(known, key=lambda e: parse_time(e['captured_at'])) if known else main
            target = _sev(latest['after']) or 0.
            used = 1. - fs['prob'] if _finite(fs['prob']) else None
            applied = used is not None and abs(used - target) <= .35
        else:
            applied = None
        detail.append(dict(r=r, cls=cls, move=move, mt=mt, kt=kt, fmove=fmove, f0=f0, main=main, chain=chain, cands=captured, opposite=opposite,
                           final_status=fs, applied=applied,
                           uncaptured=[e for e in cands if parse_time(e['captured_at']) >= tip]))
    print('Class (a) news in our evidence, forecast moved the same way >= 25% of the line; (b) news in our evidence, forecast moved '
          '< 25% (or the other way); (c) no availability event in our evidence (official editions we captured, ESPN lists we archived) '
          f'in [T-7h, line move + 30 min] of impact >= {EVENT_FLOOR} severity-minutes in the line\'s direction.')
    for cls in 'abc':
        sel = [d for d in detail if d['cls'] == cls]
        ratio = [d['fmove'] / d['move'] for d in sel if d['fmove'] is not None]
        print(f'  ({cls}) {classes[cls]:3d} moves; median our move / line move = {fmt(median(ratio), 2)}; '
              f'median |line move| = {fmt(median([abs(d["move"]) for d in sel]), 1)}')
    print('Median lags in minutes (n), main event = the largest captured event in the line\'s direction: published->captured, '
          'captured->first run applying it, DK line move - published (negative: line moved first; DK captures every 15 min), '
          'Kalshi move (half the DK move) - published, first run applying it - DK line move, published before tip')
    for cls in ('a', 'b', 'all'):
        parts = []
        for k in ('capture_lag', 'run_lag', 'line_minus_pub', 'kalshi_minus_pub', 'run_minus_line', 'pub_before_tip'):
            v = lags.get((cls, k), [])
            parts.append(f'{k}={fmt(median(v), 0)} ({len(v)})')
        print(f'  ({cls}) ' + '; '.join(parts))
    app = Counter((d['cls'], d['applied']) for d in detail if d['main'])
    print('Final run carried the main player\'s latest captured status (1 - participation probability within 0.35 of its severity): '
          + ', '.join(f'({c}) {"yes" if a else "no" if a is False else "unknown"}: {n}' for (c, a), n in sorted(app.items(), key=str)))
    offonly = Counter()
    for d in detail:
        off = [e for e in d['cands'] if e['source'] == 'official']
        offonly['c' if not off else d['cls']] += 1
    print('Classes counting official editions only (ESPN-only moves become (c)): ' + ', '.join(f'({c}) {offonly[c]}' for c in 'abc'))
    for cls in 'abc':
        sel = [d for d in detail if d['cls'] == cls and d['f0'] is not None]
        ahead = sum(1 for d in sel if abs(d['f0'] - d['r']['pts']['close']) < abs(d['f0'] - d['r']['pts']['T-6h']))
        print(f'  ({cls}) our forecast before the window was already nearer the close than the T-6h line: {ahead} of {len(sel)}')
    for cls in ('a', 'b'):
        sh = [d['chain'].get('step_share') for d in detail if d['cls'] == cls and d['chain'].get('step_share') is not None]
        print(f'  ({cls}) our margin step at the first run applying the main official event, as a share of the line move: '
              f'median {fmt(median(sh), 2)} (n={len(sh)})')
    src = Counter((d['cls'], d['main']['source']) for d in detail if d['main'])
    print('Explaining event source by class: ' + ', '.join(f'({c}) {s}: {n}' for (c, s), n in sorted(src.items())))
    print(f'Moves whose explaining news we only captured at or after tip: {sum(1 for d in detail if d["cls"] == "c" and d["uncaptured"])}')
    print('\nPer move (home margin; times UTC; T- minutes before tip):')
    for d in sorted(detail, key=lambda d: (d['cls'], d['r']['tip'])):
        r, g = d['r'], d['r']['game']
        mt = f'T-{_minutes(r["tip"] - d["mt"]):.0f}' if d['mt'] else '-'
        if d['kt']:
            mt += f', Kalshi T-{_minutes(r["tip"] - d["kt"]):.0f}'
        print(f'  ({d["cls"]}) {g["game_id"]} {g["away_tricode"]}@{g["home_tricode"]} {r["day"]} tip {r["tip"]:%H:%M}: line {fmt(r["pts"]["T-6h"])}->{fmt(r["pts"]["close"])} '
              f'(move {d["move"]:+.1f} at {mt}); ours {fmt(d["f0"])}->{fmt(r["fpts"]["close"])} (move {fmt(d["fmove"])}); '
              f'kalshi {fmt(r["kpts"]["T-6h"])}->{fmt(r["kpts"]["close"])}')
        path, last = [], None
        for run in r['runs']:
            t = parse_time(run['created_at'])
            v = run['margin']['published']
            if t < r['tip'] - timedelta(hours=6) or v is None:
                continue
            if last is None or abs(v - last) >= .3:
                path.append(f'T-{_minutes(r["tip"] - t):.0f} {v:.1f}')
                last = v
        lpath, llast = [], None
        for t, v in r['lines'].series():
            if t >= r['tip'] - timedelta(hours=6) and (llast is None or v != llast):
                lpath.append(f'T-{_minutes(r["tip"] - t):.0f} {v:.1f}')
                llast = v
        print(f'      our path: {", ".join(path[:14])}' + (' ...' if len(path) > 14 else ''))
        print(f'      line path: {", ".join(lpath[:14])}' + (' ...' if len(lpath) > 14 else ''))
        if d['final_status']:
            fs = d['final_status']
            print(f'      final run used for {d["main"]["name"]}: status {fs["status"]} ({fs["reported"]}, {fs["basis"]}, edition {fs["published"]}), '
                  f'p={fmt(fs["prob"], 2)}; applied={d["applied"]}')
        for e in sorted(d['cands'], key=lambda e: -abs(e['impact']))[:3]:
            fr = first_applying_run(r['runs'], e)
            frs = ''
            if fr:
                i = r['runs'].index(fr)
                pv = r['runs'][i - 1]['margin']['published'] if i > 0 else None
                frs = f'first run applying {parse_time(fr["created_at"]):%H:%M} (margin {fmt(pv)} -> {fmt(fr["margin"]["published"])})'
            print(f'      {e["source"]:8s} {e["name"]} ({"home" if e["team_id"] == g["home_team_id"] else "away"}, {e["minutes"]:.0f} min) '
                  f'{e["before"]} -> {e["after"]}: published {parse_time(e["published_at"]):%H:%M}, captured {parse_time(e["captured_at"]):%H:%M}, '
                  f'impact {e["impact"]:+.0f} {frs}')
        for e in sorted(d['uncaptured'], key=lambda e: -abs(e['impact']))[:2]:
            print(f'      not captured before tip: {e["source"]} {e["name"]} {e["before"]} -> {e["after"]} published {e["published_at"]}')
        for e in sorted(d['opposite'], key=lambda e: -abs(e['impact']))[:1]:
            print(f'      against the move: {e["source"]} {e["name"]} {e["before"]} -> {e["after"]} published {parse_time(e["published_at"]):%H:%M} impact {e["impact"]:+.0f}')

    # ---- 3. regression of our move on the line's move
    print('\n== 3. Our forecast change regressed on the line change (slope; 1 = we move as much as the market) ==')
    print('One standard error clustered by Eastern game date (CR1). y = our published margin at the end minus at the start '
          '(the last run created by each time); x = DK home margin change over the same window.')
    for wlabel, start_label in (('last 6 h (T-6h -> close)', 'T-6h'), ('T-2h -> close', 'T-2h'), ('first run -> close', 'first')):
        base = [r for r in rows if r['pts'][start_label] is not None and r['pts']['close'] is not None
                and r['fpts'][start_label] is not None and r['fpts']['close'] is not None]
        groups = [('all', base)] + ([('live replay reference games', [r for r in base if r['game']['game_id'] in ref])] if ref else [])
        for glabel, sel in groups + [
                            ('with an availability event', [r for r in base if has_event(r, start_label)]),
                            ('without', [r for r in base if not has_event(r, start_label)])]:
            xs = [r['pts']['close'] - r['pts'][start_label] for r in sel]
            ys = [r['fpts']['close'] - r['fpts'][start_label] for r in sel]
            b, se, n = clustered_slope(xs, ys, [r['day'] for r in sel])
            sdx = (sum(x * x for x in xs) / n) ** .5 if n else None
            sdy = (sum(y * y for y in ys) / n) ** .5 if n else None
            print(f'  {wlabel:26s} {glabel:28s} n={n:3d} slope={fmt(b, 2)} ± {fmt(se, 2)}   rms line move={fmt(sdx, 2)} rms our move={fmt(sdy, 2)}')
        # Kalshi as the market instead
        kb = [r for r in base if r['kpts'].get(start_label) is not None and r['kpts'].get('close') is not None]
        xs = [r['kpts']['close'] - r['kpts'][start_label] for r in kb]
        ys = [r['fpts']['close'] - r['fpts'][start_label] for r in kb]
        b, se, n = clustered_slope(xs, ys, [r['day'] for r in kb])
        print(f'  {wlabel:26s} {"all, Kalshi implied margin":28s} n={n:3d} slope={fmt(b, 2)} ± {fmt(se, 2)}')
        if start_label in ('T-6h', 'T-2h'):
            eb = [r for r in rows if r['pts'][start_label] is not None and r['pts']['close'] is not None
                  and r['fpts'].get(start_label + '-30') is not None and r['fpts']['close'] is not None]
            xs = [r['pts']['close'] - r['pts'][start_label] for r in eb]
            ys = [r['fpts']['close'] - r['fpts'][start_label + '-30'] for r in eb]
            b, se, n = clustered_slope(xs, ys, [r['day'] for r in eb])
            print(f'  {wlabel:26s} {"ours from 30 min earlier":28s} n={n:3d} slope={fmt(b, 2)} ± {fmt(se, 2)}   (the DK capture lags: '
                  'a move we made just before the window counts)')
            for glabel, sel in (('  ... with event', [r for r in eb if has_event(r, start_label + '-30')]),
                                ('  ... without', [r for r in eb if not has_event(r, start_label + '-30')])):
                xs = [r['pts']['close'] - r['pts'][start_label] for r in sel]
                ys = [r['fpts']['close'] - r['fpts'][start_label + '-30'] for r in sel]
                b, se, n = clustered_slope(xs, ys, [r['day'] for r in sel])
                sdx = (sum(x * x for x in xs) / n) ** .5 if n else None
                print(f'  {wlabel:26s} {glabel:28s} n={n:3d} slope={fmt(b, 2)} ± {fmt(se, 2)}   rms line move={fmt(sdx, 2)}')
            ab = [r for r in rows if r['pts'][start_label] is not None and r['pts']['close'] is not None
                  and r['fall'].get(start_label) is not None and r['fall'].get('close') is not None]
            xs = [r['pts']['close'] - r['pts'][start_label] for r in ab]
            ys = [r['fall']['close'] - r['fall'][start_label] for r in ab]
            b, se, n = clustered_slope(xs, ys, [r['day'] for r in ab])
            sdy = (sum(y * y for y in ys) / n) ** .5 if n else None
            print(f'  {wlabel:26s} {"incl. player-sum fallbacks":28s} n={n:3d} slope={fmt(b, 2)} ± {fmt(se, 2)}   rms our move={fmt(sdy, 2)}')
    print(f'  (availability event: an official edition or ESPN list change for either team published in the window, captured before tip, '
          f'|impact| >= {EVENT_FLOOR} severity-minutes, either direction)')

    # ---- 4. last pre-tip edition: questionable/probable vs reality and our final assumption
    print('\n== 4. Last official edition published before tip: its questionable/probable/doubtful players vs the box score and our final '
          'run (assumed to play: participation probability >= 0.5) ==')
    tab = defaultdict(lambda: Counter())
    pub_lead, cap_lag, applied_gap, stale, stale_changed, late_capture, stale_games = [], [], [], 0, 0, 0, []
    examples4 = []
    for r in rows:
        g, tip = r['game'], r['tip']
        pre = [t for t in r['seq'] if parse_time(t[0]['published_at']) < tip]
        if not pre or not r['final']:
            continue
        e, covered, erows = pre[-1]
        pub_lead.append(_minutes(tip - parse_time(e['published_at'])))
        cap_lag.append(_minutes(parse_time(e['first_retrieved_at']) - parse_time(e['published_at'])))
        late_capture += parse_time(e['first_retrieved_at']) >= tip
        # Did the final run apply the last edition we had captured before tip?
        held = [t for t in pre if parse_time(t[0]['first_retrieved_at']) < tip]
        ed = applied_edition(r['final'])
        if ed and held:
            last_held = held[-1]
            gap = _minutes(parse_time(last_held[0]['published_at']) - parse_time(ed['published_at']))
            applied_gap.append(gap)
            stale += gap > 0
            if gap > 0:
                used = next((t for t in r['seq'] if t[0]['sha256'] == ed['sha256']), None)
                if used is not None:
                    a_rows = {k: v['reported_status'] for k, v in used[2].items()}
                    b_rows = {k: v['reported_status'] for k, v in last_held[2].items()}
                    if a_rows != b_rows:
                        stale_changed += 1
                        stale_games.append(f'{g["game_id"]} {g["away_tricode"]}@{g["home_tricode"]}: final run '
                                           f'{parse_time(r["final"]["created_at"]):%H:%M} applied {parse_time(ed["published_at"]):%H:%M}, '
                                           f'last held edition {parse_time(last_held[0]["published_at"]):%H:%M}')
        box = {b['player_id']: b for b in json.loads((copy / 'finals' / (g['game_id'] + '.json')).read_text())}
        preds = {p['player_id']: p for p in r['final']['predictions']}
        for pid, row in erows.items():
            label = row['reported_status']
            if label not in ('Questionable', 'Probable', 'Doubtful'):
                continue
            b = box.get(pid)
            played = bool(b) and _finite(b.get('minutes')) and b['minutes'] > 0
            p = preds.get(pid) or {}
            prob = p.get('participation_probability')
            if p.get('status') == 'out':
                prob = 0.
            assumed = prob is not None and prob >= .5
            t = tab[label]
            t['n'] += 1
            t['played'] += played
            t['prob_sum'] += prob or 0.
            t['prob_n'] += prob is not None
            t['brier'] += ((prob or 0.) - played) ** 2 if prob is not None else 0.
            t['differ'] += prob is not None and assumed != played
            t['assumed_play_dnp'] += prob is not None and assumed and not played
            t['assumed_out_played'] += prob is not None and not assumed and played
            t['no_forecast'] += prob is None
            if prob is not None and assumed != played and len(examples4) < 8:
                examples4.append(f'{g["game_id"]} {g["away_tricode"]}@{g["home_tricode"]} {row["source_name"]} {label}: p={prob:.2f}, '
                                 f'{"played " + format(b["minutes"], ".0f") + " min" if played else "did not play"}; edition '
                                 f'{parse_time(e["published_at"]):%H:%M} (T-{_minutes(tip - parse_time(e["published_at"])):.0f}), captured '
                                 f'+{_minutes(parse_time(e["first_retrieved_at"]) - parse_time(e["published_at"])):.0f} min')
    print(f'{"status":>12} {"n":>4} {"played":>6} {"mean p":>6} {"Brier":>6} {"differ":>6} {"p>=.5,DNP":>9} {"p<.5,played":>11} {"no fcst":>7}')
    for label in ('Probable', 'Questionable', 'Doubtful'):
        t = tab[label]
        if not t['n']:
            continue
        print(f'{label:>12} {t["n"]:4d} {t["played"]:6d} {fmt(t["prob_sum"] / t["prob_n"] if t["prob_n"] else None, 2):>6} '
              f'{fmt(t["brier"] / t["prob_n"] if t["prob_n"] else None, 3):>6} {t["differ"]:6d} {t["assumed_play_dnp"]:9d} '
              f'{t["assumed_out_played"]:11d} {t["no_forecast"]:7d}')
    q = quartiles(pub_lead)
    print(f'That edition published before tip, minutes (quartiles): {fmt(q[0], 0)} / {fmt(q[1], 0)} / {fmt(q[2], 0)} (n={len(pub_lead)})')
    q = quartiles(cap_lag)
    print(f'Captured after its publication, minutes (quartiles): {fmt(q[0], 0)} / {fmt(q[1], 0)} / {fmt(q[2], 0)}; '
          f'captured only after tip (outages, recovered later): {late_capture} games')
    q = quartiles([g for g in applied_gap if g > 0])
    print(f'Final run applied an older edition than the last one captured before tip: {stale} of {len(applied_gap)} games '
          f'(gap minutes quartiles {fmt(q[0], 0)} / {fmt(q[1], 0)} / {fmt(q[2], 0)}); of them, the game\'s listed statuses differed in {stale_changed}')
    for x in stale_games:
        print('  stale: ' + x)
    for x in examples4:
        print('  e.g. ' + x)

    # ---- context: lineups, cadence
    confirmed = sum(1 for r in rows for run in r['runs'] for lu in run['preflight']['lineups'].values() if lu.get('confirmed'))
    print(f'\nProjected lineups marked confirmed in any run: {confirmed} (of {sum(len(r["runs"]) * 2 for r in rows)} team-runs)')
    gaps = []
    for r in rows:
        ts = [parse_time(x['created_at']) for x in r['runs'] if parse_time(x['created_at']) >= r['tip'] - timedelta(hours=6)]
        gaps += [_minutes(b - a) for a, b in zip(ts, ts[1:])]
    q = quartiles(gaps)
    print(f'Minutes between consecutive pregame runs in the last 6 h (quartiles): {fmt(q[0], 0)} / {fmt(q[1], 0)} / {fmt(q[2], 0)} (n={len(gaps)})')
    if args.dump:
        Path(args.dump).write_text(json.dumps([dict(game_id=d['r']['game']['game_id'], cls=d['cls'], move=d['move'], fmove=d['fmove'],
                                                     move_time=d['mt'].isoformat() if d['mt'] else None, chain=d['chain'],
                                                     events=d['cands'], opposite=d['opposite']) for d in detail], indent=1))


def has_event(r, start_label):
    start = r['starts'][start_label]
    return any(start <= parse_time(e['published_at']) <= r['tip'] and parse_time(e['captured_at']) < r['tip']
               and abs(e['impact']) >= EVENT_FLOOR for e in r['official'] + r['espn'])


# ---------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    e = sub.add_parser('extract', help='read every pregame run of the finished games into a small JSONL file')
    e.add_argument('--copy', required=True)
    e.add_argument('--output', required=True)
    e.add_argument('--share', type=float, default=1 / 3, help='largest fraction of the time spent reading (default a third)')
    e.add_argument('--stop-at', help='UTC time at which to stop (resume later)')
    e.add_argument('--limit', type=int, help='read at most this many runs (a test)')
    e.set_defaults(func=cmd_extract)
    p = sub.add_parser('prepare', help='index and parse the captured report editions; read the archived ESPN injury lists')
    p.add_argument('--copy', required=True)
    p.add_argument('--runs', required=True, help='the extract output')
    p.add_argument('--out', required=True, help='folder for editions.json and espn.json')
    p.add_argument('--games', help='comma-separated game ids only (a test)')
    p.set_defaults(func=cmd_prepare)
    a = sub.add_parser('analyze', help='print the tables')
    a.add_argument('--copy', required=True)
    a.add_argument('--folder', required=True, help='folder holding news-runs.jsonl, editions.json and espn.json')
    a.add_argument('--dump', help='write the per-move details as JSON here')
    a.add_argument('--all-runs', action='store_true', help='keep runs that published the player-sum fallback in the forecast path')
    a.add_argument('--reference', help="a live replay output (main-rebuilt.json): also count its games with closes")
    a.set_defaults(func=cmd_analyze)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()
