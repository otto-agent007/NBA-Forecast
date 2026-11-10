"""How much would leaning the published margin on the market line help? A measurement, not a model.

blend = (1 - w) * model margin + w * line, for w in 0, 0.25, 0.5, 0.65, 0.75, 0.85, 1.

Replay (three gate seasons; the only line the archive has is the CLOSE, which holds news up to
tip-off that a forecast made thirty minutes earlier cannot have, so these figures are an upper
bound on what a live blend could do):

    python3 scripts/research/line_blend.py --reference datasets/backtest-2026-11-07-market-strength

Live (no look-ahead): every finished game of a production COPY, the line being the last
DraftKings spread captured at or before the forecast's own `as_of`
(`market/game-lines/<game>.jsonl`, `retrieved_at <= as_of`, recovered closes never), for the
final pregame forecast and for the first forecast of the game:

    python3 scripts/research/line_blend.py --live <production copy>

The ledger is read by run id only (`state.json` names each game's runs), one row per statement;
production itself needs `--allow-production`, and reading stops if a tick runs longer than two minutes. Margin error
is against the final margin; the winner Brier puts the model's own margin SD around the
blended location (`backtest._home_win_probability`); the least-squares weight is the slope of
(actual - model) on (line - model). Every +- is ONE standard error clustered by Eastern date;
changes are paired against w = 0 on the same games.
"""
import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from statistics import fmean

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))
from stats_lite import slope  # noqa: E402
from nba_forecast import backtest as B  # noqa: E402
from nba_forecast.validation import parse_time  # noqa: E402

WEIGHTS = (0., .25, .5, .65, .75, .85, 1.)
BUCKETS = (('under 2', 0., 2.), ('2 to 4', 2., 4.), ('4 to 6', 4., 6.), ('6 or more', 6., 1e9))
OUTLIER = 10.  # |model - close| at or above this: the four live games the gap table sets aside
ABSENCE_ACTIVE = 1.  # rating points of absence adjustment that count as an active term
PROVIDER = 'Draft Kings'


def clustered(values, days):
    """Mean and its one-way date-clustered standard error (never below the independent one)."""
    se, _ = B._mean_se(values, [(d,) for d in days])
    return fmean(values), se


def blend(g, w):
    return (1 - w) * g['model'] + w * g['line']


def brier(g, location):
    return (B._home_win_probability(location, g['sd']) - (g['actual'] > 0)) ** 2


def grid(title, games, target='actual'):
    """One row per weight: error against the result (or the eventual close), paired changes against w = 0."""
    if len(games) < 5:
        print(f'\n{title}: {len(games)} games, too few')
        return
    days = [g['day'] for g in games]
    print(f'\n{title}: {len(games)} games on {len(set(days))} dates')
    if target == 'actual':
        print(f"  {'w':>5s} {'MAE':>7s} {'change':>8s} {'1 se':>6s} {'Brier':>7s} {'change':>8s} {'1 se':>7s} {'bias':>6s}")
    else:
        print(f"  {'w':>5s} {'mean |blend - close|':>20s}")
    base_error = [abs(g['model'] - g[target]) for g in games]
    scored = [g for g in games if g.get('sd')]
    for w in WEIGHTS:
        error = [abs(blend(g, w) - g[target]) for g in games]
        if target != 'actual':
            print(f'  {w:5.2f} {fmean(error):20.2f}')
            continue
        change, se = clustered([e - b for e, b in zip(error, base_error)], days)
        line = f'  {w:5.2f} {fmean(error):7.3f} {change:+8.3f} {se:6.3f}'
        if scored:
            b_change, b_se = clustered([brier(g, blend(g, w)) - brier(g, g['model']) for g in scored], [g['day'] for g in scored])
            line += f' {fmean(brier(g, blend(g, w)) for g in scored):7.4f} {b_change:+8.4f} {b_se:7.4f}'
        print(line + f' {fmean(blend(g, w) - g[target] for g in games):+6.2f}')


def least_squares_weight(games):
    x, y = [g['line'] - g['model'] for g in games], [g['actual'] - g['model'] for g in games]
    if len(games) < 8 or len(set(g['day'] for g in games)) < 3 or not any(x):
        return float('nan'), float('nan')
    return slope(x, y, [g['day'] for g in games])


def split(title, games, groups):
    """Does the best weight differ between groups of games? MAE at each weight, the grid's best, the least-squares weight."""
    print(f'\n{title}')
    print(f"  {'group':24s} {'games':>5s} " + ' '.join(f'w={w:<4g}' for w in WEIGHTS) + '  best w   least-squares weight on the line')
    for name, test in groups:
        subset = [g for g in games if test(g)]
        if len(subset) < 5:
            print(f'  {name:24s} {len(subset):5d}  (too few)')
            continue
        maes = [fmean(abs(blend(g, w) - g['actual']) for g in subset) for w in WEIGHTS]
        best = WEIGHTS[min(range(len(WEIGHTS)), key=lambda i: maes[i])]
        weight, se = least_squares_weight(subset)
        print(f'  {name:24s} {len(subset):5d} ' + ' '.join(f'{m:6.2f}' for m in maes) + f'  {best:6.2f}   {weight:+.2f} ± {se:.2f}')


def splits(games, label):
    split(f'{label}: by the size of the disagreement |model - line|', games,
          [(name, lambda g, lo=lo, hi=hi: lo <= abs(g['model'] - g['line']) < hi) for name, lo, hi in BUCKETS])
    known = [g for g in games if g.get('absence') is not None]
    if known:
        split(f'{label}: by whether an absence term of {ABSENCE_ACTIVE:g}+ rating points is active', known,
              [('absence term active', lambda g: abs(g['absence']) >= ABSENCE_ACTIVE),
               ('no absence term', lambda g: abs(g['absence']) < ABSENCE_ACTIVE)])


def openers(datasets, market_history, season):
    """game_id -> the opening home margin of the closing line's provider, from the archived ESPN odds responses."""
    from clv import opener_and_close
    out = {}
    if market_history is None:
        return out
    for line in B.read_json(Path(datasets) / f'market-closing-{season}' / 'closing-lines.json.gz')['lines']:
        raw = Path(market_history) / season / 'references' / 'raw' / f"{line.get('source_sha256')}.json"
        if line.get('home_spread') is not None and raw.exists():
            spreads = opener_and_close(json.loads(raw.read_bytes()), line['provider'])
            if spreads is not None and spreads[1] == line['home_spread']:  # the archived close is the dataset's close
                out[line['game_id']] = -spreads[0]
    return out


def replay_games(reference, datasets, market_history=None):
    """The reference's games with a closing line, each game's absence adjustment (absence_harness) and opener where archived."""
    import absence_harness as H
    board = B.read_json(Path(reference) / 'rows.json.gz')
    rows = {g[0]: dict(zip(board['game_fields'], g)) for g in board['games']}
    seeded = len(rows) > 3000  # the early-season reference replays every game of each season
    games = []
    for season in H.SEASONS:
        opened = openers(datasets, market_history, season['label'])
        for g in H.season_games(datasets, season, seeded=seeded):
            row = rows.get(g['game_id'])
            if row and g['close'] is not None:
                games.append(dict(game_id=g['game_id'], season=g['season'], day=g['day'], model=row['projected_margin'],
                                  line=g['close'], close=g['close'], actual=row['actual_margin'], sd=row['prior_sd'], week_days=g['week_days'],
                                  absence=H.adjustment(g, 'current') if g['rated'] else 0., opener=opened.get(g['game_id'])))
    return games, len(rows)


def run_replay(args):
    games, total = replay_games(args.reference, args.datasets, args.market_history)
    print(f'Replay, {args.reference}: {len(games)} of {total} games have a closing line. The line is the CLOSE: an upper bound '
          f'for a blend with the line available at forecast time.')
    print(f"mean |model - close| {fmean(abs(g['model'] - g['line']) for g in games):.2f} points")
    grid('Pooled, against the final margin', games)
    for season in sorted({g['season'] for g in games}):
        grid(f'{season}, against the final margin', [g for g in games if g['season'] == season])
    if total > 3000:
        import absence_harness as H
        for name, lo, hi in H.WINDOWS:
            grid(f'{name} since opening night, against the final margin', [g for g in games if lo <= g['week_days'] < hi])
    splits(games, 'Replay')
    opened = [dict(g, line=g['opener']) for g in games if g['opener'] is not None]
    if opened:
        print(f"\nThe same blend with the OPENING line (a lower bound: a forecast thirty minutes before tip sees a later line), "
              f"{len(opened)} games of {sorted({g['season'] for g in opened})} with an archived opener of the close's provider; "
              f"mean |open - close| {fmean(abs(g['opener'] - g['close']) for g in opened):.2f}, mean |model - open| "
              f"{fmean(abs(g['model'] - g['opener']) for g in opened):.2f}")
        grid('Opener, against the final margin', opened)
        for season in sorted({g['season'] for g in opened}):
            grid(f'Opener, {season}' + (' (Betfair; the research found its openers soft, docs/research-lessons-2026-10.md)'
                                        if season == '2023-24' else ''), [g for g in opened if g['season'] == season])
        closes = [g for g in games if g['opener'] is not None]
        grid('The close on the same games, against the final margin', closes)
        splits(opened, 'Opener')


PRODUCTION_MARKER = 'operations/production'
TICK_LIMIT = 120.  # seconds: stop reading production if a tick ran longer than this
TICK_STALE = 600.  # how long to wait for a fresh tick before giving up


def is_production(folder):
    return PRODUCTION_MARKER in str(Path(folder).resolve())


def production_guard(folder):
    """Before each batch of reads from production: wait until the last tick finished within two minutes; stop if it ran longer."""
    if not is_production(folder):
        return
    import time
    from datetime import datetime, timezone
    waited = 0
    while True:
        tick = json.loads((Path(folder) / 'last-tick.json').read_text())
        took = (parse_time(tick['finished_at']) - parse_time(tick['started_at'])).total_seconds()
        age = (datetime.now(timezone.utc) - parse_time(tick['finished_at'])).total_seconds()
        if took > TICK_LIMIT and _READ[0]:
            sys.exit(f'stopping: a tick took {took:.0f} s while this script was reading')
        if took <= TICK_LIMIT and age <= TICK_LIMIT:
            _READ[0] = True
            return
        if waited >= TICK_STALE:
            sys.exit(f'stopping: no tick under {TICK_LIMIT:.0f} s finished in the last {TICK_LIMIT:.0f} s for {waited} s')
        time.sleep(5)
        waited += 5


_READ = [False]  # set once reading from production has begun


def pause(folder):
    """About a second after each multi-megabyte run read from production, so the disk stays free for the tick."""
    if is_production(folder):
        import time
        time.sleep(1.)


def open_ledger(folder):
    """The ledger, read-only. Callers fetch one row per statement and consume it whole, so no read transaction stays open."""
    return sqlite3.connect((Path(folder) / 'forecast.sqlite').resolve().as_uri() + '?mode=ro', uri=True, timeout=30)


def one(conn, query, value):
    """A single row by key, its statement finished before returning."""
    rows = conn.execute(query, (value,)).fetchall()
    return rows[0] if rows else None


class RunCache:
    """The few fields the live tables need from each run, read from the ledger once and kept in a small local file.

    One row per statement (`one`), a tick guard before every ten reads and a pause after each when the
    source is production; with ``path`` every fetched run is saved at once, so a stopped read resumes
    where it left off. Kept per run: as_of, created_at, stage, and for the asked game its margin forecast
    (without the probability tables), its reported-out players with `components.absence`, and the team
    ratings the snapshot froze.
    """
    QUERY = ("SELECT as_of, created_at, json_extract(forecast_json, '$.forecast_stage'), json_extract(forecast_json, '$.margin_forecasts'), "
             "json_extract(forecast_json, '$.predictions'), json_extract(snapshot_json, '$.model_enhancements.margin_inputs.team_ratings') "
             'FROM run_records WHERE run_id = ?')

    def __init__(self, folder, path=None):
        self.folder, self.path, self.conn, self.reads = Path(folder), path, None, 0
        self.runs = json.loads(Path(path).read_text()) if path and Path(path).exists() else {}

    def _connection(self):
        if self.conn is None:
            self.conn = open_ledger(self.folder)
        return self.conn

    def times(self, run_id):
        """as_of and created_at only (the row's first page, no JSON)."""
        if run_id in self.runs:
            return self.runs[run_id]['as_of'], self.runs[run_id]['created_at']
        return one(self._connection(), 'SELECT as_of, created_at FROM run_records WHERE run_id = ?', run_id)

    def rowid(self, run_id):
        """Insertion order, from the primary-key index alone."""
        found = one(self._connection(), 'SELECT rowid FROM run_records WHERE run_id = ?', run_id)
        return found[0] if found else None

    def get(self, run_id, game_id):
        key = f'{run_id}:{game_id}'
        if key not in self.runs:
            if self.reads % 10 == 0:
                production_guard(self.folder)
            self.reads += 1
            row = one(self._connection(), self.QUERY, run_id)
            pause(self.folder)
            if row is None:
                return None
            as_of, created, stage, margins, predictions, ratings = row
            margin = next((m for m in json.loads(margins or '[]') if m.get('game_id') == game_id), {})
            out = [dict(player_id=p['player_id'], team_id=p['team_id'], absence=(p.get('components') or {}).get('absence'))
                   for p in json.loads(predictions or '[]') if p.get('game_id') == game_id and p.get('status') == 'out']
            self.runs[key] = dict(as_of=as_of, created_at=created, stage=stage, out=out,
                                  margin={k: v for k, v in margin.items() if not k.endswith('pmf')},
                                  ratings={t: {'market_strength': v.get('market_strength')} for t, v in json.loads(ratings or '{}').items()})
            self.runs[run_id] = dict(as_of=as_of, created_at=created)
            if self.path:
                temporary = Path(str(self.path) + '.tmp')
                temporary.write_text(json.dumps(self.runs))
                temporary.replace(self.path)
        return self.runs[key]


def check_source(parser, folder, allow_production):
    if PRODUCTION_MARKER in str(Path(folder).resolve()) and not allow_production:
        parser.error('this is production; pass --allow-production to read it (read-only, by run id) or use a copy')


def _spread(record):
    value = (((record.get('lines') or {}).get('spread') or {}).get('home') or {}).get('current_line')
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def line_at(captures, moment):
    """The last DraftKings home margin captured at or before ``moment``, and when; recovered closes never count."""
    best = None
    for r in captures:
        if r.get('provider') != PROVIDER or r.get('recovered_close') or _spread(r) is None:
            continue
        at = parse_time(r['retrieved_at'])
        if at <= moment and (best is None or at >= best[1]):
            best = (-_spread(r), at)
    return best


def live_games(folder, cache_path=None):
    """Every finished game of the copy with a close: its first and its last pregame forecast with the line each could see."""
    from nba_forecast.market_lines import closing_lines, read_game_lines
    folder = Path(folder)
    report = json.loads((folder / 'market' / 'report.json').read_text())
    finished = {r['game_id']: r for r in report['games'] if r.get('provider') == PROVIDER and r.get('market_margin') is not None}
    jobs = json.loads((folder / 'state.json').read_text())['jobs']
    runs = defaultdict(set)
    for key, run_id in jobs.items():
        if isinstance(run_id, str) and key.split(':', 1)[0] in finished:
            runs[key.split(':', 1)[0]].add(run_id)
    cache = RunCache(folder, cache_path)
    out, missing = [], defaultdict(int)
    for gid, row in sorted(finished.items()):
        tip = parse_time(row['start_time'])
        captures = read_game_lines(folder, gid)
        close = closing_lines(captures, row['start_time'])
        close = next((-_spread(r) for r in close.values() if r.get('provider') == PROVIDER and _spread(r) is not None), None)
        if close is None:
            missing['no DraftKings close'] += 1
            continue
        final = cache.get(row['run_id'], gid)
        if final is None or not (parse_time(final['as_of']) < tip and parse_time(final['created_at']) < tip):
            missing['selected run not pregame'] += 1
            continue
        # The game's runs oldest first by insertion order (primary-key index only); the first saved and made before tip.
        order = sorted((i, r) for i, r in ((cache.rowid(r), r) for r in runs.get(gid, ()) | {row['run_id']}) if i is not None)
        first = next((r for _, r in order if (lambda t: t and parse_time(t[0]) < tip and parse_time(t[1]) < tip)(cache.times(r))), None)
        picks = dict(final=row['run_id'], first=first)
        entry = dict(game_id=gid, day=B._day(row['start_time']).isoformat(), actual=row['actual_margin'], close=close,
                     runs=len(order), start_time=row['start_time'],
                     outlier=abs(row['model_margin'] - row['market_margin']) >= OUTLIER)
        for name, run_id in picks.items():
            if run_id is None:
                missing[f'{name}: no run saved before tip'] += 1
                continue
            got = cache.get(run_id, gid)
            as_of, margin, stage = got['as_of'], got['margin'], got['stage']
            line = line_at(captures, parse_time(as_of))
            if margin.get('status') != 'estimated' or line is None:
                missing[f"{name}: {'no margin forecast' if margin.get('status') != 'estimated' else 'no line captured by then'}"] += 1
                continue
            entry[name] = dict(run_id=run_id, as_of=as_of, stage=stage, model=margin['mean_margin'], sd=margin['standard_deviation'],
                               line=line[0], line_at=line[1].isoformat(),
                               minutes_before_tip=(tip - parse_time(as_of)).total_seconds() / 60,
                               absence=margin.get('absence_rating_adjustment'))
        out.append(entry)
    if 'final' in (out[0] if out else {}):
        agree = max(abs(g['final']['model'] - finished[g['game_id']]['model_margin']) for g in out if 'final' in g)
        print(f"check: largest |ledger margin - market report margin| on the final forecasts {agree:.2e}; "
              f"largest |close here - close in the report| {max(abs(g['close'] - finished[g['game_id']]['market_margin']) for g in out):.2e}")
    return out, dict(missing), len(finished)


def run_live(args):
    entries, missing, finished = live_games(args.live, args.cache)
    print(f'Live, {args.live}: {finished} finished games with a DraftKings close in the market report; {len(entries)} usable; '
          f'left out: {missing or "none"}')
    if args.json:
        Path(args.json).write_text(json.dumps(entries, indent=1, sort_keys=True))
    for name, label in (('final', 'Final pregame forecast'), ('first', 'First forecast of the game')):
        games = [dict(game_id=e['game_id'], day=e['day'], actual=e['actual'], close=e['close'], outlier=e['outlier'], **e[name])
                 for e in entries if name in e]
        if not games:
            continue
        lead = sorted(g['minutes_before_tip'] for g in games)
        move = [abs(g['line'] - g['close']) for g in games]
        print(f"\n=== {label}: {len(games)} games; made a median {lead[len(lead) // 2]:.0f} minutes before tip "
              f"(quartiles {lead[len(lead) // 4]:.0f} and {lead[3 * len(lead) // 4]:.0f}); "
              f"the line it could see was {fmean(move):.2f} points from the close on average (mean absolute move), "
              f"exactly the close in {sum(m == 0 for m in move)} games, {sum(m >= 1.5 for m in move)} moved 1.5+")
        close_mae = fmean(abs(g['close'] - g['actual']) for g in games)
        print(f"    the close itself misses the final margin by {close_mae:.3f} on these games; "
              f"mean |model - line| {fmean(abs(g['model'] - g['line']) for g in games):.2f}")
        calm = [g for g in games if not g['outlier']]
        print(f"    outlier games (the final forecast's published margin {OUTLIER:g}+ points from the close, as in the "
              f"market report): {sorted(g['game_id'] for g in games if g['outlier'])}")
        for subset, tag in ((games, 'all games'), (calm, 'without the outlier games')):
            grid(f'{label}, {tag}, against the final margin', subset)
            grid(f'{label}, {tag}, against the eventual close', subset, target='close')
            splits(subset, f'{label}, {tag}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--reference', type=Path, help='a backtest reference (rows.json.gz) for the replay tables')
    parser.add_argument('--datasets', type=Path, default=HERE.parent.parent / 'datasets')
    parser.add_argument('--market-history', type=Path, help="the archived ESPN odds responses (data/market-history, gitignored) "
                        "for the opener bracket of the replay")
    parser.add_argument('--live', type=Path, help='a production copy for the live tables')
    parser.add_argument('--allow-production', action='store_true', help='--live may be production itself: files and the '
                        'ledger are only read (by run id, one row at a time), and reading stops if a tick runs long')
    parser.add_argument('--json', help='with --live: write the per-game records here')
    parser.add_argument('--cache', type=Path, help='with --live: keep the fields read from each run here, and reuse them')
    args = parser.parse_args()
    if not args.reference and not args.live:
        parser.error('pass --reference, --live or both')
    if args.reference:
        run_replay(args)
    if args.live:
        check_source(parser, args.live, args.allow_production)
        run_live(args)


if __name__ == '__main__':
    main()
