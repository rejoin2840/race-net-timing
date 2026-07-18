#!/bin/bash
# check.sh — the test gate. Runs locally before every commit, and in GitHub
# Actions on every push/PR (.github/workflows/ci.yml runs the fast tier only).
#
#   ./check.sh          fast: run every tests/test_*.py (standalone runners, no pytest)
#   ./check.sh --full   also run the IMSA regression suite (validate_races.py —
#                       rebuilds 6 replay archives, takes minutes; run it before
#                       committing any calculator/replay/evaluator change)
#
# Archives for --full live in ARCHIVE_DIR (config.json, default ~/Downloads).
cd "$(dirname "$0")" || exit 1
PY=./venv/bin/python
fail=0

echo "== unit tests =="
for t in tests/test_*.py; do
  echo "-- $t"
  QT_QPA_PLATFORM=offscreen "$PY" "$t" || fail=1
done

if [ "$1" = "--full" ]; then
  echo "== IMSA regression suite (validate_races.py) =="
  "$PY" src/validate_races.py || fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo ""
  echo "CHECK FAILED"
  exit 1
fi
echo ""
echo "ALL CHECKS PASSED"
