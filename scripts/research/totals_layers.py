"""Which production layer moves a team's projected points: an instrumented production-path replay.

    python3 scripts/research/totals_layers.py --stride 3 --output /tmp/totals-layers.json.gz
    python3 scripts/research/totals_layers.py --read /tmp/totals-layers.json.gz

Replays every ``--stride``-th evaluation game of the three gate seasons on
production's path (``candidate.forecast_candidate``, as ``backtest --production-path``
does) and records, for every projected player, the numbers the baseline stage
left and the minutes, rates and factors the production layers then gave him.
A team's points are linear in those, so the total is rebuilt layer by layer:

    baseline            the baseline stage's own total (weighted player sum plus the replacement credit)
    second budget       the enhanced-stage usage budget applied to the baseline's numbers alone
    minute grants       vacancy minutes ``rotation_allocation`` granted, after the caller's volume scale
    shot rates          FGA, 3PA and FTA rates it added, after the caller's volume caps
    turnover rates      turnover rates it added (the caller does not cap them)
    pace                the pace factor on every rate and on the usage target
    defense, context    the opponent-defense and rest/travel factors replacing the baseline's opponent proxy
    scoreline           ``scoreline.reconcile`` moving points between the two teams

The usage budget is re-applied after every layer, as production applies it once
at the end, so a layer's figure includes what the budget does in response to it.
The rebuilt total is checked against the forecast's own to 1e-6 points on every
team-game. Means are per team-game; the ± is one standard error clustered by
Eastern game date.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats_lite import ols  # noqa: E402

# One entry per replayed season, as AGENTS.md's backtest command names them.
SEASONS = (
    ('2023-24', 'espn-season-2023-24/history.json.gz', 'espn-season-2023-24/team-games.json.gz'),
    ('2024-25', 'espn-season-2024-25/history.json.gz', 'espn-season-2024-25/team-games.json.gz'),
    ('2025-26', 'regular-season-expanded-2026-10-22/history.json.gz', 'model-upgrade-2026-10-23/team-games.json.gz'),
)
ARTIFACT = 'model-upgrade-2026-10-23/artifact.json.gz'
LAYERS = ('baseline', 'second budget', 'minute grants', 'shot rates', 'turnover rates', 'pace', 'defense, context', 'scoreline')
RATES = ('fga', 'fg3a', 'fta', 'tov')
TOLERANCE = 1e-6


def mean_se(values, clusters):
    """The mean and its one-way cluster-robust standard error (G/(G-1) factor)."""
    if len(set(clusters)) < 2:
        return (values[0] if values else float('nan')), float('nan')
    beta, se = ols([[1.]] * len(values), values, clusters)
    return beta[0], se[0]


def _points_per_minute(rates, pct):
    return 2 * (rates['fga'] - rates['fg3a']) * pct[0] + 3 * rates['fg3a'] * pct[1] + rates['fta'] * pct[2]


def _team_total(players, key, team):
    """The team total and the budget factor for one stage of the players' numbers.

    ``key`` names the stage's minutes, rates and factor on each player. The
    budget is ``model._usage_budget``'s rule with no locked players; the
    replacement credit is ``totals.game_totals``'s.
    """
    from nba_forecast.model import REPLACEMENT_USAGE_PER_MINUTE, USAGE_BOUNDS
    from nba_forecast.totals import REPLACEMENT_POINTS_PER_MINUTE, RESIDUAL_MIN_PROJECTED
    minutes, rates, factor, pace, budget = key
    expected = sum(p['P'] * p[minutes] for p in players)
    residual = max(0., 240. - expected)
    scale = 1.
    if budget:
        target = (team['usage_per_240'] - residual * REPLACEMENT_USAGE_PER_MINUTE) * pace
        before = sum(p['P'] * p[minutes] * pace * (p[rates]['fga'] + .44 * p[rates]['fta'] + p[rates]['tov']) for p in players)
        raw = target / before if before > 0 else 1.
        scale = min(USAGE_BOUNDS[1], max(USAGE_BOUNDS[0], raw))
        if len(players) < RESIDUAL_MIN_PROJECTED and not team['short']:
            scale = min(1., scale)
    points = sum(p['P'] * p[minutes] * _points_per_minute(p[rates], p['pct']) * pace * scale * p[factor] for p in players)
    filled = len(players) >= RESIDUAL_MIN_PROJECTED or team['short']
    return points + (residual * REPLACEMENT_POINTS_PER_MINUTE if filled else 0.), scale


def _stages(players, team):
    """Cumulative team totals after each layer, and the usage-budget factor at each."""
    for p in players:
        p['shots'] = dict(p['rates0'], **{k: p['rates1'][k] for k in ('fga', 'fg3a', 'fta')})
    pace = team['pace']
    keys = (('minutes0', 'rates0', 'factor0', 1., False), ('minutes0', 'rates0', 'factor0', 1., True),
            ('minutes1', 'rates0', 'factor0', 1., True), ('minutes1', 'shots', 'factor0', 1., True),
            ('minutes1', 'rates1', 'factor0', 1., True), ('minutes1', 'rates1', 'factor0', pace, True),
            ('minutes1', 'rates1', 'factor1', pace, True))
    return [_team_total(players, key, team) for key in keys]


def replay_layers(datasets, stride, offset=0, progress=None, artifact=None):
    """Team-game records with the cumulative total after each layer, from an instrumented replay."""
    from nba_forecast import backtest, candidate
    from nba_forecast.injury_history import read_statuses
    from nba_forecast.margins import ABSENCE_MINIMUM_MINUTES
    from nba_forecast.market_history import read_lines
    from nba_forecast.totals import short_bench
    datasets = Path(datasets)
    artifact = backtest.read_json(artifact or datasets / ARTIFACT)
    state, captured = {}, {}
    baseline_forecast, production_forecast, evaluation_games = (
        candidate._baseline_forecast, backtest._production_forecast, backtest.evaluation_games)

    def baseline(snapshot, history):
        result = baseline_forecast(snapshot, history)
        state['environment'] = snapshot['model_enhancements']['environment']['teams']
        state['players'] = {}
        for p in result['predictions']:
            if p['status'] != 'projected':
                continue
            if p['components'].get('manual_adjustment'):
                raise ValueError('a replayed player carries a manual adjustment; the layer rebuild assumes none')
            minutes, scoring = p['expected_minutes'], p['components']['scoring']
            rates = dict.fromkeys(RATES, 0.)
            if minutes > 0:
                rates.update(fga=p['expected_fga'] / minutes, fg3a=scoring['expected_three_point_attempts'] / minutes,
                             fta=p['expected_fta'] / minutes, tov=p['expected_tov'] / minutes)
            state['players'][p['player_id']] = dict(
                team_id=p['team_id'], P=p['participation_probability'], minutes0=minutes, rates0=rates,
                pct=[p['components']['shooting'][k]['percentage'] for k in ('two_point', 'three_point', 'free_throw')],
                factor0=scoring['opponent_multiplier'] * scoring['home_court_multiplier'])
        state['baseline_totals'] = {t['team_id']: t['points_mean'] for item in result['game_totals'] for t in item['teams']}
        state['baseline_usage'] = {p['team_id']: p['components']['team_usage'] for p in result['predictions']
                                   if p['status'] == 'projected'}
        return result

    def production(snapshot, history, artifact, team_games, calendar):
        result = production_forecast(snapshot, history, artifact, team_games, calendar)
        game = snapshot['games'][0]
        published = {t['team_id']: t for item in result['game_totals'] for t in item['teams']}
        for team in (game['home_team_id'], game['away_team_id']):
            rows = [p for p in result['predictions'] if p['team_id'] == team]
            projected = [p for p in rows if p['status'] == 'projected']
            if not projected:
                continue
            players = []
            for p in projected:
                item = state['players'][p['player_id']]
                recipient = p['components']['injury_allocation']['recipient'] or {}
                environment = p['components']['game_environment']
                item.update(minutes1=recipient.get('adjusted_minutes', item['minutes0']),
                            rates1=recipient.get('adjusted_rates', item['rates0']),
                            factor1=environment['defense_factor'] * environment['context_factor']
                            * p['components'].get('home_court', {}).get('factor', 1.))
                players.append(item)
            usage, audit = projected[0]['components']['team_usage'], result['allocation_audit'].get(team, {})
            info = dict(usage_per_240=usage['usage_prior']['usage_per_240'], short=short_bench(rows),
                        pace=projected[0]['components']['game_environment']['pace_factor'])
            stages = _stages(players, info)
            checks = (stages[0][0] - state['baseline_totals'][team], stages[-1][0] - published[team]['points_mean'],
                      stages[-1][1] - usage['factor'])
            if max(abs(c) for c in checks) > TOLERANCE:
                raise ValueError(f"layer rebuild differs from the forecast for {game['game_id']} {team}: {checks}")
            absent = [p['components']['absence'] for p in rows if p['status'] == 'out' and p['components'].get('absence')]
            regulars = [a for a in absent if a.get('played_recent_team_game') and (a.get('minutes_per_game') or 0) >= ABSENCE_MINIMUM_MINUTES]
            weighted = lambda minutes, rates, field: sum(p['P'] * p[minutes] * p[rates][field] for p in players)
            captured[(game['game_id'], team)] = dict(
                game_id=game['game_id'], team_id=team, season=snapshot['season'], totals=[s[0] for s in stages],
                budget_factors=[s[1] for s in stages], projected_players=len(projected),
                reported_out=sum(p['status'] == 'out' for p in rows), out_regulars=len(regulars),
                out_regular_minutes=sum(a['minutes_per_game'] for a in regulars),
                donors=len(audit.get('out_player_ids', [])), vacated_minutes=audit.get('vacated_minutes', 0.),
                vacated_attempts=audit.get('vacated_attempts'), unallocated_attempts=audit.get('unallocated_attempts'),
                baseline_budget_factor=state['baseline_usage'][team]['factor'],
                baseline_budget_raw=state['baseline_usage'][team]['raw_factor'],
                enhanced_budget_raw=usage['raw_factor'], residual_minutes=published[team]['residual_minutes'],
                unallocated_minutes=published[team]['unallocated_minutes'],
                conditional_minutes=sum(p['minutes0'] for p in players),
                granted_minutes=sum(p['P'] * (p['minutes1'] - p['minutes0']) for p in players),
                added={field: weighted('minutes1', 'rates1', field) - weighted('minutes1', 'rates0', field) for field in RATES},
                expected={field: weighted('minutes0', 'rates0', field) for field in RATES},
                projected={field: sum(p['participation_probability'] * p['expected_' + field] for p in projected)
                           for field in ('fga', 'fta', 'tov')},
                # The caller's FGA volume budget: its target, and the baseline attempts it compares
                # them with (conditional on playing, as candidate.py sums them) or weighted.
                fga_target=(state['environment'].get(team, {}).get('expected_fga') or 0.) / (state['environment'].get(team, {}).get('pace_factor') or 1.),
                fga_conditional=sum(p['minutes0'] * p['rates0']['fga'] for p in players),
                fga_weighted=weighted('minutes0', 'rates0', 'fga'))
        return result

    def sampled(records, warmup_fraction=.3):
        ordered, evaluated = evaluation_games(records, warmup_fraction)
        return ordered, evaluated[offset::stride]

    candidate._baseline_forecast, backtest._production_forecast, backtest.evaluation_games = baseline, production, sampled
    records_out = []
    try:
        for season, history_path, games_path in SEASONS:
            history, records = backtest.read_json(datasets / history_path), backtest.read_json(datasets / games_path)
            dates = {r['game_id']: backtest._day(r['game_time']).isoformat() for r in records}
            by_id = {r['game_id']: r for r in records}
            replayed = backtest.replay(
                history, records, datetime.now(timezone.utc).isoformat(), progress=progress, artifact=artifact,
                injuries=read_statuses(datasets / f'injury-reports-{season}/statuses.json.gz'),
                market=read_lines(datasets / f'market-closing-{season}/closing-lines.json.gz'))
            for row in replayed['teams']:
                t = dict(zip(backtest.TEAM_FIELDS, row))
                record = captured.get((t['game_id'], t['team_id']))
                if record is None or t['actual_points_regulation'] is None:
                    continue
                game = by_id[t['game_id']]
                scale = 240. / game['team_minutes'][t['team_id']]
                record.update(date=dates[t['game_id']], home=t['home'], published=t['projected_points'],
                              actual_regulation=t['actual_points_regulation'], actual=t['actual_points'],
                              actual_volume={field: game['team_stats'][t['team_id']][key] * scale
                                             for field, key in (('fga', 'fga'), ('fta', 'fta'), ('tov', 'player_tov'))})
                record['totals'].append(t['projected_points'])
                records_out.append(record)
    finally:
        candidate._baseline_forecast, backtest._production_forecast, backtest.evaluation_games = (
            baseline_forecast, production_forecast, evaluation_games)
    return records_out


def _group(count):
    return '3+' if count >= 3 else str(count)


def table(records, title):
    """Each layer's mean effect on the team total, pooled, by season and by reported-out regulars."""
    print('\n' + title)
    header = f"  {'':14s} {'n':>5s} " + ' '.join(f'{name:>16s}' for name in LAYERS) + f" {'published':>10s} {'actual reg.':>11s} {'error':>14s}"
    print(header)
    groups = [('all', records)]
    groups += [(season, [r for r in records if r['season'] == season]) for season in sorted({r['season'] for r in records})]
    groups += [(f'out regulars {g}', [r for r in records if _group(r['out_regulars']) == g]) for g in ('0', '1', '2', '3+')]
    for label, rows in groups:
        if not rows:
            continue
        dates = [r['date'] for r in rows]
        cells = []
        for i, name in enumerate(LAYERS):
            values = [r['totals'][i] - (r['totals'][i - 1] if i else 0.) for r in rows]
            mean, se = mean_se(values, dates)
            cells.append(f'{mean:16.2f}' if i == 0 else f'{mean:+9.3f} ±{se:5.3f}')
        published, _ = mean_se([r['published'] for r in rows], dates)
        actual, _ = mean_se([r['actual_regulation'] for r in rows], dates)
        error, se = mean_se([r['published'] - r['actual_regulation'] for r in rows], dates)
        check = max(abs(sum(r['totals'][i] - (r['totals'][i - 1] if i else 0.) for i in range(len(LAYERS))) - r['published']) for r in rows)
        if check > TOLERANCE:
            raise ValueError(f'layers do not add up to the published total ({check})')
        print(f"  {label:14s} {len(rows):5d} " + ' '.join(cells) + f" {published:10.2f} {actual:11.2f} {error:+7.2f} ±{se:5.2f}")


def details(records):
    """What the vacancy allocation asked for and what survived, per team-game with a donor."""
    donors = [r for r in records if r['donors']]
    print(f"\nteam-games with at least one vacancy donor: {len(donors)} of {len(records)} "
          f"({len(donors) / len(records):.1%}); means over those, per team-game")
    dates = [r['date'] for r in donors]
    print(f"  vacated minutes {mean_se([r['vacated_minutes'] for r in donors], dates)[0]:.1f}; "
          f"expected minutes granted {mean_se([r['granted_minutes'] for r in donors], dates)[0]:+.2f}; "
          f"conditional minutes of the projected players before any grant {mean_se([r['conditional_minutes'] for r in donors], dates)[0]:.1f}")
    for field in RATES:
        vacated = mean_se([r['vacated_attempts'][field] for r in donors], dates)[0]
        added = mean_se([r['added'][field] for r in donors], dates)[0]
        level = mean_se([r['expected'][field] for r in donors], dates)[0]
        print(f"  {field:5s} vacated {vacated:6.2f}   added to teammates' expected attempts {added:+6.3f}   baseline expected level {level:6.2f}")
    if all('fga_target' in r for r in donors):
        room = sum(r['fga_target'] > r['fga_conditional'] for r in donors)
        weighted_room = sum(r['fga_target'] > r['fga_weighted'] for r in donors)
        print(f"  FGA volume target {mean_se([r['fga_target'] for r in donors], dates)[0]:.2f} against the baseline's attempts conditional on playing "
              f"{mean_se([r['fga_conditional'] for r in donors], dates)[0]:.2f} (participation-weighted "
              f"{mean_se([r['fga_weighted'] for r in donors], dates)[0]:.2f}); no room for added shots in {len(donors) - room} of {len(donors)} "
              f"team-games ({(len(donors) - room) / len(donors):.1%}); room in {weighted_room} if the baseline were weighted")
    budget = mean_se([r['budget_factors'][4] - r['budget_factors'][3] for r in donors], dates)
    print(f"  usage-budget factor change from the turnover rates: {budget[0]:+.4f} ± {budget[1]:.4f}")
    if all('projected' in r and 'actual_volume' in r for r in records):
        dates = [r['date'] for r in records]
        print('team volume per team-game, projected (participation-weighted, after every layer) against the actual '
              '(regulation-equivalent; player turnovers, as the usage prior counts them), ± one date-clustered standard error')
        for field in ('fga', 'fta', 'tov'):
            projected = mean_se([r['projected'][field] for r in records], dates)[0]
            actual = mean_se([r['actual_volume'][field] for r in records], dates)[0]
            gap = mean_se([r['projected'][field] - r['actual_volume'][field] for r in records], dates)
            print(f"  {field:4s} projected {projected:6.2f}  actual {actual:6.2f}  difference {gap[0]:+6.2f} ± {gap[1]:.2f}")
    clipped = [r for r in records if abs(r['baseline_budget_raw'] - r['baseline_budget_factor']) > 1e-9]
    others = [r for r in records if abs(r['baseline_budget_raw'] - r['baseline_budget_factor']) <= 1e-9]
    second = lambda rows: mean_se([r['totals'][1] - r['totals'][0] for r in rows], [r['date'] for r in rows])
    print(f"baseline usage budget clipped at its bounds in {len(clipped)} of {len(records)} team-games, where the second "
          f"budget changes the total by {second(clipped)[0]:+.3f} ± {second(clipped)[1]:.3f}; elsewhere by "
          f"{second(others)[0]:+.3f} (largest {max((abs(r['totals'][1] - r['totals'][0]) for r in others), default=0.):.6f})")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--datasets', default=str(ROOT / 'datasets'))
    parser.add_argument('--stride', type=int, default=3, help='Replay every Nth evaluation game of each season (1: all)')
    parser.add_argument('--offset', type=int, default=0, help='First sampled game within the stride')
    parser.add_argument('--artifact', help=f'Production model artifact (default: <datasets>/{ARTIFACT})')
    parser.add_argument('--output', help='Write the team-game records here (.json.gz)')
    parser.add_argument('--read', help='Print the tables of records written earlier instead of replaying')
    args = parser.parse_args()
    if args.read:
        saved = json.loads(gzip.decompress(Path(args.read).read_bytes()))
        records, label = saved['records'], f"{args.read} (stride {saved['stride']}, offset {saved['offset']})"
    else:
        records = replay_layers(args.datasets, args.stride, args.offset, artifact=args.artifact,
                                progress=lambda item: print(json.dumps(item), file=sys.stderr, flush=True))
        label = f'stride {args.stride}, offset {args.offset}'
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_bytes(gzip.compress(json.dumps(
                dict(stride=args.stride, offset=args.offset, layers=LAYERS, records=records), sort_keys=True).encode(), mtime=0))
    games = len({r['game_id'] for r in records})
    print(f'{len(records)} team-games of {games} replayed games, production path; {label}')
    print('points per team-game; each layer is the change it makes to the projected total, ± one date-clustered standard error')
    table(records, 'layer effects (baseline is a level; the others are changes; they sum to the published total)')
    details(records)


if __name__ == '__main__':
    main()
