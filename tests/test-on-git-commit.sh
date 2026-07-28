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
# Flow D yields to Flow E (proactive Distill): report mechanical facts only,
# never instruct the host to "Ask the user before recording" — that would
# contradict Flow E's "Do NOT ask, distill now" and paralyze the host.
assert_absent "$OUT" 'Ask the user before recording'
assert_absent "$OUT" 'never assert a debugging fact you cannot quote'

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

# Test 8: transcript with error/retry hits → mechanical_evidence lists them.
echo "Test 8: mechanical evidence from transcript hits"
TMPREPO="$(mktemp -d)"; TMPTRANS="$(mktemp)"
( cd "$TMPREPO"; git init -q; git config user.email t@t.t; git config user.name t
  echo a > a.txt && git add a.txt && git commit -q -m "init"
  echo b > a.txt && git add a.txt && git commit -q -m "fix: rate limit" )
cat > "$TMPTRANS" <<'JSONL'
{"role":"user","content":"it throws an error: NullPointerException"}
{"role":"assistant","content":"let me retry with a different approach"}
{"role":"user","content":"now it failed differently"}
{"role":"assistant","content":"that worked"}
JSONL
STDIN="{\"transcript_path\":\"$TMPTRANS\",\"tool_input\":{\"command\":\"git commit -m \\\"fix: rate limit\\\"\"},\"tool_response\":{\"stdout\":\"[main hit0001] fix: rate limit\\n 1 file changed\"}}"
OUT=$(printf '%s' "$STDIN" | (cd "$TMPREPO" && bash "$HOOK") || true)
assert_contains "$OUT" 'mechanical_evidence:'
assert_contains "$OUT" 'NullPointerException'
assert_contains "$OUT" 'retry'
rm -rf "$TMPREPO" "$TMPTRANS"

# Test 9: no transcript path set → mechanical_evidence says unavailable.
echo "Test 9: missing transcript → no script evidence"
TMPREPO="$(mktemp -d)"
( cd "$TMPREPO"; git init -q; git config user.email t@t.t; git config user.name t
  echo a > a.txt && git add a.txt && git commit -q -m "fix: x" )
STDIN='{"tool_input":{"command":"git commit -m \"fix: x\""},"tool_response":{"stdout":"[main non0002] fix: x\n 1 file changed"}}'
OUT=$(printf '%s' "$STDIN" | (cd "$TMPREPO" && bash "$HOOK") || true)
assert_contains "$OUT" 'mechanical_evidence: (transcript unavailable'
rm -rf "$TMPREPO"

# Test 10: transcript with no keyword hits → 0 keyword hits.
echo "Test 10: clean transcript → 0 keyword hits"
TMPREPO="$(mktemp -d)"; TMPTRANS="$(mktemp)"
( cd "$TMPREPO"; git init -q; git config user.email t@t.t; git config user.name t
  echo a > a.txt && git add a.txt && git commit -q -m "fix: y" )
printf 'hello world\nthis is fine\n' > "$TMPTRANS"
STDIN="{\"transcript_path\":\"$TMPTRANS\",\"tool_input\":{\"command\":\"git commit -m \\\"fix: y\\\"\"},\"tool_response\":{\"stdout\":\"[main zero0003] fix: y\\n 1 file changed\"}}"
OUT=$(printf '%s' "$STDIN" | (cd "$TMPREPO" && bash "$HOOK") || true)
assert_contains "$OUT" 'mechanical_evidence: 0 keyword hits'
rm -rf "$TMPREPO" "$TMPTRANS"

# Test 11: control chars in transcript → output still valid JSON.
echo "Test 11: control chars (tab/CR) don't break JSON"
TMPREPO="$(mktemp -d)"; TMPTRANS="$(mktemp)"
( cd "$TMPREPO"; git init -q; git config user.email t@t.t; git config user.name t
  echo a > a.txt && git add a.txt && git commit -q -m "init"
  echo b > a.txt && git add a.txt && git commit -q -m "fix: ctrl chars" )
printf 'error\tsome tabbed line\r\nretry another\r\n' > "$TMPTRANS"
STDIN="{\"transcript_path\":\"$TMPTRANS\",\"tool_input\":{\"command\":\"git commit -m \\\"fix: ctrl chars\\\"\"},\"tool_response\":{\"stdout\":\"[main ctrl001] fix: ctrl chars\n 1 file changed\"}}"
OUT=$(printf '%s' "$STDIN" | (cd "$TMPREPO" && bash "$HOOK") || true)
# Assert it's valid JSON by parsing with python3.
if printf '%s' "$OUT" | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
  echo "  ok: output is valid JSON despite control chars"
else
  echo "  FAIL: output is not valid JSON:"
  printf '%s\n' "$OUT" | sed 's/^/  /'
  FAILS=$((FAILS + 1))
fi
# And confirm the evidence still surfaced (the tab line still listed as a raw hit).
assert_contains "$OUT" 'some tabbed line'
rm -rf "$TMPREPO" "$TMPTRANS"

echo "----"
if [[ "$FAILS" -eq 0 ]]; then echo "ALL PASS"; exit 0; else echo "$FAILS FAIL(S)"; exit 1; fi
