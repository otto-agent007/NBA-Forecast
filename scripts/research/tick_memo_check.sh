#!/bin/bash
# Main vs perf/tick-memo on one copy of a production data folder.
# Usage: tick_memo_check.sh <data-dir-copy> <main-checkout> <branch-checkout> [results-dir]
#   SNAPSHOTS=<dir> uses saved snapshot files instead of reading the copy's ledger.
# Makes two hard-linked views of the copy (every writer in the package replaces files by rename,
# so the views never change each other's files), then:
#   (a)+(b) tick_memo_tick_identity.py: merged history, player pages and index digests; history-phase and sync CPU
#           (main twice, branch cold then warm twice; a branch run's warm CPU is the idle-tick figure)
#   (c)     tick_memo_prepare_identity.py on the copy's newest snapshots: main, branch cold, branch warm twice
# Never point it at production: the views are hard links. ionice does not protect the live tick on a host whose
# disk scheduler ignores I/O classes (mq-deadline), so run it when the service is quiet. Results go to
# [results-dir]/results-<timestamp>/ (default: the current directory).
set -euo pipefail
COPY=$(realpath "$1"); MAIN=$(realpath "$2"); BRANCH=$(realpath "$3")
case "$COPY" in */data/operations/production*) echo "point this at a copy, not production" >&2; exit 2;; esac
HERE=$(dirname "$(realpath "$0")"); OUT=${4:-$PWD}/results-$(date +%Y%m%d-%H%M); mkdir -p "$OUT"
export PYTHONDONTWRITEBYTECODE=1
cp -al "$COPY" "$OUT/main-view"; cp -al "$COPY" "$OUT/branch-view"
if [ -n "${SNAPSHOTS:-}" ]; then cp "$SNAPSHOTS"/*.json "$OUT/snapshots/" 2>/dev/null || { mkdir -p "$OUT/snapshots"; cp "$SNAPSHOTS"/*.json "$OUT/snapshots/"; }
else python3 "$HERE/tick_memo_snapshots.py" "$COPY" "$OUT/snapshots" 4 | tee "$OUT/snapshots.txt"; fi
SNAPS=$(ls "$OUT"/snapshots/*.json)
run() { nice -n 19 env PYTHONPATH="$1" python3 "${@:2}"; }
{ for i in 1 2; do run "$MAIN" "$HERE/tick_memo_tick_identity.py" "$OUT/main-view"; done
  for i in 1 2 3; do run "$BRANCH" "$HERE/tick_memo_tick_identity.py" "$OUT/branch-view"; done; } | tee "$OUT/tick_identity.jsonl"
{ echo '== main'; run "$MAIN" "$HERE/tick_memo_prepare_identity.py" "$OUT/main-view" $SNAPS
  echo '== branch cold'; run "$BRANCH" "$HERE/tick_memo_prepare_identity.py" "$OUT/branch-view" $SNAPS --clear-replay-cache
  echo '== branch warm'; run "$BRANCH" "$HERE/tick_memo_prepare_identity.py" "$OUT/branch-view" $SNAPS
  echo '== branch warm 2'; run "$BRANCH" "$HERE/tick_memo_prepare_identity.py" "$OUT/branch-view" $SNAPS; } | tee "$OUT/prepare_identity.log"
python3 "$HERE/tick_memo_compare.py" "$OUT"
