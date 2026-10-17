#!/usr/bin/env bash
# Checks that setup-browser.sh connects the browser to EVERY agent folder
# and un-hides a disabled playwright server. Runs the connect step only.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
for a in ai-victoriarubis ai-marketing ai-assistant; do
  mkdir -p "$T/$a/dashi-plugin-claude-code/plugin/.claude"
  echo '{"mcpServers":{"dashi":{"command":"bun"}}}' >"$T/$a/dashi-plugin-claude-code/plugin/.mcp.json"
done
echo '{"disabledMcpjsonServers":["chrome-devtools","playwright"]}' \
  >"$T/ai-marketing/dashi-plugin-claude-code/plugin/.claude/settings.local.json"
mkdir -p "$T/not-an-agent"

out=$(AGENT_BROWSER_CONNECT_ONLY=1 AGENT_BROWSER_AGENTS_ROOT="$T" AGENT_BROWSER_PORT=9999 \
  bash "$HERE/setup-browser.sh" 2>&1) || { echo "FAIL: script exited non-zero"; echo "$out"; exit 1; }

fail=0
for a in ai-victoriarubis ai-marketing ai-assistant; do
  python3 - "$T/$a/dashi-plugin-claude-code/plugin/.mcp.json" <<'PY' || { echo "FAIL: $a"; fail=1; }
import json, sys
c = json.load(open(sys.argv[1]))["mcpServers"]
assert "dashi" in c, "kept existing server"
assert "http://127.0.0.1:9999" in c["playwright"]["args"], "browser wired"
PY
done
python3 - "$T/ai-marketing/dashi-plugin-claude-code/plugin/.claude/settings.local.json" <<'PY' || { echo "FAIL: playwright still disabled"; fail=1; }
import json, sys
assert json.load(open(sys.argv[1]))["disabledMcpjsonServers"] == ["chrome-devtools"]
PY
[ "$fail" = 0 ] && echo "PASS: browser connected to all 3 agents" || exit 1
