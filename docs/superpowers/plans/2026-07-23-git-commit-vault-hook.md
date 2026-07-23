# Git Commit Hook → Vault Bug Recording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PostToolUse Bash hook that fires on `git commit` and does the **deterministic** parts (parse commit info, classify prefix into a default lean, mechanically scan the session transcript for debugging-evidence keywords and list the raw hits), re-injecting all of that as `additionalContext`; then a new SKILL.md Flow D where Claude does the **analytic** parts (judge whether the raw hits are real wrong-paths, corroborate or overturn the initial lean) under a hard no-fabrication rule and a user-confirmation gate before any vault write.

**Architecture:** Strict division of labor — *clear deterministic processes go in the script, analysis/scheduling goes to the LLM, and the two must cross-confirm with zero fabrication.* The bash hook (`hooks/on-git-commit.sh`) parses stdin JSON, extracts commit message/hash, runs `git diff --name-only HEAD~1`, **classifies the commit prefix into a default lean via a fixed rule table** (no LLM), and **greps the session transcript file for debugging-evidence keywords, emitting the raw matched lines as mechanical evidence** (it reports only what it finds, never interprets). Claude (per Flow D) reads the mechanical evidence + the live session context, judges whether the raw hits are genuine wrong-paths or noise, and only when script-evidence and LLM-judgment agree (or the disagreement is explicitly flagged and surfaced to the user) does it ask to record. Registered in `plugin.json` under `PostToolUse` with `matcher: "Bash"` + `if: "Bash(git commit*)"` so non-commit Bash never spawns the hook.

**Tech Stack:** Bash (`set -uo pipefail`), git, Claude Code hooks (PostToolUse + `if` matcher), Markdown (SKILL.md). No new dependencies — JSON parsing + transcript scan use `grep` / `sed` / bash builtins, no `jq`, no `bats`.

## Global Constraints

- **Spec of record:** `docs/specs/2026-07-23-git-commit-vault-hook-design.md` — decisions A2/B1/C2 are approved; do not re-litigate them. This plan refines the *division of labor* (script does prefix lean + mechanical evidence scan; LLM does corroboration/judgment) consistent with B1/C2 — the prefix still only sets a *default lean* and the session context still *corroborates*; we've just made the "prefix lean" and the "evidence gathering" deterministic so the LLM is left with genuine analysis, and added a no-fabrication guardrail.
- **No new dependencies** (user red line). No `jq`, no `bats`. JSON parsing + grep assertions use `grep` / `sed` / bash string ops.
- **Hook never blocks commit.** PostToolUse runs after commit completes anyway, but the script must `exit 0` on every path and emit no output on failure/parse-error so it stays silent, never noisy.
- **Strict division of labor (user principle).** Deterministic/clear processes = script (prefix classification, transcript keyword scan, file list). Analysis/judgment = LLM (is this hit a real wrong-path? does the lean hold?). The two cross-confirm — see the no-fabrication rule below.
- **NO FABRICATION (top-level red line, user-mandated).** Claude must never assert a debugging fact it cannot point to a concrete source for. Concretely:
  1. Every corroborating claim must **quote the script's raw matched line** (a literal transcript excerpt) OR be explicitly tagged `UNVERIFIED-BY-SCRIPT` with a one-line reason. "I recall we tried X" / "we probably hit Y" is fabrication and is forbidden.
  2. If the script's mechanical scan returns **zero** keyword hits, Claude may NOT claim a debugging process existed on the basis of session context alone. It may say: "mechanical scan found no evidence; my read of the context suggests Z, but this is unverified — please confirm." It must NOT silently record on that basis.
  3. The script reports **only raw matches** (the exact transcript lines + counts). It never interprets whether a match is "a real wrong-path" — that judgment is the LLM's, and must be stated as judgment, not as fact.
- **Hook does not write the vault.** Unlike `vault-stop.sh`, this hook does not resolve `VAULT_ROOT` and never calls `vault_*` tools — it only re-injects text. Do not copy the `VAULT_ROOT` resolution block into it.
- **Work on a branch** off `main`, never commit to `main` directly (user red line). Do not auto-push.
- **`additionalContext` ≤ 10,000 chars.** Truncate the file list (and the raw-evidence excerpt) when it exceeds the budget. The transcript excerpt is the second unbounded component after the file list — both must be bounded.
- **Surgical.** Only touch the three files this feature needs: `hooks/on-git-commit.sh` (new), `.claude-plugin/plugin.json` (modify), `skills/vault/SKILL.md` (modify — add Flow D). Plus a new test file. No reformatting of unrelated code.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `hooks/on-git-commit.sh` | Create | PostToolUse hook: parse stdin JSON → extract commit message/hash → classify prefix into default lean via fixed rule table → `git diff --name-only HEAD~1` for file list → grep the session transcript for debugging-evidence keywords, list raw matched lines → emit `{hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:"..."}}`. Reports only raw facts; interprets nothing; writes nothing to vault; no `VAULT_ROOT`. |
| `.claude-plugin/plugin.json` | Modify | Register the new hook under `hooks.PostToolUse` with `matcher:"Bash"` + `if:"Bash(git commit*)"`. Leave existing `Stop` hook untouched. |
| `skills/vault/SKILL.md` | Modify | Add **Flow D — Commit-triggered bug evaluation**: the LLM-side analytic layer — read script's initial lean + raw mechanical evidence, judge whether hits are real wrong-paths, corroborate/overturn the lean, enforce the no-fabrication rule, user-confirmation gate. References Flow B threshold + recording procedure. Notes it does not replace the Stop hook. |
| `tests/test-on-git-commit.sh` | Create | Bash test harness: feeds fixed stdin JSON to `on-git-commit.sh` inside a temp git repo + temp transcript fixture, asserts on the emitted `additionalContext` (prefix lean, file list, raw evidence excerpt, no-parent fallback, truncation, non-commit silence). Zero deps. |

---

## Task 1: Scaffold the test harness + first failing test (non-commit Bash is silent)

**Files:**
- Create: `tests/test-on-git-commit.sh`
- Create: `hooks/on-git-commit.sh` (empty stub for this task)

**Interfaces:**
- Produces: a runnable `tests/test-on-git-commit.sh` that exits non-zero on failure, zero on pass; establishes the `run_hook` / `run_hook_with_transcript` / `assert_contains` / `assert_absent` helpers used by every later task. The hook is invoked via `bash "$HOOK"`; a transcript fixture path is passed via the env var `VAULT_SESSION_TRANSCRIPT` (the env var the real hook will read — see Task 4).

- [ ] **Step 1: Create an empty stub hook so the test has something to call**

Create `hooks/on-git-commit.sh` with just the shebang + `exit 0`:

```bash
#!/bin/bash
# on-git-commit.sh — PostToolUse hook for git commit → vault bug evaluation.
# Stub; implementation lands in later tasks.
exit 0
```

Make it executable:

```bash
chmod +x hooks/on-git-commit.sh
```

- [ ] **Step 2: Write the failing test — non-commit Bash produces no output**

Create `tests/test-on-git-commit.sh`:

```bash
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
```

Make it executable:

```bash
chmod +x tests/test-on-git-commit.sh
```

- [ ] **Step 3: Run the test — it must PASS against the stub**

Run: `bash tests/test-on-git-commit.sh`
Expected: `ALL PASS` (the stub emits nothing, which is exactly what Test 1 asserts). If it fails, the harness is broken — fix the harness, not the hook.

- [ ] **Step 4: Commit**

```bash
git add hooks/on-git-commit.sh tests/test-on-git-commit.sh
git commit -m "test: scaffold on-git-commit hook test harness"
```

---

## Task 2: Parse stdin JSON, extract commit message + hash, classify prefix into default lean, emit additionalContext

**Files:**
- Modify: `hooks/on-git-commit.sh`
- Modify: `tests/test-on-git-commit.sh` (append Test 2 + Test 3 + Test 4)

**Interfaces:**
- Consumes: stdin JSON with `tool_input.command` (e.g. `git commit -m "fix: rate limit reset"`) and `tool_response.stdout` (e.g. `[main abc1234] fix: ...\n 2 files changed`).
- Produces: stdout JSON `{hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:"<text>"}}` where `<text>` includes commit hash, message, and an `initial_verdict` line. File-list + transcript-evidence blocks are added in later tasks. The `additionalContext` text format contract (later tasks + Flow D rely on it):
  - Line 1: `git commit landed: <hash>`
  - Line 2: `message: <message>`
  - Line 3: `initial_verdict: <lean> (prefix: <prefix>)` — `<lean>` is one of `worth-recording` / `not-worth-recording` / `unknown`; `<prefix>` is the raw Conventional-Commit type (`fix`, `feat`, `chore`, …) or `none`.
  - Then (Task 3) a `files:` block; then (Task 4) a `mechanical_evidence:` block; then the trailing nudge line `If this fixes a non-trivial bug, evaluate against the vault bug threshold (Flow B); corroborate with the mechanical_evidence below — never assert a debugging fact you cannot quote. Ask the user before recording.` (exact text — Flow D relies on the nudge wording).

**Prefix→lean rule table (deterministic, lives in the script):**
- `fix`, `feat`, `refactor`, `perf`, `build` → `worth-recording`
- `chore`, `docs`, `style`, `test`, `ci` → `not-worth-recording`
- anything else / no prefix → `unknown`

- [ ] **Step 1: Append failing Tests 2, 3, 4 — hash + message + initial_verdict lean**

Append to `tests/test-on-git-commit.sh` **before** the final `echo "----"` summary block:

```bash
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
```

- [ ] **Step 2: Run tests — Tests 2/3/4 must FAIL**

Run: `bash tests/test-on-git-commit.sh`
Expected: Test 1 ok, Tests 2/3/4 FAIL (stub emits nothing). Final line: `3 FAIL(S)`. Confirms the tests fail for the right reason before implementing.

- [ ] **Step 3: Implement the hook — stdin parse + message/hash + prefix lean + JSON emit**

Replace the entire contents of `hooks/on-git-commit.sh`:

```bash
#!/bin/bash
# on-git-commit.sh — PostToolUse hook for git commit → vault bug evaluation.
#
# Division of labor: this script does the DETERMINISTIC parts only —
#   parse stdin JSON, extract commit message/hash, classify the commit prefix
#   into a default lean via a fixed rule table, list changed files, and grep the
#   session transcript for debugging-evidence keywords (reporting raw matches).
# It reports only raw facts; it interprets NOTHING and writes nothing to the
# vault. All judgment (is this hit a real wrong-path? does the lean hold?) is the
# LLM's, per skills/vault/SKILL.md Flow D, under a strict no-fabrication rule.
#
# Silent on every error path (exit 0, no output) — never noisy, never blocks the
# commit (PostToolUse runs post-commit anyway).

set -uo pipefail

PAYLOAD="$(cat)"

# --- guard: only act on a `git commit` command (the hook's if:"Bash(git commit*)"
# matcher already filters; keep this so the script is safe if invoked directly). ---
COMMAND="$(printf '%s' "$PAYLOAD" | grep -o '"command"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*"command"[[:space:]]*:[[:space:]]*"//; s/"$//')"
case "$COMMAND" in
  *git\ commit*) ;;
  *) exit 0 ;;
esac

# --- extract commit message from tool_input.command ---
MSG="$(printf '%s' "$COMMAND" | sed -n 's/.*git commit -m "\([^"]*\)".*/\1/p')"
MSG="${MSG//\\\"/\"}"   # unescape JSON-doubled inner quotes

# --- extract commit hash from tool_response.stdout bracket form [branch hash] ---
STDOUT="$(printf '%s' "$PAYLOAD" | grep -o '"stdout"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*"stdout"[[:space:]]*:[[:space:]]*"//; s/"$//')"
HASH="$(printf '%s' "$STDOUT" | sed -n 's/^\[[^ ]* \([0-9a-f][0-9a-f]*\)\].*/\1/p')"

# --- classify prefix into a default lean (deterministic rule table) ---
PREFIX="$(printf '%s' "$MSG" | sed -n 's/^\([a-z][a-z0-9]*\)\(\(.*\)\)\(:\|(\|!\).*/\1/p')"
# Simplify: take the word up to the first ':' or '(' or '!' — Conventional Commit type.
PREFIX="$(printf '%s' "$MSG" | awk '{
  s=$0; sub(/^([a-z0-9]+)(\(.*)?!?:.*/, "\\1", s);
  if (s==$0) s="none"; print s
}' | head -1)"
case "$PREFIX" in
  fix|feat|refactor|perf|build) LEAN="worth-recording" ;;
  chore|docs|style|test|ci)      LEAN="not-worth-recording" ;;
  *)                             LEAN="unknown"; PREFIX="${PREFIX:-none}" ;;
esac

# --- build additionalContext (files + mechanical_evidence added in later tasks) ---
CTX_FILE="$(mktemp)"
{
  printf 'git commit landed: %s\n' "$HASH"
  printf 'message: %s\n' "$MSG"
  printf 'initial_verdict: %s (prefix: %s)\n' "$LEAN" "$PREFIX"
  printf '\nIf this fixes a non-trivial bug, evaluate against the vault bug threshold (Flow B); corroborate with the mechanical_evidence below — never assert a debugging fact you cannot quote. Ask the user before recording.\n'
} > "$CTX_FILE"

# --- emit PostToolUse JSON (files/mechanical_evidence appended before this in later tasks) ---
CTX="$(cat "$CTX_FILE")"
rm -f "$CTX_FILE"
CTX_ESC="${CTX//\\/\\\\}"
CTX_ESC="${CTX_ESC//\"/\\\"}"
CTX_ESC="${CTX_ESC//$'\n'/\\n}"
printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"%s"}}\n' "$CTX_ESC"
exit 0
```

- [ ] **Step 4: Run tests — Tests 2/3/4 must now PASS**

Run: `bash tests/test-on-git-commit.sh`
Expected: `ALL PASS` (Tests 1–4). If Test 4 fails on the `unknown` lean, the `awk` prefix extractor likely returned the whole message — fix the regex so a message with no `:` yields `none`. Debug by temporarily printing `$PREFIX` after extraction.

- [ ] **Step 5: Commit**

```bash
git add hooks/on-git-commit.sh tests/test-on-git-commit.sh
git commit -m "feat(hook): parse commit + classify prefix into default lean"
```

---

## Task 3: Changed-file list via `git diff --name-only HEAD~1` + truncation

**Files:**
- Modify: `hooks/on-git-commit.sh`
- Modify: `tests/test-on-git-commit.sh` (append Test 5 + Test 6 + Test 7)

**Interfaces:**
- Produces: the `additionalContext` now also contains a `files:` block listing the changed files, one per line prefixed `- `. When `HEAD~1` is unavailable the block is `files: (none — no parent commit or no changed files)`. When the list exceeds 50 files, truncated to first 50 plus `... (N more)`. The `files:` block sits between the `initial_verdict:` line and the trailing nudge.

- [ ] **Step 1: Append failing Test 5 — files block lists changed files (real temp git repo)**

Append before the summary block:

```bash
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
```

- [ ] **Step 2: Append failing Test 6 — no parent (first commit) yields safe placeholder**

Append:

```bash
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
```

- [ ] **Step 3: Append failing Test 7 — >50 files truncates with `... (N more)`**

Append:

```bash
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
```

- [ ] **Step 4: Run tests — Tests 5/6/7 must FAIL**

Run: `bash tests/test-on-git-commit.sh`
Expected: Tests 5/6/7 FAIL (no `files:` block yet). Final line: `3 FAIL(S)`.

- [ ] **Step 5: Implement the file-list block in the hook**

In `hooks/on-git-commit.sh`, replace the block from `# --- build additionalContext ...` through the `{ printf ... } > "$CTX_FILE"` heredoc with:

```bash
# --- changed-file list via git diff --name-only HEAD~1 ---
# Runs in the user's cwd (where the commit happened). HEAD~1 may not exist
# (first commit / shallow clone) — handle gracefully.
FILES_RAW=""
if git rev-parse --verify HEAD~1 >/dev/null 2>&1; then
  FILES_RAW="$(git diff --name-only HEAD~1 2>/dev/null || true)"
fi

MAX_FILES=50
FILE_COUNT=$(printf '%s\n' "$FILES_RAW" | grep -c . || true)
TRUNCATED=0
if [[ "$FILE_COUNT" -gt "$MAX_FILES" ]]; then
  TRUNCATED=$((FILE_COUNT - MAX_FILES))
  FILES_RAW="$(printf '%s\n' "$FILES_RAW" | head -n "$MAX_FILES")"
fi

FILES_BLOCK=""
if [[ -z "$FILES_RAW" ]]; then
  FILES_BLOCK="files: (none — no parent commit or no changed files)"
else
  FILES_BLOCK="files:"
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    FILES_BLOCK="$FILES_BLOCK"$'\n'"- $f"
  done <<< "$FILES_RAW"
  if [[ "$TRUNCATED" -gt 0 ]]; then
    FILES_BLOCK="$FILES_BLOCK"$'\n'"... ($TRUNCATED more)"
  fi
fi

# --- build additionalContext (message + lean + files; mechanical_evidence next task) ---
CTX_FILE="$(mktemp)"
{
  printf 'git commit landed: %s\n' "$HASH"
  printf 'message: %s\n' "$MSG"
  printf 'initial_verdict: %s (prefix: %s)\n' "$LEAN" "$PREFIX"
  printf '%s\n' "$FILES_BLOCK"
  printf '\nIf this fixes a non-trivial bug, evaluate against the vault bug threshold (Flow B); corroborate with the mechanical_evidence below — never assert a debugging fact you cannot quote. Ask the user before recording.\n'
} > "$CTX_FILE"
```

(The JSON-emit block after this stays as written in Task 2 Step 3.)

- [ ] **Step 6: Run tests — all must PASS**

Run: `bash tests/test-on-git-commit.sh`
Expected: `ALL PASS` (Tests 1–7).

- [ ] **Step 7: Commit**

```bash
git add hooks/on-git-commit.sh tests/test-on-git-commit.sh
git commit -m "feat(hook): add changed-file list with truncation + no-parent fallback"
```

---

## Task 4: Mechanical evidence scan of the session transcript

**Files:**
- Modify: `hooks/on-git-commit.sh`
- Modify: `tests/test-on-git-commit.sh` (append Test 8 + Test 9 + Test 10)

**Interfaces:**
- Consumes: env var `VAULT_SESSION_TRANSCRIPT` — absolute path to the current session's transcript file (JSONL). If unset or the file is missing, the mechanical_evidence block states `mechanical_evidence: (transcript unavailable — no script evidence)`. How `VAULT_SESSION_TRANSCRIPT` gets set is out of scope for the hook (the hook just reads it); **it is NOT auto-set by this plan** — see Task 7 note. The hook reads it best-effort.
- Produces: a `mechanical_evidence:` block appended to `additionalContext`, between the `files:` block and the trailing nudge. The block reports **raw matched lines only** (grep -i hits for the keyword set, capped at 20 lines), prefixed with the hit count: `mechanical_evidence: N keyword hit(s)` then `- <line>` per hit. Zero hits → `mechanical_evidence: 0 keyword hits (no script evidence of a debugging process)`. The block is hard-capped at 4000 chars (inside the 10k overall budget) and truncates with `... (evidence truncated)`.

**Keyword set (deterministic, grep -i, word-ish boundary):** `error`, `exception`, `traceback`, `failed`, `failure`, `retry`, `tried`, `wrong`, `bug`, `fatal`, `panic`, `segfault`, `null pointer`, `undefined`, `not defined`, `grep -r`, `stack trace`.

- [ ] **Step 1: Append failing Test 8 — transcript with hits yields raw matched lines + count**

Append before the summary block:

```bash
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
STDIN='{"tool_input":{"command":"git commit -m \"fix: rate limit\""},"tool_response":{"stdout":"[main hit0001] fix: rate limit\n 1 file changed"}}'
OUT=$(printf '%s' "$STDIN" | VAULT_SESSION_TRANSCRIPT="$TMPTRANS" (cd "$TMPREPO" && bash "$HOOK") || true)
assert_contains "$OUT" 'mechanical_evidence:'
assert_contains "$OUT" 'NullPointerException'
assert_contains "$OUT" 'retry'
rm -rf "$TMPREPO" "$TMPTRANS"
```

- [ ] **Step 2: Append failing Test 9 — transcript absent → explicit "no script evidence"**

Append:

```bash
# Test 9: no transcript path set → mechanical_evidence says unavailable.
echo "Test 9: missing transcript → no script evidence"
TMPREPO="$(mktemp -d)"
( cd "$TMPREPO"; git init -q; git config user.email t@t.t; git config user.name t
  echo a > a.txt && git add a.txt && git commit -q -m "fix: x" )
STDIN='{"tool_input":{"command":"git commit -m \"fix: x\""},"tool_response":{"stdout":"[main non0002] fix: x\n 1 file changed"}}'
OUT=$(printf '%s' "$STDIN" | (cd "$TMPREPO" && env -u VAULT_SESSION_TRANSCRIPT bash "$HOOK") || true)
assert_contains "$OUT" 'mechanical_evidence: (transcript unavailable'
rm -rf "$TMPREPO"
```

- [ ] **Step 3: Append failing Test 10 — zero-hit transcript → "0 keyword hits"**

Append:

```bash
# Test 10: transcript with no keyword hits → 0 keyword hits.
echo "Test 10: clean transcript → 0 keyword hits"
TMPREPO="$(mktemp -d)"; TMPTRANS="$(mktemp)"
( cd "$TMPREPO"; git init -q; git config user.email t@t.t; git config user.name t
  echo a > a.txt && git add a.txt && git commit -q -m "fix: y" )
printf 'hello world\nthis is fine\n' > "$TMPTRANS"
STDIN='{"tool_input":{"command":"git commit -m \"fix: y\""},"tool_response":{"stdout":"[main zero0003] fix: y\n 1 file changed"}}'
OUT=$(printf '%s' "$STDIN" | VAULT_SESSION_TRANSCRIPT="$TMPTRANS" (cd "$TMPREPO" && bash "$HOOK") || true)
assert_contains "$OUT" 'mechanical_evidence: 0 keyword hits'
rm -rf "$TMPREPO" "$TMPTRANS"
```

- [ ] **Step 4: Run tests — Tests 8/9/10 must FAIL**

Run: `bash tests/test-on-git-commit.sh`
Expected: Tests 8/9/10 FAIL (no `mechanical_evidence:` block yet). Final line: `3 FAIL(S)`.

- [ ] **Step 5: Implement the mechanical-evidence scan in the hook**

In `hooks/on-git-commit.sh`, insert this block immediately **after** the `} > "$CTX_FILE"` line that writes the files block (i.e. after the heredoc closes, before the `# --- emit PostToolUse JSON ---` block):

```bash
# --- mechanical evidence: grep the session transcript for debugging keywords ---
# Reads VAULT_SESSION_TRANSCRIPT (a JSONL transcript path, if available).
# Reports RAW matched lines only — never interprets whether a hit is a real
# wrong-path (that is the LLM's job, per Flow D). Capped at 20 lines / 4000 chars.
TRANS="${VAULT_SESSION_TRANSCRIPT:-}"
EVIDENCE_BLOCK=""
if [[ -z "$TRANS" || ! -f "$TRANS" ]]; then
  EVIDENCE_BLOCK="mechanical_evidence: (transcript unavailable — no script evidence)"
else
  KEYWORDS='error|exception|traceback|failed|failure|retry|tried|wrong|bug|fatal|panic|segfault|null pointer|undefined|not defined|grep -r|stack trace'
  HITS="$(grep -iE "$KEYWORDS" "$TRANS" 2>/dev/null | head -n 20 || true)"
  HIT_COUNT=$(printf '%s\n' "$HITS" | grep -c . || true)
  if [[ -z "$HITS" || "$HIT_COUNT" -eq 0 ]]; then
    EVIDENCE_BLOCK="mechanical_evidence: 0 keyword hits (no script evidence of a debugging process)"
  else
    EVIDENCE_BLOCK="mechanical_evidence: $HIT_COUNT keyword hit(s)"
    while IFS= read -r h; do
      [[ -z "$h" ]] && continue
      EVIDENCE_BLOCK="$EVIDENCE_BLOCK"$'\n'"- $h"
    done <<< "$HITS"
  fi
fi

# Hard cap the evidence block at 4000 chars (overall additionalContext ≤ 10000).
if [[ ${#EVIDENCE_BLOCK} -gt 4000 ]]; then
  EVIDENCE_BLOCK="${EVIDENCE_BLOCK:0:3990}
... (evidence truncated)"
fi
```

Then, still inside the heredoc that writes `$CTX_FILE` (the `{ printf ... } > "$CTX_FILE"` block from Task 3 Step 5), add one line so the evidence block is written between the files block and the nudge. Change that block to:

```bash
CTX_FILE="$(mktemp)"
{
  printf 'git commit landed: %s\n' "$HASH"
  printf 'message: %s\n' "$MSG"
  printf 'initial_verdict: %s (prefix: %s)\n' "$LEAN" "$PREFIX"
  printf '%s\n' "$FILES_BLOCK"
  printf '%s\n' "$EVIDENCE_BLOCK"
  printf '\nIf this fixes a non-trivial bug, evaluate against the vault bug threshold (Flow B); corroborate with the mechanical_evidence below — never assert a debugging fact you cannot quote. Ask the user before recording.\n'
} > "$CTX_FILE"
```

(Note: the evidence-scan block above computes `$EVIDENCE_BLOCK` *before* this heredoc runs — order the two blocks so the scan executes first, then the heredoc references `$EVIDENCE_BLOCK`. In the script, place the scan block right where this step says to insert it, then the heredoc follows.)

- [ ] **Step 6: Run tests — all must PASS**

Run: `bash tests/test-on-git-commit.sh`
Expected: `ALL PASS` (Tests 1–10).

- [ ] **Step 7: Commit**

```bash
git add hooks/on-git-commit.sh tests/test-on-git-commit.sh
git commit -m "feat(hook): scan transcript for mechanical debugging evidence"
```

---

## Task 5: Register the hook in plugin.json

**Files:**
- Modify: `.claude-plugin/plugin.json`

**Interfaces:**
- Produces: a valid `plugin.json` whose `hooks.PostToolUse` array contains one entry with `matcher:"Bash"` and a nested hook with `type:"command"`, `if:"Bash(git commit*)"`, `command:"${CLAUDE_PLUGIN_ROOT}/hooks/on-git-commit.sh"`. The existing `hooks.Stop` entry is unchanged.

- [ ] **Step 1: Add the PostToolUse block to plugin.json**

Edit `.claude-plugin/plugin.json` so the `hooks` object becomes (keep `name`/`version`/`description`/`author` exactly as-is):

```json
{
  "name": "vault-experience",
  "version": "0.1.0",
  "description": "Dev experience vault: auto-search/record bugs, snippets, daily logs, and ADRs to a local Obsidian vault across multiple projects.",
  "author": {
    "name": "tal"
  },
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/vault-stop.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "if": "Bash(git commit*)",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/on-git-commit.sh"
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: Validate the JSON parses**

Run: `python3 -c "import json; json.load(open('.claude-plugin/plugin.json')); print('valid json')"`
Expected: `valid json`. (python3, no `jq` needed.) Fix trailing commas / brackets before proceeding if it errors.

- [ ] **Step 3: Re-run the hook tests to confirm nothing regressed**

Run: `bash tests/test-on-git-commit.sh`
Expected: `ALL PASS`.

- [ ] **Step 4: Commit**

```bash
git add .claude-plugin/plugin.json
git commit -m "feat(plugin): register PostToolUse git-commit hook"
```

---

## Task 6: Add Flow D to SKILL.md (the LLM analytic layer + no-fabrication rule)

**Files:**
- Modify: `skills/vault/SKILL.md` (insert new section between Flow C and the "Quick reference" section)

**Interfaces:**
- Produces: a new `## Flow D — Commit-triggered bug evaluation` section. Its wording matches spec decisions B1 + C2 AND enforces the new division of labor (script gives `initial_verdict` + raw `mechanical_evidence`; LLM does judgment + corroboration) and the no-fabrication red line. Four outcome branches, user-confirmation gate, no silent skip. References Flow B threshold + recording procedure. Notes it does not replace the Stop hook. Notes the `VAULT_SESSION_TRANSCRIPT` dependency is best-effort (Task 7).

- [ ] **Step 1: Write the Flow D section**

Insert the following block into `skills/vault/SKILL.md` immediately **after** the Flow C section's last line + trailing `---` and **before** the `## Quick reference` heading:

````markdown
## Flow D — Commit-triggered bug evaluation

**Trigger**: the `on-git-commit.sh` PostToolUse hook fires after a `git commit` run via the Bash tool and re-injects a `git commit landed: <hash>` / `message: …` / `initial_verdict: <lean> (prefix: …)` / `files:` / `mechanical_evidence:` system reminder. Use that reminder as a **deterministic trigger** to evaluate whether the commit fixes a recordable bug — this bypasses the unreliable "Claude proactively decides" path. Only commits made through the Bash tool in-session trigger this flow (terminal/IDE commits do not).

**Division of labor (hard rule).** The script does the deterministic parts; you (Claude) do the analytic parts, and the two cross-confirm:
- **Script (deterministic, already done by the time you see the reminder)**: classified the commit prefix into an `initial_verdict` lean; listed the changed files; grepped the session transcript for debugging keywords and listed the **raw matched lines** under `mechanical_evidence:` (it reports only what it found — it does not interpret).
- **You (analytic)**: read the raw evidence + the live session context; judge whether each raw hit is a *genuine* wrong-path (a keyword hit can be a quote, a discussion, or unrelated code — you decide); corroborate or overturn the `initial_verdict` lean.

**NO FABRICATION (top-level red line).** You must never assert a debugging fact you cannot point to a concrete source for:
1. Every corroborating claim must **quote a raw line from `mechanical_evidence:`** (a literal excerpt) OR be explicitly tagged `UNVERIFIED-BY-SCRIPT` with a one-line reason. "I recall we tried X" / "we probably hit Y" is fabrication and is forbidden.
2. If `mechanical_evidence:` is `0 keyword hits` or `(transcript unavailable)`, you may NOT claim a debugging process existed on context alone. You may say: "mechanical scan found no evidence; my read of the context suggests Z, but this is unverified — please confirm." Do NOT silently record on that basis.
3. If a hit is ambiguous (could be a quote / unrelated), you may use it **only** after explaining in your preview why it is a genuine wrong-path — and that explanation is your judgment, stated as judgment, not as fact.

### Evaluation (two layers, cross-confirming)

1. **Initial verdict (from the reminder's `initial_verdict`)** — the script's deterministic lean from the commit prefix (B1: prefix sets a *default lean*, overridable):
   - `worth-recording` (`fix`/`feat`/`refactor`/`perf`/`build`) → lean toward record.
   - `not-worth-recording` (`chore`/`docs`/`style`/`test`/`ci`) → lean toward skip.
   - `unknown` → no lean; decide purely on corroboration.
2. **Corroboration (your analysis of `mechanical_evidence:` + session context)** — does the evidence confirm or overturn the lean? Use the **Flow B bug threshold clauses** as the corroboration check (`≥ 5 min and ≥ 2 wrong paths` / `first Google result wasn't the answer` / `step on this again` / `production-grade · multi-person · cross-service impact`), and every clause you cite must be backed by a quoted raw line.

### Four outcomes (the only valid branches)

1. **Lean "worth-recording" + corroborated by quoted raw evidence** → judge **qualified**. Preview to the user — *"commit `<hash>`: initial verdict `<lean>`; corroboration: `<quoted raw line(s)>` satisfies Flow B clause `<clause>`."* — and only on user confirmation run Flow B's recording procedure (dedup → collect fields → `vault_create_entry`) in the current turn.
2. **Lean "worth-recording" + `mechanical_evidence` is empty/0-hits/unavailable** → do **not** finalize on the lean alone. Tell the user plainly: *"initial verdict leans worth-recording, but the mechanical scan found no script evidence for this commit's debugging; my read of the context suggests `<X>` (UNVERIFIED-BY-SCRIPT) — supply the debugging process or confirm whether to record."* Do not silently record, do not silently drop.
3. **Lean "not-worth-recording" + no overturning quoted evidence** → tell the user the skip reason (e.g. *"diff is a 1-line typo; `mechanical_evidence` shows no wrong-path hits"*) and do not write.
4. **Lean "not-worth-recording" + quoted raw evidence overturns it** (e.g. a `docs:` commit whose `mechanical_evidence` shows a schema-pitfall traceback) → overturn the lean, judge **qualified**, preview + ask the user as in branch 1.

### Rules
- **Never silent.** Whether qualified or not, the verdict + its script-evidence basis (or the explicit `UNVERIFIED-BY-SCRIPT` tag) must be visible to the user.
- **Never auto-write.** Writing to the vault is writing to Obsidian — outward-facing, requires user confirmation (user red line). Always preview + ask first.
- The **bug threshold** is Flow B's; the **recording procedure** is Flow B's (dedup → collect required fields → `vault_create_entry`); if a field is missing, ask the user rather than writing an incomplete entry.
- The `mechanical_evidence` scan depends on `VAULT_SESSION_TRANSCRIPT` being set; if it is not, the block reports `(transcript unavailable)` and you proceed on the lean + your own context read only — but with every claim tagged `UNVERIFIED-BY-SCRIPT` (no fabrication).
- This flow does **not** replace the Stop hook — daily-log generation still goes through the Stop hook at session end. Flow D is specifically the bug-recording trigger anchored on `git commit`.
````

- [ ] **Step 2: Verify the section sits between Flow C and Quick reference**

Run: `grep -n '^## Flow D\|^## Quick reference\|^## Flow C' skills/vault/SKILL.md`
Expected: Flow C, then Flow D, then Quick reference — in that order, no other `## Flow` heading between Flow D and Quick reference.

- [ ] **Step 3: Self-review the prose against spec C2 + the no-fabrication rule**

Re-read `docs/specs/2026-07-23-git-commit-vault-hook-design.md` §"决策 3（C2）" + "完整判断逻辑", and the Global Constraints "NO FABRICATION" clauses above. Confirm Flow D covers: (a) script `initial_verdict` lean, (b) LLM corroboration of raw `mechanical_evidence`, (c) all four outcome branches with exact lean+evidence combinations, (d) user-confirmation gate, (e) no-silent-skip, (f) the three no-fabrication rules (quote-or-tag, zero-hits ⇒ no claim, ambiguous-hit ⇒ judgment-not-fact), (g) reference to Flow B threshold + recording procedure, (h) note that Stop hook is not replaced, (i) `VAULT_SESSION_TRANSCRIPT` best-effort note. If any is missing, edit Flow D to add it. Do not commit until all nine are present.

- [ ] **Step 4: Commit**

```bash
git add skills/vault/SKILL.md
git commit -m "docs(skill): add Flow D — commit-triggered bug evaluation with no-fabrication guard"
```

---

## Task 7: Resolve `VAULT_SESSION_TRANSCRIPT` sourcing (decision + wiring) — no code if unresolvable

**Files:**
- Possibly modify: `.claude-plugin/plugin.json` (only if Step 1 finds a supported way to pass the transcript path to the hook as env).
- Possibly modify: `hooks/on-git-commit.sh` (only if a fallback sourcing path is needed).
- No modification if Step 1 finds no clean way — document the limitation in this task's notes and stop.

**Interfaces:**
- Consumes: the hook's `VAULT_SESSION_TRANSCRIPT` env var read (Task 4).
- Produces: either (a) a confirmed mechanism that sets `VAULT_SESSION_TRANSCRIPT` for the hook at commit time, or (b) a documented decision that the transcript scan degrades to `(transcript unavailable)` in practice, with Flow D already handling that path.

**Context — read this before deciding the task is needed:** Claude Code hooks do **not** receive the session transcript path as a standard env var. The hook gets stdin JSON (tool_input/tool_response) and a small set of env vars (`CLAUDE_PROJECT_DIR`, `CLAUDE_PLUGIN_ROOT`, etc.) — **not** the transcript file. So `VAULT_SESSION_TRANSCRIPT` will normally be unset, and the `mechanical_evidence` block will read `(transcript unavailable)` in real use. That is an acceptable, safe degradation because Flow D explicitly handles it (every claim tagged `UNVERIFIED-BY-SCRIPT`, no fabrication). **This task is a research/decision task: determine if a clean sourcing mechanism exists; if not, accept the degradation and document it. Do not hack around it.**

- [ ] **Step 1: Research whether Claude Code exposes the transcript path to hooks**

Check the official hooks documentation for any env var or stdin field carrying the session/transcript path. Fetch and read:

```bash
# Use the claude-code-guide agent or WebFetch the hooks doc; look for any env var
# like CLAUDE_TRANSCRIPT, CLAUDE_SESSION_*, or a stdin JSON field with the path.
```

Concretely, run: `Agent(subagent_type: claude-code-guide, prompt: "Does a PostToolUse Bash hook receive the current session's transcript file path, as an env var or in the stdin JSON payload? List exactly which env vars and stdin JSON fields a PostToolUse Bash hook receives. I need to know if there is any way for a hook script to read the current Claude Code session transcript file.")`

Decision rule from the agent's answer:
- If a documented env var / stdin field exists → use it in the hook (Step 2).
- If none exists → accept degradation; skip to Step 3 (document + stop).

- [ ] **Step 2 (only if Step 1 found a mechanism): wire it into the hook**

If a documented source exists (say env var `CLAUDE_TRANSCRIPT_PATH`), change the hook's transcript read line from:

```bash
TRANS="${VAULT_SESSION_TRANSCRIPT:-}"
```

to also fall back to the documented var:

```bash
TRANS="${VAULT_SESSION_TRANSCRIPT:-${CLAUDE_TRANSCRIPT_PATH:-}}"
```

Then add a test (Test 11) asserting that when `VAULT_SESSION_TRANSCRIPT` is unset but the documented var is set, the block lists hits. Append:

```bash
# Test 11 (only if a documented transcript env var exists): fallback var used.
# echo "Test 11: falls back to documented transcript env var"
# TMPREPO="$(mktemp -d)"; TMPTRANS="$(mktemp)"
# ... (mirror Test 8 but set the documented var instead of VAULT_SESSION_TRANSCRIPT)
```

Uncomment and fill in the real var name from Step 1's answer. Run `bash tests/test-on-git-commit.sh` → `ALL PASS`.

- [ ] **Step 3 (the expected outcome): document the degradation, commit the decision**

If Step 1 found **no** documented mechanism (the expected case), append a `## Limitation` note to the Flow D section of `skills/vault/SKILL.md` (after the Rules, before the next `---`):

```markdown
### Limitation: transcript scan is best-effort
The `mechanical_evidence` scan depends on `VAULT_SESSION_TRANSCRIPT`, which Claude Code hooks do not currently receive as a standard env var or stdin field. In practice the block will read `(transcript unavailable)` and Flow D proceeds on the `initial_verdict` lean + Claude's own context read, with every claim tagged `UNVERIFIED-BY-SCRIPT`. This is a safe degradation (no fabrication); the scan activates only if a future Claude Code version exposes the transcript path, or if the user manually exports `VAULT_SESSION_TRANSCRIPT` before committing.
```

And record the decision as a vault ADR via the `vault-adr` skill (out of scope for this plan's commit; note it as a follow-up). **Do not hack** the transcript path (e.g. guessing `~/.claude/projects/*/` filenames) — guessing a path is itself fabrication.

- [ ] **Step 4: Commit the decision**

```bash
git add skills/vault/SKILL.md   # (+ hooks/on-git-commit.sh, plugin.json if Step 2 applied)
git commit -m "docs(skill): document transcript-scan degradation (VAULT_SESSION_TRANSCRIPT best-effort)"
```

---

## Task 8: End-to-end manual verification (no code change, no commit)

**Files:**
- None modified. Verification gate before declaring done.

**Interfaces:**
- Consumes: the implemented hook (Task 4), registered plugin.json (Task 5), Flow D (Task 6), and the transcript-sourcing decision (Task 7).

- [ ] **Step 1: Run the full unit test suite — must be green**

Run: `bash tests/test-on-git-commit.sh`
Expected: `ALL PASS` (Tests 1–10, or 1–11 if Task 7 Step 2 applied). If red, stop and fix before the manual E2E.

- [ ] **Step 2: Manual hook E2E in a throwaway repo (with a transcript fixture)**

```bash
TMPREPO="$(mktemp -d)"; TMPTRANS="$(mktemp)"
cd "$TMPREPO"
git init -q; git config user.email t@t.t; git config user.name t
echo a > a.txt && git add a.txt && git commit -q -m "init"
echo broken > a.txt && git add a.txt && git commit -q -m "fix: handle rate limit window reset"
cat > "$TMPTRANS" <<'JSONL'
{"role":"user","content":"throws error: NullPointerException on reset"}
{"role":"assistant","content":"retry with delayed reset"}
{"role":"user","content":"still failed"}
JSONL
echo '{"tool_input":{"command":"git commit -m \"fix: handle rate limit window reset\""},"tool_response":{"stdout":"[main e2e0001] fix: handle rate limit window reset\n 1 file changed"}}' \
  | VAULT_SESSION_TRANSCRIPT="$TMPTRANS" bash "$REPO_ROOT/hooks/on-git-commit.sh"
cd "$REPO_ROOT"; rm -rf "$TMPREPO" "$TMPTRANS"
```
Expected: a single JSON line on stdout. The `additionalContext` must contain: `git commit landed: e2e0001`, `message: fix: ...`, `initial_verdict: worth-recording (prefix: fix)`, a `files:` block listing `a.txt`, a `mechanical_evidence:` block listing `≥2 keyword hit(s)` including the `NullPointerException` line, and the trailing nudge. No `fatal:` text. If malformed/empty, debug with `set -x`.

- [ ] **Step 3: Manual regression — non-commit Bash does NOT trigger**

```bash
echo '{"tool_input":{"command":"git status"},"tool_response":{"stdout":"nothing to commit"}}' | bash "$REPO_ROOT/hooks/on-git-commit.sh"
```
Expected: no stdout (empty). The script's internal guard enforces silence even though the `if` matcher is the real gate.

- [ ] **Step 4: Walk one Flow D branch by hand (qualified + confirmed)**

Using the Step-2 output: `initial_verdict` = `worth-recording` (fix:). Per Flow D Layer 2, the `mechanical_evidence` shows `NullPointerException` + `retry` + `failed` — that is real corroboration of "≥ 2 wrong paths". Branch 1 (qualified). Draft the preview you would show the user:

```
commit e2e0001: initial verdict worth-recording (fix: handle rate limit window reset).
Corroboration (quoted from mechanical_evidence):
  - "throws error: NullPointerException on reset"
  - "retry with delayed reset"
  - "still failed"
→ satisfies Flow B "≥ 5 min and ≥ 2 wrong paths".
Record to vault as a bug?
```

Confirm this matches Flow D branch-1 wording: every corroboration quotes a raw line, no fabricated claim. No file change, no commit — reading check that Task 6's prose produces the intended user-facing prompt.

- [ ] **Step 5: Walk the no-evidence branch by hand (branch 2 — the no-fabrication test)**

Same commit, but this time with **no** transcript set. The `mechanical_evidence` block reads `(transcript unavailable)`. Per Flow D branch 2, you may NOT claim a debugging process. Draft the preview:

```
commit e2e0001: initial verdict leans worth-recording (fix: ...).
Mechanical scan found no script evidence (transcript unavailable).
My read of the context suggests a rate-limit bug was debugged (UNVERIFIED-BY-SCRIPT) —
supply the debugging process or confirm whether to record.
```

Confirm: the `UNVERIFIED-BY-SCRIPT` tag is present, no raw-line quote is fabricated, the user is asked — not auto-recorded. This is the no-fabrication gate's live check.

- [ ] **Step 6: Report results**

Summarize: unit suite green / hook E2E output correct (lean + files + evidence) / regression silent / Flow D branch-1 preview quotes raw lines / Flow D branch-2 preview tags `UNVERIFIED-BY-SCRIPT`. If any step failed, list it and do not claim done. (No commit — pure verification.)

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- §组件 1 hook script (parse + diff + emit) → Tasks 2 + 3.
- §组件 1 边界处理 (non-commit silent, parse-fail silent, no-parent placeholder, >50 truncation, 10k budget) → Task 1/2 (silent), Task 3 (no-parent, truncate), Task 4 (4000-char evidence cap inside the 10k budget). Covered.
- §组件 2 plugin.json → Task 5.
- §组件 3 SKILL.md Flow D → Task 6.
- §数据流 → exercised by Task 8.
- §错误处理 (never block, silent on error) → `set -uo pipefail` + `exit 0` on every path.
- §测试 (4 context-judgment cases + regression) → unit tests cover the hook mechanics (Tasks 1–4, 8). The four judgment cases are Claude-behavior; covered by Flow D prose (Task 6) + Task 8 Steps 4–5 walk-throughs (cannot be unit-tested — they depend on live LLM judgment, which is the point of the division of labor).
- §范围与非目标 (in-session Bash-tool commits only; not auto-write; not `-F`/IDE) → stated in Flow D Trigger + Rules.
- **User principle "deterministic=script, analysis=LLM"** → prefix lean (Task 2) + mechanical evidence scan (Task 4) are in the script; judgment + corroboration (Task 6 Flow D) are in the LLM. Covered.
- **User red line "杜绝杜撰"** → Global Constraints "NO FABRICATION" + Flow D no-fabrication rules (Task 6) + Task 8 Step 5 live check. Covered.
- **Transcript sourcing** → Task 7 explicitly researches and documents the `VAULT_SESSION_TRANSCRIPT` limitation rather than guessing a path (guessing would itself be fabrication).

**2. Placeholder scan** — no TBD/TODO/"handle edge cases"/"similar to N". Every code step shows full code; every command shows expected output. Task 7 Step 2's Test 11 is deliberately template-commented pending Step 1's answer (that is a real conditional, not a placeholder — the task says uncomment only if the mechanism exists). Task 7 Step 3 references a `vault-adr` follow-up explicitly noted out-of-scope. ✅

**3. Type consistency** — `additionalContext` text format contract stated in Task 2 Interfaces (`git commit landed:` / `message:` / `initial_verdict: <lean> (prefix: <prefix>)` / `files:` / `mechanical_evidence:` / nudge) and respected by Tasks 3 + 4 (each appends its block to the same contract). Helper names `run_hook` / `run_hook_with_transcript` / `assert_contains` / `assert_absent` defined in Task 1, reused unchanged in Tasks 2–4. `MAX_FILES=50`, `... (N more)`, `mechanical_evidence:` prefix, keyword set, and 4000-char cap used consistently between impl and tests. Hook filename `on-git-commit.sh` + command path `${CLAUDE_PLUGIN_ROOT}/hooks/on-git-commit.sh` consistent across Tasks 1–5. Env var name `VAULT_SESSION_TRANSCRIPT` consistent across Tasks 1/4/7/8. ✅
