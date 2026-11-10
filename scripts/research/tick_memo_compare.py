"""Summarize a tick_memo_check.sh results folder: every digest must agree between main and the branch."""
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
ticks = [json.loads(l) for l in (out/'tick_identity.jsonl').read_text().splitlines() if l.startswith('{')]
keys = ('merged', 'merged_order', 'pages', 'pages_digest', 'index', 'summaries', 'enrich_skipped', 'merge_errors')
ref = {k: ticks[0][k] for k in keys}
print('(a)+(b) history and pages identical across', len(ticks), 'runs:', all({k: t[k] for k in keys} == ref for t in ticks))
for t in ticks:
    print('   %-6s history phase cpu %5.2f s, history phase + sync cpu %5.2f s, process cpu %5.2f s' % (t['code'], t['history_phase_cpu'], t['both_phases_cpu'], t['process_cpu_total']))
runs, label = {}, None
for line in (out/'prepare_identity.log').read_text().splitlines():
    if line.startswith('=='):
        label = line[3:]; runs[label] = []
    elif line.startswith('{'):
        runs[label].append(json.loads(line))
sig = lambda d: (d['game'], d['digests']['model_enhancements'], d['digests']['shot_profiles'], d['digests']['role_context'],
                 d['digests']['shot_quality'], d['preflight_stable'], d['preflight'], d['profiles'], d['forecast'])
base = [sig(d) for d in runs.get('main', [])]
if not base or any(len(rows) != len(base) for rows in runs.values()):
    sys.exit('(c) FAILED: a prepare run stopped early: ' + str({label: len(rows) for label, rows in runs.items()}))
for label, rows in runs.items():
    print('(c) %-16s identical to main: %s; first game %.2f s, later games %s s' % (label, [sig(d) for d in rows] == base,
          rows[0]['total'], [d['total'] for d in rows[1:]]))
