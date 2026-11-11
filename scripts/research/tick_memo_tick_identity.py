"""The tick's history phase and player sync, as one tick runs them, on a data folder; digests and timings.

Usage: PYTHONPATH=<repo> python3 tick_memo_tick_identity.py <data-dir> [--history FILE ...]
Runs with whichever code is on PYTHONPATH (main: enrich + merge in the history phase and again inside
sync_player_data; branch: tick_memo shared by both). No shot fetches (max_shot_games=0), network blocked.
Prints the merged history digest, the player pages and index digests without ``as_of``, and the
process CPU/wall time of the two phases (run it several times in fresh processes for the idle-tick figure).
"""
import sys, time, json, hashlib, resource
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent))
from tick_memo_offline import block_network, load_history, HISTORY
block_network()
from pathlib import Path
T0 = time.monotonic(); C0 = time.process_time()
folder = Path(sys.argv[1])
paths = sys.argv[sys.argv.index('--history')+1:] if '--history' in sys.argv else [HISTORY]
history = [r for p in paths for r in load_history(p)]
t_load = time.monotonic()-T0
from nba_forecast.schedule import canonical
from nba_forecast.validation import validate_history
D = lambda v: hashlib.sha256(canonical(v)).hexdigest()[:16]
roster = json.loads((folder/'roster.json').read_text())
schedule = json.loads((folder/'schedule/latest.json').read_text())
w = time.monotonic(); c = time.process_time()
try:
    from nba_forecast.tick_memo import HistoryMemo, history_key
    branch = True
except ImportError:
    branch = False
from nba_forecast.player_sync import sync_player_data
if branch:
    memo = HistoryMemo(folder, history, history_key(paths))
    memo.validate_input()
    merged, skipped, errors, key, failure = memo.merged()
    phase_history = (time.monotonic()-w, time.process_time()-c)
    synced = sync_player_data(folder, merged, roster=roster, max_shot_games=0, schedule=schedule, memo=memo)
else:
    from nba_forecast.player_sync import enrich_final_cache
    from nba_forecast.operations import _merge_finals
    validate_history(history)
    skipped = enrich_final_cache(folder, skipped=[])
    errors = []
    merged = _merge_finals(history, folder/'finals', errors=errors)
    phase_history = (time.monotonic()-w, time.process_time()-c)
    synced = sync_player_data(folder, merged, roster=roster, max_shot_games=0, schedule=schedule)
phase_total = (time.monotonic()-w, time.process_time()-c)
strip = lambda page: {k: v for k, v in page.items() if k != 'as_of'}
pages = {p.stem: strip(json.loads(p.read_text())) for p in sorted((folder/'players').glob('*.json')) if p.stem != 'index'}
index = json.loads((folder/'players'/'index.json').read_text())
index_cmp = {k: v for k, v in index.items() if k not in ('as_of', 'player_summaries')}
summaries = {pid: {k: v for k, v in s.items() if k != 'as_of'} for pid, s in index.get('player_summaries', {}).items()}
print(json.dumps(dict(code='branch' if branch else 'main', merged_rows=len(merged), merged=D(merged), merged_order=D([[r['game_id'], r['player_id']] for r in merged]),
    enrich_skipped=len(skipped), merge_errors=len(errors), pages=len(pages), pages_digest=D(pages), index=D(index_cmp), summaries=D(summaries),
    history_phase_wall=round(phase_history[0], 2), history_phase_cpu=round(phase_history[1], 2),
    both_phases_wall=round(phase_total[0], 2), both_phases_cpu=round(phase_total[1], 2), load_history_s=round(t_load, 2),
    process_cpu_total=round(time.process_time()-C0, 2)), sort_keys=True))
