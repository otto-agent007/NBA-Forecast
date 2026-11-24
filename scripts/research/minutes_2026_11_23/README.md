# Minutes, 2026-11-23: the 240-minute budget, who plays, and what the budget cannot see

The measurements behind `model.BUDGET_*`, `SQUEEZE_*` and `TYPICAL_*`
(`docs/backtest.md`, "Reference: 2026-11-23"). Standard library only, except
`predictable.py` (numpy). The three analysis scripts of the changes read two
files and write nothing: `dump_roles.py`'s file from a
replay, and that replay's own `rows.json.gz`. Differences are paired per player,
± one standard error clustered by game; constants are picked on two seasons and
scored on the third.

| Script | Question |
|---|---|
| `dump_roles.py` | Runs the backtest unchanged and writes what the budget saw for every projected player: role minutes, participation probability before and after the squeeze, the budget's scale, the published minutes, games for the team, games missed since his last one, whether he started it, his season average. A replay is a heavy job: run it like the gate |
| `budget_rules.py` | Who gives up or takes on minutes when a team's roles do not add to 240 (median of actual minus role minutes by role level and by the team's total before the budget); the minutes error of the rule used until 2026-11-23 and of the share rule; with `grid`, the share rule's constants out of season |
| `participation_squeeze.py` | Mean probability beside the share who played, by role level and team total; the logit shift per twenty minutes of shortfall scored on Brier and on minutes after the share budget, out of season |
| `after_budget.py` | The terms applied after the budget (minutes above the knee at half, the first game back, started the last game or not), each alone and together: minutes error, points error (first order) and the mean points bias; the pull toward the season average that lowers error but moves the mean, and was left out; with `grid`, each term's constants out of season |
| `predictable.py` | How much of the minutes miss is predictable before the game: a boosted fit on about fifty pregame features built from the histories, leave one season out, against a calibration of the forecast level alone; and sturdier role averages substituted for the plain one. Reads a reference's `rows.json.gz`, the gate's histories and team games |

```bash
python3 scripts/research/minutes_2026_11_23/dump_roles.py /tmp/roles.jsonl <the gate's arguments> --output /tmp/roles-replay
python3 scripts/research/minutes_2026_11_23/budget_rules.py /tmp/roles.jsonl /tmp/roles-replay/rows.json.gz grid
python3 scripts/research/minutes_2026_11_23/participation_squeeze.py /tmp/roles.jsonl /tmp/roles-replay/rows.json.gz
python3 scripts/research/minutes_2026_11_23/after_budget.py /tmp/roles.jsonl /tmp/roles-replay/rows.json.gz grid
```

What was measured and not built: on the reference rows of 2026-11-22, before
these changes, a boosted fit of the minutes miss on pregame features lowers the
error 0.27 minutes out of season (points 0.044 first order), so the signal is
there; a calibration of the forecast level alone is worth 0.04; sturdier role
averages (overtime-normalised, blowouts discounted, outliers clipped, a
weighted median) are worth 0.01 or less (`predictable.py`). After the budget, a
cut for deep-bench roles by participation probability and a pull toward the
season average lower the error further but move the mean points bias, which
the gate does not allow (`after_budget.py`, the `+ bench` and `+ season` rows).
