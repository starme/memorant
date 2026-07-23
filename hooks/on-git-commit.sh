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

# JSON string-value extractor (no jq): walk chars, honor backslash escapes,
# stop at the closing un-escaped quote. Prints the decoded string for ONE key.
# Used because tool_input.command contains escaped inner quotes (\"...).
json_string_at() {
  printf '%s' "$1" | awk -v KEY="$2" '
    function decode(s,   i, n, c, out, esc) {
      out = ""; esc = 0; n = length(s)
      for (i = 1; i <= n; i++) {
        c = substr(s, i, 1)
        if (esc) { out = out c; esc = 0; continue }
        if (c == "\\") { esc = 1; continue }
        if (c == "\"") break
        out = out c
      }
      return out
    }
    {
      pat = "\"" KEY "\"[[:space:]]*:[[:space:]]*\""
      if (match($0, pat)) {
        rest = substr($0, RSTART + RLENGTH)
        print decode(rest)
      }
    }
  '
}

# --- guard: only act on a `git commit` command (the hook's if:"Bash(git commit*)"
# matcher already filters; keep this so the script is safe if invoked directly). ---
COMMAND="$(json_string_at "$PAYLOAD" command)"
case "$COMMAND" in
  *git\ commit*) ;;
  *) exit 0 ;;
esac

# --- extract commit message from tool_input.command ---
MSG="$(printf '%s' "$COMMAND" | sed -n 's/.*git commit -m "\([^"]*\)".*/\1/p')"

# --- extract commit hash from tool_response.stdout bracket form [branch hash] ---
STDOUT="$(json_string_at "$PAYLOAD" stdout)"
HASH="$(printf '%s' "$STDOUT" | sed -n 's/^\[[^ ]* \([0-9a-f][0-9a-f]*\)\].*/\1/p')"

# --- classify prefix into a default lean (deterministic rule table) ---
# Extract Conventional-Commit type word: leading [a-z0-9]+ immediately followed
# by ':' '(' or '!'; otherwise 'none'. (BSD-awk-compatible: regex as string to
# avoid '/' lexing issues; no backref syntax in sub() replacement.)
PREFIX="$(printf '%s' "$MSG" | awk '
  BEGIN { re = "^[a-z][a-z0-9]*" }
  {
    if (match($0, re)) {
      p = substr($0, RSTART, RLENGTH)
      r = substr($0, RSTART + RLENGTH)
      c = substr(r, 1, 1)
      if (c == ":" || c == "(" || c == "!") print p
      else print "none"
    } else {
      print "none"
    }
  }
' | head -1)"
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
