"""Fixed chronological backtest: the scoreboard every model change must run.

One season is replayed game by game with the current model code. For each game
the roster is that box score's own players, all treated as available; the
history is every box score from earlier Eastern dates; the forecast is the
ordinary baseline model. Player metrics condition on actual positive
participation, like the live calibration population; team totals and margins
use full team scores. This measures the points and minutes model under the
information a live run has, except availability, which live runs get from
injury reports and a replay cannot. Prior-season aggregate priors and the
archive-bound enhancements are not available historically either.

A scoreboard is compared with a reference by pairing the same player-games, so
the verdict reflects a change beyond sampling noise (two clustered standard errors
of the mean paired difference), not a difference in the games evaluated.
"""
import gzip
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, pstdev, variance
from zoneinfo import ZoneInfo

from .intervals import HEURISTIC_Z80, INTERVAL_FAMILY
from .model import MODEL_VERSION, _baseline_forecast
from .runner_release import package_digest
from .schedule import atomic_json, canonical
from .validation import parse_time

VERSION = 'backtest-1'
EASTERN = ZoneInfo('America/New_York')
BUCKETS = (('0-10', 0., 10.), ('10-20', 10., 20.), ('20-30', 20., 30.), ('30+', 30., math.inf))
PLAYER_FIELDS = ('game_id', 'player_id', 'team_id', 'expected_minutes', 'actual_minutes', 'points_mean',
                 'points_sd', 'points_lower80', 'points_upper80', 'actual_points', 'actual_status',
                 'participation_probability')
TEAM_FIELDS = ('game_id', 'team_id', 'home', 'projected_points', 'actual_points_regulation', 'actual_points',
               'projected_players', 'covered_actual_points')
GAME_FIELDS = ('game_id', 'home_team_id', 'away_team_id', 'projected_margin', 'actual_margin', 'prior_sd',
               'home_win_probability')
# The margin distribution is the production one: a discrete normal centred on the
# projected margin with the paired-final outcome dispersion of earlier game days
# as its scale (margins.prepare_margin_inputs), once that many finals are known.
MARGIN_PRIOR_MIN_GAMES = 100
# Location and scale corrections are fitted on the same bounds as the live
# prospective calibration, by likelihood of the actual margin.
CALIBRATION_BIASES = [b / 2 for b in range(-16, 17)]
CALIBRATION_SCALES = [s / 50 for s in range(35, 71)]
LIMITATIONS = ('Rosters are the box score itself, all players available: no injury reports, so participation '
               'is not forecast and DNP players still draw minutes. No prior-season aggregate priors, no '
               'archive-bound enhancements. Player metrics condition on positive participation. Team points '
               'use regulation-equivalent scores; margins and winners use final scores.')


def read_json(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)


def _day(value):
    return parse_time(value).astimezone(EASTERN).date()


def _margin_prior_sd(margins):
    """sqrt(sample variance + squared mean) of earlier paired final margins, as production prepares it."""
    if len(margins) < MARGIN_PRIOR_MIN_GAMES:
        return None
    return math.sqrt(variance(margins) + fmean(margins) ** 2)


def _normal_cdf(z):
    return .5 * math.erfc(-z / math.sqrt(2.))


def _home_win_probability(location, sd):
    """P(final margin > 0) under the discrete normal: the mass above the half-integer step."""
    return 1. - _normal_cdf((.5 - location) / sd)


def _margin_cell_probability(actual, location, sd):
    return max(_normal_cdf((actual + .5 - location) / sd) - _normal_cdf((actual - .5 - location) / sd), 1e-9)


def evaluation_games(records, warmup_fraction=.3):
    """All games in order, and the fixed evaluation set: every game after the warm-up dates."""
    if not 0 <= warmup_fraction < 1:
        raise ValueError('warmup_fraction must be in [0, 1)')
    ordered = sorted(records, key=lambda r: (parse_time(r['game_time']), r['game_id']))
    dates = sorted({_day(r['game_time']) for r in ordered})
    first = dates[int(len(dates) * warmup_fraction)] if dates else None
    return ordered, [r for r in ordered if first is not None and _day(r['game_time']) >= first]


def replay(history, records, as_of, *, warmup_fraction=.3, limit=None, progress=None):
    """Forecast each evaluation game from earlier game days only."""
    home_of = {r['game_id']: r['home_team_id'] for r in records}
    by_game = defaultdict(list)
    for r in history:
        if r['game_id'] in home_of and 'home' not in r:
            r = dict(r, home=r['team_id'] == home_of[r['game_id']])
        by_game[r['game_id']].append(r)
    ordered, evaluated = evaluation_games(records, warmup_fraction)
    if limit:
        evaluated = evaluated[-limit:]
    targets = {g['game_id'] for g in evaluated}
    players, teams, games, skipped, prior = [], [], [], defaultdict(int), []
    prior_days = []  # (day, rows) in order, so the history before a game is a prefix.
    margin_days = []  # (day, final home margins), the same way.
    for index, game in enumerate(ordered):
        rows = by_game.get(game['game_id'], [])
        day = _day(game['game_time'])
        if game['game_id'] in targets:
            if not rows:
                skipped['missing_player_history'] += 1
            else:
                history_before = [r for d, batch in prior_days if d < day for r in batch]
                g = dict(game_id=game['game_id'], season_type='Regular Season', start_time=game['game_time'],
                         home_team_id=game['home_team_id'], away_team_id=game['away_team_id'])
                roster = [dict(player_id=r['player_id'], name=r['name'], team_id=r['team_id'], status='available',
                               status_source_url=r['source_url'], status_observed_at=r['known_at']) for r in rows]
                snapshot = dict(schema_version=1, as_of=as_of, season=game['season'], forecast_stage='preliminary',
                                research_reconstruction=True, games=[g],
                                roster=dict(players=roster, observed_at=as_of, source_url=rows[0]['source_url'],
                                            complete=False, team_ids=[g['home_team_id'], g['away_team_id']]))
                result = _baseline_forecast(snapshot, history_before)
                actual = {r['player_id']: r for r in rows}
                totals = defaultdict(lambda: dict(projected=0., count=0, covered=0.))
                for p in result['predictions']:
                    a = actual[p['player_id']]
                    projected = p['status'] == 'projected'
                    probability = p.get('participation_probability') if projected else None
                    players.append([game['game_id'], p['player_id'], p['team_id'],
                                    p['expected_minutes'] if projected else None, a['minutes'],
                                    p['points_mean'] if projected else None, p['points_sd'] if projected else None,
                                    p['points_lower80'] if projected else None, p['points_upper80'] if projected else None,
                                    a['pts'], a['status'], probability])
                    if projected:
                        t = totals[p['team_id']]
                        weight = 1.0 if probability is None else probability
                        t['projected'] += weight * p['points_mean']; t['count'] += 1; t['covered'] += a['pts']
                for tid in (g['home_team_id'], g['away_team_id']):
                    stats, minutes = game['team_stats'][tid], game['team_minutes'][tid]
                    teams.append([game['game_id'], tid, tid == g['home_team_id'], totals[tid]['projected'],
                                  stats['pts'] * 48 / (minutes / 5) if minutes else None, stats['pts'],
                                  totals[tid]['count'], totals[tid]['covered']])
                if all(totals[t]['count'] for t in (g['home_team_id'], g['away_team_id'])):
                    sd = _margin_prior_sd([m for d, batch in margin_days if d < day for m in batch])
                    location = totals[g['home_team_id']]['projected'] - totals[g['away_team_id']]['projected']
                    actual_margin = game['team_stats'][g['home_team_id']]['pts'] - game['team_stats'][g['away_team_id']]['pts']
                    games.append([game['game_id'], g['home_team_id'], g['away_team_id'], location, actual_margin, sd,
                                  _home_win_probability(location, sd) if sd else None])
        final_margin = game['team_stats'][game['home_team_id']]['pts'] - game['team_stats'][game['away_team_id']]['pts']
        if margin_days and margin_days[-1][0] == day:
            margin_days[-1][1].append(final_margin)
        else:
            margin_days.append((day, [final_margin]))
        if rows:
            if prior_days and prior_days[-1][0] == day:
                prior_days[-1][1].extend(rows)
            else:
                prior_days.append((day, list(rows)))
        if progress and index % 100 == 0:
            progress(dict(games_seen=index, player_rows=len(players)))
    return dict(players=players, teams=teams, games=games, skipped=dict(skipped),
                evaluated_games=len(evaluated), total_games=len(ordered))


def _records(rows, fields):
    """Rows saved without a newer field read back with it as None."""
    return [dict(zip(PLAYER_FIELDS if fields is PLAYER_FIELDS else fields, r)) if len(r) == len(fields)
            else {**dict.fromkeys(fields), **dict(zip(fields[:len(r)], r))} for r in rows]


def _fit_margin_correction(rows):
    """The location bias and scale factor that maximise the likelihood of the actual margins."""
    def log_loss(bias, scale):
        return fmean(-math.log(_margin_cell_probability(r['actual_margin'], r['projected_margin'] + bias, r['prior_sd'] * scale)) for r in rows)
    best = min(((log_loss(b, s), b, s) for b in CALIBRATION_BIASES for s in CALIBRATION_SCALES), key=lambda x: (x[0], abs(x[1]), abs(x[2] - 1)))
    return dict(location_bias=best[1], scale_factor=best[2], log_loss=best[0])


def _margin_probability_metrics(rows):
    """How good the production margin distribution would have been: winner Brier, log loss, reliability, fitted corrections."""
    rows = [r for r in rows if r.get('prior_sd')]
    if len(rows) < 2:
        return dict(n=len(rows))
    def brier(bias=0., scale=1.):
        return fmean((_home_win_probability(r['projected_margin'] + bias, r['prior_sd'] * scale) - (r['actual_margin'] > 0)) ** 2 for r in rows)
    def log_loss(subset, bias=0., scale=1.):
        return fmean(-math.log(_margin_cell_probability(r['actual_margin'], r['projected_margin'] + bias, r['prior_sd'] * scale)) for r in subset)
    bins = defaultdict(list)
    for r in rows:
        bins[min(int(r['home_win_probability'] * 10), 9)].append(r)
    reliability = [dict(bin=k / 10, n=len(v), mean_probability=fmean(r['home_win_probability'] for r in v),
                        home_win_share=fmean(r['actual_margin'] > 0 for r in v)) for k, v in sorted(bins.items())]
    fit = _fit_margin_correction(rows)
    half = len(rows) // 2
    holdout = None
    if half >= 16:
        first = _fit_margin_correction(rows[:half])
        holdout = dict(fitted_on=half, scored=len(rows) - half, location_bias=first['location_bias'], scale_factor=first['scale_factor'],
                       log_loss_prior=log_loss(rows[half:]), log_loss_fitted=log_loss(rows[half:], first['location_bias'], first['scale_factor']))
    return dict(n=len(rows), winner_brier=brier(), log_loss=log_loss(rows), mean_home_win_probability=fmean(r['home_win_probability'] for r in rows),
                home_win_share=fmean(r['actual_margin'] > 0 for r in rows), mean_prior_sd=fmean(r['prior_sd'] for r in rows),
                residual_sd=pstdev([r['projected_margin'] - r['actual_margin'] for r in rows]), reliability=reliability,
                calibration_fit=dict(**fit, log_loss_prior=log_loss(rows), winner_brier_fitted=brier(fit['location_bias'], fit['scale_factor']),
                                     biases=[CALIBRATION_BIASES[0], CALIBRATION_BIASES[-1]], scales=[CALIBRATION_SCALES[0], CALIBRATION_SCALES[-1]]),
                holdout=holdout,
                basis='Discrete normal on the projected margin with the paired-final dispersion of earlier game days; '
                      'the home-win probability is the mass above zero.')


def _metrics(rows):
    """Points and minutes accuracy for projected rows whose player played."""
    if not rows:
        return dict(n=0)
    errors = [r['points_mean'] - r['actual_points'] for r in rows]
    minutes = [r['expected_minutes'] - r['actual_minutes'] for r in rows]
    covered = [r['points_lower80'] <= r['actual_points'] <= r['points_upper80'] for r in rows]
    return dict(n=len(rows), points_mae=fmean(abs(e) for e in errors),
                points_rmse=math.sqrt(fmean(e * e for e in errors)), points_bias=fmean(errors),
                minutes_mae=fmean(abs(m) for m in minutes), minutes_bias=fmean(minutes),
                coverage80=fmean(covered), mean_width80=fmean(r['points_upper80'] - r['points_lower80'] for r in rows))


def _participation_metrics(projected):
    """Brier score of the participation probability against actually playing, with the constant-rate baseline."""
    outcomes = [(r['participation_probability'], r['actual_status'] == 'played' and r['actual_minutes'] > 0)
                for r in projected if r['participation_probability'] is not None]
    if not outcomes:
        return dict(n=0)
    share = fmean(y for _, y in outcomes)
    return dict(n=len(outcomes), brier=fmean((p - y) ** 2 for p, y in outcomes), played_share=share,
                mean_probability=fmean(p for p, _ in outcomes), constant_rate_brier=share * (1 - share),
                assume_all_play_brier=fmean((1 - y) ** 2 for _, y in outcomes))


def summarize(players, teams, game_rows=()):
    rows = _records(players, PLAYER_FIELDS)
    projected = [r for r in rows if r['points_mean'] is not None]
    played = [r for r in projected if r['actual_status'] == 'played' and r['actual_minutes'] > 0]
    dnp = [r for r in projected if r not in played]
    buckets = {name: _metrics([r for r in played if lo <= r['expected_minutes'] < hi]) for name, lo, hi in BUCKETS}
    team_rows = _records(teams, TEAM_FIELDS)
    team_rows = [t for t in team_rows if t['projected_players'] > 0 and t['actual_points_regulation'] is not None]
    team_errors = [t['projected_points'] - t['actual_points_regulation'] for t in team_rows]
    by_game = defaultdict(dict)
    for t in team_rows:
        by_game[t['game_id']]['home' if t['home'] else 'away'] = t
    games = [g for g in by_game.values() if 'home' in g and 'away' in g]
    margins = [(g['home']['projected_points'] - g['away']['projected_points'],
                g['home']['actual_points'] - g['away']['actual_points']) for g in games]
    return dict(
        players=dict(all=_metrics(played), by_expected_minutes=buckets,
                     projected_players=len(projected), played=len(played), participation=_participation_metrics(projected),
                     projected_dnp=dict(count=len(dnp), share=len(dnp) / len(projected) if projected else None,
                                        mean_expected_minutes=fmean(r['expected_minutes'] for r in dnp) if dnp else None,
                                        mean_points_mean=fmean(r['points_mean'] for r in dnp) if dnp else None),
                     unsupported_rows=len(rows) - len(projected)),
        teams=dict(n=len(team_rows), points_mae=fmean(abs(e) for e in team_errors) if team_errors else None,
                   points_bias=fmean(team_errors) if team_errors else None,
                   covered_share=fmean(t['covered_actual_points'] / t['actual_points'] for t in team_rows if t['actual_points']) if team_rows else None),
        games=dict(n=len(games), margin_mae=fmean(abs(p - a) for p, a in margins) if margins else None,
                   margin_bias_home=fmean(p - a for p, a in margins) if margins else None,
                   winner_accuracy=fmean((p > 0) == (a > 0) for p, a in margins if a != 0) if margins else None,
                   home_pick_share=fmean(p > 0 for p, a in margins) if margins else None,
                   home_win_share=fmean(a > 0 for p, a in margins) if margins else None,
                   probabilities=_margin_probability_metrics(_records(game_rows, GAME_FIELDS))))


def _cluster_variance(values, labels):
    """Variance of the mean with residuals summed within each label (one-way cluster-robust), or None."""
    mean, sums = fmean(values), defaultdict(float)
    for value, label in zip(values, labels):
        sums[label] += value - mean
    groups = len(sums)
    if groups < 2:
        return None
    return groups / (groups - 1) * sum(s * s for s in sums.values()) / len(values) ** 2


def _mean_se(values, clusters):
    """Standard error of the mean and its design effect (variance ratio to independence).

    Player-games in one game share overtime, blowouts and the 240-minute budget, a
    player's role change follows him all season, and a date shares its news, so
    errors are not independent. ``clusters`` gives each value a (date, unit) pair;
    the variance is two-way clustered on both (Cameron, Gelbach and Miller), or
    one-way for a single label, and is never taken below the independent variance.
    """
    n = len(values)
    independent = pstdev(values) ** 2 / n
    if not clusters or independent == 0:
        return math.sqrt(independent), 1.0
    ways = [_cluster_variance(values, [c[i] for c in clusters]) for i in range(len(clusters[0]))]
    ways = [v for v in ways if v is not None]
    if not ways:
        return math.sqrt(independent), 1.0
    combined = max(sum(ways) - (len(ways) - 1) * independent, *ways, independent)
    return math.sqrt(combined), combined / independent


def _paired(current, reference, cluster=None):
    """Mean paired difference (current minus reference) with its clustered standard error and verdict."""
    keys = sorted(set(current) & set(reference))
    if len(keys) < 2:
        return dict(n=len(keys), mean=None, se=None, verdict='insufficient')
    d = [current[k] - reference[k] for k in keys]
    mean = fmean(d)
    se, effect = _mean_se(d, cluster and [cluster(k) for k in keys])
    verdict = 'improved' if mean + 2 * se < 0 else 'worse' if mean - 2 * se > 0 else 'within_noise'
    return dict(n=len(d), mean=mean, se=se, se_independent=pstdev(d) / math.sqrt(len(d)), design_effect=effect,
                verdict=verdict, rule='improved/worse when the mean paired difference exceeds two standard errors, '
                                      'clustered by date and player (team for team rows) and never below independence')


def _bias(current, reference, cluster=None):
    """Signed-error means: a clear bias that shrinks is an improvement; one that appears or grows is worse."""
    keys = sorted(set(current) & set(reference))
    if len(keys) < 2:
        return dict(n=len(keys), current=None, reference=None, se=None, verdict='insufficient')
    cur, ref = [current[k] for k in keys], [reference[k] for k in keys]
    labels = cluster and [cluster(k) for k in keys]
    new, old, se = fmean(cur), fmean(ref), max(_mean_se(cur, labels)[0], _mean_se(ref, labels)[0])
    if abs(old) > 2 * se and abs(old) - abs(new) > se:
        verdict = 'improved'
    elif abs(new) > 2 * se and abs(new) - abs(old) > se:
        verdict = 'worse'
    else:
        verdict = 'within_noise'
    return dict(n=len(keys), current=new, reference=old, se=se, verdict=verdict,
                rule='a bias more than two standard errors from zero that shrinks by at least one standard error is '
                     'improved; one that appears or grows by at least one standard error is worse')


def compare(current, reference, dates=None):
    """Pair the same player-games, teams and games: absolute errors (lower is better) and signed biases.

    ``dates`` maps game IDs to their Eastern date for clustering; a game without one
    is its own cluster.
    """
    def errors(rows):
        points, minutes, point_bias, participation = {}, {}, {}, {}
        for r in _records(rows['players'], rows.get('player_fields', PLAYER_FIELDS)):
            if r['points_mean'] is None:
                continue
            key = r['game_id'] + ':' + r['player_id']
            played = r['actual_status'] == 'played' and r['actual_minutes'] > 0
            # A reference without probabilities assumed everyone plays.
            probability = 1.0 if r.get('participation_probability') is None else r['participation_probability']
            participation[key] = (probability - played) ** 2
            if not played:
                continue
            points[key] = abs(r['points_mean'] - r['actual_points'])
            point_bias[key] = r['points_mean'] - r['actual_points']
            minutes[key] = abs(r['expected_minutes'] - r['actual_minutes'])
        teams, team_bias, home, away = {}, {}, {}, {}
        for t in _records(rows['teams'], TEAM_FIELDS):
            if t['projected_players'] > 0 and t['actual_points_regulation'] is not None:
                teams[t['game_id'] + ':' + t['team_id']] = abs(t['projected_points'] - t['actual_points_regulation'])
                team_bias[t['game_id'] + ':' + t['team_id']] = t['projected_points'] - t['actual_points_regulation']
                (home if t['home'] else away)[t['game_id']] = t
        margin_bias = {gid: (home[gid]['projected_points'] - away[gid]['projected_points'])
                       - (home[gid]['actual_points'] - away[gid]['actual_points']) for gid in home if gid in away}
        margins = {gid: abs(v) for gid, v in margin_bias.items()}
        winner = {}
        for r in _records(rows.get('games', []), GAME_FIELDS):
            if r['prior_sd']:
                winner[r['game_id']] = (r['home_win_probability'] - (r['actual_margin'] > 0)) ** 2
        if not winner and prior_sd:
            # A reference saved without margin rows is scored with the current run's prior scale.
            for gid in margin_bias:
                if prior_sd.get(gid):
                    location = home[gid]['projected_points'] - away[gid]['projected_points']
                    winner[gid] = (_home_win_probability(location, prior_sd[gid]) - (home[gid]['actual_points'] > away[gid]['actual_points'])) ** 2
        return dict(points=points, minutes=minutes, team_points=teams, margin=margins, participation=participation,
                    winner=winner, points_bias=point_bias, team_points_bias=team_bias, margin_bias=margin_bias)
    prior_sd = {r['game_id']: r['prior_sd'] for r in _records(current.get('games', []), GAME_FIELDS)}
    cur, ref = errors(current), errors(reference)
    dates = dates or {}
    def by_date_and_unit(key):
        gid, _, unit = key.partition(':')
        return dates.get(gid, gid), unit
    def by_date(key):
        return (dates.get(key, key),)
    cluster = dict(points=by_date_and_unit, minutes=by_date_and_unit, participation=by_date_and_unit,
                   points_bias=by_date_and_unit, team_points=by_date_and_unit, team_points_bias=by_date_and_unit,
                   margin=by_date, winner=by_date, margin_bias=by_date)
    result = {name: _paired(cur[name], ref[name], cluster[name]) for name in ('points', 'minutes', 'team_points', 'margin', 'participation', 'winner')}
    result.update({name: _bias(cur[name], ref[name], cluster[name]) for name in ('points_bias', 'team_points_bias', 'margin_bias')})
    verdicts = {v['verdict'] for v in result.values()}
    result['verdict'] = 'worse' if 'worse' in verdicts else 'improved' if 'improved' in verdicts else 'within_noise'
    return result


def _point_differential_margins(records, game_ids, min_games=10):
    """Walk-forward naive margins: home edge plus the season-to-date average margin difference.

    Only games on earlier Eastern dates count; the home edge is the average home
    margin of those games. A team with fewer than ``min_games`` earlier games has no
    rating, so its games are left out.
    """
    by_day = defaultdict(list)
    for r in records:
        stats = r.get('team_stats') or {}
        home, away = r['home_team_id'], r['away_team_id']
        if home in stats and away in stats:
            by_day[(r['season'], _day(r['game_time']))].append((r['game_id'], home, away,
                                                               stats[home]['pts'] - stats[away]['pts']))
    margins, totals, home_margins = {}, defaultdict(lambda: [0., 0]), defaultdict(list)
    for season, day in sorted(by_day):
        games = by_day[(season, day)]
        for gid, home, away, _ in games:
            h, a = totals[(season, home)], totals[(season, away)]
            if gid in game_ids and h[1] >= min_games and a[1] >= min_games and home_margins[season]:
                margins[gid] = fmean(home_margins[season]) + h[0] / h[1] - a[0] / a[1]
        for gid, home, away, margin in games:
            totals[(season, home)][0] += margin; totals[(season, home)][1] += 1
            totals[(season, away)][0] -= margin; totals[(season, away)][1] += 1
            home_margins[season].append(margin)
    return margins


def _no_vig_home(line):
    home, away = line.get('home_moneyline'), line.get('away_moneyline')
    if not home or not away:
        return None
    implied = [100 / (o + 100) if o > 0 else -o / (-o + 100) for o in (home, away)]
    return implied[0] / sum(implied)


def market_metrics(game_rows, team_rows, records, lines):
    """The closing line and a naive rating beside the model, on the same replayed games.

    The encompassing slope regresses the market's miss (actual minus closing margin)
    on the model's disagreement with the close (Fair and Shiller): a slope clearly
    above zero means the model knows something the close did not.
    """
    games = {r['game_id']: r for r in _records(game_rows, GAME_FIELDS)}
    lined = {gid: lines[gid] for gid in games if gid in lines and lines[gid].get('home_spread') is not None}
    if len(lined) < 2:
        return dict(n=len(lined))
    naive = _point_differential_margins(records, set(lined))
    rows = []
    for gid, line in lined.items():
        g = games[gid]
        rows.append(dict(model=g['projected_margin'], market=-line['home_spread'], naive=naive.get(gid),
                         actual=g['actual_margin'], sd=g['prior_sd'], p_model=g['home_win_probability'],
                         p_market=_no_vig_home(line)))
    won = lambda r: float(r['actual'] > 0)
    def summary(subset, key, probability):
        scored = [r for r in subset if probability(r) is not None]
        return dict(margin_mae=fmean(abs(r[key] - r['actual']) for r in subset),
                    winner_accuracy=fmean(float((r[key] > 0) == (r['actual'] > 0)) for r in subset),
                    winner_brier=fmean((probability(r) - won(r)) ** 2 for r in scored) if scored else None)
    naive_rows = [r for r in rows if r['naive'] is not None]
    result = dict(n=len(rows), source='ESPN core odds closing line (market_history)',
                  model=summary(rows, 'model', lambda r: r['p_model'] if r['sd'] else None),
                  market=summary(rows, 'market', lambda r: r['p_market']))
    if naive_rows:
        result['naive_point_differential'] = dict(
            n=len(naive_rows), model_margin_mae=fmean(abs(r['model'] - r['actual']) for r in naive_rows),
            **summary(naive_rows, 'naive', lambda r: _normal_cdf(r['naive'] / r['sd']) if r['sd'] else None))
    x = [r['model'] - r['market'] for r in rows]
    y = [r['actual'] - r['market'] for r in rows]
    mx, my = fmean(x), fmean(y)
    sxx = sum((v - mx) ** 2 for v in x)
    if sxx > 0:
        slope = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx
        residuals = [b - my - slope * (a - mx) for a, b in zip(x, y)]
        se = math.sqrt(sum(e * e for e in residuals) / (len(rows) - 2) / sxx)
        result['encompassing'] = dict(slope=slope, se=se, intercept=my - slope * mx,
                                      rule='slope of (actual - close) on (model - close); above zero by two standard '
                                           'errors means the model adds information beyond the closing line')
    teams = [t for t in _records(team_rows, TEAM_FIELDS)
             if t['game_id'] in lined and lines[t['game_id']].get('total') is not None
             and t['actual_points_regulation'] is not None and t['projected_players'] > 0]
    if teams:
        def implied(t):
            line = lines[t['game_id']]
            return (line['total'] - line['home_spread']) / 2 if t['home'] else (line['total'] + line['home_spread']) / 2
        result['team_points'] = dict(
            n=len(teams), target='regulation-equivalent points, as the model is scored (the close prices overtime too)',
            model_mae=fmean(abs(t['projected_points'] - t['actual_points_regulation']) for t in teams),
            market_mae=fmean(abs(implied(t) - t['actual_points_regulation']) for t in teams),
            market_bias=fmean(implied(t) - t['actual_points_regulation'] for t in teams))
    return result


def run_backtest(history, records, output, *, reference=None, warmup_fraction=.3, limit=None, as_of=None,
                 sources=None, progress=None, market=None):
    """Write ``scoreboard.json`` and ``rows.json.gz`` under ``output``; return the scoreboard."""
    as_of = as_of or datetime.now(timezone.utc).isoformat()
    cutoff = parse_time(as_of)
    if any(parse_time(r['known_at']) > cutoff for r in history) or any(parse_time(r['known_at']) > cutoff for r in records):
        raise ValueError('backtest cutoff cannot precede future-known sources')
    out = Path(output)
    if out.exists() and any(out.iterdir()):
        raise ValueError('output directory exists and is not empty')
    replayed = replay(history, records, as_of, warmup_fraction=warmup_fraction, limit=limit, progress=progress)
    rows = dict(version=VERSION, player_fields=list(PLAYER_FIELDS), team_fields=list(TEAM_FIELDS), game_fields=list(GAME_FIELDS),
                players=replayed['players'], teams=replayed['teams'], games=replayed['games'])
    scoreboard = dict(
        schema_version=1, version=VERSION, created_at=as_of, model_version=MODEL_VERSION,
        code_sha256=package_digest(Path(__file__).parent),
        sources=sources or {}, parameters=dict(warmup_fraction=warmup_fraction, limit=limit,
                                                 interval_family=INTERVAL_FAMILY, heuristic_z80=HEURISTIC_Z80),
        games=dict(total=replayed['total_games'], evaluated=replayed['evaluated_games'], skipped=replayed['skipped']),
        metrics=summarize(replayed['players'], replayed['teams'], replayed['games']), limitations=LIMITATIONS)
    if market is not None:
        scoreboard['metrics']['market'] = market_metrics(replayed['games'], replayed['teams'], records, market)
    if reference is not None:
        ref_rows = read_json(Path(reference) / 'rows.json.gz')
        ref_board = read_json(Path(reference) / 'scoreboard.json')
        scoreboard['comparison'] = dict(reference=str(reference), reference_created_at=ref_board.get('created_at'),
                                        reference_code_sha256=ref_board.get('code_sha256'),
                                        reference_model_version=ref_board.get('model_version'),
                                        **compare(rows, ref_rows, {r['game_id']: _day(r['game_time']).isoformat()
                                                                   for r in records}))
    out.mkdir(parents=True, exist_ok=True)
    (out / 'rows.json.gz').write_bytes(gzip.compress(canonical(rows), mtime=0))
    atomic_json(out / 'scoreboard.json', scoreboard)
    return scoreboard


def sha256_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def format_scoreboard(board):
    """A short readable table for the terminal."""
    m = board['metrics'];lines = []
    lines.append(f"games evaluated {board['games']['evaluated']} of {board['games']['total']}  model {board['model_version']}  code {board['code_sha256'][:12]}")
    lines.append(f"{'players (played)':18s} {'n':>6s} {'pts MAE':>8s} {'bias':>6s} {'min MAE':>8s} {'cov80':>6s} {'width':>6s}")
    for name, item in [('all', m['players']['all'])] + list(m['players']['by_expected_minutes'].items()):
        if item.get('n'):
            lines.append(f"{name:18s} {item['n']:6d} {item['points_mae']:8.2f} {item['points_bias']:+6.2f} {item['minutes_mae']:8.2f} {item['coverage80']:6.1%} {item['mean_width80']:6.1f}")
    d = m['players']['projected_dnp']
    lines.append(f"projected but did not play: {d['count']} ({d['share']:.1%} of projected), averaging {d['mean_expected_minutes']:.1f} min / {d['mean_points_mean']:.1f} pts" if d['count'] else 'projected but did not play: 0')
    pm = m['players'].get('participation') or {}
    if pm.get('n'):
        lines.append(f"participation: Brier {pm['brier']:.3f} (constant rate {pm['constant_rate_brier']:.3f}, assume all play {pm['assume_all_play_brier']:.3f}); mean P(plays) {pm['mean_probability']:.2f}, played {pm['played_share']:.2f}")
    t, g = m['teams'], m['games']
    if t['n']:
        lines.append(f"teams n={t['n']} points MAE {t['points_mae']:.2f} bias {t['points_bias']:+.2f} (projected players cover {t['covered_share']:.0%} of team points)")
    if g['n']:
        lines.append(f"games n={g['n']} margin MAE {g['margin_mae']:.2f} home bias {g['margin_bias_home']:+.2f} winner accuracy {g['winner_accuracy']:.1%} (home picked {g['home_pick_share']:.0%}, home won {g['home_win_share']:.0%})")
    pr = g.get('probabilities') or {}
    if pr.get('n', 0) > 1:
        f, h = pr['calibration_fit'], pr.get('holdout')
        lines.append(f"margin probabilities n={pr['n']} winner Brier {pr['winner_brier']:.4f} log loss {pr['log_loss']:.4f}; mean P(home) {pr['mean_home_win_probability']:.3f} vs home won {pr['home_win_share']:.3f}; prior sd {pr['mean_prior_sd']:.1f} vs residual sd {pr['residual_sd']:.1f}")
        lines.append(f"  best correction location {f['location_bias']:+.2f} scale {f['scale_factor']:.2f}: log loss {f['log_loss_prior']:.4f} -> {f['log_loss']:.4f}, Brier {pr['winner_brier']:.4f} -> {f['winner_brier_fitted']:.4f}"
                     + (f"; holdout ({h['scored']} games) log loss {h['log_loss_prior']:.4f} -> {h['log_loss_fitted']:.4f}" if h else ''))
        lines.append('  reliability ' + ' | '.join(f"{b['bin']:.1f}: n={b['n']} P {b['mean_probability']:.2f} won {b['home_win_share']:.2f}" for b in pr['reliability']))
    mk = m.get('market') or {}
    if mk.get('n', 0) > 1:
        def row(name, item):
            brier = f"{item['winner_brier']:.4f}" if item.get('winner_brier') is not None else '  -   '
            return f"  {name:24s} margin MAE {item['margin_mae']:6.2f}  winners {item['winner_accuracy']:.1%}  Brier {brier}"
        lines.append(f"market (same {mk['n']} games, closing line):")
        lines.append(row('model', mk['model']));lines.append(row('closing line', mk['market']))
        nv = mk.get('naive_point_differential')
        if nv:
            lines.append(row(f"point differential (n={nv['n']})", nv) + f"  (model on those {nv['model_margin_mae']:.2f})")
        if mk.get('encompassing'):
            e = mk['encompassing']
            lines.append(f"  encompassing slope {e['slope']:+.3f} ± {2 * e['se']:.3f} (above zero: the model adds to the close)")
        if mk.get('team_points'):
            tp = mk['team_points']
            lines.append(f"  team points (n={tp['n']}): model MAE {tp['model_mae']:.2f}, implied by the close {tp['market_mae']:.2f} (bias {tp['market_bias']:+.2f})")
    c = board.get('comparison')
    if c:
        lines.append(f"vs reference ({c['reference_model_version']} {str(c['reference_code_sha256'])[:12]}): {c['verdict'].upper()}")
        for name in ('points', 'minutes', 'team_points', 'margin', 'participation', 'winner'):
            item = c.get(name, {})
            if item.get('mean') is not None:
                what = 'Brier change' if name in ('participation', 'winner') else 'mean abs-error change'
                lines.append(f"  {name:18s} n={item['n']:6d} {what} {item['mean']:+.4f} ± {2 * item['se']:.4f}"
                             f" (design effect {item.get('design_effect', 1.0):.2f})  {item['verdict']}")
        for name in ('points_bias', 'team_points_bias', 'margin_bias'):
            item = c.get(name, {})
            if item.get('current') is not None:
                lines.append(f"  {name:18s} n={item['n']:6d} bias {item['reference']:+.2f} -> {item['current']:+.2f} (se {item['se']:.2f})  {item['verdict']}")
    return '\n'.join(lines)
