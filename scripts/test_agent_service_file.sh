#!/usr/bin/env bash
# docs/agent.service — the lesson's unit (step 9 «На сервере»), downloadable in one line for students
# whose terminal mangles the multi-line heredoc paste (Windows/PowerShell, 15.11.2026).
set -euo pipefail
f="$(cd "$(dirname "$0")/.." && pwd)/docs/agent.service"
fail() { echo "FAIL: $*"; exit 1; }
head -1 "$f" | grep -qx '\[Unit\]' || fail "must start with [Unit]"
grep -qx 'WorkingDirectory=/root/ai-victoriarubis' "$f" || fail "folder"
grep -q 'new-session -d -s agent ./start.sh' "$f" || fail "start"
grep -q 'has-session -t =agent:' "$f" || fail "exact room name (=agent:)"
grep -qx 'Restart=always' "$f" || fail "restart"
grep -qx 'WantedBy=multi-user.target' "$f" || fail "install target"
! grep -q 'EOF' "$f" || fail "heredoc marker leaked into the file"
echo "OK: agent.service"
