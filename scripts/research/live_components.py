"""Live-only components switched off one at a time on the live replay, scored against actual results.

The live system trails its own production-path backtest over the same weeks of the season
(error decomposition, 2026-11-10). The production-path backtest lacks a few inputs only the
live path has; this script replays the live ledger (``live_replay.py``, rebuilt mode) with each
of them switched off just before ``model.forecast``, so we can see which ones make live better or
worse against what actually happened. The package itself is unchanged; every variant works on a
shallow copy of the prepared snapshot, so a later run never sees a removed input.

    none              the hook is installed and changes nothing: must reproduce the reference replay
    no-role           no role context: ``snapshot['role_context']`` removed, so no verified injury exit
                      is left out of a player's normal-role minutes (``role_context.role_excluded_game_ids``
                      returns no game, in ``model._project_player`` and ``profiles``)
    no-shot-quality   no shot-location shooting adjustment: ``snapshot['shot_quality']`` removed, so
                      ``shot_quality.adjust_shooting`` keeps the box-score percentages
    no-priors         no prior-season aggregate priors: ``snapshot['historical_priors']`` removed, so
                      ``historical.season_prior`` returns None (``model``, ``boxscore_model``, ``profiles``)
    no-live-only      the three above together: the live path closest to the production-path backtest
    no-absence        the team-rating absence term off: ``margins.ABSENCE_RATING_PER_MINUTE`` = 0 for the
                      run (the player-sum half still withholds the out players)
    half-absence      the absence term at half size: ``margins.ABSENCE_RATING_PER_MINUTE`` halved for the run
    official-absence  the absence term counts only players out on an official NBA injury report
                      (roster ``status_basis`` ``explicit_report_status`` with an nba.com source), as the
                      backtest's T-minus-30 report does; any other basis adds no absent minutes

Shot profiles (``shot_profiles.apply_shot_profiles``) are not a variant: they split the expected
2PA/3PA into zones and leave every scoring estimate unchanged (its docstring and code).

Every replayed row carries a ``components`` audit: the players with an injury exit left out of the
role, with a shot-quality change to scoring, with a prior-season prior, and the absent minutes the
term counted by roster status basis and source host.

Run one variant (heavy: through the flock/scope/guard wrapper; the arguments after ``--`` go to
live_replay.py unchanged):

    python3 scripts/research/live_components.py replay --variant no-role --immutable-ledger -- \\
        --copy COPY --ledger LEDGER --artifacts ARTIFACTS --no-guard --output-rebuilt OUT/no-role-rebuilt.json

Print the tables (``lineup_oracle.py tables``: paired against the reference on the same games, error
against actual results, ± one SE clustered by Eastern date; a variant named O0 also gets the
byte-identity proof), then the audit summary:

    python3 scripts/research/live_components.py tables REFERENCE.json O0=none.json no-role=no-role.json ...
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import live_replay as L  # noqa: E402  (puts the package beside it on the path)
import lineup_oracle as LO  # noqa: E402

REMOVED = {'none': (), 'no-role': ('role_context',), 'no-shot-quality': ('shot_quality',),
           'no-priors': ('historical_priors',), 'no-live-only': ('role_context', 'shot_quality', 'historical_priors'),
           'no-absence': (), 'half-absence': (), 'official-absence': ()}
OFFICIAL_HOSTS = ('ak-static.cms.nba.com', 'official.nba.com')

_state = dict(variant='none', snapshot=None, audits=[])


def _host(url):
    url = str(url or '')
    return url.split('/')[2] if '//' in url else (url[:40] or None)


def is_official_out(player):
    return (player.get('status') == 'out' and player.get('status_basis') == 'explicit_report_status'
            and _host(player.get('status_source_url')) in OFFICIAL_HOSTS)


def audit_of(snapshot, result, removed):
    """What the components did in this forecast, and the absent minutes the term counted by basis."""
    from nba_forecast import margins
    roster = {(p['team_id'], p['player_id']): p for p in snapshot['roster']['players']}
    predictions = result.get('predictions') or []
    audit = dict(variant=_state['variant'], removed=list(removed),
                 present={k: k in snapshot for k in ('role_context', 'shot_quality', 'historical_priors')},
                 role_excluded=0, shot_quality_changed=0, historical_prior=0, absence_by_basis={})
    by_basis = Counter()
    for p in predictions:
        flags, comps = p.get('flags') or (), p.get('components') or {}
        audit['role_excluded'] += 'injury_shortened_role_excluded' in flags
        audit['shot_quality_changed'] += bool((comps.get('shot_quality') or {}).get('scoring_changed'))
        audit['historical_prior'] += bool(comps.get('historical_prior'))
        absence = comps.get('absence')
        if p.get('status') == 'out' and absence:
            minutes = absence.get('minutes_per_game')
            if (absence.get('played_recent_team_game') and L._finite(minutes)
                    and minutes >= margins.ABSENCE_MINIMUM_MINUTES):
                r = roster.get((p['team_id'], p['player_id'])) or {}
                by_basis[f"{r.get('status_basis')}|{_host(r.get('status_source_url'))}"] += minutes
    audit['absence_by_basis'] = dict(sorted(by_basis.items()))
    return audit


def install(variant):
    """Wrap ``model.forecast`` (and the absence term for its two variants); 'none' installs the wrapper and changes nothing."""
    from nba_forecast import margins as G, model as M
    _state.update(variant=variant)
    removed = REMOVED[variant]
    original = dict(forecast=M.forecast, absent_minutes=G.absent_minutes)

    def forecast(snapshot, history):
        prepared = dict(snapshot)  # shallow: a removed input stays in the caller's snapshot
        for key in removed:
            prepared.pop(key, None)
        _state['snapshot'] = prepared
        try:
            result = original['forecast'](prepared, history)
        finally:
            _state['snapshot'] = None
        _state['audits'].append(audit_of(prepared, result, removed))
        return result

    def official_absent_minutes(predictions, game_id, team_id):
        snapshot = _state['snapshot']
        if snapshot is None:
            raise RuntimeError('official-absence: absent_minutes called outside a wrapped forecast')
        official = {p['player_id'] for p in snapshot['roster']['players'] if p['team_id'] == team_id and is_official_out(p)}
        return original['absent_minutes']([p for p in predictions if p.get('player_id') in official], game_id, team_id)

    M.forecast = forecast
    if variant == 'no-absence':
        G.ABSENCE_RATING_PER_MINUTE = 0.
    elif variant == 'half-absence':
        G.ABSENCE_RATING_PER_MINUTE = G.PARAMETERS['absence_rating_per_minute'] / 2
    elif variant == 'official-absence':
        G.absent_minutes = official_absent_minutes

    identity = L.code_identity

    def code_identity():
        code = identity()
        code['components'] = dict(variant=variant, removed=list(removed), script=str(Path(__file__).resolve()),
                                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        code['commit'] = f"{code['commit']} + live components {variant}"
        return code
    L.code_identity = code_identity

    replay_game = L.replay_game

    def replay_game_with_audit(*args, **kwargs):
        _state['audits'] = []
        rows = replay_game(*args, **kwargs)
        for row in rows.values():
            row['components'] = deepcopy(_state['audits'][-1]) if _state['audits'] else None
        return rows
    L.replay_game = replay_game_with_audit


def replay(argv):
    parser = argparse.ArgumentParser(prog='live_components.py replay')
    parser.add_argument('--variant', choices=list(REMOVED), required=True)
    parser.add_argument('--immutable-ledger', action='store_true',
                        help='Open --ledger immutable (a copy nobody writes, with an empty -wal): no -shm writes into it')
    args, rest = parser.parse_known_args(argv)
    rest = [a for a in rest if a != '--']
    if args.immutable_ledger:
        init = L.Ledger.__init__

        def immutable_init(self, path, *, immutable=False):
            init(self, path, immutable=True)
        L.Ledger.__init__ = immutable_init
    install(args.variant)
    sys.argv = [str(HERE / 'live_replay.py')] + rest
    L.main()


def audit_summary(specs):
    print('\n=== component audit (per replayed game, summed; absent minutes the absence term counted, by roster basis|source host)')
    for spec in specs:
        name, path = spec.split('=', 1)
        games = [g for g in json.loads(Path(path).read_text())['games'] if g.get('replayed')]
        audits = [g.get('components') or {} for g in games]
        missing = sum(1 for a in audits if not a)
        basis = Counter()
        for a in audits:
            basis.update(a.get('absence_by_basis') or {})
        present = Counter(k for a in audits for k, v in (a.get('present') or {}).items() if v)
        print(f'  {name}: {len(games)} replayed games ({missing} without an audit); inputs present in '
              f'{json.dumps(dict(sorted(present.items())))} games; players with an injury exit left out of the role '
              f'{sum(a.get("role_excluded", 0) for a in audits)}, shot quality changing scoring {sum(a.get("shot_quality_changed", 0) for a in audits)}, '
              f'with a prior-season prior {sum(a.get("historical_prior", 0) for a in audits)}')
        total = math.fsum(basis.values())
        print(f'    absent minutes counted: {total:.0f}' + ''.join(f'; {k} {v:.0f}' for k, v in sorted(basis.items(), key=lambda kv: -kv[1])))


def tables(argv):
    variants = [a for a in argv[1:] if '=' in a]
    LO.tables(argv)
    audit_summary(variants)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('replay', 'tables'):
        sys.exit(__doc__)
    (replay if sys.argv[1] == 'replay' else tables)(sys.argv[2:])


if __name__ == '__main__':
    main()
