"""Deterministic, retrospective clutch comparisons. Never imported by forecasts.

python -m nba_forecast.clutch_ratings --output data/operations/live/clutch.json
"""
import argparse
from collections import defaultdict
import gzip
import hashlib
from itertools import permutations
import json
from pathlib import Path
import random
import statistics

from .clutch import clutch_url, normalize

ARCHIVE = Path(__file__).resolve().parents[1] / 'datasets/clutch-2026-10-22'
SEASONS = ('2023-24', '2024-25', '2025-26')
COUNTS = ('GP', 'MIN', 'FGM', 'FGA', 'FG3M', 'FG3A', 'FTM', 'FTA', 'PTS', 'AST', 'TOV')
RATE_FIELDS = {'pts_min': 'PTS', 'fga_min': 'FGA', 'fta_min': 'FTA', 'ast_min': 'AST', 'tov_min': 'TOV'}
PRIOR_ATTEMPTS = 30
PRIOR_MINUTES = 50
DRAWS = 1024
POLICY = ('Historical description only. No overall-impact or psychological clutch-skill rating. '
          'No forecast weights changed. Same-period priors and retrospectively retrieved totals '
          'are not eligible historical pregame evidence.')


def divide(a, b):
    return a / b if b else None


def load_archive(directory=ARCHIVE):
    """Check both byte hashes, replay official validation, reconcile normalized rows."""
    root = Path(directory)
    manifest = json.loads((root / 'manifest.json').read_text())
    entries = {}
    decoded = {}
    for item in manifest:
        path = item['path']
        target = (root / path).resolve()
        if not target.is_relative_to(root.resolve()) or path in entries:
            raise ValueError('Invalid or duplicate archive path')
        compressed = target.read_bytes()
        if hashlib.sha256(compressed).hexdigest() != item['compressed_sha256']:
            raise ValueError(f'Compressed hash mismatch: {path}')
        raw = gzip.decompress(compressed)
        if hashlib.sha256(raw).hexdigest() != item['uncompressed_sha256']:
            raise ValueError(f'Uncompressed hash mismatch: {path}')
        entries[path] = item
        decoded[path] = json.loads(raw)
    seasons = []
    for season in SEASONS:
        name = f'{season}.json.gz'
        data = decoded[name]
        url = clutch_url(season)
        if (data['season'] != season or data['source_url'] != url or data['definition'] !=
                {'seconds_remaining': 300, 'max_score_margin': 5, 'season_type': 'Regular Season'}):
            raise ValueError('Archive definition mismatch')
        originals = [(path, payload) for path, payload in decoded.items()
                     if path.startswith('raw/') and payload.get('parameters', {}).get('Season') == season]
        if len(originals) != 1:
            raise ValueError('Expected one original response per season')
        raw_path, original = originals[0]
        provenance = json.loads((root / raw_path.replace('.json.gz', '.provenance.json')).read_text())
        if provenance['source_url'] != url or provenance['sha256'] != entries[raw_path]['uncompressed_sha256']:
            raise ValueError('Original provenance mismatch')
        rows = normalize(original, season)
        if rows != data['rows'] or data['parameters'] != original['parameters']:
            raise ValueError('Normalized rows differ from verified original')
        for r in rows:
            if r['FGM'] - r['FG3M'] > r['FGA'] - r['FG3A']:
                raise ValueError('Impossible two-point counts')
            if not r['GP'] or (not r['MIN'] and any(r[k] for k in ('FGA', 'FTA', 'PTS', 'AST', 'TOV'))):
                raise ValueError('Counts without exposure')
        seasons.append(dict(season=season, rows=rows, source_url=url,
                            retrieved_at=provenance['retrieved_at'],
                            normalized_sha256=entries[name]['uncompressed_sha256'],
                            original_sha256=entries[raw_path]['uncompressed_sha256']))
    return seasons


def shot_counts(r):
    return {'two': (r['FGM']-r['FG3M'], r['FGA']-r['FG3A']),
            'three': (r['FG3M'], r['FG3A']), 'ft': (r['FTM'], r['FTA'])}


def shooting(makes, attempts, prior_mean, strength, seed):
    """Beta-binomial posterior; deterministic Monte Carlo quantiles, not calibrated CI."""
    mean = min(1-1e-6, max(1e-6, prior_mean))
    alpha, beta = makes + strength*mean, attempts-makes + strength*(1-mean)
    rng = random.Random(seed)
    draws = [rng.betavariate(alpha, beta) for _ in range(DRAWS)]
    ordered = sorted(draws)
    return dict(raw=divide(makes, attempts), adjusted=alpha/(alpha+beta),
                interval90=[ordered[int(.05*DRAWS)], ordered[int(.95*DRAWS)]],
                makes=makes, attempts=attempts, prior_mean=prior_mean,
                prior_share=strength/(attempts+strength), draws=draws)


def pool_rows(seasons):
    pooled = {}
    for rows in seasons:
        for r in rows:
            target = pooled.setdefault(r['PLAYER_ID'], dict(PLAYER_ID=r['PLAYER_ID'], PLAYER_NAME=r['PLAYER_NAME'], **dict.fromkeys(COUNTS, 0)))
            target['PLAYER_NAME'] = r['PLAYER_NAME']
            for k in COUNTS:
                target[k] += r[k]
    return sorted(pooled.values(), key=lambda r: r['PLAYER_ID'])


def rate_period(label, rows):
    totals = {k: sum(r[k] for r in rows) for k in COUNTS}
    total_shots = shot_counts(totals)
    rated = []
    for r in rows:
        raw = {k: divide(r[field], r['MIN']) for k, field in RATE_FIELDS.items()}
        raw.update(pts_gp=divide(r['PTS'], r['GP']), min_gp=divide(r['MIN'], r['GP']),
                   fg=divide(r['FGM'], r['FGA']), efg=divide(r['FGM']+.5*r['FG3M'], r['FGA']),
                   ts=divide(r['PTS'], 2*(r['FGA']+.44*r['FTA'])),
                   three_share=divide(r['FG3A'], r['FGA']), ast_tov=divide(r['AST'], r['TOV']))
        peers_minutes = totals['MIN']-r['MIN']
        rate_priors = {k: divide(totals[field]-r[field], peers_minutes) for k, field in RATE_FIELDS.items()}
        estimates = {}
        for factor in (.5, 1, 2):
            exposure = PRIOR_MINUTES*factor
            estimates[str(factor)] = {k: (r[field]+exposure*rate_priors[k])/(r['MIN']+exposure)
                                     if rate_priors[k] is not None else raw[k]
                                     for k, field in RATE_FIELDS.items()}
        shots = {}
        components = shot_counts(r)
        for key, (made, attempts) in components.items():
            tm, ta = total_shots[key]
            # Jeffreys smoothing only prevents degenerate peer baselines at 0/100%.
            prior = (tm-made+.5)/(ta-attempts+1)
            seed = int.from_bytes(hashlib.sha256(f'{label}:{r["PLAYER_ID"]}:{key}'.encode()).digest()[:8], 'big')
            shots[key] = shooting(made, attempts, prior, PRIOR_ATTEMPTS, seed)
            shots[key]['qualified'] = r['GP'] >= 20 and r['MIN'] >= 50 and attempts >= 30
            for factor in (.5, 1, 2):
                strength = PRIOR_ATTEMPTS*factor
                estimates[str(factor)][key] = (made+strength*prior)/(attempts+strength)
        mix = divide(r['FG3A'], r['FGA'])
        efg_interval = None
        if mix is not None:
            efg_draws = sorted((1-mix)*a+1.5*mix*b for a, b in zip(shots['two']['draws'], shots['three']['draws']))
            efg_interval = [efg_draws[int(.05*DRAWS)], efg_draws[int(.95*DRAWS)]]
        for estimate in estimates.values():
            estimate['efg'] = (1-mix)*estimate['two']+1.5*mix*estimate['three'] if mix is not None else None
        for shot in shots.values():
            del shot['draws']
        qualified = r['GP'] >= 20 and r['MIN'] >= 50
        rated.append(dict(player_id=r['PLAYER_ID'], name=r['PLAYER_NAME'], totals={k:r[k] for k in COUNTS},
                          raw=raw, adjusted=estimates['1'], sensitivity=estimates, shooting=shots,
                          efg_interval90=efg_interval, qualified=qualified,
                          efficiency_qualified=qualified and r['FGA'] >= 30,
                          rate_prior_share=PRIOR_MINUTES/(r['MIN']+PRIOR_MINUTES) if peers_minutes > 0 else 0,
                          rate_prior_available=peers_minutes > 0,
                          ratings={}))
    cohorts = {}
    for metric in ('pts_min', 'fga_min', 'fta_min', 'ast_min', 'tov_min', 'efg', 'two', 'three', 'ft'):
        def eligible(r):
            if metric in ('two', 'three', 'ft'):
                return r['shooting'][metric]['qualified']
            return r['efficiency_qualified'] if metric == 'efg' else r['qualified']
        population = [r['adjusted'][metric] for r in rated if eligible(r)]
        cohorts[metric] = len(population)
        for r in rated:
            v = r['adjusted'][metric]
            # Midrank percentile; tied values have identical ratings. Lower TOV is better,
            # but explicitly not a workload-adjusted ball-security rating.
            score = (sum(x < v for x in population)+.5*sum(x == v for x in population))/len(population)*100 if eligible(r) and population else None
            r['ratings'][metric] = (100-score if metric == 'tov_min' else score) if score is not None else None
    return dict(period=label, rows=rated, cohorts=cohorts)


def decompose(a, b):
    """Symmetric three-factor Shapley accounting of the change in clutch PTS/GP."""
    factors = []
    for r in (a, b):
        shots = list(shot_counts(r).values())
        if not r['GP'] or not r['MIN'] or any(attempts == 0 for _, attempts in shots):
            return None
        factors.append([r['MIN']/r['GP'], [n/r['MIN'] for _, n in shots], [m/n for m, n in shots]])
    def value(v):
        return v[0]*sum(weight*rate*accuracy for weight, rate, accuracy in zip((2, 3, 1), v[1], v[2]))
    effects = [0., 0., 0.]
    for order in permutations(range(3)):
        state = list(factors[0])
        for i in order:
            old = value(state)
            state[i] = factors[1][i]
            effects[i] += (value(state)-old)/6
    return dict(delta_pts_gp=b['PTS']/b['GP']-a['PTS']/a['GP'],
                effects=dict(zip(('court_time', 'attempt_rates', 'conversion'), effects)))


def build_report(directory=ARCHIVE):
    archive = load_archive(directory)
    periods = [rate_period(a['season'], a['rows']) for a in archive]
    periods.append(rate_period('pooled', pool_rows([a['rows'] for a in archive])))
    lookup = [{r['player_id']:r for r in p['rows']} for p in periods]
    originals = [{r['PLAYER_ID']:r for r in a['rows']} for a in archive]
    players = []
    for pooled in periods[-1]['rows']:
        pid = pooled['player_id']
        seasons = [table.get(pid) for table in lookup[:3]]
        trends = {}
        for metric in ('pts_min', 'fga_min', 'fta_min', 'ast_min', 'tov_min', 'efg'):
            values = [r['adjusted'][metric] for r in seasons if r and
                      (r['efficiency_qualified'] if metric == 'efg' else r['qualified'])]
            trends[metric] = dict(qualified_seasons=len(values),
                                 range=max(values)-min(values) if len(values)>1 else None)
        transitions = []
        for i in range(2):
            a, b = originals[i].get(pid), originals[i+1].get(pid)
            transitions.append(dict(start=SEASONS[i], end=SEASONS[i+1],
                                    decomposition=decompose(a,b) if a and b else None))
        players.append(dict(player_id=pid, name=pooled['name'], seasons=seasons, pooled=pooled,
                            available_seasons=sum(r is not None for r in seasons),
                            trends=trends, transitions=transitions))
    consistency = []
    for i in range(2):
        for metric in ('pts_min', 'fga_min', 'fta_min', 'ast_min', 'tov_min', 'efg'):
            pairs = [(lookup[i][pid]['ratings'][metric], lookup[i+1][pid]['ratings'][metric])
                     for pid in sorted(lookup[i].keys() & lookup[i+1].keys())
                     if lookup[i][pid]['ratings'][metric] is not None and lookup[i+1][pid]['ratings'][metric] is not None]
            # Descriptive Pearson correlation of within-season midrank percentiles.
            corr = statistics.correlation(*zip(*pairs)) if len(pairs)>2 and all(len(set(x))>1 for x in zip(*pairs)) else None
            consistency.append(dict(start=SEASONS[i], end=SEASONS[i+1], metric=metric, pairs=len(pairs), rank_correlation=corr))
    return dict(schema_version=1, method_version='clutch-descriptive-v1', forecast_eligible=False,
                seasons=list(SEASONS), definition='Final five minutes of Q4 or overtime, score margin within five; regular season.',
                policy=POLICY, priors=dict(attempts=PRIOR_ATTEMPTS, minutes=PRIOR_MINUTES,
                                          sensitivity_factors=[.5,1,2], monte_carlo_draws=DRAWS),
                qualification=dict(games=20, minutes=50, shooting_attempts=30),
                sources=[{k:v for k,v in a.items() if k != 'rows'} for a in archive],
                periods=periods, players=players, consistency=consistency)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=ARCHIVE)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n')
    temporary.replace(args.output)
    print(f'{sum(len(p["rows"]) for p in report["periods"][:3])} player-seasons; {len(report["players"])} players; forecast weights unchanged')


if __name__ == '__main__':
    main()
