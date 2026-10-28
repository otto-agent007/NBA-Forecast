"""Team totals that agree with the margin forecast.

The margin location blends the player-sum difference with a team rating
(``margins.blended_location``), so the summed player projections imply one
margin and the margin forecast another. Each team's projected players are
scaled so the two team totals differ by the location while their sum, the
game total, is unchanged: each total moves half the gap. On the three-season
reference (``datasets/backtest-2026-10-26-absence``) the location missed by
0.52 points a game less than the player-sum difference, and team totals moved
this way missed by 0.13 less, with player points unchanged.

A scaled player keeps his pre-reconciliation mean in ``components.scoreline``;
the margin is always located from those (``player_projections``), so a saved
forecast reproduces its own margin.
"""
from math import fsum, isfinite

from .boxscore_model import PERCENTAGES, expected_makes
from .totals import game_totals

METHOD = 'margin_location_half_gap_player_scale_v1'
BASIS = ('Projected players scaled so the team totals differ by the margin location with the game total '
         'unchanged; makes reallocated over the same attempts')


def player_projections(predictions):
    """The predictions as the player model produced them, before any scoreline scaling."""
    return [dict(p, points_mean=p['components']['scoreline']['points_mean_before'])
            if 'scoreline' in (p.get('components') or {}) else p for p in predictions]


def _weight(p):
    value = p.get('participation_probability')
    return 1. if value is None else value


def reconcile(predictions, game, location, *, lineups=None):
    """Scale the game's projected players in place toward ``location``; the audit, or None if unchanged.

    Nothing changes unless both team totals are complete, each side's player
    points can absorb its shift, and every scaled player's makes fit his attempts.
    """
    if location is None or not isfinite(location):
        return None
    totals = game_totals(game, player_projections(predictions), lineups=lineups)
    if totals['status'] != 'complete':
        return None
    sides = {t['side']: t for t in totals['teams']}
    shift = (location - (sides['home']['points_mean'] - sides['away']['points_mean'])) / 2
    updates, factors = [], {}
    for side, signed in (('home', shift), ('away', -shift)):
        team = game[side + '_team_id']
        rows = [p for p in predictions if p.get('game_id') == game['game_id'] and p['team_id'] == team
                and p['status'] == 'projected']
        points = fsum(_weight(p) * p['points_mean'] for p in rows)
        if not points > 0 or points + signed <= 0:
            return None
        factor = factors[side] = 1 + signed / points
        for p in rows:
            update = dict(points_mean=p['points_mean'] * factor)
            for key in ('points_sd', 'points_lower80', 'points_upper80'):
                if p.get(key) is not None:
                    update[key] = p[key] * factor
            if p.get('expected_fgm') is not None:
                three, two = p['expected_fg3a'], p['expected_fga'] - p['expected_fg3a']
                shooting = p['components']['shooting']
                try:
                    made = expected_makes([two, three, p['expected_fta']],
                        [shooting[k]['percentage'] for k in ('two_point', 'three_point', 'free_throw')],
                        update['points_mean'])
                except ValueError:
                    return None
                update.update(expected_fgm=made[0] + made[1], expected_fg3m=made[1], expected_ftm=made[2])
                for key, (m, a) in PERCENTAGES.items():
                    update['expected_' + key] = update['expected_' + m] / p['expected_' + a] if p['expected_' + a] else None
            updates.append((p, update, dict(method=METHOD, factor=factor, points_mean_before=p['points_mean'],
                                            location=location, team_shift=signed, basis=BASIS)))
    for p, update, audit in updates:
        p.update(update)
        p.setdefault('components', {})['scoreline'] = audit
    return dict(method=METHOD, location=location, team_shift=shift, factors=factors)
