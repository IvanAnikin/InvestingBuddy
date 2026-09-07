#!/usr/bin/env bash
#
# Run the gates a V3 slice must pass, locally.
#
# WHY THIS EXISTS
# ===============
# `api-ci.yml` and `web-ci.yml` trigger on `main` only, so a PR into
# `develop/v3` gets NO automatic checks. Whether to add `develop/v3` to those
# workflows is OPEN DECISION #17 and it belongs to the user — it is a preference
# about CI minutes, and it touches a file that also lives on `main`.
#
# Until it is decided, every slice runs its gates by hand and records the output
# in the PR body. This script is that, in one command, running the EXACT
# commands the workflows run — so "it passed locally" means the same thing CI
# would have meant, rather than whatever the person remembered to type.
#
# `mypy` is included even though CI does not run it, because it is a real local
# gate for this repository. Its count is SCOPE-DEPENDENT: `mypy app` and a
# broader scope including `tests/` produce very different numbers, so compare
# the SAME command between `develop/v3` and the branch. Diffing a narrow
# baseline against a broad one manufactures a regression that is not there.
#
# Usage:
#   scripts/v3-gates.sh          # everything
#   scripts/v3-gates.sh api      # backend only
#   scripts/v3-gates.sh web      # frontend only
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-all}"
FAILED=0

step() { printf '\n\033[1m── %s\033[0m\n' "$1"; }

run() {
  local label="$1"; shift
  step "$label"
  printf '$ %s\n' "$*"
  if "$@"; then
    printf '\033[32mPASS\033[0m %s\n' "$label"
  else
    printf '\033[31mFAIL\033[0m %s\n' "$label"
    FAILED=1
  fi
}

if [[ "$TARGET" == "all" || "$TARGET" == "api" ]]; then
  cd "$ROOT/apps/api" || exit 1
  PY="./.venv/bin/python"
  if [[ ! -x "$PY" ]]; then
    echo "No venv at apps/api/.venv — create it before running the gates." >&2
    exit 1
  fi
  run "api · ruff (api-ci.yml)"      ./.venv/bin/ruff check .
  run "api · pytest (api-ci.yml)"    "$PY" -m pytest tests/ -q

  # mypy is NOT in CI and is NOT clean: `mypy app` has a long-standing baseline
  # of pre-existing errors. Failing on a non-zero count would make this gate red
  # on every run, which trains a reader to ignore it — so what is checked is
  # whether the count went UP.
  #
  # The baseline is scope-dependent: `mypy app` and a broader scope including
  # `tests/` produce very different numbers. Compare the SAME command between
  # `develop/v3` and the branch; diffing a narrow baseline against a broad one
  # manufactures a regression that is not there.
  step "api · mypy app (local gate, baseline-compared)"
  printf '$ ./.venv/bin/mypy app\n'
  MYPY_OUT="$(./.venv/bin/mypy app 2>&1 | tail -1)"
  printf '%s\n' "$MYPY_OUT"
  COUNT="$(printf '%s' "$MYPY_OUT" | sed -n 's/^Found \([0-9]*\) error.*/\1/p')"
  [[ -z "$COUNT" ]] && COUNT=0
  BASELINE_FILE="$ROOT/scripts/mypy-baseline.txt"
  BASELINE="$(cat "$BASELINE_FILE" 2>/dev/null || echo "$COUNT")"
  if (( COUNT > BASELINE )); then
    printf '\033[31mFAIL\033[0m mypy went %s -> %s. New type errors in this branch.\n' \
      "$BASELINE" "$COUNT"
    FAILED=1
  elif (( COUNT < BASELINE )); then
    printf '\033[32mPASS\033[0m mypy went %s -> %s. Update %s.\n' \
      "$BASELINE" "$COUNT" "$BASELINE_FILE"
  else
    printf '\033[32mPASS\033[0m mypy unchanged at %s (baseline).\n' "$COUNT"
  fi
fi

if [[ "$TARGET" == "all" || "$TARGET" == "web" ]]; then
  cd "$ROOT/apps/web" || exit 1
  run "web · typecheck (web-ci.yml)" npm run typecheck
  run "web · lint (web-ci.yml)"      npm run lint
  run "web · build (web-ci.yml)"     npm run build
fi

printf '\n'
if [[ "$FAILED" -eq 0 ]]; then
  printf '\033[32mAll gates passed.\033[0m Record the output in the PR body.\n'
else
  printf '\033[31mOne or more gates failed.\033[0m A slice does not merge on a red gate.\n'
fi
exit "$FAILED"
