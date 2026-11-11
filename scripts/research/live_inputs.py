"""What a live production forecast carried that the replayed production path does not (read-only).

    nice -n 19 ionice -c3 python3 scripts/research/live_inputs.py --directory data/operations/production --runs 70 --stride 9

Reads saved runs (runs/<id>/snapshot.json and forecast.json), newest first, every
``--stride``-th folder; keeps the newest run of each game (only final-stage ones with a
stride) and counts, over the projected players of the forecast game, the inputs and
layers a live forecast has that `backtest --production-path` does not build. Nothing is
written. The answer depends on the runs present when it is run (docs/backtest.md,
"Production path", quotes 2026-11-09).
"""
import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import fmean

# The snapshot keys backtest.replay builds (backtest.py: replay and _production_forecast).
REPLAY_SNAPSHOT = {'schema_version', 'as_of', 'season', 'forecast_stage', 'research_reconstruction', 'games',
                   'reported_unavailable', 'roster', 'model_enhancements', 'schedule_context'}
REPLAY_ENHANCEMENT = {'model_version', 'model_key', 'known_at', 'parameters', 'context_model', 'calibration',
                      'environment', 'excluded_history_game_ids'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--runs', type=int, default=60, help='Run folders to read, newest first')
    parser.add_argument('--stride', type=int, default=1, help='Read every Nth run folder, to reach back over more game days')
    args = parser.parse_args()
    folder = Path(args.directory) / 'runs'
    newest = sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[::args.stride][:args.runs]
    chosen = {}
    for path in newest:
        try:
            snapshot = json.loads((path / 'snapshot.json').read_text())
            forecast = json.loads((path / 'forecast.json').read_text())
        except (OSError, ValueError):
            continue
        game = snapshot['games'][0]['game_id']
        if game not in chosen:  # newest first
            chosen[game] = (path.name, snapshot, forecast)
    print(f'runs read {len(newest)}; games {len(chosen)} (newest run of each); '
          f"as_of {min(s['as_of'] for _, s, _ in chosen.values())[:16]} .. {max(s['as_of'] for _, s, _ in chosen.values())[:16]}")
    chosen = {game: item for game, item in chosen.items() if item[1].get('forecast_stage') == 'final'} if args.stride > 1 else chosen
    stages = Counter(s.get('forecast_stage') for _, s, _ in chosen.values())
    versions = Counter(f['model_version'] for _, _, f in chosen.values())
    print('stages', dict(stages), 'model versions', dict(versions))
    extra_snapshot, extra_enhancement = Counter(), Counter()
    players = Counter()
    shot_points, margins, env_games, roster_sizes, unreconciled = [], Counter(), [], [], 0
    interval = Counter()
    for run, snapshot, forecast in chosen.values():
        for key in set(snapshot) - REPLAY_SNAPSHOT:
            value = snapshot[key]
            extra_snapshot[key] += bool(value) if not isinstance(value, dict) else 1
        enhancement = snapshot.get('model_enhancements') or {}
        for key in set(enhancement) - REPLAY_ENHANCEMENT:
            extra_enhancement[key + ('' if enhancement[key] is not None else ' (null)')] += 1
        extra_enhancement['calibration ' + str((enhancement.get('calibration') or {}).get('status'))] += 1
        env_games.append(enhancement.get('environment', {}).get('audit', {}).get('eligible_games'))
        roster_sizes.append(len(snapshot['roster']['players']))
        game = snapshot['games'][0]
        projected = [p for p in forecast['predictions'] if p['status'] == 'projected' and p.get('game_id') == game['game_id']]
        scaled = 0
        for p in projected:
            c = p['components']
            players['projected'] += 1
            players['historical_prior'] += 'historical_prior' in c
            quality = c.get('shot_quality') or {}
            players['shot_quality applied'] += quality.get('status') == 'applied'
            players['shot_quality scoring_changed'] += bool(quality.get('scoring_changed'))
            if quality.get('scoring_changed'):
                before, after = quality['baseline_percentages'], quality['adjusted_percentages']
                three = p.get('expected_fg3a') or 0.
                two = (p.get('expected_fga') or 0.) - three
                shot_points.append(2 * two * (after['two_point'] - before['two_point'])
                                   + 3 * three * (after['three_point'] - before['three_point'])
                                   + (p.get('expected_fta') or 0.) * (after['free_throw'] - before['free_throw']))
            players['role_context exclusion'] += 'role_context' in c
            players['manual_adjustment'] += bool(c.get('manual_adjustment'))
            status = (c.get('uncertainty', {}).get('calibration') or {}).get('status')
            interval[status] += 1
            scaled += 'scoreline' in c
            players['boxscore extension (expected_fgm)'] += p.get('expected_fgm') is not None
            players['modeled_shot_profile'] += p.get('modeled_shot_profile') is not None
        for m in forecast.get('margin_forecasts') or []:
            margins[(m['status'], m.get('reason'), m.get('calibration_status'), m.get('coverage_status'))] += 1
            if m['status'] == 'estimated' and projected and not scaled:
                unreconciled += 1
        players['lineups in preflight (games)'] += bool((snapshot.get('preflight') or {}).get('lineups'))
    print('\nsnapshot keys a live run carries and the replay does not build (games with a non-empty value):')
    for key, n in sorted(extra_snapshot.items()):
        print(f'  {key:28s} {n:3d} of {len(chosen)}')
    print('model_enhancements keys beyond the replay\'s:')
    for key, n in sorted(extra_enhancement.items()):
        print(f'  {key:28s} {n:3d} of {len(chosen)}')
    print(f"environment eligible team games per run: min {min(env_games)} max {max(env_games)}; roster players per snapshot: "
          f'min {min(roster_sizes)} max {max(roster_sizes)}')
    n = players['projected']
    print(f'\nprojected players of the forecast game: {n}')
    for key, value in players.items():
        if key != 'projected':
            print(f'  {key:36s} {value:5d}' + (f' ({value / n:.1%})' if 'games' not in key else f' of {len(chosen)}'))
    print('  interval calibration status', dict(interval))
    if shot_points:
        print(f'shot quality, attempts x percentage change in points (before budgets), players it changed: n={len(shot_points)} '
              f'mean {fmean(shot_points):+.3f} mean |.| {fmean(abs(v) for v in shot_points):.3f} max |.| {max(abs(v) for v in shot_points):.2f}')
    print('\nmargin forecasts (status, reason, calibration, totals coverage):')
    for key, value in sorted(margins.items(), key=lambda kv: -kv[1]):
        print(f'  {value:3d}  {key}')
    print(f'estimated margins whose team totals were not reconciled (no scoreline component): {unreconciled}')


if __name__ == '__main__':
    main()
