"""What ``kalshi_props.props_report`` costs a derived rebuild, per finished game, and how large its report is.

    python3 scripts/research/props_report_cost.py REPO [GAMES] [RECORDS]

REPO is a checkout whose ``nba_forecast`` is imported (to compare two versions,
run it once per checkout). It builds GAMES finished games (default 10), each
with RECORDS pre-tip captures (default 74, about 2 MB a game: 18 players, four
statistics, the record the 2026-11-10 review built), a final box score and an
injected forecast selection, in a temporary folder under scratch/tmp, then times
``props_report`` twice: the first rebuild after the games finish, and the next
one with nothing new. Prints milliseconds per game for each, and the bytes of the
report and of any files it keeps.
"""
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from nba_forecast import kalshi_props as P  # noqa: E402

GAMES = int(sys.argv[2]) if len(sys.argv) > 2 else 10
RECORDS = int(sys.argv[3]) if len(sys.argv) > 3 else 74
random.seed(7)
ladders = dict(points=(9.5, 14.5, 19.5, 24.5, 29.5, 34.5), rebounds=(3.5, 5.5, 7.5, 9.5), assists=(1.5, 3.5, 5.5, 7.5), threes=(0.5, 1.5, 2.5))


def build(folder, index):
    gid = f'00226{index:05d}'
    game = dict(game_id=gid, start_time='2026-11-10T03:00:00Z', home_team_id='1610612744', away_team_id='1610612740',
                home_tricode='GSW', away_tricode='NOP', status='final')
    roster, markets, finals, predictions = [], {s: [] for s in P.SERIES}, [], []
    pid = 0
    for team, tid in (('GSW', '1610612744'), ('NOP', '1610612740')):
        for _ in range(9):
            pid += 1
            name = f'Player{pid} Surname{pid}'
            roster.append(dict(player_id=str(pid), name=name, team_id=tid))
            finals.append(dict(player_id=str(pid), pts=random.randint(0, 35), minutes=random.uniform(0, 38)))
            mean = random.uniform(4, 28)
            predictions.append(dict(player_id=str(pid), status='projected', points_mean=mean, points_sd=mean ** .5 + 2,
                                    points_lower80=max(0., mean - 8), points_upper80=mean + 9))
            for stat, rungs in ladders.items():
                event = P.event_ticker(P.SERIES[stat], game)
                for t in rungs:
                    p = random.random()
                    markets[stat].append(dict(ticker=f'{event}-{team}P{pid}SURNAME{pid}-{int(t + .5)}', title=f'{name}: {int(t + .5)}+ {stat}',
                                              floor_strike=t, strike_type='greater', status='active', yes_bid_dollars=f'{max(0, p - .01):.4f}',
                                              yes_ask_dollars=f'{min(1, p + .01):.4f}', volume_fp='123.00', open_interest_fp='45.00',
                                              custom_strike=dict(basketball_player=f'uuid-{pid}')))
    sources = [dict(source_url='https://api.elections.kalshi.com/trade-api/v2/markets?event_ticker=X&limit=1000',
                    retrieved_at='2026-11-10T02:00:00+00:00', sha256='a' * 64, bytes=200000, archive='raw/' + 'a' * 64 + '.json.gz')] * 4
    line = json.dumps(P.extract_props(game, markets, roster, '2026-11-10T02:00:00+00:00', sources), sort_keys=True)
    path = folder / 'market' / 'kalshi-props' / (gid + '.jsonl')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((line + '\n') * RECORDS)
    (folder / 'finals').mkdir(exist_ok=True)
    (folder / 'finals' / (gid + '.json')).write_text(json.dumps(finals))
    return game, {(gid, 'final'): dict(run_id=f'run-{gid}', predictions=predictions)}


def main():
    scratch = '/mnt/2TBHDD/NBA-Forecast/scratch/tmp'
    with tempfile.TemporaryDirectory(dir=scratch if os.path.isdir(scratch) else None) as d:
        folder = Path(d)
        games, selected = [], {}
        for i in range(GAMES):
            game, choice = build(folder, i)
            games.append(game)
            selected.update(choice)
        schedule = dict(season='2026-27', games=games)
        size = sum(p.stat().st_size for p in (folder / 'market' / 'kalshi-props').glob('*.jsonl'))
        print(f'{GAMES} finished games, {RECORDS} captures each, props files {size / 1e6:.1f} MB')
        for label in ('first rebuild after the games finish', 'next rebuild, nothing new'):
            start = time.perf_counter()
            report = P.props_report(folder, schedule, '2026-11-10T09:00:00Z', selected=selected)
            seconds = time.perf_counter() - start
            report_bytes = len(json.dumps(report))
            kept = [p for p in (folder / 'market' / 'kalshi-props').rglob('*.json')]
            print(f'{label}: {seconds * 1000 / GAMES:.1f} ms per game ({seconds * 1000:.0f} ms), report {report_bytes} B '
                  f'({report_bytes / GAMES:.0f} B a game), files kept {len(kept)} totalling {sum(p.stat().st_size for p in kept)} B, '
                  f'rungs scored {report["summary"]["rungs"]}')
        print(f'at 1,230 games: next-rebuild cost about {seconds * 1230 / GAMES:.1f} s, report about {report_bytes * 1230 / GAMES / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
