"""Run the backtest unchanged and also save each replayed game's margin components.

    python3 scripts/research/replay_components.py <the arguments of `python -m nba_forecast backtest`>

The scoreboard and rows are the gate's own (the same code path, the same
verdict). Beside them `<output>/components.json.gz` holds, per replayed game,
what the rows do not: the player-sum difference, the home edge, both team
ratings, the absence adjustment with each side's counted minutes, and every
reported-out player's `components.absence`. `absence_harness.py` reads it to
prove that its offline reconstruction of the absence term is the replay's
(location rebuilt to the digit) before it re-scores any other rule.

Nothing in the package is edited: the two functions the replay calls for the
location (`backtest.absence_adjustment`, `backtest.blended_location`) are
wrapped to record their inputs and outputs.
"""
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from nba_forecast import backtest as B  # noqa: E402
from nba_forecast.__main__ import main  # noqa: E402

RECORDS = []
_pending = {}
_absence_adjustment, _blended_location = B.absence_adjustment, B.blended_location


def absence_adjustment(predictions, game):
    adjustment, minutes = _absence_adjustment(predictions, game)
    out = [dict(player_id=p['player_id'], team_id=p['team_id'], **(p.get('components') or {}).get('absence') or {})
           for p in predictions if p.get('game_id') == game['game_id'] and p['status'] == 'out']
    _pending.clear()
    _pending.update(game_id=game['game_id'], home_team_id=game['home_team_id'], away_team_id=game['away_team_id'],
                    absence_adjustment=adjustment, absent_minutes=minutes, out=out)
    return adjustment, minutes


def blended_location(player_sum, home_edge, ratings, home, away, absence=0.):
    location, rating = _blended_location(player_sum, home_edge, ratings, home, away, absence)
    if _pending.get('home_team_id') != home or _pending.get('away_team_id') != away:
        raise RuntimeError('the replay no longer calls absence_adjustment just before blended_location')
    RECORDS.append(dict(_pending, player_sum=player_sum, home_edge=home_edge, location=location, rating_margin=rating,
                        ratings={side: ratings.get(team) for side, team in (('home', home), ('away', away))}))
    _pending.clear()
    return location, rating


if __name__ == '__main__':
    arguments = sys.argv[1:]
    if '--output' not in arguments:
        sys.exit('pass the backtest arguments, including --output')
    B.absence_adjustment, B.blended_location = absence_adjustment, blended_location
    code = main(['backtest', *arguments])
    target = Path(arguments[arguments.index('--output') + 1]) / 'components.json.gz'
    target.write_bytes(gzip.compress(json.dumps(dict(games=RECORDS), sort_keys=True, separators=(',', ':')).encode(), mtime=0))
    print(f'{len(RECORDS)} games of margin components written to {target}', file=sys.stderr)
    sys.exit(code)
