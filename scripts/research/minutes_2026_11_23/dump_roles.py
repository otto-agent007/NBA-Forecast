"""Replay the backtest and write what the minutes budget saw for every projected player.

    python3 dump_roles.py OUT.jsonl --history ... --team-games ... --output DIR [any other backtest argument]

Runs ``python3 -m nba_forecast backtest`` unchanged (same output, same verdict)
with the two forecast calls wrapped, and writes one line per projected player
per game: role minutes before the budget, participation probability, the
budget's scale, the published minutes, and the inputs of the terms applied
after the budget. The outcomes are in the run's own ``DIR/rows.json.gz``; ``budget_rules.py``
joins the two. A replay is a heavy job: run it like the gate.
"""
import json, pathlib, sys

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from nba_forecast import backtest          # noqa: E402
from nba_forecast.__main__ import main     # noqa: E402


def wrap(function, out):
    def wrapped(snapshot, history, *args, **kwargs):
        result = function(snapshot, history, *args, **kwargs)
        # His average minutes for this team this season: an input measured in after_budget.py that the model does not use.
        played = {}
        for r in history:
            if r['season'] == snapshot['season'] and r['status'] == 'played' and r['minutes'] > 0:
                played.setdefault((r['player_id'], r['team_id']), []).append(r['minutes'])
        for p in result['predictions']:
            if p['status'] != 'projected':
                continue
            c = p['components']; rotation = c['rotation']; role = c.get('current_team_rotation') or {}
            typical = c.get('typical_inputs') or {}
            mine = played.get((p['player_id'], p['team_id']))
            out.write(json.dumps(dict(
                g=p['game_id'], pid=p['player_id'], t=p['team_id'], m=p['expected_minutes'], P=p['participation_probability'],
                role=c['opportunities']['minutes_before_adjustment'], scale=rotation['scale'], before=rotation['team_minutes_before_scaling'],
                short=rotation['short_bench'], core=p['player_id'] in rotation['protected_rotation_player_ids'],
                manual=bool(c.get('manual_adjustment')), started=role.get('last_game_started'),
                status_factor=c['participation']['status_factor'],
                P_before_squeeze=c['participation'].get('probability_before_squeeze', p['participation_probability']),
                games=typical.get('games'), missed=typical.get('missed_since_last'),
                season_mean=sum(mine) / len(mine) if mine else None,
                points=p['points_mean'])) + '\n')
        return result
    return wrapped


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    with open(sys.argv[1], 'w') as out:
        backtest._baseline_forecast = wrap(backtest._baseline_forecast, out)
        backtest._production_forecast = wrap(backtest._production_forecast, out)
        status = main(['backtest'] + sys.argv[2:])
    sys.exit(status)
