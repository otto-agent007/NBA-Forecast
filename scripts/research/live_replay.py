"""Score this checkout's code on the inputs its live forecasts actually had.

The gate (``python3 -m nba_forecast backtest``) replays past seasons on the baseline
path with box-score rosters. This tool takes the live ledger instead: for every
finished game it picks the pregame run the market report scores, hands that run's
saved snapshot and history to ``forecast`` of the package beside this script, and
scores the result against the final and the closing line next to what was
published. Copy the script into another worktree and it scores that code.

    nice -n 19 python3 scripts/research/live_replay.py --copy COPY \
        --output-frozen OUT/frozen.json --output-rebuilt OUT/rebuilt.json
    python3 scripts/research/live_replay.py --compare MAIN/rebuilt.json BRANCH/rebuilt.json

Two modes, each with its own output file (give one or both):

frozen   the saved snapshot exactly as stored. A run made by this same code must
         come back byte for byte; that is the self-test (``identical``). Code that
         runs inside ``forecast`` shows up; whatever was prepared before the
         forecast and saved in the snapshot does not. A margin forecast needs
         margin inputs of this code's method and parameters, so a run of an
         earlier margin method replays without one (reason
         ``unsupported_margin_inputs``) and its margin is the player sum.
rebuilt  additionally recomputes ``snapshot['model_enhancements']`` with
         ``enhancements.prepare`` of this code, from the run's own model artifact
         and as of the run's own ``prepared_at``: the pace and defense
         environment and the margin inputs (team ratings, the market-implied
         strength from closes retrieved by then, the outcome dispersion). Every
         other prepared input stays as saved (``FROZEN_INPUTS``). ``prepare`` reads
         line captures (bounded by ``retrieved_at``) and environment finals
         (bounded by ``known_at``). A final's record carries no time it was
         written, and a record rewritten later (a re-fetched box score) keeps the
         box score's earlier ``known_at``, so by default ``prepare`` sees only the
         records the run itself read (``live_team_game_sources``, its own audit);
         ``--finals known-at`` lets it read every record by ``known_at`` instead,
         and every run reports which records that would have added.

``COPY`` is a copy of the production directory, never production itself; it is
opened read-only and nothing is written into it. ``--ledger`` may name another
ledger (production's, read-only, rows by key only). The disks ignore I/O
priority classes, so the tool throttles itself (``Throttle``: at most a third of
the time reading by default) and pauses while production's tick looks stuck. Runs are found from
``state.json`` (job key -> run), ranked by the ledger's ``created_at`` and row
order read by ``run_id``; no JSON column is scanned. The ± printed is ONE standard
error, clustered by Eastern game date (``backtest._mean_se``, never below the
independent error). Limits: a few weeks of games; the lines are captures, not
quotes; ``docs/backtest.md``, "Live replay".
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from nba_forecast import backtest as B  # noqa: E402
from nba_forecast import store  # noqa: E402
from nba_forecast.files import atomic_bytes, canonical  # noqa: E402
from nba_forecast.validation import parse_time  # noqa: E402

TOOL = 'live-replay-1'
OUTLIER = 10.  # |published margin - closing margin| at or above this is reported separately
SE_LABEL = ('one standard error, clustered by Eastern game date (backtest._mean_se: one-way cluster-robust, '
            'G/(G-1), never below the independent error)')
KEY = re.compile(r'^(?P<game_id>[^:]+):(?P<start>.+?):(?P<stage>final|preliminary):model:(?P<model>[^:]+)(?::|$)')
# What ``enhancements.prepare`` computes, and what it copies from the artifact.
REBUILT_INPUTS = ('environment', 'margin_inputs', 'live_team_game_sources')
ARTIFACT_INPUTS = ('artifact_sha256', 'known_at', 'model_version', 'model_key', 'parameters', 'context_model',
                   'calibration', 'excluded_history_game_ids', 'margin_calibration')
FROZEN_INPUTS = {
    'roster': 'the roster and each player\'s availability as the injury report, ESPN list and reviews were parsed and applied at the time',
    'schedule_context': 'rest and travel context from the calendar version of the time',
    'reported_unavailable': 'earlier games each player was reported out of; the reader takes injuries/games/*/final.json as it stands, '
                            'and late captures (recover-evidence) are added to those files without a time the reader filters on',
    'historical_priors': 'the multi-season scoring priors book of the time',
    'shot_profiles': 'prepared from the shot archives; its preparation writes evidence files into the data folder, so it is not run on a read-only copy',
    'shot_quality': 'as shot_profiles',
    'role_context': 'recap role evidence replayed from the raw archives at the time (about 20 s a game to prepare again; not attempted)',
    'preflight': 'the ESPN cross-check, availability supplements and projected lineups, which needed the network at the time',
    'history': 'the box-score history the run was saved with, by its digest',
    'model_artifact': 'the fields prepare copies from the run\'s own artifact: parameters, context model, interval calibration, '
                      'margin calibration (dropped in rebuilt mode when it is not of this code\'s family, as a deploy would), model key',
}


def _sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _eastern(start_time):
    return B._day(start_time).isoformat()


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# ---------------------------------------------------------------- selection (pure)

def parse_job_key(key):
    """Game, tipoff, stage, model version, model key and artifact digest from a ``state.json`` job key, or None."""
    match = KEY.match(key)
    if not match:
        return None
    extra = dict(re.findall(r':(configuration|artifact):([0-9a-f]{64})', key))
    return dict(game_id=match['game_id'], start=match['start'], stage=match['stage'], model_version=match['model'],
                model_key=extra.get('configuration'), artifact_sha256=extra.get('artifact'))


def index_jobs(jobs):
    """Runs per game from the job map (key -> run_id); keys this tool cannot read are counted, not guessed."""
    by_game, unreadable = defaultdict(list), 0
    for key, run_id in jobs.items():
        parsed = parse_job_key(key)
        if parsed is None:
            unreadable += 1
            continue
        by_game[parsed['game_id']].append(dict(parsed, run_id=run_id))
    return dict(by_game), unreadable


def finished_games(schedule, finals):
    """Scheduled games with a saved final box score, by tipoff."""
    games = [g for g in schedule.get('games', []) if g.get('start_time') and g.get('status') not in ('postponed', 'cancelled', 'tbd')
             and (Path(finals) / (g['game_id'] + '.json')).exists()]
    return sorted(games, key=lambda g: (parse_time(g['start_time']), g['game_id']))


def eligible(game, candidates):
    """Candidates that are live league runs for this tipoff, created before it."""
    tip = parse_time(game['start_time'])
    return [c for c in candidates if c.get('mode') == 'live' and c.get('scope') == 'league'
            and c['stage'] in ('final', 'preliminary') and parse_time(c['start']) == tip and parse_time(c['created_at']) < tip]


def select_run(game, candidates, which='final'):
    """The run to replay for a game, or None.

    ``final`` is the market report's choice (``market_lines.market_report`` over
    ``daily_review._selected``): the last final-stage run created before tipoff,
    else the last preliminary one. ``first`` is the earliest pregame run of the
    game. Ties on the creation time fall to the ledger's row order. Candidates
    carry ``run_id``, ``stage``, ``start`` (the tipoff in the job key),
    ``created_at``, ``insertion_order``, ``mode`` and ``scope``.
    """
    usable = eligible(game, candidates)
    if not usable:
        return None
    rank = lambda c: (parse_time(c['created_at']), c['insertion_order'])
    if which == 'first':
        return min(usable, key=rank)
    return max([c for c in usable if c['stage'] == 'final'] or usable, key=rank)


def as_of_captures(captures, as_of):
    """Line captures retrieved by ``as_of``: what ``market_lines.closing_margins`` may read for a forecast made then."""
    cutoff = parse_time(as_of)
    return [c for c in captures if parse_time(c['retrieved_at']) <= cutoff]


def as_of_finals(records, as_of):
    """Each game's newest environment final known by ``as_of``, as ``enhancements._contemporary_games`` chooses it.

    ``records`` are (content digest, record) pairs; returns {game_id: digest}.
    """
    cutoff = parse_time(as_of)
    chosen = {}
    for digest, record in records:
        if parse_time(record['known_at']) > cutoff:
            continue
        rank = (parse_time(record['known_at']), digest)
        if record['game_id'] not in chosen or rank > chosen[record['game_id']][0]:
            chosen[record['game_id']] = (rank, digest)
    return {gid: digest for gid, (_, digest) in chosen.items()}


# ---------------------------------------------------------------- statistics (pure)

def mean_se(values, dates):
    """Mean and one date-clustered standard error (``SE_LABEL``); the error is None below two values."""
    if not values:
        return None, None
    mean = math.fsum(values) / len(values)
    if len(values) < 2:
        return mean, None
    return mean, B._mean_se(list(values), [(d,) for d in dates])[0]


def paired(items, first, second):
    """Mean of ``first - second`` over items that have both, with its clustered error and the two levels."""
    rows = [i for i in items if _finite(i.get(first)) and _finite(i.get(second))]
    mean, se = mean_se([r[first] - r[second] for r in rows], [r['date'] for r in rows])
    level = lambda key: math.fsum(r[key] for r in rows) / len(rows) if rows else None
    return dict(n=len(rows), dates=len({r['date'] for r in rows}), first=level(first), second=level(second), mean=mean, se=se)


def pair_outputs(base, candidate):
    """Game rows of two outputs that replay the same original run, and those that do not pair."""
    left = {g['game_id']: g for g in base['games']}
    right = {g['game_id']: g for g in candidate['games']}
    pairs, unpaired = [], []
    for gid in sorted(set(left) | set(right)):
        a, b = left.get(gid), right.get(gid)
        if a is None or b is None:
            unpaired.append(dict(game_id=gid, reason='only in ' + ('candidate' if a is None else 'base')))
        elif a['run_id'] != b['run_id']:
            unpaired.append(dict(game_id=gid, reason='different original run'))
        else:
            pairs.append((a, b))
    return pairs, unpaired


def differences(stored, replayed, limit=30):
    """Where two JSON values differ: leaf paths with list positions generalized, how often, and the largest numeric gap."""
    found = {}

    def note(path, a, b):
        item = found.setdefault(path, dict(path=path, count=0, max_abs_difference=None, example=None))
        item['count'] += 1
        if _finite(a) and _finite(b):
            gap = abs(a - b)
            if item['max_abs_difference'] is None or gap > item['max_abs_difference']:
                item['max_abs_difference'] = gap
        if item['example'] is None:
            item['example'] = [json.dumps(a, sort_keys=True)[:80], json.dumps(b, sort_keys=True)[:80]]

    def walk(path, a, b):
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a or key not in b:
                    note(f'{path}.{key}', a.get(key, '<absent>'), b.get(key, '<absent>'))
                else:
                    walk(f'{path}.{key}', a[key], b[key])
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                note(path + '[length]', len(a), len(b))
            for x, y in zip(a, b):
                walk(path + '[*]', x, y)
        elif a != b or type(a) is not type(b):
            note(path, a, b)

    walk('', stored, replayed)
    ordered = sorted(found.values(), key=lambda d: (-d['count'], d['path']))
    return dict(paths=len(ordered), listed=ordered[:limit])


# ---------------------------------------------------------------- the copy

class Ledger:
    """Read-only access to a ledger by run_id and history digest; one history is kept at a time.

    Rows are read only by key from ``run_records`` and ``histories`` (never the
    ``runs`` view, never a scan of the JSON columns). ``immutable`` skips SQLite's
    locking and shared-memory file, which is right only for a copy nobody writes;
    a live ledger is opened read-only in its own journal mode. ``io_seconds``
    adds up the time spent reading, for the caller's throttle.
    """

    def __init__(self, path, *, immutable=False):
        path = Path(path)
        wal = Path(str(path) + '-wal')
        # A copy with an unmerged log needs the log, so it is not opened immutable either.
        self.immutable = immutable and not (wal.exists() and wal.stat().st_size)
        self.conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro' + ('&immutable=1' if self.immutable else ''), uri=True, timeout=60)
        self.conn.execute('PRAGMA query_only=ON')
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_records'").fetchone():
            raise SystemExit('This ledger keeps histories inside each run (before dedupe-histories); it is not supported.')
        self._history = (None, None)
        self.history_reads = 0
        self.io_seconds = 0.

    def _one(self, query, args):
        began = time.monotonic()
        try:
            return self.conn.execute(query, args).fetchone()
        finally:
            self.io_seconds += time.monotonic() - began

    def meta(self, run_id):
        row = self._one('SELECT rowid,created_at,as_of,mode,scope FROM run_records WHERE run_id=?', (run_id,))
        return None if row is None else dict(insertion_order=row[0], created_at=row[1], as_of=row[2], mode=row[3], scope=row[4])

    def history_digest(self, run_id):
        return self._one('SELECT history_sha256 FROM run_records WHERE run_id=?', (run_id,))[0]

    def run(self, run_id):
        row = self._one('SELECT rowid,created_at,as_of,mode,scope,snapshot_json,forecast_json,snapshot_sha256,forecast_sha256,history_sha256 '
                        'FROM run_records WHERE run_id=?', (run_id,))
        for text, digest, name in ((row[5], row[7], 'snapshot'), (row[6], row[8], 'forecast')):
            if hashlib.sha256(text.encode()).hexdigest() != digest:
                raise ValueError(f'Saved {name} hash mismatch: {run_id}')
        return dict(run_id=run_id, insertion_order=row[0], created_at=row[1], as_of=row[2], mode=row[3], scope=row[4],
                    snapshot_json=row[5], forecast_json=row[6], forecast_sha256=row[8], history_sha256=row[9])

    def history(self, digest):
        if self._history[0] != digest:
            self._history = (None, None)  # release the previous one before reading the next
            text = self._one('SELECT history_json FROM histories WHERE history_sha256=?', (digest,))[0]
            if hashlib.sha256(text.encode()).hexdigest() != digest:
                raise ValueError('Saved history hash mismatch: ' + digest)
            self._history = (digest, json.loads(text))
            self.history_reads += 1
        return self._history[1]


class Artifacts:
    """Model artifacts by digest, verified against their name; the last few are kept."""

    def __init__(self, folder):
        self.folder, self.cache = Path(folder), {}

    def get(self, digest):
        if digest not in self.cache:
            raw = (self.folder / (digest + '.json')).read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('Model artifact archive hash mismatch: ' + digest)
            if len(self.cache) >= 4:
                self.cache.pop(next(iter(self.cache)))
            self.cache[digest] = json.loads(raw)
        return self.cache[digest]


class Throttle:
    """Keep this process's disk use to about ``share`` of the time.

    The host's disks use mq-deadline, which ignores I/O classes, so ``ionice -c3``
    does not make reads yield to production: on 2026-11-10 a copy and a reader
    together held the disk until systemd killed a tick at its ten-minute limit.
    After each step that read, sleep ``(1 - share) / share`` times the reading
    time, at least ``after_history`` seconds after a history; and before each
    step, ``guard`` waits while production's tick looks stuck (``wait_for_tick``).
    """

    def __init__(self, share=1 / 3, after_history=1., guard=None, log=print):
        self.factor, self.after_history, self.guard, self.log = (1 - share) / share, after_history, guard, log
        self.slept = self.waited = 0.

    def rest(self, io_seconds, history_read=False):
        pause = max(io_seconds * self.factor, self.after_history if history_read else 0.)
        if pause > 0:
            time.sleep(pause)
            self.slept += pause

    def check(self):
        if self.guard:
            self.waited += wait_for_tick(self.guard, log=self.log)


def tick_stalled(tick, now, limit=120.):
    """Whether production's last completed tick (``last-tick.json``) started more than ``limit`` seconds ago.

    The file is written when a tick ends, and ticks start every minute, so a
    start older than two minutes means the running tick is slow or failing.
    """
    try:
        return (now - parse_time(tick['started_at'])).total_seconds() > limit
    except (KeyError, TypeError, ValueError):
        return False


def wait_for_tick(path, limit=120., poll=15., log=print):
    """While the tick looks stalled, wait until a tick completes (a newer ``finished_at``); returns the seconds waited."""
    def read():
        try:
            return json.loads(Path(path).read_text())
        except (OSError, ValueError):
            return {}
    tick, waited = read(), 0.
    if not tick_stalled(tick, datetime.now(timezone.utc), limit):
        return 0.
    seen = tick.get('finished_at')
    log(f"production tick: the last completed tick started at {tick.get('started_at')}; pausing until a tick completes", flush=True)
    while True:
        time.sleep(poll)
        waited += poll
        tick = read()
        if tick.get('finished_at') != seen or not tick_stalled(tick, datetime.now(timezone.utc), limit):
            log(f"production tick: completed at {tick.get('finished_at')}; continuing after {waited:.0f} s", flush=True)
            return waited


def code_identity():
    from nba_forecast import margins
    from nba_forecast.model_versions import REFINED_VERSION
    from nba_forecast.runner_release import package_digest
    def git(*args):
        try:
            return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    inside = git('rev-parse', '--show-toplevel')
    here = inside is not None and Path(inside).resolve() == ROOT  # an export under some other repository is not that repository
    status = git('status', '--porcelain', '--', 'nba_forecast') if here else None
    # A `git archive` export has no repository; the exporter may leave the commit in a COMMIT file (unverified, labelled so).
    exported = (ROOT / 'COMMIT').read_text().strip() if not here and (ROOT / 'COMMIT').exists() else None
    return dict(root=str(ROOT), commit=git('rev-parse', 'HEAD') if here else exported,
                commit_source='git' if here else 'COMMIT file of an export' if exported else None,
                branch=git('rev-parse', '--abbrev-ref', 'HEAD') if here else None,
                package_dirty=None if status is None else bool(status), package_sha256=package_digest(ROOT / 'nba_forecast'),
                script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), model_version=REFINED_VERSION,
                margin_method=margins.METHOD, margin_parameters=dict(margins.PARAMETERS),
                margin_family=getattr(margins, 'CALIBRATION_FAMILY', None))


def family_of(method, settlement, parameters):
    """The margin calibration family a run recorded (``margins.calibration_family``), or None without its parameters."""
    from nba_forecast import margins
    if not method or not isinstance(parameters, dict) or not hasattr(margins, 'calibration_family'):
        return None
    return margins.calibration_family(method, settlement, parameters)


def short_method(method):
    match = re.search(r'_(v\d+)$', method or '')
    return match[1] if match else (method or 'none')


# ---------------------------------------------------------------- rebuilding prepared inputs

class FinalsView:
    """A directory ``prepare`` can read: the copy's line captures, and the environment-final records a run read.

    ``prepare`` takes a data directory and reads ``market/game-lines`` and
    ``environment-finals/records`` from it. Here both are symbolic links into the
    copy (nothing is written into the copy); the records are only the ones named.
    """

    def __init__(self, copy, root):
        self.copy, self.root = Path(copy), Path(root)
        self.records = self.root / 'environment-finals' / 'records'
        self.records.mkdir(parents=True, exist_ok=True)
        market = self.root / 'market'
        if not market.is_symlink():
            market.symlink_to(self.copy / 'market', target_is_directory=True)

    def show(self, digests):
        for link in self.records.iterdir():
            link.unlink()
        for digest in sorted(set(digests)):
            (self.records / (digest + '.json')).symlink_to(self.copy / 'environment-finals' / 'records' / (digest + '.json'))
        return self.root


def load_finals(copy):
    """Every environment-final record in the copy, as (content digest, record), each checked against its name."""
    out = []
    for path in sorted((Path(copy) / 'environment-finals' / 'records').glob('*.json')):
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != path.stem:
            raise ValueError('Environment final archive hash mismatch: ' + path.name)
        out.append((path.stem, json.loads(raw)))
    return out


def capture_audit(copy):
    """What the ``retrieved_at`` bound on line captures rests on, counted in the copy.

    ``market_lines.closing_margins`` reads, for games tipped before a forecast,
    the captures retrieved by its ``as_of``. That reproduces what the forecast
    could read if every capture was appended before its game tipped (the tick
    captures only games not yet started) and a recovered close was appended no
    earlier than its recorded retrieval.
    """
    from nba_forecast.market_lines import read_game_lines
    out = Counter()
    late = []
    for path in sorted((Path(copy) / 'market' / 'game-lines').glob('*.jsonl')):
        try:
            records = read_game_lines(copy, path.stem)
        except ValueError:
            out['damaged_files'] += 1
            continue
        for r in records:
            out['captures'] += 1
            if r.get('recovered_close'):
                out['recovered_closes'] += 1
                if parse_time(r['captured_at']) > parse_time(r['retrieved_at']):
                    out['recovered_captured_after_retrieval'] += 1
            elif parse_time(r['captured_at']) >= parse_time(r['start_time']):
                out['captured_at_or_after_tip'] += 1
                late.append(path.stem)
            elif parse_time(r['retrieved_at']) >= parse_time(r['start_time']):
                out['retrieved_at_or_after_tip'] += 1
    return dict(out, games_with_a_capture_after_tip=sorted(set(late)))


def rebuild_enhancements(copy, snapshot, artifact, code, *, finals, view=None):
    """``snapshot['model_enhancements']`` as this code prepares it as of the run's ``prepared_at``, with the as-of evidence.

    The run's own artifact supplies the team games and the fields ``prepare``
    copies. ``prepare`` reads the line captures and bounds them by
    ``retrieved_at``; with a ``view`` it reads only the environment-final
    records the run itself read, otherwise every record in the copy bounded by
    ``known_at``. ``finals`` (``load_finals``) is used only to report what the
    ``known_at`` bound alone would choose. The comparison returned says whether
    preparing again reproduced what the run itself saved.
    """
    from nba_forecast.enhancements import prepare
    frozen = snapshot['model_enhancements']
    if len(snapshot['games']) != 1:
        raise ValueError('A prepared model environment covers exactly one game')
    read = {s['game_id']: s['sha256'] for s in frozen.get('live_team_game_sources') or []}
    if view is not None and 'live_team_game_sources' not in frozen:
        raise ValueError('the run did not record which environment finals it read; use --finals known-at')
    directory = view.show(read.values()) if view is not None else copy
    rebuilt = prepare(artifact, frozen['artifact_sha256'], snapshot['games'][0], frozen['prepared_at'], directory=directory)
    by_known_at = as_of_finals(finals, frozen['prepared_at'])
    note = 'none'
    if rebuilt.get('margin_calibration') is not None:
        from nba_forecast.prospective_margin_calibration import validate_margin_calibration
        note = 'kept'
        if not validate_margin_calibration(rebuilt['margin_calibration'], code['margin_family'], snapshot['as_of']):
            rebuilt['margin_calibration'], note = None, 'dropped (not of this code\'s family)'
    fm, rm = frozen.get('margin_inputs') or {}, rebuilt.get('margin_inputs') or {}
    old = {s['game_id']: s['sha256'] for s in frozen.get('live_team_game_sources') or []}
    new = {s['game_id']: s['sha256'] for s in rebuilt.get('live_team_game_sources') or []}
    same = lambda key: canonical(frozen.get(key)) == canonical(rebuilt.get(key))
    closes = (fm.get('market_strength') or {}).get('closing_lines_sha256')
    proof = dict(
        finals_source='records the run read' if view is not None else 'every record in the copy, by known_at',
        # What the known_at bound alone would have chosen against what the run read: records it could not have read.
        known_at_bound_adds=sorted(g for g in by_known_at if g not in read),
        known_at_bound_other_version=sorted(g for g in by_known_at if g in read and by_known_at[g] != read[g]),
        known_at_bound_drops=sorted(g for g in read if g not in by_known_at),
        prepared_at=frozen['prepared_at'], artifact_sha256=frozen['artifact_sha256'],
        run_package_sha256=artifact.get('runner_package_sha256'),
        same_release=artifact.get('runner_package_sha256') == code['package_sha256'],
        same_margin_family=fm.get('method') == code['margin_method'] and fm.get('parameters') == code['margin_parameters'],
        model_enhancements_identical=canonical(frozen) == canonical(rebuilt),
        environment_identical=same('environment'), live_team_game_sources_identical=same('live_team_game_sources'),
        margin_inputs_identical=same('margin_inputs'),
        frozen_margin_inputs_sha256=fm.get('inputs_sha256'), rebuilt_margin_inputs_sha256=rm.get('inputs_sha256'),
        # Independent of the margin method: the finals accepted and the dispersion fitted on them.
        margin_sources_identical=fm.get('source_sha256') == rm.get('source_sha256'),
        margin_dispersion_identical=all(fm.get(k) == rm.get(k) for k in ('sample_count', 'standard_deviation', 'historical_mean_margin', 'margin_variance')),
        # Only a run of a method that reads closes recorded which ones it read.
        closing_lines_identical=None if closes is None else closes == (rm.get('market_strength') or {}).get('closing_lines_sha256'),
        artifact_fields_identical=all(same(k) for k in ARTIFACT_INPUTS if k != 'margin_calibration'),
        finals_only_in_rebuilt=sorted(set(new) - set(old)), finals_only_in_frozen=sorted(set(old) - set(new)),
        finals_other_version=sorted(g for g in set(old) & set(new) if old[g] != new[g]),
        margin_calibration=note,
        rebuilt_market_strength={k: (rm.get('market_strength') or {}).get(k) for k in ('priced_games', 'rated_teams')})
    return rebuilt, proof


# ---------------------------------------------------------------- scoring

def _actual_rows(copy, game, predictions):
    """The final rows a run is evaluated on, assembled as the tick does (``operations._evaluate_runs``)."""
    from nba_forecast.operations import _absent_from_final
    rows = json.loads((Path(copy) / 'finals' / (game['game_id'] + '.json')).read_text())
    keys = {(p['game_id'], p['player_id'], p['team_id']) for p in predictions}
    actuals = [r for r in rows if (r['game_id'], r['player_id'], r['team_id']) in keys]
    actuals.extend(_absent_from_final(rows, predictions))
    return rows, sorted(actuals, key=lambda r: (r['game_id'], r['player_id']))


PLAYER_FIELDS = ('player_id', 'team_id', 'forecast_status', 'actual_status', 'played', 'participation_probability',
                 'predicted_points', 'actual_points', 'predicted_minutes', 'actual_minutes', 'points_error', 'minutes_error',
                 'interval80_contains_actual', 'interval80_width')


def score_forecast(copy, season, game, stage, run, forecast, snapshot):
    """One forecast of one game against its final and its closing lines, by the reports' own rules.

    Margin, total and winner come from ``market_lines.market_report`` (the
    published margin is the margin forecast's location, else the player sum),
    player rows from ``store._report`` on the finals the tick evaluates.
    """
    from nba_forecast.market_lines import market_report
    gid = game['game_id']
    predictions = [p for p in forecast['predictions'] if p['game_id'] == gid]
    totals = next((t for t in forecast.get('game_totals', []) if t['game_id'] == gid), None)
    margin = next((m for m in forecast.get('margin_forecasts', []) if m['game_id'] == gid), None)
    selection = dict(run_id=run['run_id'], totals=totals, margin_forecast=margin,
                     lineups=snapshot.get('preflight', {}).get('lineups'))
    report = market_report(copy, dict(season=season, games=[game]), run['as_of'], selected={(gid, stage): selection})
    rows = report['games']
    # As the market report reads them: totals saved before team sides were recorded name the team.
    teams = {t.get('side') or ('home' if t['team_id'] == game['home_team_id'] else 'away'): t.get('points_mean')
             for t in (totals or {}).get('teams', [])}
    out = dict(margin=None, margin_basis=None, total=None, home_win=None, home_points=teams.get('home'), away_points=teams.get('away'),
               totals_status=(totals or {}).get('status'), totals_schema=(totals or {}).get('schema_version'),
               margin_status=(margin or {}).get('status'), margin_reason=(margin or {}).get('reason'),
               margin_method=(margin or {}).get('method'),
               **{k: (margin or {}).get(k) for k in ('player_sum_margin', 'team_rating_margin', 'rating_weight', 'absence_rating_adjustment',
                                                    'standard_deviation', 'calibration_status')})
    if rows:
        out.update(margin=rows[0]['model_margin'], margin_basis=rows[0]['model_margin_basis'], total=rows[0]['model_total'],
                   home_win=rows[0]['model_home_win'], player_sum_margin=rows[0]['player_sum_margin'])
    closes = [dict(provider=r['provider'], provider_id=r['provider_id'], market_margin=r.get('market_margin'), total_line=r.get('total_line'),
                   market_home_win=r.get('market_home_win'), line_captured_at=r.get('line_captured_at'), close_recovered=r.get('close_recovered'),
                   kalshi_margin=r.get('kalshi_margin')) for r in rows]
    finals, actuals = _actual_rows(copy, game, predictions)
    points = {side: sum(r['pts'] for r in finals if r['team_id'] == game[side + '_team_id']) for side in ('home', 'away')}
    periods = {r.get('game_period') for r in finals}
    actual = dict(home_points=points['home'], away_points=points['away'], margin=points['home'] - points['away'],
                  total=points['home'] + points['away'], overtime=max(periods) > 4 if periods and None not in periods else None)
    scored = store._report(run, dict(forecast, predictions=predictions), actuals, 0, run['created_at'])
    players = [{k: m[k] for k in PLAYER_FIELDS} for m in scored['matched']]
    return out, closes, actual, players, report['errors']


def original_identity(snapshot, forecast, artifact, gid):
    enhancement = snapshot.get('model_enhancements') or {}
    inputs = enhancement.get('margin_inputs') or {}
    margin = next((m for m in forecast.get('margin_forecasts', []) if m['game_id'] == gid), None) or {}
    method = margin.get('method') or inputs.get('method')
    return dict(model_version=forecast.get('model_version'), model_key=forecast.get('model_key'),
                artifact_sha256=enhancement.get('artifact_sha256'), package_sha256=(artifact or {}).get('runner_package_sha256'),
                margin_method=method, era=short_method(method), rating_weight=(inputs.get('parameters') or {}).get('rating_weight'),
                margin_family=family_of(method, margin.get('settlement') or inputs.get('settlement'), inputs.get('parameters')))


def replay_game(copy, ledger, artifacts, season, game, chosen, modes, code, *, finals=(), view=None):
    """Rows for one game, one per requested mode: published and replayed numbers side by side."""
    from nba_forecast.model import forecast as run_forecast
    began = time.monotonic()
    gid = game['game_id']
    run = ledger.run(chosen['run_id'])
    snapshot, stored = json.loads(run['snapshot_json']), json.loads(run['forecast_json'])
    enhancement = snapshot.get('model_enhancements') or {}
    artifact = artifacts.get(enhancement['artifact_sha256']) if enhancement.get('artifact_sha256') else None
    history = ledger.history(run['history_sha256'])
    tip = parse_time(game['start_time'])
    published, closes, actual, published_players, errors = score_forecast(copy, season, game, chosen['stage'], run, stored, snapshot)
    base = dict(game_id=gid, start_time=game['start_time'], date=_eastern(game['start_time']),
                home_team_id=game['home_team_id'], away_team_id=game['away_team_id'],
                matchup=f"{game.get('away_tricode', game['away_team_id'])} @ {game.get('home_tricode', game['home_team_id'])}",
                run_id=run['run_id'], stage=chosen['stage'], created_at=run['created_at'], as_of=run['as_of'],
                minutes_before_tip=round((tip - parse_time(run['created_at'])).total_seconds() / 60, 1),
                pregame_runs=chosen.get('pregame_runs'), history_sha256=run['history_sha256'],
                original=original_identity(snapshot, stored, artifact, gid), actual=actual, closes=closes,
                published=published, market_errors=errors)
    base['original']['same_release'] = base['original']['package_sha256'] == code['package_sha256']
    rows = {}
    for mode in modes:
        started = time.monotonic()
        row = dict(base, mode=mode, code_commit=code['commit'], code_package_sha256=code['package_sha256'])
        try:
            used = json.loads(run['snapshot_json'])  # the forecast must not see another mode's objects
            if mode == 'rebuilt':
                if artifact is None or 'margin_inputs' not in enhancement:
                    raise ValueError('the run has no refined model artifact to prepare from')
                prepared = time.monotonic()
                used['model_enhancements'], row['rebuild'] = rebuild_enhancements(copy, used, artifact, code, finals=finals, view=view)
                row['rebuild_seconds'] = round(time.monotonic() - prepared, 2)
            replayed = run_forecast(used, history)
            text = store._canonical(replayed)
            row['identical'] = text == run['forecast_json']
            row['replayed_forecast_sha256'] = hashlib.sha256(text.encode()).hexdigest()
            if not row['identical']:
                row['differences'] = differences(stored, json.loads(text))
            row['replayed'], _, _, replayed_players, more = score_forecast(copy, season, game, chosen['stage'], run, json.loads(text), used)
            row['market_errors'] = errors + [e for e in more if e not in errors]
            row['players'] = pair_players(published_players, replayed_players)
        except Exception as exc:  # one run that this code cannot forecast is a result, not a reason to stop
            row.update(replayed=None, identical=False, error=f'{type(exc).__name__}: {exc}',
                       players=pair_players(published_players, []))
        row['elapsed_seconds'] = round(time.monotonic() - started, 2)
        rows[mode] = row
    for row in rows.values():  # reading the run and its history, scoring and every mode's forecast
        row['game_seconds'] = round(time.monotonic() - began, 2)
    return rows


def pair_players(published, replayed):
    """Per player: the final, and the published and replayed forecast rows as ``store._report`` matched them."""
    shared = ('player_id', 'team_id', 'actual_status', 'played', 'actual_points', 'actual_minutes')
    own = [k for k in PLAYER_FIELDS if k not in shared]
    right = {p['player_id']: p for p in replayed}
    out = []
    for p in published:
        other = right.pop(p['player_id'], None)
        out.append(dict({k: p[k] for k in shared}, published={k: p[k] for k in own}, replayed={k: other[k] for k in own} if other else None))
    for p in right.values():
        out.append(dict({k: p[k] for k in shared}, published=None, replayed={k: p[k] for k in own}))
    return out


# ---------------------------------------------------------------- summary

def flatten(games):
    """One item per game and closing line, with the absolute errors every table pairs."""
    items = []
    for g in games:
        published, replayed, actual = g['published'], g.get('replayed') or {}, g['actual']
        for close in g['closes']:
            item = dict(game_id=g['game_id'], date=g['date'], era=g['original']['era'], family=g['original']['margin_family'],
                        rating_weight=g['original']['rating_weight'], provider_id=close['provider_id'], run_id=g['run_id'])
            for name, side in (('published', published), ('replayed', replayed)):
                if _finite(side.get('margin')):
                    item[name + '_margin'] = side['margin']
                    item[name + '_margin_error'] = abs(side['margin'] - actual['margin'])
                if _finite(side.get('total')):
                    item[name + '_total_error'] = abs(side['total'] - actual['total'])
                if _finite(side.get('home_win')) and actual['margin'] != 0:
                    item[name + '_brier'] = (side['home_win'] - (actual['margin'] > 0)) ** 2
            if _finite(close.get('market_margin')):
                item['close_margin'] = close['market_margin']
                item['close_margin_error'] = abs(close['market_margin'] - actual['margin'])
            if _finite(close.get('total_line')):
                item['close_total_error'] = abs(close['total_line'] - actual['total'])
            if _finite(close.get('market_home_win')) and actual['margin'] != 0:
                item['close_brier'] = (close['market_home_win'] - (actual['margin'] > 0)) ** 2
            if 'published_margin' in item and 'close_margin' in item:
                item['outlier'] = abs(item['published_margin'] - item['close_margin']) >= OUTLIER
            items.append(item)
    return items


def game_outlier(g):
    """Whether the published margin stood OUTLIER or more from the game's first closing line; None without both."""
    close = next((c['market_margin'] for c in g['closes'] if _finite(c.get('market_margin'))), None)
    margin = g['published'].get('margin')
    return abs(margin - close) >= OUTLIER if _finite(margin) and close is not None else None


def _game_item(g, **extra):
    item = dict(game_id=g['game_id'], date=g['date'], era=g['original']['era'], family=g['original']['margin_family'],
                rating_weight=g['original']['rating_weight'], **extra)
    outlier = game_outlier(g)
    if outlier is not None:
        item['outlier'] = outlier
    return item


def team_items(games):
    """One item per team: absolute error of its points against the final score (overtime included, as the daily review scores it)."""
    items = []
    for g in games:
        for side in ('home', 'away'):
            item = _game_item(g, team_id=g[side + '_team_id'])
            for name in ('published', 'replayed'):
                value = (g.get(name) or {}).get(side + '_points')
                if _finite(value):
                    item[name + '_error'] = abs(value - g['actual'][side + '_points'])
                    item[name + '_signed'] = value - g['actual'][side + '_points']
            items.append(item)
    return items


def player_items(games):
    """One item per scored player: errors where he was projected and played, the participation Brier wherever a probability was given."""
    items = []
    for g in games:
        for p in g.get('players', []):
            item = _game_item(g, player_id=p['player_id'])
            for name in ('published', 'replayed'):
                side = p.get(name)
                if not side:
                    continue
                if side['forecast_status'] == 'projected' and p['played']:
                    item[name + '_points_error'] = abs(side['points_error'])
                    item[name + '_minutes_error'] = abs(side['minutes_error'])
                    item[name + '_points_signed'] = side['points_error']
                    item[name + '_minutes_signed'] = side['minutes_error']
                if side['participation_probability'] is not None and p['played'] is not None:
                    item[name + '_participation_brier'] = (side['participation_probability'] - p['played']) ** 2
            items.append(item)
    return items


def _three(items, stem):
    """Published, replayed and the close on the items that have all three, and the three paired differences."""
    keys = [f'{name}_{stem}' for name in ('published', 'replayed', 'close')]
    rows = [i for i in items if all(_finite(i.get(k)) for k in keys)]
    out = dict(n=len(rows), dates=len({r['date'] for r in rows}))
    for name, key in zip(('published', 'replayed', 'close'), keys):
        out[name] = math.fsum(r[key] for r in rows) / len(rows) if rows else None
    for label, a, b in (('replayed_minus_published', keys[1], keys[0]), ('published_minus_close', keys[0], keys[2]),
                        ('replayed_minus_close', keys[1], keys[2])):
        pair = paired(rows, a, b)
        out[label] = dict(mean=pair['mean'], se=pair['se'])
    return out


def _two(items, stem):
    pair = paired(items, f'replayed_{stem}', f'published_{stem}')
    return dict(n=pair['n'], dates=pair['dates'], published=pair['second'], replayed=pair['first'],
                replayed_minus_published=dict(mean=pair['mean'], se=pair['se']))


def _bias(items, key):
    rows = [i for i in items if _finite(i.get(key))]
    mean, se = mean_se([r[key] for r in rows], [r['date'] for r in rows])
    return dict(n=len(rows), mean=mean, se=se)


def groups(items):
    """All games, the games within OUTLIER of the close when published, the outliers, and each era and family of the original run."""
    out = [('all', items)]
    if any('outlier' in i for i in items):
        out.append((f'without |published - close| >= {OUTLIER:g}', [i for i in items if i.get('outlier') is False]))
        out.append((f'only |published - close| >= {OUTLIER:g}', [i for i in items if i.get('outlier')]))
    for era in sorted({i['era'] for i in items}):
        out.append(('original run ' + era, [i for i in items if i['era'] == era]))
    families = sorted({(i['era'], str(i.get('rating_weight')), i.get('family') or 'not recorded') for i in items})
    if len(families) > len({i['era'] for i in items}):
        for era, weight, family in families:
            label = f"original run {era}, rating weight {weight}, family {family.split('|')[-1][:8]}"
            out.append((label, [i for i in items if i['era'] == era and (i.get('family') or 'not recorded') == family]))
    return out


def summarize(output):
    games = [g for g in output['games'] if g.get('replayed')]
    lines, teams, players = flatten(games), team_items(games), player_items(games)
    summary = dict(se=SE_LABEL, games_in_output=len(output['games']), games_replayed=len(games),
                   replay_errors=[dict(game_id=g['game_id'], error=g['error']) for g in output['games'] if g.get('error')],
                   games_without_a_closing_line=sorted(g['game_id'] for g in games if not g['closes']),
                   replayed_margin_basis=dict(Counter(str(g['replayed'].get('margin_basis')) for g in games if g['closes'])),
                   published_margin_basis=dict(Counter(str(g['published'].get('margin_basis')) for g in games if g['closes'])),
                   replayed_margin_reasons=dict(Counter(str(g['replayed'].get('margin_reason')) for g in games if g['replayed'].get('margin_status') != 'estimated')),
                   outliers=[dict(game_id=i['game_id'], date=i['date'], era=i['era'], published_minus_close=i['published_margin'] - i['close_margin'],
                                  replayed_minus_close=(i['replayed_margin'] - i['close_margin']) if 'replayed_margin' in i else None)
                             for i in lines if i.get('outlier')],
                   margin=[dict(group=label, **_three(rows, 'margin_error')) for label, rows in groups(lines)],
                   total=[dict(group=label, **_three(rows, 'total_error')) for label, rows in groups(lines)],
                   winner_brier=[dict(group=label, **_three(rows, 'brier')) for label, rows in groups(lines)],
                   team_points=[dict(group=label, **_two(rows, 'error'), published_bias=_bias(rows, 'published_signed'),
                                     replayed_bias=_bias(rows, 'replayed_signed')) for label, rows in groups(teams)],
                   player_points=[dict(group=label, **_two(rows, 'points_error'), published_bias=_bias(rows, 'published_points_signed'),
                                       replayed_bias=_bias(rows, 'replayed_points_signed')) for label, rows in groups(players)],
                   player_minutes=[dict(group=label, **_two(rows, 'minutes_error'), published_bias=_bias(rows, 'published_minutes_signed'),
                                        replayed_bias=_bias(rows, 'replayed_minutes_signed')) for label, rows in groups(players)],
                   participation_brier=[dict(group=label, **_two(rows, 'participation_brier')) for label, rows in groups(players)],
                   self_test=self_test(output), elapsed=dict(
                       seconds=round(math.fsum(g.get('elapsed_seconds', 0) for g in output['games']), 1),
                       per_game=round(math.fsum(g.get('elapsed_seconds', 0) for g in output['games']) / len(output['games']), 2) if output['games'] else None,
                       wall_seconds_all_modes=round(math.fsum(g.get('game_seconds', 0) for g in output['games']), 1)))
    if output['mode'] == 'rebuilt':
        summary['as_of_proof'] = as_of_proof(games)
    return summary


def self_test(output):
    """Whether replaying a run reproduces its saved forecast, by the original run's method and by whether this release made it."""
    out = []
    by = defaultdict(list)
    for g in output['games']:
        by[(g['original']['era'], bool(g['original'].get('same_release')))].append(g)
    for (era, same), rows in sorted(by.items()):
        different = [g for g in rows if not g.get('identical')]
        paths = Counter()
        for g in different:
            for d in (g.get('differences') or {}).get('listed', []):
                paths[d['path']] += 1
        out.append(dict(era=era, made_by_this_release=same, games=len(rows), identical=len(rows) - len(different),
                        different_games=[g['game_id'] for g in different][:40],
                        differing_paths=[dict(path=p, games=n) for p, n in paths.most_common(25)]))
    return out


def as_of_proof(games):
    """Does preparing again as of each run reproduce what the run saved? Counted over the runs that can show it."""
    proofs = [g['rebuild'] for g in games if g.get('rebuild')]
    def count(rows, key):
        return dict(runs=len(rows), identical=sum(bool(r[key]) for r in rows))
    release = [p for p in proofs if p['same_release']]
    family = [p for p in proofs if p['same_margin_family']]
    with_closes = [p for p in proofs if p['closing_lines_identical'] is not None]
    out = dict(
        runs=len(proofs),
        this_release=dict(count(release, 'model_enhancements_identical'), input='model_enhancements (everything prepare returns)'),
        this_margin_family=dict(count(family, 'margin_inputs_identical'), input='margin_inputs (same inputs_sha256)'),
        environment=count(proofs, 'environment_identical'), live_team_game_sources=count(proofs, 'live_team_game_sources_identical'),
        margin_sources=count(proofs, 'margin_sources_identical'), margin_dispersion=count(proofs, 'margin_dispersion_identical'),
        closing_lines=dict(count(with_closes, 'closing_lines_identical'), note='runs whose saved margin inputs record the closes they read'),
        artifact_fields=count(proofs, 'artifact_fields_identical'),
        margin_calibration=dict(Counter(p['margin_calibration'] for p in proofs)),
        finals_source=dict(Counter(p['finals_source'] for p in proofs)),
        known_at_bound_adds=dict(runs=sum(bool(p['known_at_bound_adds']) for p in proofs),
                                 games=sorted({g for p in proofs for g in p['known_at_bound_adds']})),
        known_at_bound_other_version=dict(runs=sum(bool(p['known_at_bound_other_version']) for p in proofs),
                                          games=sorted({g for p in proofs for g in p['known_at_bound_other_version']})),
        known_at_bound_drops=dict(runs=sum(bool(p['known_at_bound_drops']) for p in proofs),
                                  games=sorted({g for p in proofs for g in p['known_at_bound_drops']})),
        finals_only_in_rebuilt=sum(bool(p['finals_only_in_rebuilt']) for p in proofs),
        finals_only_in_frozen=sum(bool(p['finals_only_in_frozen']) for p in proofs),
        finals_other_version=sum(bool(p['finals_other_version']) for p in proofs))
    out['verdict'] = ('PROOF MISSING: no run made by this release is in the copy, so reproducing a saved input exactly could not be shown'
                      if not release else
                      'reproduced' if out['this_release']['identical'] == len(release) else 'NOT REPRODUCED for every run of this release')
    return out


def _pm(value):
    return 'n/a' if value is None or value['mean'] is None else (f"{value['mean']:+.3f}" + (f" ± {value['se']:.3f}" if value['se'] is not None else ' ± n/a'))


def _level(value, digits=2):
    return 'n/a' if value is None else f'{value:.{digits}f}'


def print_summary(output):
    s = output['summary']
    code = output['code']
    print(f"\nlive replay, mode {output['mode']}, {output['which']} pregame run per game; code {code['commit'] or 'no commit'}"
          f"{' (package modified)' if code['package_dirty'] else ''} package {code['package_sha256'][:8]}, margin method {short_method(code['margin_method'])}")
    print(f"copy {output['copy']} (copied {output.get('copied_at') or 'time not recorded'}); ± is {SE_LABEL}")
    print(f"games replayed {s['games_replayed']} of {s['games_in_output']} in the output; without a closing line {len(s['games_without_a_closing_line'])}; "
          f"replay errors {len(s['replay_errors'])}")
    print(f"published margin basis {s['published_margin_basis']}; replayed margin basis {s['replayed_margin_basis']}"
          + (f"; replayed without a margin forecast: {s['replayed_margin_reasons']}" if s['replayed_margin_reasons'] else ''))
    if output['mode'] == 'rebuilt':
        print('rebuilt: ' + ', '.join('model_enhancements.' + k for k in REBUILT_INPUTS) + ' (enhancements.prepare, as of each run\'s prepared_at)')
    print('frozen: ' + ', '.join(([] if output['mode'] == 'rebuilt' else ['model_enhancements']) + list(FROZEN_INPUTS)))
    for title, key, digits in (('MARGIN, absolute error against the final margin', 'margin', 2), ('GAME TOTAL, absolute error', 'total', 2),
                               ('WINNER, Brier score of the home-win probability', 'winner_brier', 4)):
        print(f'\n{title}\n  {"group":<58}{"n":>4}{"dates":>6}{"published":>10}{"replayed":>9}{"close":>8}   replayed−published   published−close     replayed−close')
        for row in s[key]:
            print(f"  {row['group']:<58}{row['n']:>4}{row['dates']:>6}{_level(row['published'], digits):>10}{_level(row['replayed'], digits):>9}{_level(row['close'], digits):>8}"
                  f"   {_pm(row['replayed_minus_published']):<20} {_pm(row['published_minus_close']):<18} {_pm(row['replayed_minus_close'])}")
    for title, key in (('TEAM POINTS, absolute error against the final score (bias = forecast minus actual)', 'team_points'),
                       ('PLAYER POINTS, projected players who played', 'player_points'), ('PLAYER MINUTES, projected players who played', 'player_minutes'),
                       ('PARTICIPATION, Brier score of P(plays)', 'participation_brier')):
        print(f'\n{title}\n  {"group":<58}{"n":>6}{"dates":>6}{"published":>10}{"replayed":>9}   replayed−published' + ('   published bias   replayed bias' if key != 'participation_brier' else ''))
        for row in s[key]:
            digits = 4 if key == 'participation_brier' else 2
            tail = f"   {_pm(row['published_bias']):<16} {_pm(row['replayed_bias'])}" if 'published_bias' in row else ''
            print(f"  {row['group']:<58}{row['n']:>6}{row['dates']:>6}{_level(row['published'], digits):>10}{_level(row['replayed'], digits):>9}   {_pm(row['replayed_minus_published']):<20}{tail}")
    if s['outliers']:
        print(f'\ngames where the published margin stood {OUTLIER:g}+ points from the close (published − close; replayed − close):')
        for o in s['outliers']:
            print(f"  {o['date']} {o['game_id']} {o['era']}: {o['published_minus_close']:+.1f}; " + ('n/a' if o['replayed_minus_close'] is None else f"{o['replayed_minus_close']:+.1f}"))
    print('\nSELF-TEST (replayed forecast byte-identical to the saved one), by the original run\'s margin method:')
    for row in s['self_test']:
        print(f"  {row['era']:<6} made by this release: {str(row['made_by_this_release']):<5} identical {row['identical']} of {row['games']}")
        for d in row['differing_paths'][:8]:
            print(f"      differs in {d['games']} games: {d['path']}")
    if 'as_of_proof' in s:
        p = s['as_of_proof']
        print(f"\nAS-OF PROOF (preparing again as of the run reproduces what the run saved): {p['verdict']}")
        print(f"  runs made by this release: model_enhancements identical {p['this_release']['identical']} of {p['this_release']['runs']}")
        print(f"  runs of this margin method and parameters: margin_inputs identical {p['this_margin_family']['identical']} of {p['this_margin_family']['runs']}")
        print(f"  all {p['runs']} runs: environment {p['environment']['identical']}, live_team_game_sources {p['live_team_game_sources']['identical']}, "
              f"margin finals {p['margin_sources']['identical']}, margin dispersion {p['margin_dispersion']['identical']}, artifact fields {p['artifact_fields']['identical']}")
        print(f"  closes read (runs that recorded them): identical {p['closing_lines']['identical']} of {p['closing_lines']['runs']}")
        a = output.get('capture_audit') or {}
        print(f"  line captures in the copy: {a.get('captures', 0)}; appended at or after their game's tip (recovered closes aside): "
              f"{a.get('captured_at_or_after_tip', 0)}; recovered closes {a.get('recovered_closes', 0)}, of them retrieved before their recovery "
              f"began (the bound would admit them early) {a.get('recovered_captured_after_retrieval', 0)}; damaged files {a.get('damaged_files', 0)}")
        print(f"  environment finals read: {p['finals_source']}; the known_at bound alone would add a record the run did not read in "
              f"{p['known_at_bound_adds']['runs']} runs, choose another version in {p['known_at_bound_other_version']['runs']}, drop one in {p['known_at_bound_drops']['runs']}")
        print(f"  runs where preparing again reads a final the run did not: {p['finals_only_in_rebuilt']}; misses one it read: {p['finals_only_in_frozen']}; "
              f"reads another version of one: {p['finals_other_version']}; margin calibration {p['margin_calibration']}")
    e = s['elapsed']
    print(f"\nforecast time in this mode {e['seconds']} s for {s['games_in_output']} games ({e['per_game']} s a game); "
          f"wall time per game for all modes run together, reading and scoring included: {e['wall_seconds_all_modes']} s in all")


# ---------------------------------------------------------------- compare

def _merge(base_items, candidate_items, key, stems):
    """Base items with each stem's replayed value of both outputs beside it, as ``base_<stem>`` and ``candidate_<stem>``."""
    keyed = {tuple(i[k] for k in key): i for i in candidate_items}
    out = []
    for item in base_items:
        other = keyed.get(tuple(item[k] for k in key), {})
        merged = dict(item)
        for stem in stems:
            if 'replayed_' + stem in item:
                merged['base_' + stem] = item['replayed_' + stem]
            if 'replayed_' + stem in other:
                merged['candidate_' + stem] = other['replayed_' + stem]
        out.append(merged)
    return out


def compare(base, candidate):
    """Candidate minus base on the games both replayed from the same original run."""
    pairs, unpaired = pair_outputs(base, candidate)
    usable = [(a, b) for a, b in pairs if a.get('replayed') and b.get('replayed')]
    left, right = [a for a, _ in usable], [b for _, b in usable]
    lines = _merge(flatten(left), flatten(right), ('game_id', 'provider_id'), ('margin_error', 'total_error', 'brier', 'margin'))
    teams = _merge(team_items(left), team_items(right), ('game_id', 'team_id'), ('error',))
    players = _merge(player_items(left), player_items(right), ('game_id', 'player_id'), ('points_error', 'minutes_error', 'participation_brier'))

    def table(items, stem):
        rows = []
        for label, members in groups(items):
            pair = paired(members, 'candidate_' + stem, 'base_' + stem)
            rows.append(dict(group=label, n=pair['n'], dates=pair['dates'], base=pair['second'], candidate=pair['first'],
                             candidate_minus_base=dict(mean=pair['mean'], se=pair['se'])))
        return rows
    moved = sum(1 for i in lines if _finite(i.get('base_margin')) and _finite(i.get('candidate_margin')) and i['base_margin'] != i['candidate_margin'])
    return dict(tool=TOOL, se=SE_LABEL, base=_header(base), candidate=_header(candidate), games_paired=len(usable),
                unpaired=unpaired + [dict(game_id=a['game_id'], reason='not replayed in one output') for a, b in pairs if not (a.get('replayed') and b.get('replayed'))],
                margins_moved=moved,
                margin=table(lines, 'margin_error'), total=table(lines, 'total_error'), winner_brier=table(lines, 'brier'),
                team_points=table(teams, 'error'), player_points=table(players, 'points_error'),
                player_minutes=table(players, 'minutes_error'), participation_brier=table(players, 'participation_brier'))


def _header(output):
    return dict(mode=output['mode'], which=output['which'], finals=output.get('finals'), copy=output['copy'], commit=output['code']['commit'],
                package_sha256=output['code']['package_sha256'], games=len(output['games']))


def print_comparison(result):
    for name in ('base', 'candidate'):
        h = result[name]
        print(f"{name:<9} mode {h['mode']}, {h['which']} run, code {h['commit'] or 'no commit'} package {h['package_sha256'][:8]}, {h['games']} games, copy {h['copy']}")
    if result['base']['mode'] != result['candidate']['mode'] or result['base']['which'] != result['candidate']['which']:
        print('WARNING: the two outputs differ in mode or in the run chosen per game; the difference below is not a code change alone.')
    print(f"paired on the same original run: {result['games_paired']} games; not paired: {len(result['unpaired'])}"
          + (' ' + json.dumps(Counter(u['reason'] for u in result['unpaired'])) if result['unpaired'] else ''))
    print(f"closing-line rows whose replayed margin differs between the two: {result['margins_moved']}; ± is {SE_LABEL}; negative = the candidate is better")
    for title, key, digits in (('MARGIN, absolute error', 'margin', 2), ('GAME TOTAL, absolute error', 'total', 2), ('WINNER Brier', 'winner_brier', 4),
                               ('TEAM POINTS, absolute error', 'team_points', 2), ('PLAYER POINTS, projected players who played in both', 'player_points', 2),
                               ('PLAYER MINUTES', 'player_minutes', 2), ('PARTICIPATION Brier', 'participation_brier', 4)):
        print(f'\n{title}\n  {"group":<58}{"n":>6}{"dates":>6}{"base":>9}{"candidate":>10}   candidate−base')
        for row in result[key]:
            print(f"  {row['group']:<58}{row['n']:>6}{row['dates']:>6}{_level(row['base'], digits):>9}{_level(row['candidate'], digits):>10}   {_pm(row['candidate_minus_base'])}")


# ---------------------------------------------------------------- driver

def _idle():
    """Lowest CPU priority, and the idle I/O class, which only a BFQ disk scheduler honours (this host's do not; see Throttle)."""
    try:
        os.nice(19)
    except OSError:
        pass
    try:
        subprocess.run(['ionice', '-c3', '-p', str(os.getpid())], capture_output=True, check=False)
    except OSError:
        pass


def _read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def choose_runs(copy, ledger, schedule, which, since=None, until=None, only=None, throttle=None):
    """The run to replay for every finished game, and how the choice was reached."""
    jobs, unreadable = index_jobs((_read_json(Path(copy) / 'state.json', {}) or {}).get('jobs', {}))
    report = dict(job_keys_unreadable=unreadable, finished_games=0, selected=0, without_a_pregame_run=[], metadata_reads=0)
    chosen = {}
    for game in finished_games(schedule, Path(copy) / 'finals'):
        gid, day = game['game_id'], _eastern(game['start_time'])
        if (since and day < since) or (until and day > until) or (only and gid not in only):
            continue
        report['finished_games'] += 1
        tip = parse_time(game['start_time'])
        mine = [c for c in jobs.get(gid, []) if parse_time(c['start']) == tip]
        if throttle:
            throttle.check()
            before = ledger.io_seconds
        # Ledger columns are read only for the candidates the rule can pick: final-stage runs first.
        order = [mine] if which == 'first' else [[c for c in mine if c['stage'] == 'final'], [c for c in mine if c['stage'] != 'final']]
        pick = None
        for batch in order:
            for c in batch:
                meta = ledger.meta(c['run_id'])
                report['metadata_reads'] += 1
                if meta:
                    c.update(meta)
            pick = select_run(game, [c for c in batch if 'created_at' in c], which)
            if pick:
                break
        if throttle:
            throttle.rest(ledger.io_seconds - before)
        if pick is None:
            report['without_a_pregame_run'].append(gid)
            continue
        chosen[gid] = dict(pick, pregame_runs=len(mine))
        report['selected'] += 1
    return chosen, report


def check_against_market_report(copy, chosen):
    """How this tool's choice of run compares with the copy's own market report (same rule, different route)."""
    report = _read_json(Path(copy) / 'market' / 'report.json')
    if not report:
        return dict(status='no market/report.json in the copy')
    theirs = {r['game_id']: r['run_id'] for r in report['games']}
    both = sorted(set(theirs) & set(chosen))
    different = [g for g in both if theirs[g] != chosen[g]['run_id']]
    return dict(report_generated_at=report.get('generated_at'), games_in_report=len(theirs), games_in_both=len(both), same_run=len(both) - len(different),
                different_run=different, only_in_report=sorted(set(theirs) - set(chosen)), only_selected=sorted(set(chosen) - set(theirs)))


def _load_output(path, header):
    path = Path(path)
    if not path.exists():
        return dict(header, games=[])
    saved = json.loads(path.read_text())
    for key in ('tool', 'mode', 'which', 'copy', 'finals'):
        if saved.get(key) != header[key]:
            raise SystemExit(f'{path} holds another run ({key}: {saved.get(key)!r}, not {header[key]!r}); choose another output file.')
    if saved['code']['package_sha256'] != header['code']['package_sha256']:
        raise SystemExit(f'{path} was made by other code (package {saved["code"]["package_sha256"][:8]}); choose another output file.')
    return dict(header, games=saved['games'])


def _save(path, output):
    output['games'].sort(key=lambda g: (g['start_time'], g['game_id']))
    output['generated_at'] = datetime.now(timezone.utc).isoformat()
    output['summary'] = summarize(output)
    atomic_bytes(path, json.dumps(output, sort_keys=True, allow_nan=False).encode())


def run(args):
    copy = Path(args.copy).resolve()
    if 'data/operations/production' in str(copy) and 'scratch' not in str(copy):
        raise SystemExit('Refusing to run on production; point --copy at a copy (--ledger may name a live ledger, read by key only).')
    if not 0 < args.io_share <= 1:
        raise SystemExit('--io-share must be in (0, 1].')
    if not args.no_idle:
        _idle()
    try:
        from nba_forecast.operations import saved_calendar
    except ImportError:  # code older than 2026-10-29 kept only the saved NBA calendar
        def saved_calendar(folder):
            saved = _read_json(Path(folder) / 'schedule' / 'latest.json')
            return (saved, 'saved_nba') if saved else (None, None)
    targets = {mode: Path(path) for mode, path in (('frozen', args.output_frozen), ('rebuilt', args.output_rebuilt)) if path}
    if not targets:
        raise SystemExit('Give --output-frozen and/or --output-rebuilt.')
    code = code_identity()
    schedule, source = saved_calendar(copy)
    if schedule is None:
        raise SystemExit('The copy has no saved calendar.')
    ledger_path = Path(args.ledger).resolve() if args.ledger else copy / 'forecast.sqlite'
    inside_copy = ledger_path.parent == copy
    if not inside_copy:
        print(f'reading runs by run_id from {ledger_path}, read-only (not immutable)', flush=True)
    ledger = Ledger(ledger_path, immutable=inside_copy and not args.live_ledger)
    artifacts = Artifacts(Path(args.artifacts) if args.artifacts else copy / 'model-artifacts')
    guard = None if args.no_guard else Path(args.guard_tick)
    if guard is not None and not guard.exists():
        print(f'no production tick file at {guard}; the tick guard is off', flush=True)
        guard = None
    throttle = Throttle(share=args.io_share, guard=guard)
    finals, view = (), None
    if 'rebuilt' in targets:
        finals = load_finals(copy)
        if args.finals == 'run':
            import tempfile
            view = FinalsView(copy, tempfile.mkdtemp(prefix='live-replay-view-', dir=args.work or targets['rebuilt'].parent))
    copied = copy / '.copied_at'
    started = time.monotonic()
    chosen, selection = choose_runs(copy, ledger, schedule, args.which, args.since, args.until, set(args.games) if args.games else None,
                                    throttle=throttle)
    selection['market_report'] = check_against_market_report(copy, chosen) if args.which == 'final' else None
    selection['seconds'] = round(time.monotonic() - started, 1)
    audit = capture_audit(copy) if 'rebuilt' in targets else None
    outputs = {}
    for mode, path in targets.items():
        header = dict(tool=TOOL, mode=mode, which=args.which, copy=str(copy), copied_at=copied.read_text().strip() if copied.exists() else None,
                      schedule_source=source, season=schedule.get('season'), code=code, selection=selection, se=SE_LABEL,
                      finals=args.finals if mode == 'rebuilt' else None, capture_audit=audit if mode == 'rebuilt' else None,
                      inputs=dict(rebuilt=['model_enhancements.' + k for k in REBUILT_INPUTS] if mode == 'rebuilt' else [],
                                  frozen=dict(FROZEN_INPUTS, **({} if mode == 'rebuilt' else {'model_enhancements': 'everything enhancements.prepare '
                                              'returned at the time: environment, margin inputs (team ratings, dispersion, method and parameters), artifact fields'}))),
                      command=' '.join(sys.argv))
        outputs[mode] = _load_output(path, header)
    done = {mode: {g['game_id'] for g in out['games']} for mode, out in outputs.items()}
    pending = [gid for gid in chosen if any(gid not in done[mode] for mode in targets)]
    print(f"{selection['finished_games']} finished games, {selection['selected']} with a pregame run ({selection['metadata_reads']} run rows read in "
          f"{selection['seconds']} s); {len(pending)} to replay, {len(chosen) - len(pending)} already in the output", flush=True)
    if selection['market_report'] and 'same_run' in selection['market_report']:
        m = selection['market_report']
        print(f"against the copy's market report: same run in {m['same_run']} of {m['games_in_both']} games; different {m['different_run']}; "
              f"only in the report {len(m['only_in_report'])}; only selected here {len(m['only_selected'])}", flush=True)
    if args.limit is not None:
        pending = sorted(pending, key=lambda g: chosen[g]['created_at'])[:args.limit]
    # One history at a time, each read once: games are taken history by history.
    before = ledger.io_seconds
    digests = {gid: ledger.history_digest(chosen[gid]['run_id']) for gid in pending}
    throttle.rest(ledger.io_seconds - before)
    first_seen = {}
    for gid in sorted(pending, key=lambda g: chosen[g]['created_at']):
        first_seen.setdefault(digests[gid], len(first_seen))
    pending.sort(key=lambda g: (first_seen[digests[g]], chosen[g]['created_at']))
    games = {g['game_id']: g for g in schedule['games']}
    try:
        _replay_all(copy, ledger, artifacts, schedule, games, chosen, pending, targets, outputs, done, code, finals, view, throttle)
    finally:
        if view is not None:
            import shutil
            shutil.rmtree(view.root)  # symbolic links only; their targets in the copy are untouched
    for mode, path in targets.items():
        outputs[mode]['history_reads'] = ledger.history_reads
        outputs[mode]['throttle'] = dict(io_share=args.io_share, slept_seconds=round(throttle.slept, 1),
                                         waited_for_tick_seconds=round(throttle.waited, 1), guard=str(guard) if guard else None,
                                         ledger=str(ledger_path), ledger_immutable=ledger.immutable, artifacts=str(artifacts.folder))
        _save(path, outputs[mode])
        print_summary(outputs[mode])
        print(f'\nwrote {path}')


def _replay_all(copy, ledger, artifacts, schedule, games, chosen, pending, targets, outputs, done, code, finals, view, throttle):
    began = time.monotonic()
    for index, gid in enumerate(pending, 1):
        modes = [mode for mode in targets if gid not in done[mode]]
        throttle.check()
        before, histories = ledger.io_seconds, ledger.history_reads
        rows = replay_game(copy, ledger, artifacts, schedule.get('season'), games[gid], chosen[gid], modes, code, finals=finals, view=view)
        # Reading the run and its history, and the small files prepare and the scoring read, count as disk time.
        io = ledger.io_seconds - before + sum(r.get('rebuild_seconds', 0.) for r in rows.values())
        throttle.rest(io, history_read=ledger.history_reads > histories)
        for mode, row in rows.items():
            outputs[mode]['games'].append(row)
            _save(targets[mode], outputs[mode])
        spent = time.monotonic() - began
        any_row = next(iter(rows.values()))
        close = any_row['closes'][0]['market_margin'] if any_row['closes'] else None
        show = lambda v: 'n/a' if v is None else f'{v:+.1f}'
        parts = ' '.join(f"{mode}: {show((row.get('replayed') or {}).get('margin'))}{'=' if row.get('identical') else ''}"
                         + (f" ERROR {row['error']}" if row.get('error') else '') for mode, row in rows.items())
        print(f"[{index}/{len(pending)}] {any_row['date']} {gid} {any_row['matchup']} {any_row['stage']} {any_row['original']['era']} "
              f"published {show(any_row['published']['margin'])} {parts} close {show(close)} actual {any_row['actual']['margin']:+d} "
              f"| {spent / index:.1f} s/game, about {spent / index * (len(pending) - index) / 60:.0f} min left", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--copy', help='A copy of the production directory (never production itself)')
    parser.add_argument('--output-frozen', help='Output JSON for frozen mode; an existing file is resumed')
    parser.add_argument('--output-rebuilt', help='Output JSON for rebuilt mode; an existing file is resumed')
    parser.add_argument('--which', choices=('final', 'first'), default='final',
                        help='final: the run the market report scores (last final-stage run before tipoff, else last preliminary); '
                             'first: the earliest pregame run of the game')
    parser.add_argument('--since', help='Eastern game date, YYYY-MM-DD: games on or after it')
    parser.add_argument('--until', help='Eastern game date, YYYY-MM-DD: games on or before it')
    parser.add_argument('--games', nargs='+', help='Only these game IDs')
    parser.add_argument('--limit', type=int, help='Replay at most this many games now (oldest first); run again to continue')
    parser.add_argument('--finals', choices=('run', 'known-at'), default='run',
                        help='rebuilt mode: environment finals prepare may read: the records the run itself read (default), '
                             'or every record in the copy whose known_at is by the run\'s prepared_at')
    parser.add_argument('--work', help='Folder for the rebuilt mode\'s symbolic-link view (default: beside the output)')
    parser.add_argument('--ledger', help='Read runs by run_id from this ledger instead of COPY/forecast.sqlite (opened read-only, never immutable)')
    parser.add_argument('--artifacts', help='Folder of model artifacts (content-addressed, checked on read) instead of COPY/model-artifacts')
    parser.add_argument('--live-ledger', action='store_true', help='Never open the ledger immutable, even inside COPY (a copy still being written)')
    parser.add_argument('--io-share', type=float, default=1 / 3, help='Largest share of the time spent reading; the rest is slept (default 1/3)')
    parser.add_argument('--guard-tick', default='/mnt/2TBHDD/NBA-Forecast/repo/data/operations/production/last-tick.json',
                        help='Production\'s last-tick.json: pause while the last completed tick started over 120 s ago')
    parser.add_argument('--no-guard', action='store_true', help='Do not watch production\'s tick')
    parser.add_argument('--no-idle', action='store_true', help='Do not lower this process to idle CPU and disk priority')
    parser.add_argument('--summary', metavar='OUTPUT', help='Print the summary of an existing output and exit')
    parser.add_argument('--compare', nargs=2, metavar=('BASE', 'CANDIDATE'), help='Pair two outputs game by game and print candidate minus base')
    parser.add_argument('--json', action='store_true', help='With --compare: print the comparison as JSON')
    args = parser.parse_args()
    if args.compare:
        result = compare(*(json.loads(Path(p).read_text()) for p in args.compare))
        print(json.dumps(result, indent=1, sort_keys=True) if args.json else '', end='')
        if not args.json:
            print_comparison(result)
        return
    if args.summary:
        output = json.loads(Path(args.summary).read_text())
        output['summary'] = summarize(output)
        print_summary(output)
        return
    if not args.copy:
        parser.error('--copy is required')
    run(args)


if __name__ == '__main__':
    main()
