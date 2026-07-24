#!/bin/bash
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$HERE/.."
HOOK="$ROOT/hooks/memorant-hook.sh"
TMP="$(mktemp -d)"
trap 'chmod -R u+w "$TMP" 2>/dev/null || true; rm -rf "$TMP"' EXIT
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

# Current plugin source must beat a stale global CLI, work read-only/offline without
# a plugin venv, and keep SessionEnd comfortably below 1.5 seconds.
mkdir -p "$TMP/priority-bin"
mkdir -p "$TMP/plugin/mcp-server"
cp -R "$ROOT/mcp-server/memorant_mcp" "$TMP/plugin/mcp-server/"
rm -rf "$TMP/plugin/mcp-server/memorant_mcp/__pycache__"
cat > "$TMP/priority-bin/memorant-hook" <<EOF
#!/bin/bash
touch "$TMP/global-cli-called"
exit 99
EOF
chmod +x "$TMP/priority-bin/memorant-hook"
chmod -R a-w "$TMP/plugin"
export PATH="$TMP/priority-bin:/usr/bin:/bin"
export CLAUDE_PLUGIN_ROOT="$TMP/plugin"
export MEMORANT_ROOT="$TMP/readonly-vault"
START_NS="$(python3 -c 'import time; print(time.monotonic_ns())')"
OUT="$(printf '%s' '{"hook_event_name":"SessionEnd","session_id":"shell-end","cwd":"/tmp/project"}' | /bin/bash "$HOOK" 2>/dev/null)"
END_NS="$(python3 -c 'import time; print(time.monotonic_ns())')"
ELAPSED_MS=$(( (END_NS - START_NS) / 1000000 ))
[[ "$OUT" == "{}" ]] || fail "SessionEnd output was not empty JSON"
[[ ! -e "$TMP/global-cli-called" ]] || fail "stale global CLI was invoked"
[[ "$(find "$MEMORANT_ROOT/journal" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')" == "1" ]] || fail "read-only current plugin source did not write event"
[[ "$ELAPSED_MS" -lt 1500 ]] || fail "SessionEnd exceeded 1.5 seconds"
[[ ! -e "$TMP/plugin/mcp-server/uv.lock" ]] || fail "plugin uv.lock was generated"
[[ -z "$(find "$TMP/plugin" -name '__pycache__' -o -name '.venv' 2>/dev/null)" ]] || fail "plugin cache or venv was generated"

export PATH="/usr/bin:/bin"
export CLAUDE_PLUGIN_ROOT="$TMP/missing"
OUT="$(printf '%s' '{"hook_event_name":"SessionStart"}' | /bin/bash "$HOOK" 2>/dev/null)"
[[ "$OUT" == "{}" ]] || fail "missing command did not fail open"

mkdir -p "$TMP/no-perl-bin" "$TMP/slow-plugin/mcp-server/memorant_mcp"
cat > "$TMP/slow-plugin/mcp-server/memorant_mcp/hook_cli.py" <<'EOF'
import time
time.sleep(10)
print("{}")
EOF
for command in mktemp cat wc tr rm python3; do
  TARGET="$(command -v "$command")"
  ln -s "$TARGET" "$TMP/no-perl-bin/$command"
done
export PATH="$TMP/no-perl-bin"
export CLAUDE_PLUGIN_ROOT="$TMP/slow-plugin"
export MEMORANT_HOOK_TIMEOUT_SECONDS=0.2
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
