# NBA-Forecast implementation plan

**Goal:** Ship a tested ingest → forecast → immutable ledger → evaluation CLI.
**Spec:** [design.md](design.md). **Stack:** Python 3.11+ standard library.

Global constraints: regular season only; complete sourced roster; all players
represented; no future-known facts; no paid or LLM dependency; no auto-merge.

## Tasks and ownership

- [x] Data/CLI (root): `validation.py`, `data.py`, `__main__.py`; tests first for
  invalid/future/preseason rows, official boxscore mapping and CLI roundtrip.
  `validate_inputs(snapshot, history, scope='league') -> None` raises ValueError.
  `normalize_boxscore(payload, source_url, known_at) -> list[dict]`.
- [x] Model: `model.py`, `tests/test_model.py`; implement shared forecast contract.
  Tests first for shrinkage, all-pool coverage, cold starts, trade/role alerts,
  minutes consistency and bounded, auditable opponent adjustment.
- [x] Ledger: `store.py`, `tests/test_store.py`; implement shared save/evaluate
  contracts. Tests first for clock vs asserted cutoff, immutability, pending
  results, correction revisions, metrics, DNP and replay exclusion.
- [x] Integration: runnable synthetic fixture, data-source documentation, CI and
  README; full unittest suite, CLI roundtrip, independent code review and fixes.
- [ ] Submission: create branch/commit from verified main via GitHub connector,
  open PR, inspect checks. Leave unmerged.

## Review focus

1. Temporal leakage from prior-season or late-known data (validation/model tests).
2. Plausible-looking unsupported forecasts for bench players/rookies (model).
3. Silent missing-results-to-zero or repeated forecasts inflating metrics (store).
4. Invalid API schema masquerading as a verified full roster (data/validation).
5. Partial team history biasing opponent effects and rotation totals (model).

Execution uses independent file ownership and one final independent review.
The explicit user instruction to complete and submit the first version supplies
authorization; no additional design approval is necessary for reversible work.
