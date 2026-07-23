#!/bin/bash
# Fail-open shim for the Python journal observer.
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

if [[ -n "${CLAUDE_PLUGIN_ROOT:-}" \
  && -x "${CLAUDE_PLUGIN_ROOT}/mcp-server/.venv/bin/memorant-hook" ]]; then
  CMD=("${CLAUDE_PLUGIN_ROOT}/mcp-server/.venv/bin/memorant-hook")
elif command -v memorant-hook >/dev/null 2>&1; then
  CMD=(memorant-hook)
elif [[ -n "${CLAUDE_PLUGIN_ROOT:-}" \
  && -f "${CLAUDE_PLUGIN_ROOT}/mcp-server/pyproject.toml" ]] \
  && command -v uv >/dev/null 2>&1; then
  CMD=(uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}/mcp-server" memorant-hook)
else
  emit_empty
fi

command -v python3 >/dev/null 2>&1 || emit_empty
python3 - "${MEMORANT_HOOK_TIMEOUT_SECONDS:-5}" "$INPUT" "$OUTPUT" \
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
python3 -c 'import json,sys; json.load(sys.stdin)' < "$OUTPUT" >/dev/null 2>&1 \
  || emit_empty
cat "$OUTPUT" 2>/dev/null || emit_empty
exit 0
