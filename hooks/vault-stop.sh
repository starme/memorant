#!/bin/bash
# vault-stop.sh — Stop hook for the vault-experience plugin.
#
# Emits a prompt to Claude (via stdout) at session end, nudging to:
#   - record non-trivial bugs / decisions solved this session
#   - generate/append the daily log
#   - on Fridays, tidy pending_review leads
#
# This hook NEVER writes to the vault. It only outputs text; Claude performs
# actual writes via the vault_* MCP tools after user confirmation. This keeps
# the user in the loop and avoids auto-polluting the experience library.
#
# $VAULT_ROOT resolution: env var first, then .claude/vault.local.md frontmatter.

set -euo pipefail

# --- resolve VAULT_ROOT ---
VAULT_ROOT="${VAULT_ROOT:-}"
if [[ -z "$VAULT_ROOT" ]]; then
  # Fall back to plugin settings file: .claude/vault.local.md with VAULT_ROOT: <path>
  settings="${CLAUDE_PLUGIN_ROOT:-$(dirname "$0")/..}/.claude/vault.local.md"
  # search upward from cwd for a .claude/vault.local.md
  if [[ -f "$settings" ]]; then
    VAULT_ROOT=$(grep -m1 '^VAULT_ROOT:' "$settings" 2>/dev/null | sed 's/^VAULT_ROOT:[[:space:]]*//' | tr -d '"' || true)
  fi
fi

if [[ -z "$VAULT_ROOT" ]]; then
  cat <<'EOF'
[vault] VAULT_ROOT is not configured. To enable dev-experience recording, either:
  export VAULT_ROOT=/path/to/your/vault
  or create .claude/vault.local.md with `VAULT_ROOT: /path/to/your/vault` in its frontmatter.
(Skip this if you don't use the vault plugin.)
EOF
  exit 0
fi

# --- build the prompt ---
weekday=$(date +%u 2>/dev/null || echo "0")
today=$(date +%Y-%m-%d 2>/dev/null || echo "")
daily_file="$VAULT_ROOT/daily/$today.md"
pending=0
if [[ -f "$daily_file" ]]; then
  pending=$(grep -m1 '^pending_review:' "$daily_file" 2>/dev/null | sed 's/[^0-9]//g' || echo "0")
  pending=${pending:-0}
fi

{
  echo "[vault] Session ending. Quick dev-experience check:"
  echo "- Did you solve a non-trivial bug or make an architectural decision this session? If yes and not yet recorded, consider running /vault-log or /vault-adr."
  echo "- Daily log: today's file is daily/$today.md — offer to append a summary (进展/踩坑线索/待跟进/决策) if the session produced substance."
  if [[ "$weekday" == "5" ]]; then
    echo "- Friday tidy: run /vault-search on daily 'pending_review' leads ($pending currently) and promote finished ones to bugs/snippets/ADR."
  elif [[ "$pending" -gt 0 ]]; then
    echo "- $pending pending_review lead(s) in today's daily — offer to promote when ready."
  fi
  echo "(Only act on items with real substance; skip noise. Confirm with the user before writing to the vault.)"
} | head -20

exit 0
