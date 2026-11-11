"""Lineup oracle on the live replay: the final pregame forecast told who actually played (an upper bound).

How much of the live gap to the close would close if the model knew, at its final
pregame forecast, the truth about who plays? Each level replays the live ledger with
``live_replay.py`` (rebuilt mode) and overrides inputs with the game's final box score
(``COPY/finals/<game>.json``) just before ``model.forecast``, so the rest of the model
reacts as it would to real news. This is an oracle, not a source: no forecast could
know these things, so its result bounds what lineup and news information can buy.

    O0 none      the hooks are installed and override nothing: must reproduce the reference replay
    O1 played    the two teams' rostered players who logged minutes are available, with participation 1;
                 every other player of the two teams is out (explicit report status, sourced, known by the cutoff)
    O2 starters  O1, and the projected starting five of each team are the actual starters
    O3 minutes   O2, and each projected player's minutes are his actual minutes (scaled down to the
                 240-minute regulation budget when the team's minutes exceed it: overtime)

Where each override enters (the package itself is unchanged):

O1  ``snapshot['roster']['players']`` of the two teams, in place, before ``model.forecast``:
    a player who played gets ``status='available'`` (fields replaced only when he was not
    already available and known by the cutoff); everyone else ``status='out'``,
    ``status_basis='explicit_report_status'``, an https source and observed/published at the
    cutoff (kept as is when already out on such a status). From there the model's own code
    runs: ``model._project_player`` withholds the out players and records ``model._absence``;
    ``model._allocate_team`` fills the 240 minutes; ``rotation_allocation.redistribute`` hands
    the out players' minutes and attempts to the others; ``totals.short_bench`` sees an
    explicit short bench; and the team-rating half's absence term
    (``margins.absence_adjustment``, which reads out predictions with ``components.absence``)
    sees every oracle absence. ``model._participation`` is wrapped: a player who played gets
    probability 1 (status factor 1). ``snapshot['preflight']['lineups']`` is recomputed with
    ``rotation.projected_lineups`` (as preflight does) for a team whose stored five holds a
    player who did not play; otherwise it stays as stored.
O2  ``snapshot['preflight']['lineups']`` of the two teams = their rostered actual starters
    (read by ``totals.game_totals``, hence the margin's coverage and ``scoreline``), and
    ``model.rotation_role`` (the model module's reference only; profiles keep theirs) reports
    ``last_game_started`` = actual starter, which is how ``model._allocate_team`` picks the
    protected rotation (the model has no other starter term).
O3  ``candidate.redistribute`` is wrapped: before the allocator runs, each projected player's
    minutes (``original`` in ``candidate.forecast_candidate``) are set to his actual minutes and
    locked, so the allocator adds no minutes but still hands out vacated attempts as rates;
    afterwards his minutes are set again (the allocator caps at 48). Rates, usage budget,
    pace, defense, context, shooting, the margin blend and the scoreline stay the model's.

Not overridden at any level: players missing from the snapshot roster (counted in each row's
``oracle`` audit), players the model cannot project (no evidence: they stay withheld), the
team ratings and every prepared input, and the rates and shooting of every player.

Run one level (heavy: through the flock/scope/guard wrapper; the extra arguments go to
live_replay.py unchanged):

    python3 scripts/research/lineup_oracle.py replay --oracle played -- --copy COPY \\
        --ledger LEDGER --artifacts ARTIFACTS --no-guard --output-rebuilt OUT/o1-rebuilt.json

``--immutable-ledger`` opens a ledger copy that nobody writes immutable (no -shm writes).

Print the tables (paired against the reference; one close per game, its first with a margin;
± is one SE clustered by Eastern date, ``backtest._mean_se``):

    python3 scripts/research/lineup_oracle.py tables REFERENCE.json O0=o0.json O1=o1.json O2=o2.json O3=o3.json
"""
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import live_replay as L  # noqa: E402  (puts the package beside it on the path)

LEVELS = {'none': 0, 'played': 1, 'starters': 2, 'minutes': 3}
LABELS = {0: 'O0 none (hooks installed, nothing overridden)', 1: 'O1 who played', 2: 'O2 who played + starters',
          3: 'O3 who played + starters + minutes'}
ORACLE_URL = 'https://oracle.invalid/final-box-score'
SURPRISE_PROJECTED_MINUTES = 20.
SURPRISE_PLAYED_MINUTES = 15.
SURPRISE_CONFIDENT_PARTICIPATION = .8

_state = dict(level=0, copy=None, truth=None, audit=None, audits=[])


# ---------------------------------------------------------------- the oracle

def truth_of(copy, game):
    """The final box score of one game: who played (positive minutes), who started, and minutes."""
    rows = json.loads((Path(copy) / 'finals' / (game['game_id'] + '.json')).read_text())
    teams = (game['home_team_id'], game['away_team_id'])
    played = {(r['team_id'], r['player_id']): r for r in rows
              if r['team_id'] in teams and r['status'] == 'played' and (r.get('minutes') or 0) > 0}
    starters = {k for k, r in played.items() if r.get('starter') is True}
    team_minutes = {t: math.fsum(r['minutes'] for (team, _), r in played.items() if team == t) for t in teams}
    # Projections are regulation minutes and totals refuse more than 240: an overtime team's minutes scale down.
    scale = {t: min(1., 240. / m) if m else 1. for t, m in team_minutes.items()}
    minutes = {k: r['minutes'] * scale[k[0]] for k, r in played.items()}
    return dict(teams=set(teams), played=set(played), starters=starters, minutes=minutes, team_minutes=team_minutes, scale=scale)


def _known_by(value, cutoff):
    from nba_forecast.validation import parse_time
    try:
        return bool(value) and parse_time(value) <= cutoff
    except (TypeError, ValueError, AttributeError):
        return False


def apply_availability(snapshot, truth, audit):
    """O1 on the roster, in place: who played is available, everyone else of the two teams is out."""
    from nba_forecast.validation import parse_time
    cutoff = parse_time(snapshot['as_of'])
    gid = snapshot['games'][0]['game_id']
    rostered = set()
    changes = dict(to_available=[], to_out=[], kept_out=0, kept_available=0)
    for p in snapshot['roster']['players']:
        if p['team_id'] not in truth['teams']:
            continue
        key = (p['team_id'], p['player_id'])
        rostered.add(key)
        if key in truth['played']:
            if (p['status'] == 'available' and p.get('status_basis') != 'roster_membership_review'
                    and (not p.get('status_observed_at') or _known_by(p['status_observed_at'], cutoff))):
                changes['kept_available'] += 1
                continue
            changes['to_available'].append(dict(player_id=p['player_id'], team_id=p['team_id'], was=p['status'], basis=p.get('status_basis')))
            p.update(status='available', status_basis='oracle_final_box_score', reported_status='Oracle: played', status_game_id=gid,
                     status_source_url=ORACLE_URL, status_observed_at=snapshot['as_of'], status_published_at=snapshot['as_of'])
        else:
            sourced = (str(p.get('status_source_url') or '').startswith('https://')
                       and _known_by(p.get('status_observed_at'), cutoff)
                       and _known_by(p.get('status_published_at', p.get('status_observed_at')), cutoff))
            if p['status'] == 'out' and p.get('status_basis') == 'explicit_report_status' and sourced:
                changes['kept_out'] += 1
                continue
            changes['to_out'].append(dict(player_id=p['player_id'], team_id=p['team_id'], was=p['status'], basis=p.get('status_basis')))
            p.update(status='out', status_basis='explicit_report_status', reported_status='Out', status_game_id=gid,
                     status_source_url=ORACLE_URL, status_observed_at=snapshot['as_of'], status_published_at=snapshot['as_of'])
    missing = sorted(truth['played'] - rostered)
    audit.update(changes, unrostered_played=[dict(team_id=t, player_id=pid, minutes=truth['minutes'][(t, pid)]) for t, pid in missing],
                 unrostered_played_minutes=math.fsum(truth['minutes'][k] for k in missing),
                 team_minutes=truth['team_minutes'], minutes_scale=truth['scale'])


def apply_lineups(snapshot, history, truth, level, audit):
    """O1: recompute a stored five that holds a player who did not play; O2: the actual starters."""
    lineups = (snapshot.get('preflight') or {}).get('lineups')
    if lineups is None:
        audit['lineups'] = 'the snapshot has no preflight lineups'
        return
    rostered = {(p['team_id'], p['player_id']) for p in snapshot['roster']['players'] if p['team_id'] in truth['teams']}
    record = {}
    if level >= 2:
        for team in sorted(truth['teams']):
            ids = sorted(pid for t, pid in truth['starters'] if t == team and (t, pid) in rostered)
            before = (lineups.get(team) or {}).get('player_ids', [])
            lineups[team] = dict(player_ids=ids, unfilled_slots=5 - len(ids), confirmed=True,
                                 basis='Oracle: the actual starters from the final box score.')
            record[team] = dict(stored=before, oracle=ids, changed=sorted(set(before) ^ set(ids)))
    else:
        stale = [t for t in sorted(truth['teams'])
                 if any((t, pid) not in truth['played'] for pid in (lineups.get(t) or {}).get('player_ids', []))]
        if stale:
            from nba_forecast.profiles import build_profiles
            from nba_forecast.rotation import projected_lineups
            excluded = set((snapshot.get('model_enhancements') or {}).get('excluded_history_game_ids', []))
            profiles = build_profiles(snapshot, [r for r in history if r['game_id'] not in excluded])
            players = [p for p in snapshot['roster']['players'] if p['team_id'] in stale]
            fresh = projected_lineups(players, profiles['players'])
            for team in stale:
                before = (lineups.get(team) or {}).get('player_ids', [])
                lineups[team] = fresh[team]
                record[team] = dict(stored=before, oracle=fresh[team]['player_ids'], changed=sorted(set(before) ^ set(fresh[team]['player_ids'])))
    audit['lineups'] = record


def install(level, copy):
    """Wrap the package functions the oracle overrides; level 0 installs the same wrappers and changes nothing."""
    from nba_forecast import candidate as C, model as M
    _state.update(level=level, copy=Path(copy).resolve())
    original = dict(forecast=M.forecast, participation=M._participation, rotation_role=M.rotation_role, redistribute=C.redistribute)

    def forecast(snapshot, history):
        audit = dict(level=level, label=LABELS[level])
        _state['audits'].append(audit)
        if level == 0:
            return original['forecast'](snapshot, history)
        if len(snapshot['games']) != 1:
            raise ValueError('the oracle replays one game per snapshot')
        truth = truth_of(_state['copy'], snapshot['games'][0])
        apply_availability(snapshot, truth, audit)
        apply_lineups(snapshot, history, truth, level, audit)
        audit.update(participation_set=0, starters_marked=0, minutes_overridden=0, redistribute_calls=0)
        _state.update(truth=truth, audit=audit)
        try:
            return original['forecast'](snapshot, history)
        finally:
            _state.update(truth=None, audit=None)

    def participation(player, rows, team_games, role_minutes, unavailable=frozenset()):
        result = original['participation'](player, rows, team_games, role_minutes, unavailable)
        truth = _state['truth']
        if truth is not None and (player['team_id'], player['player_id']) in truth['played']:
            result = dict(result, probability=1.0, status_factor=1.0, oracle='played in the final box score: participation certain')
            _state['audit']['participation_set'] += 1
        return result

    def rotation_role(player, rows, **kwargs):
        result = original['rotation_role'](player, rows, **kwargs)
        truth = _state['truth']
        if truth is not None and level >= 2 and player.get('team_id') in truth['teams']:
            started = (player['team_id'], player['player_id']) in truth['starters']
            result = dict(result, last_game_started=started, oracle_starter=started)
            _state['audit']['starters_marked'] += started
        return result

    def redistribute(players, profiles, projection_inputs, history, as_of, season=None):
        truth = _state['truth']
        if truth is None or level < 3:
            return original['redistribute'](players, profiles, projection_inputs, history, as_of, season=season)
        _state['audit']['redistribute_calls'] += 1
        team = {p['player_id']: p['team_id'] for p in players}
        applied = {}
        for pid, item in projection_inputs.items():
            minutes = truth['minutes'].get((team.get(pid), pid))
            if minutes is None:  # projected without playing cannot happen under O1; leave it visible
                _state['audit'].setdefault('projected_without_minutes', []).append(pid)
                continue
            item.update(minutes=minutes, locked_minutes=True)  # ``original`` in forecast_candidate: the allocator adds no minutes
            applied[pid] = minutes
        inputs, audits = original['redistribute'](players, profiles, projection_inputs, history, as_of, season=season)
        for pid, minutes in applied.items():
            inputs[pid]['minutes'] = minutes  # the allocator caps at 48
        _state['audit']['minutes_overridden'] += len(applied)
        return inputs, audits

    M.forecast, M._participation, M.rotation_role, C.redistribute = forecast, participation, rotation_role, redistribute

    identity = L.code_identity

    def code_identity():
        code = identity()
        code['oracle'] = dict(level=level, label=LABELS[level], script=str(Path(__file__).resolve()),
                              script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        code['commit'] = f"{code['commit']} + lineup oracle {LABELS[level]}"
        return code
    L.code_identity = code_identity

    replay_game = L.replay_game

    def replay_game_with_audit(*args, **kwargs):
        _state['audits'] = []
        rows = replay_game(*args, **kwargs)
        for row in rows.values():
            row['oracle'] = deepcopy(_state['audits'][-1]) if _state['audits'] else None
        return rows
    L.replay_game = replay_game_with_audit


def replay(argv):
    parser = argparse.ArgumentParser(prog='lineup_oracle.py replay')
    parser.add_argument('--oracle', choices=sorted(LEVELS, key=LEVELS.get), required=True)
    parser.add_argument('--immutable-ledger', action='store_true',
                        help='Open --ledger immutable (a copy nobody writes, with an empty -wal): no -shm writes into it')
    args, rest = parser.parse_known_args(argv)
    rest = [a for a in rest if a != '--']
    copy = rest[rest.index('--copy') + 1]
    if args.immutable_ledger:
        init = L.Ledger.__init__

        def immutable_init(self, path, *, immutable=False):
            init(self, path, immutable=True)
        L.Ledger.__init__ = immutable_init
    install(LEVELS[args.oracle], copy)
    sys.argv = [str(HERE / 'live_replay.py')] + rest
    L.main()


# ---------------------------------------------------------------- tables

def _first_close(g):
    return next((c['market_margin'] for c in g['closes'] if L._finite(c.get('market_margin'))), None)


def _brier(p, margin):
    return (p - (margin > 0)) ** 2


def surprise_of(g, finals_dir):
    """A projected regular (>= 20 projected minutes, conditional on playing, in the reference replay) who did not
    play, or a player the reference replay did not project (or did not list) who played >= 15 minutes.
    ``confident``: the same with only regulars the reference also gave a participation probability >= 0.8."""
    rows = json.loads((Path(finals_dir) / (g['game_id'] + '.json')).read_text())
    teams = (g['home_team_id'], g['away_team_id'])
    actual = {r['player_id']: (r['minutes'] or 0) if r['status'] == 'played' else 0. for r in rows if r['team_id'] in teams}
    projected = {p['player_id']: p['replayed'] for p in g['players'] if p.get('replayed') and p['replayed']['forecast_status'] == 'projected'}
    missed = [pid for pid, side in projected.items() if (side['predicted_minutes'] or 0) >= SURPRISE_PROJECTED_MINUTES and actual.get(pid, 0) <= 0]
    confident = [pid for pid in missed if (projected[pid]['participation_probability'] or 0) >= SURPRISE_CONFIDENT_PARTICIPATION]
    unexpected = [pid for pid, minutes in actual.items() if minutes >= SURPRISE_PLAYED_MINUTES and pid not in projected]
    return dict(surprise=bool(missed or unexpected), confident=bool(confident or unexpected),
                missed_regulars=missed, unprojected_played=unexpected)


def _pm(mean, se):
    return 'n/a' if mean is None else f'{mean:+.3f} ± {se:.3f}' if se is not None else f'{mean:+.3f} ± n/a'


def _mse(values, dates):
    if not values:
        return None, None
    return L.mean_se(values, dates)


def o0_proof(base, cand):
    pairs, unpaired = L.pair_outputs(base, cand)
    both = [(a, b) for a, b in pairs if a.get('replayed') and b.get('replayed')]
    same_sha = sum(a['replayed_forecast_sha256'] == b['replayed_forecast_sha256'] for a, b in both)
    same_numbers = sum(L.canonical(a['replayed']) == L.canonical(b['replayed']) for a, b in both)
    same_players = sum(L.canonical(a['players']) == L.canonical(b['players']) for a, b in both)
    same_published = sum(L.canonical(a['published']) == L.canonical(b['published']) for a, b in both)
    print(f'O0 PROOF (the hooks installed, nothing overridden, against the reference): {len(both)} games replayed in both, '
          f'{len(unpaired)} not paired, {sum(1 for a, b in pairs if not (a.get("replayed") and b.get("replayed")))} replayed in only one')
    print(f'  replayed forecast byte-identical (sha256): {same_sha} of {len(both)}')
    print(f'  replayed numbers identical (margin, totals, win probability, ...): {same_numbers} of {len(both)}')
    print(f'  player rows identical: {same_players} of {len(both)}; published side identical: {same_published} of {len(both)}')
    return same_sha == len(both) == same_numbers


def rows_for(base, cand, finals_dir):
    """One row per game paired on the same run and replayed in both, with a close and both margins."""
    pairs, _ = L.pair_outputs(base, cand)
    rows = []
    for b, c in pairs:
        if not (b.get('replayed') and c.get('replayed')):
            continue
        close = _first_close(b)
        bm, cm = b['replayed'].get('margin'), c['replayed'].get('margin')
        if close is None or not (L._finite(bm) and L._finite(cm)):
            continue
        a = b['actual']
        assert a == c['actual']
        row = dict(date=b['date'], game_id=b['game_id'], base=abs(bm - a['margin']), cand=abs(cm - a['margin']),
                   close=abs(close - a['margin']), base_margin=bm, cand_margin=cm, close_margin=close, actual=a['margin'],
                   **surprise_of(b, finals_dir))
        bw, cw = b['replayed'].get('home_win'), c['replayed'].get('home_win')
        if L._finite(bw) and L._finite(cw) and a['margin'] != 0:
            row.update(base_brier=_brier(bw, a['margin']), cand_brier=_brier(cw, a['margin']))
        teams = []
        for side in ('home', 'away'):
            bp, cp = b['replayed'].get(side + '_points'), c['replayed'].get(side + '_points')
            if L._finite(bp) and L._finite(cp):
                teams.append(dict(base=bp - a[side + '_points'], cand=cp - a[side + '_points']))
        row['teams'] = teams
        players = []
        cp = {p['player_id']: p for p in c['players']}
        for p in b['players']:
            q = cp.get(p['player_id'])
            if not (q and p['played'] and p.get('replayed') and q.get('replayed')):
                continue
            if p['replayed']['forecast_status'] == 'projected' and q['replayed']['forecast_status'] == 'projected':
                players.append(dict(base_points=abs(p['replayed']['points_error']), cand_points=abs(q['replayed']['points_error']),
                                    base_minutes=abs(p['replayed']['minutes_error']), cand_minutes=abs(q['replayed']['minutes_error'])))
        row['players'] = players
        # The margin is (1 - w) player sum + w team rating; recompose it with one half from each output.
        parts = [x['replayed'] for x in (b, c)]
        if all(L._finite(x.get('team_rating_margin')) and L._finite(x.get('player_sum_margin')) and x.get('rating_weight') for x in parts):
            w = parts[0]['rating_weight']
            recomposed = [(1 - x['rating_weight']) * x['player_sum_margin'] + x['rating_weight'] * x['team_rating_margin'] for x in parts]
            row['recomposed_ok'] = all(abs(r - m) < 1e-6 for r, m in zip(recomposed, (bm, cm))) and parts[1]['rating_weight'] == w
            row['cand_players_base_rating'] = abs((1 - w) * parts[1]['player_sum_margin'] + w * parts[0]['team_rating_margin'] - a['margin'])
            row['base_players_cand_rating'] = abs((1 - w) * parts[0]['player_sum_margin'] + w * parts[1]['team_rating_margin'] - a['margin'])
            row['player_sum_base'] = abs(parts[0]['player_sum_margin'] - a['margin'])
            row['player_sum_cand'] = abs(parts[1]['player_sum_margin'] - a['margin'])
            row['absence_base'] = parts[0].get('absence_rating_adjustment') or 0.
            row['absence_cand'] = parts[1].get('absence_rating_adjustment') or 0.
            # Without the absence term: the rating half as it stood before reported-out regulars were subtracted.
            row['base_no_absence'] = abs(bm - w * row['absence_base'] - a['margin'])
            row['cand_no_absence'] = abs(cm - w * row['absence_cand'] - a['margin'])
        rows.append(row)
    return rows


def _line(label, rows, f, width=58):
    vals = [f(r) for r in rows]
    mean, se = _mse(vals, [r['date'] for r in rows])
    print(f'  {label:<{width}} {_pm(mean, se)}')


def table(name, rows):
    n, dates = len(rows), len({r['date'] for r in rows})
    if not n:
        print(f'\n{name}: no paired games')
        return
    mean = lambda key, sel=rows: math.fsum(r[key] for r in sel) / len(sel)
    print(f'\n{name} vs main: {n} games with a close, {dates} Eastern dates')
    print(f'  margin MAE: main {mean("base"):.2f}, {name} {mean("cand"):.2f}, close {mean("close"):.2f}')
    _line(f'{name} − main (margin error)', rows, lambda r: r['cand'] - r['base'])
    _line('main − close (the gap)', rows, lambda r: r['base'] - r['close'])
    _line(f'{name} − close (the gap left)', rows, lambda r: r['cand'] - r['close'])
    gap = mean('base') - mean('close')
    if gap:
        print(f'  share of the gap closed: {(mean("base") - mean("cand")) / gap:+.0%} (point estimate)')
    print(f'  mean distance from the close: main {math.fsum(abs(r["base_margin"] - r["close_margin"]) for r in rows) / n:.2f}, '
          f'{name} {math.fsum(abs(r["cand_margin"] - r["close_margin"]) for r in rows) / n:.2f}; '
          f'margins moved: {sum(abs(r["base_margin"] - r["cand_margin"]) > 1e-9 for r in rows)}')
    brier = [r for r in rows if 'base_brier' in r]
    if brier:
        print(f'  winner Brier ({len(brier)} games without a tie): main {mean("base_brier", brier):.4f}, {name} {mean("cand_brier", brier):.4f}')
        _line(f'{name} − main (winner Brier)', brier, lambda r: r['cand_brier'] - r['base_brier'])
    teams = [dict(t, date=r['date']) for r in rows for t in r['teams']]
    if teams:
        tm = lambda f: math.fsum(f(t) for t in teams) / len(teams)
        print(f'  team points ({len(teams)} team-games): MAE main {tm(lambda t: abs(t["base"])):.2f}, {name} {tm(lambda t: abs(t["cand"])):.2f}; '
              f'bias (forecast − actual) main {tm(lambda t: t["base"]):+.2f}, {name} {tm(lambda t: t["cand"]):+.2f}')
        _line(f'{name} − main (team points absolute error)', teams, lambda t: abs(t['cand']) - abs(t['base']))
        _line(f'{name} bias (team points, forecast − actual)', teams, lambda t: t['cand'])
        _line('main bias (team points, forecast − actual)', teams, lambda t: t['base'])
    players = [dict(p, date=r['date']) for r in rows for p in r['players']]
    if players:
        pm = lambda key: math.fsum(p[key] for p in players) / len(players)
        print(f'  players projected in both who played: {len(players)}; points MAE main {pm("base_points"):.2f}, {name} {pm("cand_points"):.2f}; '
              f'minutes MAE main {pm("base_minutes"):.2f}, {name} {pm("cand_minutes"):.2f}')
        _line(f'{name} − main (player points absolute error)', players, lambda p: p['cand_points'] - p['base_points'])
        _line(f'{name} − main (player minutes absolute error)', players, lambda p: p['cand_minutes'] - p['base_minutes'])
    dist = lambda r, key: abs(r[key] - r['close_margin'])
    for label, sel in (('all games', rows),
                       ('with a material availability surprise', [r for r in rows if r['surprise']]),
                       ('without one', [r for r in rows if not r['surprise']]),
                       (f'confident surprise only (missed regular had participation >= {SURPRISE_CONFIDENT_PARTICIPATION:g}, or unprojected played)',
                        [r for r in rows if r['confident']]),
                       ('without a confident surprise', [r for r in rows if not r['confident']])):
        if not sel:
            print(f'  {label}: no games')
            continue
        print(f'  {label}: {len(sel)} games, {len({r["date"] for r in sel})} dates; margin MAE main {mean("base", sel):.2f}, '
              f'{name} {mean("cand", sel):.2f}, close {mean("close", sel):.2f}; distance from the close main '
              f'{math.fsum(dist(r, "base_margin") for r in sel) / len(sel):.2f}, {name} {math.fsum(dist(r, "cand_margin") for r in sel) / len(sel):.2f}')
        if sel is not rows:
            _line(f'  {name} − main (margin error)', sel, lambda r: r['cand'] - r['base'])
            _line('  main − close', sel, lambda r: r['base'] - r['close'])
            _line(f'  {name} − close', sel, lambda r: r['cand'] - r['close'])
        _line(f'  {name} − main (distance from the close)', sel, lambda r: dist(r, 'cand_margin') - dist(r, 'base_margin'))
    # How much of the oracle's margin shift the close and the result endorse: 1 = all of it, 0 = none.
    from stats_lite import slope
    moved = [r for r in rows if abs(r['cand_margin'] - r['base_margin']) > 1e-9]
    if len({r['date'] for r in moved}) > 2:
        shift = [r['cand_margin'] - r['base_margin'] for r in moved]
        dates = [r['date'] for r in moved]
        sd_shift = math.sqrt(math.fsum(s * s for s in shift) / len(shift))
        for label, target in (('close − main', [r['close_margin'] - r['base_margin'] for r in moved]),
                              ('actual − main', [r['actual'] - r['base_margin'] for r in moved])):
            b, se = slope(shift, target, dates)
            print(f'  slope of ({label}) on ({name} − main), {len(moved)} games, rms shift {sd_shift:.2f}: {b:+.2f} ± {se:.2f} '
                  f'(1 = endorses the whole shift, 0 = none; date-clustered)')
    parts = [r for r in rows if 'recomposed_ok' in r]
    if parts:
        print(f'  margin halves ({len(parts)} games with both halves; recomposition (1 − w) player sum + w rating '
              f'matches the margin in {sum(r["recomposed_ok"] for r in parts)}):')
        print(f'    player-sum margin alone MAE: main {mean("player_sum_base", parts):.2f}, {name} {mean("player_sum_cand", parts):.2f}; '
              f'mean |absence term|: main {math.fsum(abs(r["absence_base"]) for r in parts) / len(parts):.2f}, '
              f'{name} {math.fsum(abs(r["absence_cand"]) for r in parts) / len(parts):.2f}')
        _line(f'{name} player sum + main rating (rating blind to the oracle) − main', parts, lambda r: r['cand_players_base_rating'] - r['base'], 70)
        _line(f'main player sum + {name} rating (only the absence term sees it) − main', parts, lambda r: r['base_players_cand_rating'] - r['base'], 70)
        _line('main without the absence term − main', parts, lambda r: r['base_no_absence'] - r['base'], 70)
        _line(f'{name} without the absence term − main', parts, lambda r: r['cand_no_absence'] - r['base'], 70)


def tables(argv):
    parser = argparse.ArgumentParser(prog='lineup_oracle.py tables')
    parser.add_argument('reference', help='The reference replay (main-rebuilt.json)')
    parser.add_argument('variants', nargs='+', help='NAME=output.json, e.g. O1=o1-rebuilt.json; a variant named O0 also gets the identity proof')
    parser.add_argument('--finals', help='Finals folder for the surprise split (default: REFERENCE copy/finals)')
    args = parser.parse_args(argv)
    base = json.loads(Path(args.reference).read_text())
    finals_dir = Path(args.finals) if args.finals else Path(base['copy']) / 'finals'
    print(f'reference: {args.reference} (code {base["code"]["commit"]}, package {base["code"]["package_sha256"][:8]}, '
          f'{len(base["games"])} games, copy {base["copy"]}, which {base["which"]}, finals {base.get("finals")})')
    print(f'± is one standard error clustered by Eastern game date (backtest._mean_se), paired on the same games; negative = better than main. '
          f'Material availability surprise: a player the main replay projected for >= {SURPRISE_PROJECTED_MINUTES:g} minutes (conditional on playing) '
          f'who did not play, or a player of the two teams it did not project who played >= {SURPRISE_PLAYED_MINUTES:g} minutes; '
          f'confident: only regulars it also gave participation >= {SURPRISE_CONFIDENT_PARTICIPATION:g}. '
          f'Distance from the close (|model − close|) is the low-noise check: did the oracle move the margin toward the line that knew the news?')
    for spec in args.variants:
        name, path = spec.split('=', 1)
        cand = json.loads(Path(path).read_text())
        if any(cand.get(k) != base.get(k) for k in ('mode', 'which', 'finals')):
            print(f'WARNING: {name} differs from the reference in mode, run choice or finals source')
        print(f'\n=== {name}: {path} ({cand["code"]["commit"]}, package {cand["code"]["package_sha256"][:8]}, {len(cand["games"])} games)')
        if name == 'O0':
            o0_proof(base, cand)
        audits = [g.get('oracle') or {} for g in cand['games'] if g.get('replayed')]
        if any(a.get('level') for a in audits):
            count = lambda key: sum(len(a.get(key) or []) for a in audits)
            print(f'  oracle audit over {len(audits)} replayed games: players made available {count("to_available")}, made out {count("to_out")}, '
                  f'kept out {sum(a.get("kept_out", 0) for a in audits)}; participation set to 1: {sum(a.get("participation_set", 0) for a in audits)}; '
                  f'starters marked {sum(a.get("starters_marked", 0) for a in audits)}; minutes overridden {sum(a.get("minutes_overridden", 0) for a in audits)}; '
                  f'played but not on the roster: {count("unrostered_played")} players, '
                  f'{math.fsum(a.get("unrostered_played_minutes", 0.) for a in audits):.0f} minutes; '
                  f'lineups replaced {sum(len(a.get("lineups") or {}) if isinstance(a.get("lineups"), dict) else 0 for a in audits)} team-games')
        status = {}
        for g in cand['games']:
            if g.get('replayed'):
                key = (g['replayed'].get('margin_status'), g['replayed'].get('margin_reason'))
                status[key] = status.get(key, 0) + 1
        print(f'  margin forecast status: {json.dumps({f"{k[0]}/{k[1]}": v for k, v in sorted(status.items(), key=str)})}')
        table(name, rows_for(base, cand, finals_dir))


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('replay', 'tables'):
        raise SystemExit(__doc__)
    (replay if sys.argv[1] == 'replay' else tables)(sys.argv[2:])


if __name__ == '__main__':
    main()
