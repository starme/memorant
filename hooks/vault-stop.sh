#!/bin/bash
# vault-stop.sh — compatible Stop hook for 书童 · Memorant.
#
# Emits a prompt to Claude (via stdout) at session end, nudging to:
#   - record non-trivial bugs / decisions solved this session
#   - generate/append the daily log
#   - on Fridays, tidy pending_review leads
#
# This hook NEVER writes to Memorant. It only outputs text; Claude performs
# actual writes via the vault_* MCP tools after user confirmation. This keeps
# the user in the loop and avoids auto-polluting the experience library.
#
# Root priority:
# MEMORANT_ROOT > .claude/memorant.local.md > VAULT_ROOT > .claude/vault.local.md.

set -euo pipefail

# --- resolve root ---
read_setting() {
  local file="$1"
  local key="$2"
  grep -m1 "^${key}:" "$file" 2>/dev/null \
    | sed "s/^${key}:[[:space:]]*//" \
    | tr -d "\"'" || true
}

find_setting() {
  local filename="$1"
  local key="$2"
  local value=""
  local candidate="${HOME:-}/.claude/$filename"
  if [[ -n "${HOME:-}" && -f "$candidate" ]]; then
    value=$(read_setting "$candidate" "$key")
    if [[ -n "$value" ]]; then
      echo "$value"
      return
    fi
  fi

  local cursor="$PWD"
  while true; do
    candidate="$cursor/.claude/$filename"
    if [[ -f "$candidate" ]]; then
      value=$(read_setting "$candidate" "$key")
      if [[ -n "$value" ]]; then
        echo "$value"
        return
      fi
    fi
    [[ "$cursor" == "/" ]] && break
    cursor=$(dirname "$cursor")
  done

  candidate="${CLAUDE_PLUGIN_ROOT:-$(dirname "$0")/..}/.claude/$filename"
  if [[ -f "$candidate" ]]; then
    value=$(read_setting "$candidate" "$key")
    [[ -n "$value" ]] && echo "$value"
  fi
}

MEMORANT_DATA_ROOT="${MEMORANT_ROOT:-}"
if [[ -z "$MEMORANT_DATA_ROOT" ]]; then
  MEMORANT_DATA_ROOT=$(find_setting "memorant.local.md" "MEMORANT_ROOT")
fi
if [[ -z "$MEMORANT_DATA_ROOT" ]]; then
  MEMORANT_DATA_ROOT="${VAULT_ROOT:-}"
fi
if [[ -z "$MEMORANT_DATA_ROOT" ]]; then
  MEMORANT_DATA_ROOT=$(find_setting "vault.local.md" "VAULT_ROOT")
fi

if [[ -z "$MEMORANT_DATA_ROOT" ]]; then
  cat <<'EOF'
[memorant] MEMORANT_ROOT is not configured. To enable recording, either:
  export MEMORANT_ROOT=/path/to/your/knowledge-base
  or create .claude/memorant.local.md with `MEMORANT_ROOT: /path/to/your/knowledge-base`.
Legacy VAULT_ROOT and .claude/vault.local.md remain supported.
EOF
  exit 0
fi

# --- build the prompt ---
weekday=$(date +%u 2>/dev/null || echo "0")
today=$(date +%Y-%m-%d 2>/dev/null || echo "")
daily_file="$MEMORANT_DATA_ROOT/daily/$today.md"
pending=0
if [[ -f "$daily_file" ]]; then
  pending=$(grep -m1 '^pending_review:' "$daily_file" 2>/dev/null | sed 's/[^0-9]//g' || echo "0")
  pending=${pending:-0}
fi

{
  echo "[memorant] Session ending. Quick dev-experience check:"
  echo "- Did you solve a non-trivial bug or make an architectural decision this session? If yes and not yet recorded, consider running /memorant-log or /memorant-adr."
  echo "- Daily log: today's file is daily/$today.md — offer to append a summary (进展/踩坑线索/待跟进/决策) if the session produced substance."
  if [[ "$weekday" == "5" ]]; then
    echo "- Friday tidy: run /memorant-search on daily 'pending_review' leads ($pending currently) and promote finished ones to bugs/snippets/ADR."
  elif [[ "$pending" -gt 0 ]]; then
    echo "- $pending pending_review lead(s) in today's daily — offer to promote when ready."
  fi
  echo "(Only act on items with real substance; skip noise. Confirm with the user before writing to Memorant.)"
} | head -20

exit 0
