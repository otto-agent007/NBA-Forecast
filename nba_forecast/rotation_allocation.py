"""Evidence-bounded vacancy allocation; no inferred absences or new players.

This module adds to supplied healthy projections without lowering any existing
role. The caller owns final team-budget normalization and scoring. Historical
90th percentiles are empirical capacity estimates, not claims about coaching
decisions. Unsupported minutes and attempts remain visible in the audit.
"""
from collections import defaultdict
from copy import deepcopy
from math import ceil

from .validation import parse_time


FIELDS = ('fga', 'fg3a', 'fta', 'tov')
VOLUME_FIELDS = ('fga', 'fta')
QUANTILE = .90
ROLE_WINDOW = 10
PRIOR_MIN_GAMES = 3
EPSILON = 1e-9
METHOD = 'observed_role_vacancy_allocation_v1'


def _season(cutoff):
    start = cutoff.year if cutoff.month >= 7 else cutoff.year - 1
    return f'{start}-{(start + 1) % 100:02d}'


def _quantile(values):
    ordered = sorted(values)
    return ordered[max(0, ceil(len(ordered) * QUANTILE) - 1)] if ordered else 0.


def _rate_quantile(rows, field):
    """Exposure weighting prevents a one-minute stint dominating rate caps."""
    values = sorted((r[field] / r['minutes'], r['minutes']) for r in rows)
    target = QUANTILE * sum(weight for _, weight in values)
    running = 0.
    for value, weight in values:
        running += weight
        if running >= target:
            return value
    return 0.


def _role_rows(rows, team, season):
    current = [r for r in rows if r['season'] == season]
    same_team = [r for r in current if r['team_id'] == team]
    positive = lambda rs: [r for r in rs if r['status'] == 'played' and r['minutes'] > 0]
    if same_team:
        played = positive(same_team)
        return played[-ROLE_WINDOW:], 'current_team' if played else 'no_positive_current_team_role'
    # A role observed on another team this season cannot establish the new role.
    if current:
        return [], 'new_team_role_unobserved'
    prior = positive(rows)
    same_team_prior = [r for r in prior if r['team_id'] == team]
    if len(same_team_prior) >= PRIOR_MIN_GAMES:
        return same_team_prior[-ROLE_WINDOW:], 'established_prior_current_team'
    # Opening-season trades may retain an established prior role as a transparent
    # fallback, until actual current-team observations replace it.
    if len(prior) >= PRIOR_MIN_GAMES:
        return prior[-ROLE_WINDOW:], 'established_prior_other_team'
    return [], 'insufficient_positive_role_evidence'


def _sources(rows):
    return [{key: r.get(key) for key in ('game_id', 'player_id', 'team_id', 'season',
             'source_url', 'game_time', 'known_at')} for r in rows]


def _sourced_availability(player, cutoff):
    if not str(player.get('status_source_url') or '').startswith('https://'):
        return False
    observed = player.get('status_observed_at')
    published = player.get('status_published_at', observed)
    try:
        return bool(observed and published and parse_time(observed) <= cutoff
                    and parse_time(published) <= cutoff)
    except (ValueError, TypeError, AttributeError):
        return False


def _donor_rates(profile, rows):
    evidence = profile.get('role_minutes_evidence') or {}
    rates = evidence.get('per_minute_rates') or {}
    exposure = sum(r['minutes'] for r in rows)
    result = {}
    for field in FIELDS:
        rate = rates.get(field, profile.get(f'{field}_per_minute'))
        if rate is None:
            rate = sum(r[field] for r in rows) / exposure if exposure else 0.
        result[field] = max(0., float(rate))
    result['fg3a'] = min(result['fg3a'], result['fga'])
    return result


def _recipient(player, item, rows, support):
    minutes, rates = item['minutes'], item['rates']
    observed_cap = _quantile([r['minutes'] for r in rows])
    manual_cap = min(48., max(0., float(item.get('minute_cap', 48.))))
    cap = min(manual_cap, max(minutes, observed_cap))
    rate_caps = {field: max(rates[field], _rate_quantile(rows, field)) for field in FIELDS}
    attempt_caps = {field: max(minutes * rates[field], _quantile([r[field] for r in rows]))
                    for field in FIELDS}
    headroom = max(0., cap - minutes) if rows and not item.get('locked_minutes') else 0.
    # Extra minutes themselves must stay within observed attempt capacities.
    # Baselines are retained even when a manual role exceeds the empirical cap.
    for field in FIELDS:
        if rates[field] > 0:
            headroom = min(headroom, max(0., attempt_caps[field] - minutes * rates[field]) / rates[field])
    return dict(player_id=player['player_id'], support=support,
                baseline_minutes=minutes, adjusted_minutes=minutes,
                baseline_rates=deepcopy(rates), adjusted_rates=deepcopy(rates),
                minute_cap=cap, manual_minute_cap=manual_cap, observed_minute_cap=observed_cap,
                rate_caps=rate_caps, attempt_caps=attempt_caps, minute_headroom=headroom,
                locked_minutes=bool(item.get('locked_minutes')),
                locked_rates=bool(item.get('locked_rates')),
                history_game_ids=[r['game_id'] for r in rows], history_sources=_sources(rows),
                extra_minutes_attempts={field: 0. for field in FIELDS},
                rate_added_attempts={field: 0. for field in FIELDS})


def _add_minutes(recipients, adjusted, available_minutes, remaining_attempts):
    """Share observed headroom, stopping when either volume budget binds."""
    remaining = available_minutes
    while remaining > EPSILON:
        candidates = {}
        for pid, recipient in recipients.items():
            item = adjusted[pid]
            headroom = recipient['minute_headroom'] - (item['minutes'] - recipient['baseline_minutes'])
            if headroom <= EPSILON:
                continue
            if any(item['rates'][field] > 0 and remaining_attempts[field] <= EPSILON for field in VOLUME_FIELDS):
                continue
            candidates[pid] = headroom
        total = sum(candidates.values())
        if total <= EPSILON:
            break
        fraction = min(1., remaining / total)
        for field in VOLUME_FIELDS:
            demand = sum(headroom * adjusted[pid]['rates'][field] for pid, headroom in candidates.items())
            if demand > 0:
                fraction = min(fraction, max(0., remaining_attempts[field]) / demand)
        if fraction <= 0:
            break
        additions = {pid: headroom * fraction for pid, headroom in candidates.items()}
        for pid, extra in additions.items():
            adjusted[pid]['minutes'] += extra
            for field in FIELDS:
                attempts = extra * adjusted[pid]['rates'][field]
                recipients[pid]['extra_minutes_attempts'][field] += attempts
                remaining_attempts[field] -= attempts
        remaining = max(0., remaining - sum(additions.values()))


def _add_rates(recipients, adjusted, remaining_attempts):
    for field in ('fga', 'fta', 'fg3a', 'tov'):
        remaining = max(0., remaining_attempts[field])
        capacities = {}
        for pid, recipient in recipients.items():
            item = adjusted[pid]
            if recipient['locked_rates'] or not recipient['history_game_ids'] or item['minutes'] <= 0:
                continue
            current_attempts = item['minutes'] * item['rates'][field]
            capacity = max(0., min(recipient['attempt_caps'][field],
                                   recipient['rate_caps'][field] * item['minutes']) - current_attempts)
            if field == 'fg3a':
                capacity = min(capacity, max(0., item['minutes'] * item['rates']['fga'] - current_attempts))
            if capacity > EPSILON:
                capacities[pid] = capacity
        total = sum(capacities.values())
        fraction = min(1., remaining / total) if total else 0.
        for pid, capacity in capacities.items():
            attempts = capacity * fraction
            adjusted[pid]['rates'][field] += attempts / adjusted[pid]['minutes']
            recipients[pid]['rate_added_attempts'][field] += attempts
            remaining_attempts[field] -= attempts


def redistribute(players, profiles, projection_inputs, history, as_of):
    """Return ``(deepcopied_inputs, audits_by_team_id)`` without I/O or mutation.

    ``projection_inputs`` contains supported healthy players only, with minutes,
    fga/fg3a/fta/tov rates, minute_cap and locked_rates; locked_minutes is optional.
    Source-backed confirmed out players supply the only vacancy budget. Profiles
    must come from the same cutoff as these inputs. History is independently
    restricted to games AND observations strictly before the cutoff and the
    inferred July-to-June current/prior NBA regular season.

    Each team audit reports donor sources, recipient caps and history, baseline
    and resulting minutes/attempts, additions from minutes versus rates, and
    unresolved quantities. FGA/FTA additions cannot exceed donor budgets; 3PA
    and turnover mix may change with supported extra minutes and is disclosed.
    Existing baselines, including manual locks, are retained subject to explicit
    and regulation minute caps. If their sum already exceeds 240, no minutes
    are added and the excess is reported for the caller's normalization.
    """
    cutoff = parse_time(as_of)
    season = _season(cutoff)
    start = int(season[:4]) - 1
    seasons = {season, f'{start}-{(start + 1) % 100:02d}'}
    adjusted = deepcopy(projection_inputs)
    profile_map = {p['player_id']: p for p in profiles}
    by_player = defaultdict(list)
    excluded = defaultdict(int)
    for row in history:
        if (row['season_type'] != 'Regular Season' or row['season'] not in seasons
                or parse_time(row['game_time']) >= cutoff or parse_time(row['known_at']) >= cutoff):
            excluded[row['team_id']] += 1
            continue
        by_player[row['player_id']].append(row)
    for rows in by_player.values():
        rows.sort(key=lambda r: (parse_time(r['game_time']), r['game_id']))
    teams = defaultdict(list)
    for player in players:
        teams[player['team_id']].append(player)
    audits = {}
    for team, members in sorted(teams.items()):
        members = sorted(members, key=lambda p: p['player_id'])
        team_ids = {p['player_id'] for p in members}
        active_ids = sorted(team_ids.intersection(adjusted))
        cap_corrections = []
        for pid in active_ids:
            item = adjusted[pid]
            old_minutes = item['minutes']
            item['minutes'] = min(48., max(0., item.get('minute_cap', 48.)), max(0., old_minutes))
            if old_minutes != item['minutes']:
                cap_corrections.append(dict(player_id=pid, original_minutes=old_minutes,
                                             capped_minutes=item['minutes']))
        before = sum(adjusted[pid]['minutes'] for pid in active_ids)
        baseline_attempts = {field: sum(adjusted[pid]['minutes'] * adjusted[pid]['rates'][field]
                                       for pid in active_ids) for field in FIELDS}
        audit = dict(method=METHOD, as_of=as_of, season=season, quantile=QUANTILE,
                     role_window=ROLE_WINDOW, prior_min_games=PRIOR_MIN_GAMES,
                     out_player_ids=[], out_players=[], excluded_out_players=[],
                     ignored_availability_player_ids=[], recipients={},
                     input_cap_corrections=cap_corrections,
                     excluded_history_rows=excluded[team], vacated_minutes=0.,
                     vacated_attempts={field: 0. for field in FIELDS},
                     baseline_attempts=baseline_attempts, team_minutes_before=before,
                     policy='Add only evidenced vacancy within observed headroom; preserve baselines and manual locks. FGA/FTA conserved; 3PA/TOV mix may change.')
        for player in members:
            pid = player['player_id']
            rows, support = _role_rows(by_player[pid], team, season)
            if player.get('status') == 'out':
                profile = profile_map.get(pid, {})
                minutes = profile.get('expected_minutes_baseline')
                reason = None
                if not _sourced_availability(player, cutoff):
                    reason = 'out_status_unsourced_or_future_known'
                elif not rows or minutes is None or minutes <= 0:
                    reason = 'out_role_unsupported'
                elif profile.get('team_id', team) != team:
                    reason = 'profile_team_mismatch'
                if reason:
                    audit['excluded_out_players'].append(dict(player_id=pid, reason=reason))
                    continue
                minutes = min(48., max(0., float(minutes)))
                rates = _donor_rates(profile, rows)
                audit['out_player_ids'].append(pid)
                audit['vacated_minutes'] += minutes
                attempts = {field: minutes * rates[field] for field in FIELDS}
                for field in FIELDS:
                    audit['vacated_attempts'][field] += attempts[field]
                audit['out_players'].append(dict(player_id=pid, minutes=minutes, rates=rates,
                    attempts=attempts, support=support,
                    availability_source={k: deepcopy(v) for k, v in player.items() if k.startswith('status')},
                    history_game_ids=[r['game_id'] for r in rows], history_sources=_sources(rows)))
            elif player.get('status') != 'available':
                audit['ignored_availability_player_ids'].append(pid)
            elif pid in adjusted:
                # The caller supplies supported projections; independently guard
                # against a late-known availability assertion when one is given.
                if player.get('status_observed_at') and not _sourced_availability(player, cutoff):
                    rows, support = [], 'availability_unsourced_or_future_known'
                audit['recipients'][pid] = _recipient(player, adjusted[pid], rows, support)
        remaining_attempts = audit['vacated_attempts'].copy()
        available_minutes = min(audit['vacated_minutes'], max(0., 240. - before))
        _add_minutes(audit['recipients'], adjusted, available_minutes, remaining_attempts)
        _add_rates(audit['recipients'], adjusted, remaining_attempts)
        after = sum(adjusted[pid]['minutes'] for pid in active_ids)
        audit.update(team_minutes_after=after, allocated_minutes=max(0., after - before),
                     unallocated_vacancy_minutes=max(0., audit['vacated_minutes'] - (after - before)),
                     unfilled_team_minutes=max(0., 240. - after),
                     preexisting_minutes_excess=max(0., before - 240.))
        for kind in ('extra_minutes_attempts', 'rate_added_attempts'):
            audit[kind] = {field: sum(r[kind][field] for r in audit['recipients'].values()) for field in FIELDS}
        audit['unallocated_attempts'] = {field: max(0., remaining_attempts[field]) for field in FIELDS}
        audit['attempt_mix_change'] = {field: max(0., -remaining_attempts[field]) for field in FIELDS}
        audit['adjusted_attempts'] = {field: sum(adjusted[pid]['minutes'] * adjusted[pid]['rates'][field]
                                               for pid in active_ids) for field in FIELDS}
        for pid, recipient in audit['recipients'].items():
            recipient['adjusted_minutes'] = adjusted[pid]['minutes']
            recipient['adjusted_rates'] = deepcopy(adjusted[pid]['rates'])
        audits[team] = audit
    return adjusted, audits
