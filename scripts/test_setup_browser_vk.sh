#!/usr/bin/env bash
# Second «VK only» browser (12.11.2026): wired to agents under its own MCP name,
# the main «playwright» browser entry stays untouched; proxy flags render right.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/ai-marketing/dashi-plugin-claude-code/plugin/.claude"
echo '{"mcpServers":{"playwright":{"command":"npx","args":["--cdp-endpoint","http://127.0.0.1:9222"]}}}' \
  >"$T/ai-marketing/dashi-plugin-claude-code/plugin/.mcp.json"
out=$(AGENT_BROWSER_CONNECT_ONLY=1 AGENT_BROWSER_AGENTS_ROOT="$T" AGENT_BROWSER_PORT=9226 \
  AGENT_BROWSER_MCP_NAME=vk-browser bash "$HERE/setup-browser.sh" 2>&1) || { echo "FAIL: exit"; echo "$out"; exit 1; }
python3 - "$T/ai-marketing/dashi-plugin-claude-code/plugin/.mcp.json" <<'PY' || exit 1
import json, sys
c = json.load(open(sys.argv[1]))["mcpServers"]
assert "http://127.0.0.1:9222" in c["playwright"]["args"], "main browser must stay"
assert "http://127.0.0.1:9226" in c["vk-browser"]["args"], "vk browser wired"
PY
# Proxy flags: render the same assignment the script uses and check the quoting.
PROXY=socks5://127.0.0.1:1081
eval "$(grep -E '^\[ -n "\$PROXY" \] && PROXY_FLAGS=' "$HERE/setup-browser.sh")"
line="x $PROXY_FLAGS"
case "$line" in *'--proxy-server=socks5://127.0.0.1:1081 --host-resolver-rules="MAP * ~NOTFOUND , EXCLUDE 127.0.0.1"'*) ;; *) echo "FAIL: flags: $line"; exit 1;; esac
echo PASS
