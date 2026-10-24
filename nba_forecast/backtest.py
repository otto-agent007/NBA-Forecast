"""Fixed chronological backtest: the scoreboard every model change must run.

One season is replayed game by game with the current model code. For each game
the roster is that box score's own players, all treated as available; the
history is every box score from earlier Eastern dates; the forecast is the
ordinary baseline model. Player metrics condition on actual positive
participation, like the live calibration population; team totals and margins
use full team scores. This measures the points and minutes model under the
information a live run has, except availability, which live runs get from
injury reports and a replay cannot. Prior-season aggregate priors and the
archive-bound enhancements are not available historically either.

A scoreboard is compared with a reference by pairing the same player-games, so
the verdict reflects a change beyond sampling noise (two standard errors of the
mean paired difference), not a difference in the games evaluated.
"""
import gzip
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, pstdev
from zoneinfo import ZoneInfo

from .intervals import HEURISTIC_Z80, INTERVAL_FAMILY
from .model import MODEL_VERSION, _baseline_forecast
from .runner_release import package_digest
from .schedule import atomic_json, canonical
from .validation import parse_time

VERSION = 'backtest-1'
EASTERN = ZoneInfo('America/New_York')
BUCKETS = (('0-10', 0., 10.), ('10-20', 10., 20.), ('20-30', 20., 30.), ('30+', 30., math.inf))
PLAYER_FIELDS = ('game_id', 'player_id', 'team_id', 'expected_minutes', 'actual_minutes', 'points_mean',
                 'points_sd', 'points_lower80', 'points_upper80', 'actual_points', 'actual_status')
TEAM_FIELDS = ('game_id', 'team_id', 'home', 'projected_points', 'actual_points_regulation', 'actual_points',
               'projected_players', 'covered_actual_points')
LIMITATIONS = ('Rosters are the box score itself, all players available: no injury reports, so participation '
               'is not forecast and DNP players still draw minutes. No prior-season aggregate priors, no '
               'archive-bound enhancements. Player metrics condition on positive participation. Team points '
               'use regulation-equivalent scores; margins and winners use final scores.')


def read_json(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)


def _day(value):
    return parse_time(value).astimezone(EASTERN).date()


def evaluation_games(records, warmup_fraction=.3):
    """All games in order, and the fixed evaluation set: every game after the warm-up dates."""
    if not 0 <= warmup_fraction < 1:
        raise ValueError('warmup_fraction must be in [0, 1)')
    ordered = sorted(records, key=lambda r: (parse_time(r['game_time']), r['game_id']))
    dates = sorted({_day(r['game_time']) for r in ordered})
    first = dates[int(len(dates) * warmup_fraction)] if dates else None
    return ordered, [r for r in ordered if first is not None and _day(r['game_time']) >= first]


def replay(history, records, as_of, *, warmup_fraction=.3, limit=None, progress=None):
    """Forecast each evaluation game from earlier game days only."""
    home_of = {r['game_id']: r['home_team_id'] for r in records}
    by_game = defaultdict(list)
    for r in history:
        if r['game_id'] in home_of and 'home' not in r:
            r = dict(r, home=r['team_id'] == home_of[r['game_id']])
        by_game[r['game_id']].append(r)
    ordered, evaluated = evaluation_games(records, warmup_fraction)
    if limit:
        evaluated = evaluated[-limit:]
    targets = {g['game_id'] for g in evaluated}
    players, teams, skipped, prior = [], [], defaultdict(int), []
    prior_days = []  # (day, rows) in order, so the history before a game is a prefix.
    for index, game in enumerate(ordered):
        rows = by_game.get(game['game_id'], [])
        day = _day(game['game_time'])
        if game['game_id'] in targets:
            if not rows:
                skipped['missing_player_history'] += 1
            else:
                history_before = [r for d, batch in prior_days if d < day for r in batch]
                g = dict(game_id=game['game_id'], season_type='Regular Season', start_time=game['game_time'],
                         home_team_id=game['home_team_id'], away_team_id=game['away_team_id'])
                roster = [dict(player_id=r['player_id'], name=r['name'], team_id=r['team_id'], status='available',
                               status_source_url=r['source_url'], status_observed_at=r['known_at']) for r in rows]
                snapshot = dict(schema_version=1, as_of=as_of, season=game['season'], forecast_stage='preliminary',
                                research_reconstruction=True, games=[g],
                                roster=dict(players=roster, observed_at=as_of, source_url=rows[0]['source_url'],
                                            complete=False, team_ids=[g['home_team_id'], g['away_team_id']]))
                result = _baseline_forecast(snapshot, history_before)
                actual = {r['player_id']: r for r in rows}
                totals = defaultdict(lambda: dict(projected=0., count=0, covered=0.))
                for p in result['predictions']:
                    a = actual[p['player_id']]
                    projected = p['status'] == 'projected'
                    players.append([game['game_id'], p['player_id'], p['team_id'],
                                    p['expected_minutes'] if projected else None, a['minutes'],
                                    p['points_mean'] if projected else None, p['points_sd'] if projected else None,
                                    p['points_lower80'] if projected else None, p['points_upper80'] if projected else None,
                                    a['pts'], a['status']])
                    if projected:
                        t = totals[p['team_id']]
                        t['projected'] += p['points_mean']; t['count'] += 1; t['covered'] += a['pts']
                for tid in (g['home_team_id'], g['away_team_id']):
                    stats, minutes = game['team_stats'][tid], game['team_minutes'][tid]
                    teams.append([game['game_id'], tid, tid == g['home_team_id'], totals[tid]['projected'],
                                  stats['pts'] * 48 / (minutes / 5) if minutes else None, stats['pts'],
                                  totals[tid]['count'], totals[tid]['covered']])
        if rows:
            if prior_days and prior_days[-1][0] == day:
                prior_days[-1][1].extend(rows)
            else:
                prior_days.append((day, list(rows)))
        if progress and index % 100 == 0:
            progress(dict(games_seen=index, player_rows=len(players)))
    return dict(players=players, teams=teams, skipped=dict(skipped),
                evaluated_games=len(evaluated), total_games=len(ordered))


def _records(rows, fields):
    return [dict(zip(fields, r)) for r in rows]


def _metrics(rows):
    """Points and minutes accuracy for projected rows whose player played."""
    if not rows:
        return dict(n=0)
    errors = [r['points_mean'] - r['actual_points'] for r in rows]
    minutes = [r['expected_minutes'] - r['actual_minutes'] for r in rows]
    covered = [r['points_lower80'] <= r['actual_points'] <= r['points_upper80'] for r in rows]
    return dict(n=len(rows), points_mae=fmean(abs(e) for e in errors),
                points_rmse=math.sqrt(fmean(e * e for e in errors)), points_bias=fmean(errors),
                minutes_mae=fmean(abs(m) for m in minutes), minutes_bias=fmean(minutes),
                coverage80=fmean(covered), mean_width80=fmean(r['points_upper80'] - r['points_lower80'] for r in rows))


def summarize(players, teams):
    rows = _records(players, PLAYER_FIELDS)
    projected = [r for r in rows if r['points_mean'] is not None]
    played = [r for r in projected if r['actual_status'] == 'played' and r['actual_minutes'] > 0]
    dnp = [r for r in projected if r not in played]
    buckets = {name: _metrics([r for r in played if lo <= r['expected_minutes'] < hi]) for name, lo, hi in BUCKETS}
    team_rows = _records(teams, TEAM_FIELDS)
    team_rows = [t for t in team_rows if t['projected_players'] > 0 and t['actual_points_regulation'] is not None]
    team_errors = [t['projected_points'] - t['actual_points_regulation'] for t in team_rows]
    by_game = defaultdict(dict)
    for t in team_rows:
        by_game[t['game_id']]['home' if t['home'] else 'away'] = t
    games = [g for g in by_game.values() if 'home' in g and 'away' in g]
    margins = [(g['home']['projected_points'] - g['away']['projected_points'],
                g['home']['actual_points'] - g['away']['actual_points']) for g in games]
    return dict(
        players=dict(all=_metrics(played), by_expected_minutes=buckets,
                     projected_players=len(projected), played=len(played),
                     projected_dnp=dict(count=len(dnp), share=len(dnp) / len(projected) if projected else None,
                                        mean_expected_minutes=fmean(r['expected_minutes'] for r in dnp) if dnp else None,
                                        mean_points_mean=fmean(r['points_mean'] for r in dnp) if dnp else None),
                     unsupported_rows=len(rows) - len(projected)),
        teams=dict(n=len(team_rows), points_mae=fmean(abs(e) for e in team_errors) if team_errors else None,
                   points_bias=fmean(team_errors) if team_errors else None,
                   covered_share=fmean(t['covered_actual_points'] / t['actual_points'] for t in team_rows if t['actual_points']) if team_rows else None),
        games=dict(n=len(games), margin_mae=fmean(abs(p - a) for p, a in margins) if margins else None,
                   margin_bias_home=fmean(p - a for p, a in margins) if margins else None,
                   winner_accuracy=fmean((p > 0) == (a > 0) for p, a in margins if a != 0) if margins else None,
                   home_pick_share=fmean(p > 0 for p, a in margins) if margins else None,
                   home_win_share=fmean(a > 0 for p, a in margins) if margins else None))


def _paired(current, reference):
    """Mean paired difference (current minus reference) with its standard error and verdict."""
    keys = sorted(set(current) & set(reference))
    if len(keys) < 2:
        return dict(n=len(keys), mean=None, se=None, verdict='insufficient')
    d = [current[k] - reference[k] for k in keys]
    mean, se = fmean(d), pstdev(d) / math.sqrt(len(d))
    verdict = 'improved' if mean + 2 * se < 0 else 'worse' if mean - 2 * se > 0 else 'within_noise'
    return dict(n=len(d), mean=mean, se=se, verdict=verdict,
                rule='improved/worse when the mean paired difference exceeds two standard errors')


def _bias(current, reference):
    """Signed-error means: a clear bias that shrinks is an improvement; one that appears or grows is worse."""
    keys = sorted(set(current) & set(reference))
    if len(keys) < 2:
        return dict(n=len(keys), current=None, reference=None, se=None, verdict='insufficient')
    cur, ref = [current[k] for k in keys], [reference[k] for k in keys]
    new, old, se = fmean(cur), fmean(ref), max(pstdev(cur), pstdev(ref)) / math.sqrt(len(keys))
    if abs(old) > 2 * se and abs(old) - abs(new) > se:
        verdict = 'improved'
    elif abs(new) > 2 * se and abs(new) - abs(old) > se:
        verdict = 'worse'
    else:
        verdict = 'within_noise'
    return dict(n=len(keys), current=new, reference=old, se=se, verdict=verdict,
                rule='a bias more than two standard errors from zero that shrinks by at least one standard error is '
                     'improved; one that appears or grows by at least one standard error is worse')


def compare(current, reference):
    """Pair the same player-games, teams and games: absolute errors (lower is better) and signed biases."""
    def errors(rows):
        points, minutes, point_bias = {}, {}, {}
        for r in _records(rows['players'], PLAYER_FIELDS):
            if r['points_mean'] is None or r['actual_status'] != 'played' or r['actual_minutes'] <= 0:
                continue
            key = r['game_id'] + ':' + r['player_id']
            points[key] = abs(r['points_mean'] - r['actual_points'])
            point_bias[key] = r['points_mean'] - r['actual_points']
            minutes[key] = abs(r['expected_minutes'] - r['actual_minutes'])
        teams, team_bias, home, away = {}, {}, {}, {}
        for t in _records(rows['teams'], TEAM_FIELDS):
            if t['projected_players'] > 0 and t['actual_points_regulation'] is not None:
                teams[t['game_id'] + ':' + t['team_id']] = abs(t['projected_points'] - t['actual_points_regulation'])
                team_bias[t['game_id'] + ':' + t['team_id']] = t['projected_points'] - t['actual_points_regulation']
                (home if t['home'] else away)[t['game_id']] = t
        margin_bias = {gid: (home[gid]['projected_points'] - away[gid]['projected_points'])
                       - (home[gid]['actual_points'] - away[gid]['actual_points']) for gid in home if gid in away}
        margins = {gid: abs(v) for gid, v in margin_bias.items()}
        return dict(points=points, minutes=minutes, team_points=teams, margin=margins,
                    points_bias=point_bias, team_points_bias=team_bias, margin_bias=margin_bias)
    cur, ref = errors(current), errors(reference)
    result = {name: _paired(cur[name], ref[name]) for name in ('points', 'minutes', 'team_points', 'margin')}
    result.update({name: _bias(cur[name], ref[name]) for name in ('points_bias', 'team_points_bias', 'margin_bias')})
    verdicts = {v['verdict'] for v in result.values()}
    result['verdict'] = 'worse' if 'worse' in verdicts else 'improved' if 'improved' in verdicts else 'within_noise'
    return result


def run_backtest(history, records, output, *, reference=None, warmup_fraction=.3, limit=None, as_of=None,
                 sources=None, progress=None):
    """Write ``scoreboard.json`` and ``rows.json.gz`` under ``output``; return the scoreboard."""
    as_of = as_of or datetime.now(timezone.utc).isoformat()
    cutoff = parse_time(as_of)
    if any(parse_time(r['known_at']) > cutoff for r in history) or any(parse_time(r['known_at']) > cutoff for r in records):
        raise ValueError('backtest cutoff cannot precede future-known sources')
    out = Path(output)
    if out.exists() and any(out.iterdir()):
        raise ValueError('output directory exists and is not empty')
    replayed = replay(history, records, as_of, warmup_fraction=warmup_fraction, limit=limit, progress=progress)
    rows = dict(version=VERSION, player_fields=list(PLAYER_FIELDS), team_fields=list(TEAM_FIELDS),
                players=replayed['players'], teams=replayed['teams'])
    scoreboard = dict(
        schema_version=1, version=VERSION, created_at=as_of, model_version=MODEL_VERSION,
        code_sha256=package_digest(Path(__file__).parent),
        sources=sources or {}, parameters=dict(warmup_fraction=warmup_fraction, limit=limit,
                                                 interval_family=INTERVAL_FAMILY, heuristic_z80=HEURISTIC_Z80),
        games=dict(total=replayed['total_games'], evaluated=replayed['evaluated_games'], skipped=replayed['skipped']),
        metrics=summarize(replayed['players'], replayed['teams']), limitations=LIMITATIONS)
    if reference is not None:
        ref_rows = read_json(Path(reference) / 'rows.json.gz')
        ref_board = read_json(Path(reference) / 'scoreboard.json')
        scoreboard['comparison'] = dict(reference=str(reference), reference_created_at=ref_board.get('created_at'),
                                        reference_code_sha256=ref_board.get('code_sha256'),
                                        reference_model_version=ref_board.get('model_version'),
                                        **compare(rows, ref_rows))
    out.mkdir(parents=True, exist_ok=True)
    (out / 'rows.json.gz').write_bytes(gzip.compress(canonical(rows), mtime=0))
    atomic_json(out / 'scoreboard.json', scoreboard)
    return scoreboard


def sha256_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def format_scoreboard(board):
    """A short readable table for the terminal."""
    m = board['metrics'];lines = []
    lines.append(f"games evaluated {board['games']['evaluated']} of {board['games']['total']}  model {board['model_version']}  code {board['code_sha256'][:12]}")
    lines.append(f"{'players (played)':18s} {'n':>6s} {'pts MAE':>8s} {'bias':>6s} {'min MAE':>8s} {'cov80':>6s} {'width':>6s}")
    for name, item in [('all', m['players']['all'])] + list(m['players']['by_expected_minutes'].items()):
        if item.get('n'):
            lines.append(f"{name:18s} {item['n']:6d} {item['points_mae']:8.2f} {item['points_bias']:+6.2f} {item['minutes_mae']:8.2f} {item['coverage80']:6.1%} {item['mean_width80']:6.1f}")
    d = m['players']['projected_dnp']
    lines.append(f"projected but did not play: {d['count']} ({d['share']:.1%} of projected), averaging {d['mean_expected_minutes']:.1f} min / {d['mean_points_mean']:.1f} pts" if d['count'] else 'projected but did not play: 0')
    t, g = m['teams'], m['games']
    if t['n']:
        lines.append(f"teams n={t['n']} points MAE {t['points_mae']:.2f} bias {t['points_bias']:+.2f} (projected players cover {t['covered_share']:.0%} of team points)")
    if g['n']:
        lines.append(f"games n={g['n']} margin MAE {g['margin_mae']:.2f} home bias {g['margin_bias_home']:+.2f} winner accuracy {g['winner_accuracy']:.1%} (home picked {g['home_pick_share']:.0%}, home won {g['home_win_share']:.0%})")
    c = board.get('comparison')
    if c:
        lines.append(f"vs reference ({c['reference_model_version']} {str(c['reference_code_sha256'])[:12]}): {c['verdict'].upper()}")
        for name in ('points', 'minutes', 'team_points', 'margin'):
            item = c[name]
            if item.get('mean') is not None:
                lines.append(f"  {name:18s} n={item['n']:6d} mean abs-error change {item['mean']:+.3f} ± {2 * item['se']:.3f}  {item['verdict']}")
        for name in ('points_bias', 'team_points_bias', 'margin_bias'):
            item = c.get(name, {})
            if item.get('current') is not None:
                lines.append(f"  {name:18s} n={item['n']:6d} bias {item['reference']:+.2f} -> {item['current']:+.2f} (se {item['se']:.2f})  {item['verdict']}")
    return '\n'.join(lines)
