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

# Test 2: fix commit → worth-recording lean + hash + message.
echo "Test 2: fix commit yields worth-recording lean"
STDIN='{"tool_input":{"command":"git commit -m \"fix: handle rate limit window reset\""},"tool_response":{"stdout":"[main abc1234] fix: handle rate limit window reset\n 2 files changed, 8 insertions(+)"}}'
OUT=$(run_hook "$STDIN" || true)
assert_contains "$OUT" 'hookEventName":"PostToolUse"'
assert_contains "$OUT" 'git commit landed: abc1234'
assert_contains "$OUT" 'message: fix: handle rate limit window reset'
assert_contains "$OUT" 'initial_verdict: worth-recording (prefix: fix)'
assert_contains "$OUT" 'never assert a debugging fact you cannot quote'

# Test 3: chore commit → not-worth-recording lean; hash from bracket form.
echo "Test 3: chore commit yields not-worth-recording lean"
STDIN='{"tool_input":{"command":"git commit -m \"chore: bump version\""},"tool_response":{"stdout":"[feature/xyz def98765] chore: bump version\n 1 file changed"}}'
OUT=$(run_hook "$STDIN" || true)
assert_contains "$OUT" 'git commit landed: def98765'
assert_contains "$OUT" 'initial_verdict: not-worth-recording (prefix: chore)'

# Test 4: no-prefix / unknown message → unknown lean (not crash).
echo "Test 4: no-prefix message yields unknown lean"
STDIN='{"tool_input":{"command":"git commit -m \"just a note\""},"tool_response":{"stdout":"[main 111aaaa] just a note\n 1 file changed"}}'
OUT=$(run_hook "$STDIN" || true)
assert_contains "$OUT" 'initial_verdict: unknown (prefix: none)'

# Test 5: changed-file list appears in output (temp git repo).
echo "Test 5: files block lists changed files"
TMPREPO="$(mktemp -d)"
( cd "$TMPREPO"; git init -q; git config user.email t@t.t; git config user.name t
  echo a > a.txt && git add a.txt && git commit -q -m "init"
  echo b > b.txt && git add b.txt && git commit -q -m "fix: add b" )
STDIN='{"tool_input":{"command":"git commit -m \"fix: add b\""},"tool_response":{"stdout":"[main zzz1111] fix: add b\n 1 file changed"}}'
OUT=$(printf '%s' "$STDIN" | (cd "$TMPREPO" && bash "$HOOK") || true)
assert_contains "$OUT" 'files:'
assert_contains "$OUT" 'b.txt'
rm -rf "$TMPREPO"

# Test 6: first commit (no HEAD~1) → files placeholder, not an error.
echo "Test 6: no-parent commit yields files placeholder"
TMPREPO="$(mktemp -d)"
( cd "$TMPREPO"; git init -q; git config user.email t@t.t; git config user.name t
  echo a > a.txt && git add a.txt && git commit -q -m "init" )
STDIN='{"tool_input":{"command":"git commit -m \"init\""},"tool_response":{"stdout":"[master aaa0000] init\n 1 file changed"}}'
OUT=$(printf '%s' "$STDIN" | (cd "$TMPREPO" && bash "$HOOK") || true)
assert_contains "$OUT" 'files:'
assert_absent "$OUT" 'fatal:'
rm -rf "$TMPREPO"

# Test 7: >50 changed files → truncated + "... (N more)".
echo "Test 7: file list truncated past 50"
TMPREPO="$(mktemp -d)"
( cd "$TMPREPO"; git init -q; git config user.email t@t.t; git config user.name t
  echo seed > seed.txt && git add seed.txt && git commit -q -m "seed"
  for i in $(seq 1 60); do echo "x$i" > "file$i.txt"; done
  git add -A && git commit -q -m "bulk" )
STDIN='{"tool_input":{"command":"git commit -m \"bulk\""},"tool_response":{"stdout":"[main bbb2222] bulk\n 60 files changed"}}'
OUT=$(printf '%s' "$STDIN" | (cd "$TMPREPO" && bash "$HOOK") || true)
assert_contains "$OUT" '... ('
rm -rf "$TMPREPO"

echo "----"
if [[ "$FAILS" -eq 0 ]]; then echo "ALL PASS"; exit 0; else echo "$FAILS FAIL(S)"; exit 1; fi
