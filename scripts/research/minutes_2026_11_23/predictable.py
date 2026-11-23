"""How much of the minutes miss is predictable before the game, and is the role average the problem?

    python3 predictable.py [REFERENCE_FOLDER]

Needs numpy (the other scripts here do not). For every projected player of a
reference's replay rows (default ``backtest-2026-11-22-vacancy-turnovers-production-path``)
it builds about fifty pregame features from the gate's histories and team
games, only from games before each game: his recent minutes at several
horizons, starts, games missed, rest, the team's absences and returns, the
projected margin, his rank and the team's sum of forecasts. Then, on the
players who played, leave one season out:

* the forecast against the raw averages;
* a boosted fit (L1, depth 4) of actual minus forecast on the forecast alone
  (a calibration of its level) and on every feature: the ceiling for pregame
  information, not a model to ship;
* sturdier role averages substituted for the plain one (overtime-normalised,
  blowouts discounted, outliers clipped, a weighted median).

Changes are paired per player, +- one standard error clustered by game. Reads
only; about three minutes.
"""
import collections, datetime, gzip, json, os, pathlib, statistics as st, sys

import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[3]
D = os.environ.get('NBA_DATASETS', str(REPO / 'datasets')) + '/'
REFERENCE = sys.argv[1] if len(sys.argv) > 1 else 'backtest-2026-11-22-vacancy-turnovers-production-path'
ref = json.load(gzip.open(D + REFERENCE + '/rows.json.gz'))
R = [dict(zip(ref['player_fields'], x)) for x in ref['players']]
GM = {x[0]: dict(zip(ref['game_fields'], x)) for x in ref['games']}
H = []
for f in ('espn-season-2023-24', 'espn-season-2024-25', 'regular-season-expanded-2026-10-22'):
    H += json.load(gzip.open(D + f + '/history.json.gz'))
H = [r for r in H if r['season_type'] == 'Regular Season']
TG = []
for f in ('espn-season-2023-24', 'espn-season-2024-25', 'model-upgrade-2026-10-23'):
    TG += json.load(gzip.open(D + f + '/team-games.json.gz'))
ts = lambda s: datetime.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp() / 86400.
tg = {}
for g in TG:
    if 'team_stats' not in g: continue
    h, a = g['home_team_id'], g['away_team_id']
    try:
        tg[g['game_id']] = dict(time=ts(g['game_time']), season=g['season'], home=h, away=a,
                                margin=g['team_stats'][h]['pts'] - g['team_stats'][a]['pts'],
                                ot=g['team_minutes'][h] > 240.5)
    except (KeyError, TypeError):
        pass
tgm_minutes = {}
for g_ in TG:
    for t_, m_ in (g_.get('team_minutes') or {}).items(): tgm_minutes[(g_['game_id'], t_)] = m_
# per (season, team): games in order, with each player's row
games = collections.defaultdict(dict); gtime = {}; season_of = {}
for r in H:
    games[(r['season'], r['team_id'])].setdefault(r['game_id'], {})[r['player_id']] = r
    gtime[r['game_id']] = ts(r['game_time']); season_of[r['game_id']] = r['season']
order = {k: sorted(v, key=lambda g: gtime[g]) for k, v in games.items()}
season_end = collections.defaultdict(float)
for g, s in season_of.items(): season_end[s] = max(season_end[s], gtime[g])
prev_mean = collections.defaultdict(dict)   # season -> pid -> (mean minutes, games)
tmp = collections.defaultdict(list)
for r in H:
    if r['status'] == 'played' and r['minutes'] > 0: tmp[(r['season'], r['player_id'])].append(r['minutes'])
def previous(season): y = int(season[:4]) - 1; return f'{y:04d}-{(y + 1) % 100:02d}'
byteam = collections.defaultdict(list)
for p in R: byteam[(p['game_id'], p['team_id'])].append(p)
NAMES = ['f', 'P', 'played', 'actual', 'actual_points', 'points_mean', 'season', 'k', 'm1', 'm2', 'm3', 'mean3', 'mean5', 'mean10', 'mean_all',
         'med5', 'med10', 'max10', 'min10', 'sd10', 'ew75', 'ew90', 'st1', 'st5', 'st_all', 'prev_mean', 'prev_n', 'layoff', 'rest', 'opp_rest',
         'n_team', 'last_ot', 'last_margin', 'last_exit', 'pm', 'rank', 'team_sum', 'n_proj', 'out_minutes', 'new_out_minutes', 'back_minutes',
         'days_left', 'm1_share', 'starters_out', 'team_blow', 'ot_tonight', 'margin_tonight', 'dnp5', 'pts_per_min', 'top_f', 'f_gap_above', 'f_gap_below']
rows = []; ids = []; HM = []; HO = []; HG = []; HS = []; HT = []
SEAS = {'2023-24': 0, '2024-25': 1, '2025-26': 2, '2026-27': 2}
for (g, t), prs in byteam.items():
    s = season_of.get(g)
    if s is None or g not in tg: continue
    k = (s, t); seq = order[k]
    if g not in seq: continue
    prior = seq[:seq.index(g)]
    info = tg[g]; home = info['home'] == t; opp = info['away'] if home else info['home']
    oseq = order.get((s, opp), []); oprior = [x for x in oseq if gtime[x] < gtime[g]]
    rest = gtime[g] - gtime[prior[-1]] if prior else 9.
    opp_rest = gtime[g] - gtime[oprior[-1]] if oprior else 9.
    pm = GM[g]['projected_margin']; pm = (pm if home else -pm) if pm is not None else 0.
    proj = [p for p in prs if p['expected_minutes'] is not None and p['points_mean'] is not None]
    team_sum = sum(p['expected_minutes'] * (p['participation_probability'] or 0) for p in proj)
    projected_ids = {p['player_id'] for p in proj if (p['participation_probability'] or 0) >= .5}
    # season minutes of every teammate before tonight
    mean_min = {}; last_played = {}
    for pid in {x for j in prior for x in games[k][j]}:
        ms = [games[k][j][pid]['minutes'] for j in prior if pid in games[k][j] and games[k][j][pid]['status'] == 'played' and games[k][j][pid]['minutes'] > 0]
        if ms: mean_min[pid] = sum(ms[-10:]) / len(ms[-10:])
    lastg = games[k][prior[-1]] if prior else {}
    played_last = {pid for pid, r in lastg.items() if r['status'] == 'played' and r['minutes'] > 0}
    out_now = {pid for pid in mean_min if pid not in projected_ids}
    # absent tonight = not projected with P>=.5; counted when a regular (10-game mean >= 12)
    out_minutes = sum(m for pid, m in mean_min.items() if pid in out_now and m >= 12 and any(pid in games[k][j] and games[k][j][pid]['status'] == 'played' for j in prior[-10:]))
    new_out = sum(mean_min[pid] for pid in out_now if pid in played_last and mean_min[pid] >= 12)
    back = sum(p['expected_minutes'] for p in proj if p['player_id'] in projected_ids and p['player_id'] not in played_last and prior)
    starters_last = {pid for pid, r in lastg.items() if r.get('starter')}
    starters_out = len([x for x in starters_last if x in out_now])
    blow = [abs(tg[j]['margin']) >= 15 for j in prior[-15:] if j in tg]
    team_blow = sum(blow) / len(blow) if blow else .3
    ranked = sorted(proj, key=lambda p: -p['expected_minutes'])
    fs = [p['expected_minutes'] for p in ranked]
    for rank, p in enumerate(ranked):
        pid = p['player_id']
        hist = [(j, games[k][j][pid]) for j in reversed(prior) if pid in games[k][j] and games[k][j][pid]['status'] == 'played' and games[k][j][pid]['minutes'] > 0]
        ms = [r['minutes'] for _, r in hist]
        f = p['expected_minutes']
        def mean(v, n=None): v = v[:n] if n else v; return sum(v) / len(v) if v else f
        ew = lambda d: (sum(m * d ** i for i, m in enumerate(ms)) / sum(d ** i for i in range(len(ms)))) if ms else f
        layoff = 0
        for j in reversed(prior):
            r = games[k][j].get(pid)
            if r and r['status'] == 'played' and r['minutes'] > 0: break
            layoff += 1
        lastj = hist[0][0] if hist else None
        pv = tmp.get((previous(s), pid), [])
        starts = [1. if r.get('starter') else 0. for _, r in hist]
        dnp5 = sum(1 for j in prior[-5:] if not (pid in games[k][j] and games[k][j][pid]['status'] == 'played' and games[k][j][pid]['minutes'] > 0))
        played = p['actual_status'] == 'played' and (p['actual_minutes'] or 0) > 0
        tot_min = sum(r['minutes'] for _, r in hist); tot_pts = sum(r['pts'] for _, r in hist)
        rows.append([f, p['participation_probability'] or 0., 1. if played else 0., p['actual_minutes'] or 0., p['actual_points'] or 0., p['points_mean'], SEAS[s], len(ms),
                     mean(ms, 1), ms[1] if len(ms) > 1 else mean(ms, 1), ms[2] if len(ms) > 2 else mean(ms, 2), mean(ms, 3), mean(ms, 5), mean(ms, 10), mean(ms),
                     st.median(ms[:5]) if ms else f, st.median(ms[:10]) if ms else f, max(ms[:10]) if ms else f, min(ms[:10]) if ms else f,
                     st.pstdev(ms[:10]) if len(ms) > 1 else 5., ew(.75), ew(.9), starts[0] if starts else 0., mean(starts, 5) if starts else 0., mean(starts) if starts else 0.,
                     sum(pv) / len(pv) if pv else -1., len(pv), layoff, min(rest, 9.), min(opp_rest, 9.), len(prior),
                     1. if lastj and tg.get(lastj, {}).get('ot') else 0., abs(tg[lastj]['margin']) if lastj and lastj in tg else 10.,
                     1. if ms and len(ms) > 3 and ms[0] < .6 * mean(ms[1:], 5) else 0., pm, rank, team_sum, len(proj), out_minutes, new_out, back,
                     season_end[s] - gtime[g], (ms[0] / mean(ms[1:], 5)) if len(ms) > 3 else 1., starters_out, team_blow,
                     1. if info['ot'] else 0., info['margin'] if home else -info['margin'], dnp5, tot_pts / tot_min if tot_min else 0.,
                     fs[0], (fs[rank - 1] - f) if rank else 0., (f - fs[rank + 1]) if rank + 1 < len(fs) else f])
        ids.append((g, pid, t))
        pad = lambda v: (list(v) + [float('nan')] * 15)[:15]
        HM.append(pad(ms)); HS.append(pad(starts))
        HO.append(pad([tgm_minutes.get((j, t), 240.) for j, _ in hist])); HG.append(pad([(tg[j]['margin'] if tg[j]['home'] == t else -tg[j]['margin']) if j in tg else 0. for j, _ in hist]))
        HT.append(pad([gtime[g] - gtime[j] for j, _ in hist]))
X = np.array(rows, dtype=float); names = list(NAMES); ids = np.array(ids)
HM, HO, HG = np.array(HM), np.array(HO), np.array(HG)
col = lambda n: X[:, names.index(n)]
played = col('played') == 1
f, y, season = col('f'), col('actual'), col('season').astype(int)
LEAK = {'f_old', 'played', 'actual', 'actual_points', 'ot_tonight', 'margin_tonight', 'season', 'points_mean'}
use = [n for n in names if n not in LEAK]
F = np.column_stack([col(n) for n in use])
games = ids[:, 0]


def clustered(v, g):
    u, inv = np.unique(g, return_inverse=True)
    s = np.bincount(inv, v); c = np.bincount(inv)
    m = v.mean(); K = len(u)
    return m, np.sqrt(K / (K - 1) * ((s - m * c) ** 2).sum()) / len(v)


def report(label, pred, mask=None):
    mask = played if mask is None else mask
    d = np.abs(pred[mask] - y[mask]) - np.abs(f[mask] - y[mask])
    m, se = clustered(d, games[mask])
    per = ' '.join('%+.3f' % (d[season[mask] == s].mean()) for s in range(3))
    print(f'{label:46s} MAE {np.abs(pred[mask] - y[mask]).mean():.4f}  change {m:+.4f} ± {se:.4f}  by season {per}')




class GBM:
    def __init__(self, trees=300, depth=4, rate=.05, bins=32, min_leaf=200, loss='l1'):
        self.trees, self.depth, self.rate, self.bins, self.min_leaf, self.loss = trees, depth, rate, bins, min_leaf, loss

    def bin(self, F, fit=False):
        if fit:
            self.edges = [np.unique(np.quantile(F[:, j], np.linspace(0, 1, self.bins + 1)[1:-1])) for j in range(F.shape[1])]
        return np.column_stack([np.searchsorted(e, F[:, j], side='right') for j, e in enumerate(self.edges)]).astype(np.int32)

    def fit(self, F, t):
        B = self.bin(F, True); n, p = B.shape
        self.base = np.median(t) if self.loss == 'l1' else t.mean()
        pred = np.full(n, self.base); self.model = []
        for _ in range(self.trees):
            r = t - pred
            g = np.sign(r) if self.loss == 'l1' else r
            node = np.zeros(n, dtype=np.int64); splits = []
            for level in range(self.depth):
                K = 2 ** level; chosen = {}
                best = np.full(K, 0.); bj = np.full(K, -1); bb = np.zeros(K, dtype=int)
                for j in range(p):
                    key = node * self.bins + B[:, j]
                    S = np.bincount(key, g, K * self.bins).reshape(K, self.bins).cumsum(1)
                    C = np.bincount(key, minlength=K * self.bins).reshape(K, self.bins).cumsum(1)
                    ST, CT = S[:, -1:], C[:, -1:]
                    with np.errstate(divide='ignore', invalid='ignore'):
                        gain = S ** 2 / C + (ST - S) ** 2 / (CT - C) - ST ** 2 / CT
                    gain[(C < self.min_leaf) | (CT - C < self.min_leaf)] = 0.
                    gain = np.nan_to_num(gain)
                    b = gain.argmax(1); gbest = gain[np.arange(K), b]
                    better = gbest > best
                    best[better] = gbest[better]; bj[better] = j; bb[better] = b[better]
                splits.append((bj.copy(), bb.copy()))
                j = bj[node]; go = np.where(j >= 0, B[np.arange(n), np.maximum(j, 0)] > bb[node], False)
                node = node * 2 + go
            leaves = np.zeros(2 ** self.depth)
            for leaf in np.unique(node):
                m = node == leaf
                leaves[leaf] = np.median(r[m]) if self.loss == 'l1' else r[m].mean()
            self.model.append((splits, leaves)); pred += self.rate * leaves[node]
        return self

    def predict(self, F):
        B = self.bin(F); n = len(B); pred = np.full(n, self.base)
        for splits, leaves in self.model:
            node = np.zeros(n, dtype=np.int64)
            for bj, bb in splits:
                j = bj[node]; go = np.where(j >= 0, B[np.arange(n), np.maximum(j, 0)] > bb[node], False)
                node = node * 2 + go
            pred += self.rate * leaves[node]
        return pred


def loso(make):
    out = np.zeros(len(y)); models = []
    for s in range(3):
        tr = played & (season != s); te = season == s
        m = make().fit(F[tr], (y - f)[tr]); out[te] = f[te] + m.predict(F[te]); models.append(m)
    return np.clip(out, 0, 48), models



print(f'{played.sum()} players who played, {len(use)} pregame features, reference {REFERENCE}')
report('forecast', f)
for n in ('m1', 'mean5', 'mean10', 'mean_all', 'ew75', 'ew90'):
    report('raw ' + {'m1': 'last game', 'mean5': 'last five', 'mean10': 'last ten', 'mean_all': 'season', 'ew75': 'decay 0.75', 'ew90': 'decay 0.90'}[n], col(n))
full = F
F = np.column_stack([f]); pred, _ = loso(lambda: GBM()); report('boosted fit on the forecast level alone', pred)
F = full; pred, models = loso(lambda: GBM()); report('boosted fit on every pregame feature', pred)
d = np.abs(pred - y) - np.abs(f - y)
print('  by forecast minutes: ' + ' '.join(f'[{lo},{hi}) {d[played & (f >= lo) & (f < hi)].mean():+.3f}' for lo, hi in ((0, 10), (10, 20), (20, 28), (28, 34), (34, 60))))
points, actual_points = col('points_mean'), col('actual_points')
dp = np.abs(points * pred / np.maximum(f, 1e-9) - actual_points) - np.abs(points - actual_points)
print('  points, first order: %+.4f ± %.4f; at 34 and over %+.4f' % (*clustered(dp[played], games[played]), dp[played & (f >= 34)].mean()))
rng = np.random.default_rng(0); importance = []
for j, n in enumerate(use):
    lost = 0.
    for s in range(3):
        te = played & (season == s); shuffled = F[te].copy(); shuffled[:, j] = rng.permutation(shuffled[:, j])
        lost += (np.abs(np.clip(f[te] + models[s].predict(shuffled), 0, 48) - y[te]) - np.abs(pred[te] - y[te])).sum()
    importance.append((lost / played.sum(), n))
print('  error lost when a feature is shuffled: ' + ', '.join(f'{n} {v:.3f}' for v, n in sorted(importance, reverse=True)[:12]))

# Sturdier role averages, by substitution: forecast + beta * (estimator - plain 0.75 average), three or more games.
ok = played & (col('k') >= 3); valid = ~np.isnan(HM); age = np.arange(HM.shape[1])[None, :]


def average(M, d, extra=None):
    w = np.where(valid, d ** age, 0.) * (1. if extra is None else extra)
    return np.nansum(np.where(valid, M, 0.) * w, 1) / np.maximum(w.sum(1), 1e-12)


def weighted_median(M, d):
    w = np.where(valid, d ** age, 0.); Mz = np.where(valid, M, np.inf); order = np.argsort(Mz, 1)
    ws = np.take_along_axis(w, order, 1); ms = np.take_along_axis(Mz, order, 1)
    c = ws.cumsum(1) / np.maximum(ws.sum(1, keepdims=True), 1e-12)
    return ms[np.arange(len(M)), (c >= .5).argmax(1)]


with np.errstate(all='ignore'):
    plain = average(HM, .75); median10 = np.nanmedian(HM[:, :10], 1)
    normalised = HM * 240. / np.where(np.isnan(HO), 240., HO)
    candidates = {('overtime-normalised', ''): average(normalised, .75)}
    for threshold in (15, 20, 25):
        for weight in (0., .25, .5):
            candidates[('blowouts discounted', (threshold, weight))] = average(HM, .75, np.where(np.abs(np.nan_to_num(HG)) >= threshold, weight, 1.))
    for c in (3, 4, 6, 8):
        candidates[('clipped to the median of ten', c)] = average(np.clip(HM, (median10 - c)[:, None], (median10 + c)[:, None]), .75)
    for d in (.75, .85, .9):
        candidates[('weighted median', d)] = weighted_median(HM, d)
error = np.abs(f - y)
change = lambda estimate, beta, mask: (np.abs(np.clip(f + beta * (estimate - plain), 0, 48) - y) - error)[mask]
print(f'sturdier role averages, {ok.sum()} players with three or more games for the team:')
for family in sorted({k[0] for k in candidates}):
    grid = [(k, beta) for k in candidates if k[0] == family for beta in (.25, .5, .75, 1.)]
    held = []
    for s in range(3):
        k, beta = min(grid, key=lambda kb: change(candidates[kb[0]], kb[1], ok & (season != s)).mean())
        held.append(clustered(change(candidates[k], beta, ok & (season == s)), games[ok & (season == s)]))
    print(f'  {family:30s} held-out seasons: ' + ' '.join(f'{m:+.4f} ± {se:.4f}' for m, se in held))
print('± one standard error clustered by game')
