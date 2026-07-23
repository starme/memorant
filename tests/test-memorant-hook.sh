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

# Project venv must beat uv and keep SessionEnd comfortably below 1.5 seconds.
mkdir -p "$TMP/priority-bin"
mkdir -p "$TMP/plugin/mcp-server/.venv/bin"
cat > "$TMP/priority-bin/uv" <<'EOF'
#!/bin/bash
exit 99
EOF
cat > "$TMP/plugin/mcp-server/.venv/bin/memorant-hook" <<EOF
#!/bin/bash
cat >/dev/null
touch "$TMP/venv-used"
printf '{}\n'
EOF
chmod +x "$TMP/priority-bin/uv"
chmod +x "$TMP/plugin/mcp-server/.venv/bin/memorant-hook"
export PATH="$TMP/priority-bin:/usr/bin:/bin"
export CLAUDE_PLUGIN_ROOT="$TMP/plugin"
START_NS="$(python3 -c 'import time; print(time.monotonic_ns())')"
OUT="$(printf '%s' '{"hook_event_name":"SessionEnd","session_id":"shell-end","cwd":"/tmp/project"}' | /bin/bash "$HOOK" 2>/dev/null)"
END_NS="$(python3 -c 'import time; print(time.monotonic_ns())')"
ELAPSED_MS=$(( (END_NS - START_NS) / 1000000 ))
[[ "$OUT" == "{}" ]] || fail "SessionEnd output was not empty JSON"
[[ -f "$TMP/venv-used" ]] || fail "project venv CLI was not preferred"
[[ "$ELAPSED_MS" -lt 1500 ]] || fail "SessionEnd exceeded 1.5 seconds"

export PATH="/usr/bin:/bin"
export CLAUDE_PLUGIN_ROOT="$TMP/missing"
OUT="$(printf '%s' '{"hook_event_name":"SessionStart"}' | /bin/bash "$HOOK" 2>/dev/null)"
[[ "$OUT" == "{}" ]] || fail "missing command did not fail open"

mkdir -p "$TMP/no-perl-bin"
cat > "$TMP/no-perl-bin/memorant-hook" <<'EOF'
#!/bin/bash
/bin/sleep 10
printf '{"late":true}\n'
EOF
chmod +x "$TMP/no-perl-bin/memorant-hook"
for command in mktemp cat wc tr rm python3; do
  TARGET="$(command -v "$command")"
  ln -s "$TARGET" "$TMP/no-perl-bin/$command"
done
export PATH="$TMP/no-perl-bin"
export MEMORANT_HOOK_TIMEOUT_SECONDS=1
START="$(/bin/date +%s)"
OUT="$(printf '%s' '{"hook_event_name":"SessionStart"}' | /bin/bash "$HOOK" 2>/dev/null)"
ELAPSED=$(( $(/bin/date +%s) - START ))
[[ "$OUT" == "{}" ]] || fail "timeout did not fail open"
[[ "$ELAPSED" -lt 5 ]] || fail "timeout without perl took too long"
export PATH="$OLD_PATH"

if [[ "$FAILS" -eq 0 ]]; then
  echo "ALL PASS"
  exit 0
fi
echo "$FAILS FAIL(S)"
exit 1
