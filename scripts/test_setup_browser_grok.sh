#!/usr/bin/env bash
# Agents on Grok (Cursor) read MCP from one ~/.cursor/mcp.json, not from the agent folder (26.11.2026):
# setup-browser.sh must add the browser there too, keep other servers, and do nothing without ~/.cursor.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/agents/ai-techspec/dashi-plugin-claude-code/plugin" "$T/home/.cursor" "$T/home2"
echo '{"mcpServers":{"dashi":{"command":"bun"}}}' >"$T/agents/ai-techspec/dashi-plugin-claude-code/plugin/.mcp.json"
echo '{"mcpServers":{"chatplace":{"url":"https://mcp.chatplace.io/mcp"}}}' >"$T/home/.cursor/mcp.json"
run() { HOME="$1" AGENT_BROWSER_CONNECT_ONLY=1 AGENT_BROWSER_AGENTS_ROOT="$T/agents" AGENT_BROWSER_PORT=9999 \
  AGENT_BROWSER_CURSOR_BIN=/bin/true AGENT_BROWSER_GROK_UNITS=none bash "$HERE/setup-browser.sh" >/dev/null 2>&1; }
run "$T/home" || { echo "FAIL: script exited non-zero"; exit 1; }
python3 - "$T/home/.cursor/mcp.json" <<'PY' || { echo "FAIL: cursor mcp"; exit 1; }
import json, os, stat, sys
c = json.load(open(sys.argv[1]))["mcpServers"]
assert "chatplace" in c, "kept existing server"
assert "http://127.0.0.1:9999" in c["playwright"]["args"], "browser wired for Grok"
assert stat.S_IMODE(os.stat(sys.argv[1]).st_mode) == 0o600
PY
run "$T/home2" || { echo "FAIL: non-Grok run exited non-zero"; exit 1; }
[ ! -e "$T/home2/.cursor" ] || { echo "FAIL: created ~/.cursor on a Claude-only server"; exit 1; }
echo "PASS: browser wired for Grok agents"
