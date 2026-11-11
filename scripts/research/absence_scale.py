"""How big the team-rating absence term should be, from backtests run with it switched off or shrunk.

Each replay is a reference backtest (``--output`` folder of the unchanged code) and the same replay
with ``margins.absence_rating_per_minute`` set to 0 (and optionally to another size), all on the same
games. Everything is paired on the same games and scored against actual results, ± one standard
error clustered by Eastern game date (the gate prints two):

  - margin abs-error change and winner Brier change, variant minus reference;
  - the slope of (actual margin - reference projection) on (term-off projection - reference
    projection), with an intercept. 0 means the term is the right size and 1 means it should go;
    the squared-error-best size of the term, as a fraction of the reference's, is 1 - slope
    (this assumes the term moves the projected margin linearly in its size).

    python3 scripts/research/absence_scale.py \\
        gate:REFERENCE_DIR:ZERO_DIR[:OTHER_DIR] early:REF:ZERO:OTHER ... [--by-days early]

``--by-days NAME`` also splits that replay by days since its season's opening night (days 0-20 is
the live window of 2026-11-10: three weeks). Game dates come from ``--datasets`` team-games.

``--sizes 0.25,0.5,0.75`` also prints the margin error change at other sizes by linear
interpolation, projection(s) = reference + (1 - s) * (term off - reference); ``--other-size 0.5``
names the size of OTHER_DIR and checks the interpolation against it (largest margin difference).
"""
import argparse
from collections import defaultdict
import datetime
import gzip
import json
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats_lite import slope  # noqa: E402

TEAM_GAMES = ('espn-season-2023-24', 'espn-season-2024-25', 'model-upgrade-2026-10-23')
EASTERN = ZoneInfo('America/New_York')
DAY_WINDOWS = (('days 0-20', 0, 21), ('days 21-55', 21, 56), ('days 56+', 56, None))


def games(folder):
    board = json.loads(gzip.decompress(Path(folder, 'rows.json.gz').read_bytes()))
    return {g[0]: dict(zip(board['game_fields'], g)) for g in board['games']}


def game_dates(datasets):
    """game_id -> (Eastern date, days since its season's opening night)."""
    dated = {}
    for folder in TEAM_GAMES:
        season = {}
        for r in json.loads(gzip.decompress(Path(datasets, folder, 'team-games.json.gz').read_bytes())):
            t = datetime.datetime.fromisoformat(r['game_time'].replace('Z', '+00:00'))
            season[r['game_id']] = (t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)).astimezone(EASTERN).date()
        opening = min(season.values())
        dated.update({g: (d, (d - opening).days) for g, d in season.items()})
    return dated


def mean_se(values, clusters):
    n = len(values)
    mean = math.fsum(values) / n
    sums = defaultdict(float)
    for v, c in zip(values, clusters):
        sums[c] += v - mean
    g = len(sums)
    return mean, math.sqrt(math.fsum(s * s for s in sums.values()) / n ** 2 * g / (g - 1))


def line(label, ref, var, zero, ids, dates):
    ids = [g for g in ids if ref[g]['actual_margin'] != 0]
    clusters = [dates[g][0] for g in ids]

    def abs_error(rows, g):
        return abs(rows[g]['projected_margin'] - rows[g]['actual_margin'])

    def brier(rows, g):
        return (rows[g]['home_win_probability'] - (ref[g]['actual_margin'] > 0)) ** 2
    margin, margin_se = mean_se([abs_error(var, g) - abs_error(ref, g) for g in ids], clusters)
    winner, winner_se = mean_se([brier(var, g) - brier(ref, g) for g in ids], clusters)
    text = (f'  {label:30s} n={len(ids):5d} dates={len(set(clusters)):3d}  margin {margin:+.3f} ± {margin_se:.3f}'
            f'  winner Brier {winner:+.4f} ± {winner_se:.4f}')
    if var is zero:
        x = [zero[g]['projected_margin'] - ref[g]['projected_margin'] for g in ids]
        y = [ref[g]['actual_margin'] - ref[g]['projected_margin'] for g in ids]
        k, k_se = slope(x, y, clusters)
        text += (f'  | mean |term| {math.fsum(abs(v) for v in x) / len(x):.2f}'
                 f'  slope {k:+.2f} ± {k_se:.2f}, best size {1 - k:.2f} ± {k_se:.2f} of the reference term')
    print(text)


def interpolated(label, ref, zero, ids, dates, size, other=None):
    ids = [g for g in ids if ref[g]['actual_margin'] != 0]
    clusters = [dates[g][0] for g in ids]

    def projection(g):
        return ref[g]['projected_margin'] + (1 - size) * (zero[g]['projected_margin'] - ref[g]['projected_margin'])
    change, se = mean_se([abs(projection(g) - ref[g]['actual_margin']) - abs(ref[g]['projected_margin'] - ref[g]['actual_margin'])
                          for g in ids], clusters)
    text = f'  {label:30s} n={len(ids):5d}  margin {change:+.3f} ± {se:.3f} (linear interpolation)'
    if other is not None:
        text += f'; largest |interpolated - replayed margin| {max(abs(projection(g) - other[g]["projected_margin"]) for g in ids):.3f}'
    print(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('replays', nargs='+', help='NAME:REFERENCE_DIR:ZERO_DIR[:OTHER_DIR]')
    parser.add_argument('--datasets', default=str(Path(__file__).resolve().parents[2] / 'datasets'))
    parser.add_argument('--by-days', action='append', default=[], help='replay NAME to split by days since opening night')
    parser.add_argument('--sizes', default='', help='comma-separated sizes (fractions of the reference term) to interpolate')
    parser.add_argument('--other-size', type=float, help="OTHER_DIR's size, to check the interpolation against it")
    args = parser.parse_args()
    dates = game_dates(args.datasets)
    print('Absence term off (and other size) vs reference, paired, error vs actual results, ± one SE clustered by Eastern date')
    for spec in args.replays:
        name, ref_dir, zero_dir, *other = spec.split(':')
        ref, zero = games(ref_dir), games(zero_dir)
        variants = [('term off', zero)] + [(f'other ({Path(other[0]).name})', games(other[0]))] * bool(other)
        for _, rows in variants:
            if set(rows) != set(ref) or any(rows[g]['actual_margin'] != ref[g]['actual_margin'] for g in ref):
                sys.exit(f'{name}: {rows is zero and zero_dir or other[0]} is not the same games as {ref_dir}')
        print(f'== {name} (reference {ref_dir})')
        ids = sorted(ref)
        for label, rows in variants:
            line(f'{label}, all', ref, rows, zero, ids, dates)
        for size in [float(v) for v in args.sizes.split(',') if v]:
            other_rows = variants[1][1] if len(variants) > 1 and args.other_size == size else None
            interpolated(f'size {size:g}, all', ref, zero, ids, dates, size, other_rows)
        if name in args.by_days:
            for window, lo, hi in DAY_WINDOWS:
                subset = [g for g in ids if dates[g][1] >= lo and (hi is None or dates[g][1] < hi)]
                for label, rows in variants:
                    line(f'{label}, {window}', ref, rows, zero, subset, dates)


if __name__ == '__main__':
    main()
