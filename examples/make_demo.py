"""Generate explicitly SYNTHETIC two-team test data; never a real prediction."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    folder = parser.parse_args().output
    folder.mkdir(parents=True, exist_ok=True)
    source = 'https://example.com/SYNTHETIC-TEST-DATA'
    players = [dict(player_id=f'{team}{i}', name=f'SYNTHETIC {team} Player {i}',
                    team_id=team, status='available', status_source_url=source,
                    status_observed_at='2026-10-21T17:00:00Z')
               for team in ('demo-a', 'demo-b') for i in range(10)]
    players[1]['status'] = 'out'
    players[2]['status'] = 'questionable'
    players[3]['status'] = 'unknown'
    snapshot = dict(schema_version=1, season='2026-27', as_of='2026-10-21T18:00:00Z',
                    label='SYNTHETIC OFFLINE DEMO: NOT NBA DATA',
                    roster=dict(complete=True, team_ids=['demo-a', 'demo-b'],
                                source_url=source, observed_at='2026-10-21T17:00:00Z',
                                players=players),
                    games=[dict(game_id='demo-target', start_time='2026-10-21T19:00:00Z',
                                season_type='Regular Season', home_team_id='demo-a',
                                away_team_id='demo-b')], role_adjustments=[])
    history = []
    for day in range(12):
        start = datetime(2026, 3, 1, 19, tzinfo=timezone.utc) + timedelta(days=day*2)
        for i, player in enumerate(players):
            if i == 19:  # Unseen rookie: must remain in the pool with no invented baseline.
                continue
            made = 3 + (i + day) % 3
            threes = 1 + i % 2
            history.append(dict(game_id=f'demo-history-{day}', season='2025-26',
                                season_type='Regular Season', game_time=start.isoformat(),
                                known_at=(start + timedelta(hours=4)).isoformat(),
                                source_url=source, player_id=player['player_id'],
                                name=player['name'], team_id=player['team_id'],
                                opponent_id='demo-b' if player['team_id'] == 'demo-a' else 'demo-a',
                                minutes=24, fgm=made, fga=10, fg3m=threes, fg3a=4,
                                ftm=2, fta=2, tov=1, pts=2*made+threes+2, status='played'))
    actuals = []
    for i, player in enumerate(players):
        made = 2 + i % 4
        actuals.append(dict(game_id='demo-target', season='2026-27', season_type='Regular Season',
                            game_time='2026-10-21T19:00:00Z', known_at='2026-10-21T23:00:00Z',
                            source_url=source, player_id=player['player_id'], name=player['name'],
                            team_id=player['team_id'],
                            opponent_id='demo-b' if player['team_id'] == 'demo-a' else 'demo-a',
                            minutes=24, fgm=made, fga=10, fg3m=1, fg3a=4,
                            ftm=2, fta=2, tov=1, pts=2*made+3, status='played'))
    for name, value in [('snapshot', snapshot), ('history', history), ('actuals', actuals)]:
        with (folder / f'{name}.json').open('x', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write('\n')
    print(f'Synthetic demo inputs created in {folder}')


if __name__ == '__main__':
    main()
