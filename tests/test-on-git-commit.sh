#!/bin/bash
# tests/test-on-git-commit.sh — zero-dependency tests for hooks/on-git-commit.sh
# Run: bash tests/test-on-git-commit.sh
# Exits 0 on all-pass, 1 on any failure.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$HERE/../hooks/on-git-commit.sh"
REPO_ROOT="$HERE/.."

FAILS=0

# run_hook <stdin-json> — invokes the hook with the given JSON on stdin,
# no transcript. Prints stdout, returns hook exit code.
run_hook() {
  printf '%s' "$1" | bash "$HOOK"
}

# run_hook_with_transcript <stdin-json> <transcript-file> — sets
# VAULT_SESSION_TRANSCRIPT before invoking the hook.
run_hook_with_transcript() {
  VAULT_SESSION_TRANSCRIPT="$2" bash "$HOOK" <<<"$1"
}

assert_contains() {
  if printf '%s' "$1" | grep -F -- "$2" >/dev/null 2>&1; then
    echo "  ok: contains '$2'"
  else
    echo "  FAIL: expected output to contain '$2'"
    echo "  ---- got ----"; printf '%s\n' "$1" | sed 's/^/  /'; echo "  -------------"
    FAILS=$((FAILS + 1))
  fi
}

assert_absent() {
  if printf '%s' "$1" | grep -F -- "$2" >/dev/null 2>&1; then
    echo "  FAIL: output should NOT contain '$2'"
    echo "  ---- got ----"; printf '%s\n' "$1" | sed 's/^/  /'; echo "  -------------"
    FAILS=$((FAILS + 1))
  else
    echo "  ok: absent '$2'"
  fi
}

# Test 1: non-commit Bash command → hook emits nothing (empty stdout).
echo "Test 1: non-commit Bash is silent"
OUT=$(run_hook '{"tool_input":{"command":"ls -la"},"tool_response":{"stdout":"file1\nfile2"}}' || true)
if [[ -z "$OUT" ]]; then
  echo "  ok: empty output for non-commit Bash"
else
  echo "  FAIL: expected empty output, got:"; printf '%s\n' "$OUT" | sed 's/^/  /'
  FAILS=$((FAILS + 1))
fi

echo "----"
if [[ "$FAILS" -eq 0 ]]; then echo "ALL PASS"; exit 0; else echo "$FAILS FAIL(S)"; exit 1; fi
