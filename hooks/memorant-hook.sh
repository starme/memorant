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

if [[ -n "${CLAUDE_PLUGIN_ROOT:-}" && -f "${CLAUDE_PLUGIN_ROOT}/mcp-server/pyproject.toml" ]] \
  && command -v uv >/dev/null 2>&1; then
  CMD=(uv run --quiet --no-project --with "${CLAUDE_PLUGIN_ROOT}/mcp-server" \
    memorant-hook)
elif command -v memorant-hook >/dev/null 2>&1; then
  CMD=(memorant-hook)
else
  emit_empty
fi

if command -v perl >/dev/null 2>&1; then
  perl -e 'alarm shift; exec @ARGV' "${MEMORANT_HOOK_TIMEOUT_SECONDS:-5}" \
    "${CMD[@]}" < "$INPUT" > "$OUTPUT" 2>/dev/null || emit_empty
else
  "${CMD[@]}" < "$INPUT" > "$OUTPUT" 2>/dev/null || emit_empty
fi

SIZE="$(wc -c < "$OUTPUT" 2>/dev/null | tr -d ' ')"
[[ "$SIZE" =~ ^[0-9]+$ && "$SIZE" -le 10000 && "$SIZE" -gt 0 ]] || emit_empty
command -v python3 >/dev/null 2>&1 || emit_empty
python3 -c 'import json,sys; json.load(sys.stdin)' < "$OUTPUT" >/dev/null 2>&1 \
  || emit_empty
cat "$OUTPUT" 2>/dev/null || emit_empty
exit 0
