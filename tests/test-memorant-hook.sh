#!/bin/bash
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$HERE/.."
HOOK="$ROOT/hooks/memorant-hook.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILS=0

fail() { echo "FAIL: $1"; FAILS=$((FAILS + 1)); }

export MEMORANT_ROOT="$TMP/vault"
export CLAUDE_PLUGIN_ROOT="$ROOT"
mkdir -p "$MEMORANT_ROOT"

OUT="$(printf '%s' '{"hook_event_name":"SessionStart","session_id":"shell-1","cwd":"/tmp/project"}' | /bin/bash "$HOOK" 2>/dev/null)"
printf '%s' "$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null || fail "output is not JSON"
[[ ${#OUT} -le 10000 ]] || fail "output exceeds budget"
[[ "$(find "$MEMORANT_ROOT/journal" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')" == "1" ]] || fail "event was not written"

OUT="$(printf '{' | /bin/bash "$HOOK" 2>/dev/null)"
[[ "$OUT" == "{}" ]] || fail "bad JSON did not fail open"

OLD_PATH="$PATH"
export PATH="/usr/bin:/bin"
export CLAUDE_PLUGIN_ROOT="$TMP/missing"
OUT="$(printf '%s' '{"hook_event_name":"SessionStart"}' | /bin/bash "$HOOK" 2>/dev/null)"
[[ "$OUT" == "{}" ]] || fail "missing command did not fail open"

mkdir -p "$TMP/bin"
cat > "$TMP/bin/memorant-hook" <<'EOF'
#!/bin/bash
sleep 10
printf '{"late":true}\n'
EOF
chmod +x "$TMP/bin/memorant-hook"
export PATH="$TMP/bin:/usr/bin:/bin"
export MEMORANT_HOOK_TIMEOUT_SECONDS=1
START="$(date +%s)"
OUT="$(printf '%s' '{"hook_event_name":"SessionStart"}' | /bin/bash "$HOOK" 2>/dev/null)"
ELAPSED=$(( $(date +%s) - START ))
[[ "$OUT" == "{}" ]] || fail "timeout did not fail open"
[[ "$ELAPSED" -lt 5 ]] || fail "timeout took too long"
export PATH="$OLD_PATH"

if [[ "$FAILS" -eq 0 ]]; then
  echo "ALL PASS"
  exit 0
fi
echo "$FAILS FAIL(S)"
exit 1
