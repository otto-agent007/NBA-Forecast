"""A previous season's box scores under the next season's player IDs, for a seeded replay across ID schemes.

    python3 scripts/research/previous_ids.py \
        --previous datasets/espn-season-2024-25/history.json.gz \
        --current datasets/regular-season-expanded-2026-10-22/history.json.gz --season 2025-26 \
        --output datasets/espn-season-2024-25/history-nba-ids.json.gz

2024-25 comes from ESPN (``espn-<athlete>`` IDs) and 2025-26 from the NBA, so
the backtest's ``--previous-history`` for 2025-26 needs one scheme. A previous
row takes the current season's ID when its name key (``injury_parser.person_key``)
names exactly one current player; otherwise it keeps its own ID, which no
current roster carries (players who left the league, and ambiguous names).
"""
import argparse
from collections import defaultdict
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nba_forecast.backtest import read_json  # noqa: E402
from nba_forecast.injury_parser import person_key  # noqa: E402
from nba_forecast.schedule import canonical  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--previous', required=True)
    parser.add_argument('--current', required=True)
    parser.add_argument('--season', required=True, help='The current season label in --current')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    ids = defaultdict(set)
    for r in read_json(args.current):
        if r['season'] == args.season:
            ids[person_key(r['name'])].add(r['player_id'])
    rows, mapped = [], 0
    for r in read_json(args.previous):
        match = ids.get(person_key(r['name']))
        if match and len(match) == 1:
            r = dict(r, player_id=next(iter(match)))
            mapped += 1
        rows.append(r)
    Path(args.output).write_bytes(gzip.compress(canonical(rows), mtime=0))
    print(json.dumps(dict(rows=len(rows), mapped_rows=mapped, output=args.output)))


if __name__ == '__main__':
    main()
