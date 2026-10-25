"""Uncalibrated, source-bound distributions for final integer game margins.

The scale is a pooled *outcome* prior, not a forecast-residual calibration:
paired historical home/away scores preserve game covariance. No player SDs,
old-model interval calibration, market prices, or joint-leg estimates are used.
The regulation player-sum difference supplies a location predictor for a
discrete final-score approximation; overtime is included in source outcomes.
"""
from collections import Counter
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from statistics import covariance, fmean, variance

from .totals import game_totals
from .validation import parse_time


METHOD = 'paired_final_score_outcome_dispersion_team_rating_blend_discrete_normal_v2'
SETTLEMENT = 'final_score_including_overtime'
EVIDENCE_KIND = 'historical_paired_final_score_outcomes'
MINIMUM_GAMES = 100
MAXIMUM_GAMES = 1230
SUPPORT = 120
TAIL_FLOOR = 1e-6
PARAMETERS = dict(minimum_games=MINIMUM_GAMES, maximum_games=MAXIMUM_GAMES,
                  support=SUPPORT, tail_floor_per_side=TAIL_FLOOR,
                  minimum_scale=1., required_interior_standard_deviations=3.,
                  player_weight=.4, rating_weight=.8, rating_prior_games=10, rating_prior_shrink=.6)
LIMITATION = ('Uncalibrated estimate. Historical paired final-score dispersion '
             'is an outcome prior, not this model\'s prediction-error calibration. '
             'The location uses regulation player means; final-score and overtime '
             'behavior are approximated, and future probability accuracy is unverified.')
CALIBRATED_LIMITATION = ('Location and scale were fitted on genuinely saved pregame forecasts '
    'and passed a later-game validation gate. Future accuracy is not guaranteed; overtime '
    'and the regulation player-sum location remain approximations.')


# The margin location blends the player-sum difference with a team rating:
# each team's season-to-date average final margin, padded with pseudo-games of
# last season's average regressed toward zero, plus the window's home edge.
# A summed player projection carries no defence and misses late-season
# lopsidedness; public systems blend player ratings with team results
# (FiveThirtyEight weighted Elo about 35%). Round values, not fitted here: the
# research measured current-season margin earning about 8-14% weight after 0-4
# games and 80% after 20-39, which ten pseudo-games of a 0.6-regressed prior
# reproduces. The weights need not sum to one: fitted on either replayed season
# and scored on the other, actual margins took about 0.4 of the player sum and
# 0.8 of the rating (0.36/0.79 and 0.48/0.84); half and half compressed them.
PLAYER_WEIGHT = PARAMETERS['player_weight']
RATING_WEIGHT = PARAMETERS['rating_weight']
RATING_PRIOR_GAMES = PARAMETERS['rating_prior_games']
RATING_PRIOR_SHRINK = PARAMETERS['rating_prior_shrink']


def _season(time):
    moment = parse_time(time)
    return moment.year if moment.month >= 8 else moment.year - 1


def team_ratings(samples, as_of):
    """Per-team rating in points per game from final samples known before ``as_of``.

    ``samples`` carry home/away team IDs and points and a game time; the season
    of ``as_of`` is current, the one before supplies the prior.
    """
    current = _season(as_of)
    totals = {}
    for s in samples:
        season = _season(s['game_time'])
        if season not in (current, current - 1):
            continue
        margin = s['home_points'] - s['away_points']
        for team, signed in ((s['home_team_id'], margin), (s['away_team_id'], -margin)):
            t = totals.setdefault(team, [0., 0, 0., 0])
            if season == current:
                t[0] += signed; t[1] += 1
            else:
                t[2] += signed; t[3] += 1
    ratings = {}
    for team, (now_sum, now_n, prior_sum, prior_n) in sorted(totals.items()):
        prior = RATING_PRIOR_SHRINK * prior_sum / prior_n if prior_n else 0.
        ratings[team] = dict(rating=(now_sum + RATING_PRIOR_GAMES * prior) / (now_n + RATING_PRIOR_GAMES),
                             games=now_n, prior=prior)
    return ratings


def blended_location(player_sum, home_edge, ratings, home, away):
    """The margin location and its team-rating part, or the player sum alone without both ratings."""
    if home not in ratings or away not in ratings or not _finite(home_edge):
        return player_sum, None
    rating = home_edge + ratings[home]['rating'] - ratings[away]['rating']
    return PLAYER_WEIGHT * player_sum + RATING_WEIGHT * rating, rating


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _finite(value):
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except (ValueError, OverflowError, TypeError):
        return False


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _iso(value):
    return value.isoformat().replace('+00:00', 'Z')


def _final_sample(row):
    """Validate only final-score identity/duration; never infer missing scores."""
    if not isinstance(row, Mapping):
        raise ValueError('Malformed team game')
    gid, home, away = (row[k] for k in ('game_id', 'home_team_id', 'away_team_id'))
    if not all(_text(v) for v in (gid, home, away)) or home == away:
        raise ValueError('Invalid game/team identity')
    start, known = parse_time(row['game_time']), parse_time(row['known_at'])
    if known <= start:
        raise ValueError('Final evidence must be observed after tipoff')
    if set(row['team_stats']) != {home, away}:
        raise ValueError('Final scores must identify both opposing teams')
    points = [row['team_stats'][t]['pts'] for t in (home, away)]
    if any(not _finite(p) or p < 0 or int(p) != p for p in points) or points[0] == points[1]:
        raise ValueError('Final scores must be nonnegative integer counts without a tie')
    if 'team_minutes' in row:
        if set(row['team_minutes']) != {home, away}:
            raise ValueError('Team duration identity differs')
        minutes = [row['team_minutes'][t] for t in (home, away)]
        if any(not _finite(m) or m < 239.85 for m in minutes):
            raise ValueError('Missing complete game minutes')
        periods = [round((m - 240.) / 25.) for m in minutes]
        if any(n < 0 or abs(m - (240. + 25. * n)) > .15 for m, n in zip(minutes, periods)) or periods[0] != periods[1]:
            raise ValueError('Incomplete or inconsistent overtime duration')
        overtime = periods[0]
    else:
        minutes = row['game_minutes']
        if not _finite(minutes) or minutes < 48 or (minutes - 48) % 5:
            raise ValueError('Complete duration is required')
        overtime = int((minutes - 48) / 5)
    return dict(game_id=gid, game_time=_iso(start), known_at=_iso(known),
                home_team_id=home, away_team_id=away, home_points=points[0],
                away_points=points[1], overtime=overtime, source_sha256=_sha(row))


def prepare_margin_inputs(team_games, as_of, model_key):
    """Return deterministic prior inputs using only unique, already-known finals.

    Callers supply verified team-game archives. Full source records are hashed,
    and the preparation layer can reproduce this result from those archives.
    Duplicate game IDs are all excluded; revision selection belongs upstream.
    """
    if not _text(model_key):
        raise ValueError('A nonempty model key is required')
    cutoff = parse_time(as_of)
    if not isinstance(team_games, (list, tuple)):
        raise ValueError('Team games must be a sequence of final records')
    identities = Counter(r.get('game_id') for r in team_games
                         if isinstance(r, Mapping) and _text(r.get('game_id')))
    accepted, exclusions = [], Counter()
    for row in team_games:
        try:
            sample = _final_sample(row)
            if identities[sample['game_id']] != 1:
                exclusions['duplicate_game'] += 1
            elif parse_time(sample['known_at']) > cutoff:
                exclusions['future_known'] += 1
            else:
                accepted.append(sample)
        except (KeyError, ValueError, TypeError, OverflowError):
            exclusions['invalid_final'] += 1
    accepted.sort(key=lambda r: (parse_time(r['game_time']), r['game_id']))
    if len(accepted) > MAXIMUM_GAMES:
        exclusions['outside_recent_game_window'] += len(accepted) - MAXIMUM_GAMES
        accepted = accepted[-MAXIMUM_GAMES:]
    sources = [{k: r[k] for k in ('game_id', 'game_time', 'known_at', 'home_team_id',
                                 'away_team_id', 'source_sha256')} for r in accepted]
    result = dict(schema_version=1, status='unavailable', reason='insufficient_historical_games',
        method=METHOD, settlement=SETTLEMENT, model_key=model_key, prepared_at=_iso(cutoff),
        training_cutoff=_iso(cutoff), calibration_status='uncalibrated', evidence_kind=EVIDENCE_KIND,
        sample_count=len(accepted), sources=sources, source_sha256=_sha(sources),
        first_game_time=accepted[0]['game_time'] if accepted else None,
        last_game_time=accepted[-1]['game_time'] if accepted else None,
        latest_known_at=max((r['known_at'] for r in accepted), key=parse_time) if accepted else None,
        overtime_game_count=sum(r['overtime'] > 0 for r in accepted),
        home_score_variance=None, away_score_variance=None, paired_score_covariance=None,
        margin_variance=None, historical_mean_margin=None, standard_deviation=None,
        team_ratings=team_ratings(accepted, as_of),
        parameters=dict(PARAMETERS), exclusions=dict(sorted(exclusions.items())), limitation=LIMITATION)
    if len(accepted) >= MINIMUM_GAMES:
        home = [r['home_points'] for r in accepted]
        away = [r['away_points'] for r in accepted]
        differences = [h - a for h, a in zip(home, away)]
        try:
            paired_variance = variance(differences)
            historical_mean = fmean(differences)
            # Retain the second moment about zero, rather than fitting a home
            # bias and then applying that historical outcome mean a second time.
            scale = math.sqrt(paired_variance + historical_mean ** 2)
            summary = dict(home_score_variance=variance(home), away_score_variance=variance(away),
                paired_score_covariance=covariance(home, away), margin_variance=paired_variance,
                historical_mean_margin=historical_mean, standard_deviation=scale)
            if not all(_finite(v) for v in summary.values()):
                raise ValueError('Nonfinite outcome dispersion')
            result.update(summary)
            if paired_variance <= 0 or scale < PARAMETERS['minimum_scale']:
                result['reason'] = 'insufficient_outcome_dispersion'
            else:
                result.update(status='ready', reason=None)
        except (ValueError, OverflowError):
            result['reason'] = 'invalid_outcome_dispersion'
    result['inputs_sha256'] = _sha(result)
    return result


def _input_error(inputs, model_key, cutoff, game):
    if not isinstance(inputs, Mapping):
        return 'missing_margin_inputs'
    try:
        if inputs['model_key'] != model_key:
            return 'margin_model_mismatch'
        if parse_time(inputs['prepared_at']) > cutoff or parse_time(inputs['training_cutoff']) > cutoff:
            return 'future_margin_inputs'
        if inputs['inputs_sha256'] != _sha({k: v for k, v in inputs.items() if k != 'inputs_sha256'}):
            return 'margin_inputs_hash_mismatch'
        if (type(inputs['schema_version']) is not int or inputs['schema_version'] != 1
                or inputs['method'] != METHOD or inputs['settlement'] != SETTLEMENT
                or inputs['parameters'] != PARAMETERS or inputs['calibration_status'] != 'uncalibrated'
                or inputs['evidence_kind'] != EVIDENCE_KIND):
            return 'unsupported_margin_inputs'
        sources = inputs['sources']
        ids = [r['game_id'] for r in sources]
        if (type(inputs['sample_count']) is not int or inputs['sample_count'] != len(ids)
                or len(set(ids)) != len(ids) or _sha(sources) != inputs['source_sha256']):
            return 'invalid_margin_sources'
        for r in sources:
            if not (_text(r['game_id']) and _text(r['home_team_id']) and _text(r['away_team_id'])) or r['home_team_id'] == r['away_team_id']:
                return 'invalid_margin_sources'
            if not parse_time(r['game_time']) < parse_time(r['known_at']) <= parse_time(inputs['training_cutoff']):
                return 'future_margin_sources'
        if game['game_id'] in ids:
            return 'target_game_in_margin_sources'
        if inputs['status'] != 'ready':
            return inputs.get('reason') or 'margin_inputs_unavailable'
        if (not MINIMUM_GAMES <= len(sources) <= MAXIMUM_GAMES
                or not _finite(inputs['standard_deviation'])
                or inputs['standard_deviation'] < PARAMETERS['minimum_scale']
                or not _finite(inputs['margin_variance']) or inputs['margin_variance'] <= 0):
            return 'invalid_margin_dispersion'
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'invalid_margin_inputs'
    return None


def _cdf(z):
    return .5 * math.erfc(-z / math.sqrt(2.))


def _sf(z):
    return .5 * math.erfc(z / math.sqrt(2.))


def _pmf(location, sd):
    rows = []
    for margin in range(-SUPPORT, SUPPORT + 1):
        if margin == 0:
            continue
        lo, hi = (margin - .5 - location) / sd, (margin + .5 - location) / sd
        if margin == -SUPPORT:
            probability = _cdf(hi)
        elif margin == SUPPORT:
            probability = _sf(lo)
        elif lo >= 0:
            probability = _sf(lo) - _sf(hi)
        else:
            probability = _cdf(hi) - _cdf(lo)
        rows.append(dict(margin=margin, probability=max(0., probability)))
    mass = math.fsum(r['probability'] for r in rows)
    if not _finite(mass) or mass <= 0:
        raise ValueError('Degenerate discrete margin mass')
    for r in rows:
        r['probability'] = (1 - 2 * TAIL_FLOOR) * r['probability'] / mass
        if abs(r['margin']) == SUPPORT:
            r['probability'] += TAIL_FLOOR
    # Correct only floating arithmetic, without imposing a per-query clip.
    max(rows, key=lambda r: r['probability'])['probability'] += 1. - math.fsum(r['probability'] for r in rows)
    return rows


def build_margin_forecasts(snapshot, result):
    """Derive immutable per-game distributions directly from saved player rows.

    A supplied aggregate cannot turn partial predictions into a supported game.
    Preparation must separately be reproduced against archives by the existing
    enhancement validator; the embedded digest also detects accidental changes.
    """
    enhancement = snapshot.get('model_enhancements', {})
    inputs = enhancement.get('margin_inputs')
    model_key = result.get('model_key')
    output = []
    for game in snapshot['games']:
        if game.get('season_type') != 'Regular Season':
            continue
        value = dict(schema_version=1, game_id=game.get('game_id'), home_team_id=game.get('home_team_id'),
            away_team_id=game.get('away_team_id'), model_key=model_key, status='unavailable', reason=None,
            settlement=SETTLEMENT, method=METHOD, calibration_status='uncalibrated',
            evidence_kind=EVIDENCE_KIND, sample_count=0, mean_margin=None, standard_deviation=None,
            pmf=[], supported_line_range={'minimum': -SUPPORT, 'maximum': SUPPORT, 'exclusive': True},
            joint_probability=None, joint_probability_reason='joint_model_not_supported', limitation=LIMITATION)
        output.append(value)
        try:
            cutoff = parse_time(snapshot['as_of'])
            if not all(_text(game[k]) for k in ('game_id', 'home_team_id', 'away_team_id')) or game['home_team_id'] == game['away_team_id']:
                raise ValueError('Invalid game identity')
            if parse_time(result['as_of']) != cutoff:
                value['reason'] = 'forecast_cutoff_mismatch'; continue
            if parse_time(game['start_time']) <= cutoff:
                value['reason'] = 'not_pregame'; continue
            if not _text(model_key) or enhancement.get('model_key') != model_key:
                value['reason'] = 'margin_model_mismatch'; continue
            error = _input_error(inputs, model_key, cutoff, game)
            if error:
                value['reason'] = error; continue
            totals = game_totals(game, result['predictions'], lineups=snapshot.get('preflight', {}).get('lineups'))
            value['coverage_status'] = totals['status']
            if 'game_totals' in result:
                saved = [r for r in result['game_totals'] if r.get('game_id') == game['game_id']]
                if len(saved) != 1 or _canonical(saved[0]) != _canonical(totals):
                    value['reason'] = 'totals_disagree_with_predictions'; continue
            if totals['status'] != 'complete':
                value['reason'] = 'incomplete_team_totals'; continue
            means = {t['team_id']: t['points_mean'] for t in totals['teams']}
            player_sum = means[game['home_team_id']] - means[game['away_team_id']]
            raw_location, rating_margin = blended_location(player_sum, inputs['historical_mean_margin'],
                inputs.get('team_ratings') or {}, game['home_team_id'], game['away_team_id'])
            value.update(player_sum_margin=player_sum, team_rating_margin=rating_margin,
                         rating_weight=RATING_WEIGHT if rating_margin is not None else 0.)
            raw_sd = inputs['standard_deviation']
            if not _finite(raw_location) or abs(raw_location)+PARAMETERS['required_interior_standard_deviations']*raw_sd>=SUPPORT:
                value['reason']='margin_location_or_scale_outside_support';continue
            location,sd=raw_location,raw_sd
            calibration=enhancement.get('margin_calibration')
            if calibration is not None:
                from .prospective_margin_calibration import validate_margin_calibration
                if not validate_margin_calibration(calibration,model_key,snapshot['as_of']):
                    value['reason']='invalid_margin_calibration';continue
                raw_pmf=_pmf(raw_location,raw_sd)
                location+=calibration['location_bias'];sd*=calibration['scale_factor']
                value.update(calibration_status='prospective_validated',limitation=CALIBRATED_LIMITATION,
                    calibration_audit={k:calibration[k] for k in ('method','training_cutoff','training_games',
                        'training_days','validation_games','evidence_sha256','validation_metrics')},
                    uncalibrated_mean_margin=raw_location,uncalibrated_standard_deviation=raw_sd,
                    uncalibrated_pmf=raw_pmf,uncalibrated_pmf_sha256=_sha(raw_pmf))
            if not _finite(location) or abs(location) + PARAMETERS['required_interior_standard_deviations'] * sd >= SUPPORT:
                value['reason'] = 'margin_location_or_scale_outside_support'; continue
            pmf = _pmf(location, sd)
            distribution_mean = math.fsum(r['margin'] * r['probability'] for r in pmf)
            distribution_sd = math.sqrt(math.fsum((r['margin'] - distribution_mean) ** 2 * r['probability'] for r in pmf))
            value.update(status='estimated', reason=None, mean_margin=location, standard_deviation=sd,
                pmf=pmf, pmf_sha256=_sha(pmf), sample_count=inputs['sample_count'],
                distribution_mean_margin=distribution_mean, distribution_standard_deviation=distribution_sd,
                mean_basis=(('0.4 of the unrounded regulation player-sum difference plus 0.8 of the home edge plus the '
                    'team-rating difference' if rating_margin is not None else
                    'Unrounded regulation player-sum difference (a team rating was unavailable)')
                    + (', plus validated prospective location correction' if calibration else '')),
                scale_basis=('Historical paired-final prior times validated prospective residual scale correction'
                    if calibration else 'sqrt(sample variance of paired final margins + squared historical mean margin); not residual calibration'),
                tail_policy='Outside-support mass is collected at endpoints; endpoint and farther lines are unsupported',
                tail_floor_per_side=TAIL_FLOOR, tie_policy='Zero margin excluded and remaining mass normalized',
                source_sha256=inputs['source_sha256'], inputs_sha256=inputs['inputs_sha256'],
                prepared_at=inputs['prepared_at'], forecast_as_of=snapshot['as_of'],
                training_cutoff=inputs['training_cutoff'], latest_source_known_at=inputs['latest_known_at'],
                first_source_game_time=inputs['first_game_time'], last_source_game_time=inputs['last_game_time'],
                paired_score_covariance=inputs['paired_score_covariance'], historical_margin_variance=inputs['margin_variance'],
                overtime_game_count=inputs['overtime_game_count'])
        except (KeyError, TypeError, ValueError, OverflowError):
            value['reason'] = 'invalid_margin_forecast_inputs'
    return output


def probability_above(distribution, team_id, line):
    """Evaluate a strict binary margin event. Equality belongs to NO, never push.

    Interior integer equality is exact for the frozen PMF. The support endpoints
    collect tails, so endpoint/outside queries are explicitly unavailable.
    """
    if isinstance(line, bool) or not isinstance(line, (str, int, float, Decimal)):
        raise ValueError('Margin line must be a finite decimal number')
    try:
        threshold = Decimal(str(line))
    except (InvalidOperation, ValueError):
        raise ValueError('Margin line must be a finite decimal number') from None
    if not threshold.is_finite():
        raise ValueError('Margin line must be finite')
    home, away = distribution.get('home_team_id'), distribution.get('away_team_id')
    if not _text(home) or not _text(away) or home == away or team_id not in (home, away):
        raise ValueError('Named team must be one of this game\'s distinct teams')
    value = dict(status='unavailable', reason=distribution.get('reason'), game_id=distribution.get('game_id'),
        team_id=team_id, line=str(threshold), comparison='strictly_greater_than', equality_result='NO',
        settlement=distribution.get('settlement'), calibration_status=distribution.get('calibration_status'),
        yes_probability=None, no_probability=None, equal_probability=None,
        joint_probability=None, joint_probability_reason='joint_model_not_supported')
    if distribution.get('status') == 'unavailable':
        return value
    if (distribution.get('status') != 'estimated' or distribution.get('settlement') != SETTLEMENT
            or distribution.get('calibration_status') not in ('uncalibrated','prospective_validated')):
        raise ValueError('Unsupported margin distribution')
    try:
        bounds = distribution['supported_line_range']
        low, high = bounds['minimum'], bounds['maximum']
        if not (_finite(low) and _finite(high) and low < 0 < high and bounds['exclusive'] is True):
            raise ValueError('Invalid margin support')
        pmf = distribution['pmf']
        if not isinstance(pmf, list) or not pmf:
            raise ValueError('Missing margin mass')
        seen = set()
        for row in pmf:
            k, p = row['margin'], row['probability']
            if type(k) is not int or k == 0 or k in seen or not low <= k <= high or not _finite(p) or not 0 <= p <= 1:
                raise ValueError('Invalid or duplicate margin mass')
            seen.add(k)
        if not math.isclose(math.fsum(r['probability'] for r in pmf), 1., rel_tol=0., abs_tol=1e-12):
            raise ValueError('Margin mass does not sum to one')
        if distribution.get('pmf_sha256') and distribution['pmf_sha256'] != _sha(pmf):
            raise ValueError('Margin mass hash mismatch')
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError('Malformed margin distribution') from exc
    if not Decimal(str(low)) < threshold < Decimal(str(high)):
        value['reason'] = 'line_outside_supported_range'
        return value
    sign = 1 if team_id == home else -1
    yes = math.fsum(r['probability'] for r in pmf if Decimal(sign * r['margin']) > threshold)
    equal = math.fsum(r['probability'] for r in pmf if Decimal(sign * r['margin']) == threshold)
    value.update(status='estimated', reason=None, yes_probability=min(1., max(0., yes)),
                 no_probability=1. - min(1., max(0., yes)), equal_probability=equal)
    return value
