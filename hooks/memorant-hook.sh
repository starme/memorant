#!/bin/bash
# Fail-open shim for the Memorant observer / recall CLI.
# Prefer the current plugin's stdlib-safe hook_cli.py so a stale global
# `memorant-hook` on PATH cannot hijack behavior.
set -uo pipefail

emit_empty() {
  printf '{}\n'
  exit 0
}

INPUT="$(mktemp 2>/dev/null)" || emit_empty
OUTPUT="$(mktemp 2>/dev/null)" || { rm -f "$INPUT"; emit_empty; }
cleanup() { rm -f "$INPUT" "$OUTPUT"; }
trap cleanup EXIT
cat > "$INPUT" 2>/dev/null || emit_empty

command -v python3 >/dev/null 2>&1 || emit_empty
HOOK_CLI="${CLAUDE_PLUGIN_ROOT:-}/mcp-server/memorant_mcp/hook_cli.py"
[[ -f "$HOOK_CLI" ]] || emit_empty
# -S keeps this path offline / venv-free; recall/activity soft-degrade.
CMD=(python3 -B -S "$HOOK_CLI")

python3 - "${MEMORANT_HOOK_TIMEOUT_SECONDS:-1.0}" "$INPUT" "$OUTPUT" \
  "${CMD[@]}" <<'PY' || emit_empty
import subprocess
import sys

timeout, input_path, output_path, *command = sys.argv[1:]
try:
    seconds = float(timeout)
    if not 0.1 <= seconds <= 30:
        raise ValueError("timeout outside safe range")
    with open(input_path, "rb") as stdin, open(output_path, "wb") as stdout:
        completed = subprocess.run(
            command,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.DEVNULL,
            timeout=seconds,
            check=False,
        )
    if completed.returncode != 0:
        raise SystemExit(1)
except (OSError, subprocess.TimeoutExpired, ValueError):
    raise SystemExit(1)
PY

SIZE="$(wc -c < "$OUTPUT" 2>/dev/null | tr -d ' ')"
[[ "$SIZE" =~ ^[0-9]+$ && "$SIZE" -le 10000 && "$SIZE" -gt 0 ]] || emit_empty

# Dispatch by hook type. PreCompact's context reaches the model as stdout plain
# text (it does not accept hookSpecificOutput.additionalContext); passing that
# through JSON validation would discard it. Other hooks emit JSON, validated.
HOOK_EVENT="$(python3 -B -S -c '
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("hook_event_name") or "")
except Exception:
    print("")
' "$INPUT" 2>/dev/null)"

if [[ "$HOOK_EVENT" == "PreCompact" ]]; then
  cat "$OUTPUT" 2>/dev/null || emit_empty
  exit 0
fi

python3 -c 'import json,sys; json.load(sys.stdin)' < "$OUTPUT" >/dev/null 2>&1 \
  || emit_empty
cat "$OUTPUT" 2>/dev/null || emit_empty
exit 0
