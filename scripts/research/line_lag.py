"""How stale is the ESPN (DraftKings) closing line the live capture records? Measured against Kalshi's price history.

    python3 scripts/research/line_lag.py --directory data/operations/production

For every game with ESPN line captures and a captured final, three numbers from
Kalshi's one-minute candlesticks (free public API, no key): the Kalshi price at
the moment of ESPN's last pre-tip change (the book difference), Kalshi at tip
minus Kalshi at that moment (what moved after our last capture), and ESPN minus
Kalshi at tip (the staleness the market report actually suffers). Spreads are
Kalshi's 50% crossing interpolated over its spread strikes; win probabilities
are the home winner market's bid-ask mid against ESPN's de-vigged moneyline.
Raw responses are cached under --cache so a rerun is offline. On 2026-10-27,
over 23 games: median staleness 0.33 points, mean 0.53, one game over 2 points
(MEM@UTA, where ESPN's spread disagreed with its own moneyline); win
probability median 0.8 points. ESPN's close is a fair benchmark; Kalshi at tip
is worth capturing live as a second source.
"""
import argparse
import datetime, glob, hashlib, json, math, os, re, sys, time, urllib.request
from statistics import fmean, median
PARSER = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
PARSER.add_argument('--directory', default='data/operations/production', help='Operations directory with market/game-lines and finals')
PARSER.add_argument('--cache', default='data/research/kalshi', help='Where raw Kalshi responses are kept (gitignored under data/)')
PARSER.add_argument('--since', default='2026-10-22', help='Ignore Kalshi events settled before this UTC date')
ARGS = PARSER.parse_args()
P = ARGS.directory
RAW = ARGS.cache
os.makedirs(RAW, exist_ok=True)
API = 'https://api.elections.kalshi.com/trade-api/v2'
SD = 15.6  # margin sd: one point of spread is about 2.6% of win probability near even


def get(path):
    key = os.path.join(RAW, hashlib.sha256(path.encode()).hexdigest() + '.json')
    if os.path.exists(key):
        return json.load(open(key))
    for attempt in range(3):
        try:
            with urllib.request.urlopen(API + path, timeout=30) as r:
                raw = r.read()
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == 2: raise
            time.sleep(2)
    open(key, 'wb').write(raw); time.sleep(0.15)
    return json.loads(raw)


def events(series):
    out, cursor = [], None
    for _ in range(10):
        d = get(f'/events?series_ticker={series}&status=settled&limit=200&with_nested_markets=true' + (f'&cursor={cursor}' if cursor else ''))
        out += d.get('events', []); cursor = d.get('cursor')
        if not cursor: break
    return out


def ts(iso): return datetime.datetime.fromisoformat(iso.replace('Z', '+00:00'))
def devig(home_ml, away_ml):
    imp = [100 / (o + 100) if o > 0 else -o / (-o + 100) for o in (home_ml, away_ml)]
    return imp[0] / sum(imp)


def candles(series, ticker, start, end):
    d = get(f'/series/{series}/markets/{ticker}/candlesticks?start_ts={int(start.timestamp())}&end_ts={int(end.timestamp())}&period_interval=1')
    rows = []
    for c in d.get('candlesticks', []):
        bid, ask = c.get('yes_bid', {}).get('close_dollars'), c.get('yes_ask', {}).get('close_dollars')
        bid, ask = (float(bid) if bid else None), (float(ask) if ask else None)
        mid = (bid + ask) / 2 if bid is not None and ask is not None and ask - bid <= 0.12 else None
        rows.append((c['end_period_ts'], mid))
    return rows


def at(rows, when):
    """Last quoted mid at or before ``when`` (within 30 minutes)."""
    t = when.timestamp(); best = None
    for end, mid in rows:
        if end <= t + 60 and mid is not None and end >= t - 1800: best = mid
    return best


def implied_spread(curve):
    """Home margin x where P(home margin > x) crosses 0.5, by linear interpolation over the quoted strikes."""
    pts = sorted((x, p) for x, p in curve.items() if p is not None)
    for (x0, p0), (x1, p1) in zip(pts, pts[1:]):
        if (p0 - .5) * (p1 - .5) <= 0 and p0 != p1:
            return x0 + (p0 - .5) / (p0 - p1) * (x1 - x0)
    return None


def main():
    sched = {g['game_id']: g for g in json.load(open(P + '/schedule/latest.json'))['games']}
    names = {}
    for g in sched.values():
        for side in ('home', 'away'):
            names[g[side + '_name']] = g[side + '_tricode']
    def code(name):  # Kalshi titles use city names: 'Memphis vs Utah', 'Oklahoma City', 'LA Clippers', 'Golden State'
        for full, tri in names.items():
            if full.startswith(name) or name in full: return tri
        return None
    game_events = {e['event_ticker']: e for e in events('KXNBAGAME')}
    spread_events = {e['event_ticker']: e for e in events('KXNBASPREAD')}
    print(f'Kalshi settled events: {len(game_events)} games, {len(spread_events)} spreads')
    codes = {g[side + '_tricode'] for g in sched.values() for side in ('home', 'away')}
    by_key = {}
    for e in game_events.values():
        tail = e['event_ticker'].split('-')[-1][-6:]
        away, home = tail[:3], tail[3:]
        if away not in codes or home not in codes or not e.get('markets'): continue  # Kalshi team codes are the NBA tricodes
        close = min(ts(m['close_time']) for m in e['markets'])
        if close < datetime.datetime.fromisoformat(ARGS.since).replace(tzinfo=datetime.timezone.utc): continue
        by_key.setdefault((away, home), []).append((close, e))
    market_rows = {r['game_id']: r for r in json.load(open(P + '/market/report.json'))['games']}
    results = []
    for f in sorted(glob.glob(P + '/market/game-lines/*.jsonl')):
        gid = os.path.basename(f)[:-6]; g = sched[gid]
        if not os.path.exists(P + '/finals/' + gid + '.json'): continue
        tip = ts(g['start_time'])
        recs = [json.loads(l) for l in open(f) if l.strip()]
        pre = [r for r in recs if ts(r['retrieved_at']) < tip]
        if not pre: continue
        last = pre[-1]; t_esp = ts(last['retrieved_at'])
        s_e = last['lines']['spread']['home']['current_line']; s_e = -s_e if s_e is not None else None  # home margin
        ml = last['lines']['moneyline']; p_e = devig(ml['home']['current_odds'], ml['away']['current_odds']) if ml['home']['current_odds'] and ml['away']['current_odds'] else None
        cands = [(c, e) for c, e in by_key.get((g['away_tricode'], g['home_tricode']), []) if abs((c - tip).total_seconds()) < 8 * 3600]
        if not cands: print('no Kalshi event for', gid, g['away_tricode'], g['home_tricode']); continue
        _, event = min(cands, key=lambda ce: abs((ce[0] - tip).total_seconds()))
        home_market = next(m for m in event['markets'] if m['ticker'].endswith('-' + g['home_tricode']))
        window = (min(t_esp, tip) - datetime.timedelta(minutes=20), tip + datetime.timedelta(minutes=3))
        win = candles('KXNBAGAME', home_market['ticker'], *window)
        p_k_tip, p_k_esp = at(win, tip), at(win, t_esp)
        spread_event = spread_events.get(event['event_ticker'].replace('KXNBAGAME', 'KXNBASPREAD'))
        s_k_tip = s_k_esp = None
        if spread_event and s_e is not None:
            curve_tip, curve_esp = {}, {}
            for m in spread_event.get('markets') or []:
                strike = m.get('floor_strike')
                if strike is None: continue
                team = re.search(r'-([A-Z]+)\d+$', m['ticker']).group(1)
                x = strike if team == g['home_tricode'] else -strike  # 'away wins by over s' == home margin < -s
                if abs(x - s_e) > 8: continue
                rows = candles('KXNBASPREAD', m['ticker'], *window)
                pt, pe = at(rows, tip), at(rows, t_esp)
                if team == g['home_tricode']:
                    curve_tip[x], curve_esp[x] = pt, pe
                else:
                    curve_tip[x] = 1 - pt if pt is not None else None; curve_esp[x] = 1 - pe if pe is not None else None
            s_k_tip, s_k_esp = implied_spread(curve_tip), implied_spread(curve_esp)
        fin = json.load(open(P + '/finals/' + gid + '.json'))
        actual = sum(r['pts'] for r in fin if r['team_id'] == g['home_team_id']) - sum(r['pts'] for r in fin if r['team_id'] == g['away_team_id'])
        model = market_rows.get(gid, {}).get('model_margin')
        results.append(dict(gid=gid, game=f"{g['away_tricode']}@{g['home_tricode']}", tip=g['start_time'][5:16], minutes_before=round((tip - t_esp).total_seconds() / 60),
                            s_e=s_e, s_k_esp=s_k_esp, s_k_tip=s_k_tip, p_e=p_e, p_k_esp=p_k_esp, p_k_tip=p_k_tip, actual=actual, model=model))
    print(f"\n{'game':10s} {'tip':>11s} {'ESPN age':>8s} | {'ESPN':>6s} {'Kalshi@ESPN':>11s} {'Kalshi@tip':>10s} | {'P ESPN':>6s} {'P K@ESPN':>8s} {'P K@tip':>7s} | {'actual':>6s} {'model':>6s}")
    f = lambda v, w='6.1f': f'{v:{w}}' if v is not None else ' ' * int(w.split('.')[0])
    for r in results:
        print(f"{r['game']:10s} {r['tip']:>11s} {r['minutes_before']:6d}m  | {f(r['s_e'])} {f(r['s_k_esp'],'11.1f')} {f(r['s_k_tip'],'10.1f')} | {f(r['p_e'],'6.2f')} {f(r['p_k_esp'],'8.2f')} {f(r['p_k_tip'],'7.2f')} | {r['actual']:6d} {f(r['model'])}")
    def summary(label, pairs):
        d = [a - b for a, b in pairs if a is not None and b is not None]
        if not d: print(label, 'no pairs'); return
        print(f"{label:58s} n {len(d):2d}  mean {fmean(d):+.2f}  median |d| {median(abs(v) for v in d):.2f}  mean |d| {fmean(abs(v) for v in d):.2f}  >=1 {sum(abs(v) >= 1 for v in d)}  >=2 {sum(abs(v) >= 2 for v in d)}")
    print('\nSpread (home margin, points):')
    summary('book difference at ESPN capture time: ESPN - Kalshi', [(r['s_e'], r['s_k_esp']) for r in results])
    summary('movement after ESPN last changed: Kalshi@tip - Kalshi@ESPN', [(r['s_k_tip'], r['s_k_esp']) for r in results])
    summary('staleness suffered: ESPN - Kalshi@tip', [(r['s_e'], r['s_k_tip']) for r in results])
    print('\nHome win probability (percentage points):')
    for label, pairs in (('ESPN - Kalshi@ESPN', [(r['p_e'], r['p_k_esp']) for r in results]), ('Kalshi@tip - Kalshi@ESPN', [(r['p_k_tip'], r['p_k_esp']) for r in results]), ('ESPN - Kalshi@tip', [(r['p_e'], r['p_k_tip']) for r in results])):
        summary(label, [(a * 100 if a is not None else None, b * 100 if b is not None else None) for a, b in pairs])
    print('\nAgainst the result (same games, mean absolute margin error):')
    for label, key in (('ESPN close', 's_e'), ('Kalshi at tip', 's_k_tip'), ('model', 'model')):
        rows = [r for r in results if r[key] is not None and r['s_k_tip'] is not None and r['model'] is not None]
        if rows: print(f"  {label:14s} n {len(rows)}  MAE {fmean(abs(r['actual'] - r[key]) for r in rows):.2f}")


main()
