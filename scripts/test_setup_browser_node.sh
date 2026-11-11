#!/usr/bin/env bash
# Checks that setup-browser.sh installs node itself when npx is missing
# (Claude Code's native installer brings no node; students got stuck on «нет npx»).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin"
# fake curl prints a harmless setup script; fake apt-get "installs" npx
cat >"$T/bin/curl" <<'S'
#!/bin/sh
echo 'echo setup-ok >> "$LOG"'
S
cat >"$T/bin/apt-get" <<'S'
#!/bin/sh
echo "apt-get $*" >> "$LOG"
case "$*" in *nodejs*) printf '#!/bin/sh\necho 10.0.0\n' > "$FAKEBIN/npx"; chmod +x "$FAKEBIN/npx" ;; esac
S
chmod +x "$T/bin/"*
export LOG="$T/log" FAKEBIN="$T/bin"
P="$T/bin:/usr/bin:/bin"
# make sure no real npx is visible
for d in /usr/bin /bin; do [ -x "$d/npx" ] && P="$T/bin:$T/sys"; done
if [ "$P" = "$T/bin:$T/sys" ]; then
  mkdir -p "$T/sys"
  for c in bash sh cat grep id hash printf chmod mkdir rm env sed; do
    src=$(command -v "$c" 2>/dev/null) && [ -x "$src" ] && ln -sf "$src" "$T/sys/$c"
  done
fi

out=$(PATH="$P" AGENT_BROWSER_NODE_ONLY=1 bash "$HERE/setup-browser.sh" 2>&1)
rc=$?
fail=0
[ $rc = 0 ] || { echo "FAIL: exit $rc"; echo "$out"; fail=1; }
grep -q setup-ok "$LOG" || { echo "FAIL: node setup script not run"; fail=1; }
grep -q "install -y -qq nodejs" "$LOG" || { echo "FAIL: nodejs not installed"; fail=1; }
echo "$out" | grep -q "node готов" || { echo "FAIL: no success line"; fail=1; }

# second run: npx present -> nothing installed
: > "$LOG"
PATH="$P" AGENT_BROWSER_NODE_ONLY=1 bash "$HERE/setup-browser.sh" >/dev/null 2>&1
[ -s "$LOG" ] && { echo "FAIL: reinstalled node although npx exists"; fail=1; }

[ "$fail" = 0 ] && echo "PASS: setup-browser installs node when npx is missing" || exit 1
