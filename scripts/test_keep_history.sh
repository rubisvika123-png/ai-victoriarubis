#!/usr/bin/env bash
# keep-history.py: Claude Code must keep agent history ≥90 days (Vika 15.11.2026) —
# the Claude→GPT converter builds the GPT agent's recipes from this history.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail() { echo "FAIL: $*"; exit 1; }
# no settings file yet → created with 90
HOME=$T python3 "$here/keep-history.py"
python3 -c "import json;d=json.load(open('$T/.claude/settings.json'));assert d['cleanupPeriodDays']==90,d" || fail "not created"
# other keys survive, a longer value is not shortened
echo '{"model":"opus","cleanupPeriodDays":365}' > "$T/.claude/settings.json"
HOME=$T python3 "$here/keep-history.py"
python3 -c "import json;d=json.load(open('$T/.claude/settings.json'));assert d=={'model':'opus','cleanupPeriodDays':365},d" || fail "changed other keys"
# shorter value is raised
echo '{"cleanupPeriodDays":30}' > "$T/.claude/settings.json"
HOME=$T python3 "$here/keep-history.py"
python3 -c "import json;assert json.load(open('$T/.claude/settings.json'))['cleanupPeriodDays']==90" || fail "not raised"
# broken JSON is left alone (never clobber the owner's settings)
echo '{broken' > "$T/.claude/settings.json"
HOME=$T python3 "$here/keep-history.py" || true
[ "$(cat "$T/.claude/settings.json")" = "{broken" ] || fail "broken file overwritten"
echo "OK: keep-history"
