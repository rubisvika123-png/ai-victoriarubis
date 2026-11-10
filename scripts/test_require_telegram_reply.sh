#!/usr/bin/env bash
# Самопроверка предохранителя require-telegram-reply.js.
#   ./test_require_telegram_reply.sh  → "ALL PASS" и код 0
set -uo pipefail

HOOK="$(dirname "$0")/require-telegram-reply.js"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fails=0

check() { # check ИМЯ ОЖИДАЕМОЕ ФАКТ
  if [ "$2" = "$3" ]; then echo "PASS $1"; else echo "FAIL $1: ждал '$2', получил '$3'"; fails=$((fails + 1)); fi
}

inbound='{"type":"user","message":{"content":[{"type":"text","text":"<channel source=\"dashi-channel\" chat_id=\"1\">привет</channel>"}]}}'
reply='{"type":"assistant","message":{"content":[{"type":"tool_use","name":"mcp__dashi-channel__reply"}]}}'
local_msg='{"type":"user","message":{"content":[{"type":"text","text":"посчитай 2+2"}]}}'
work='{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash"}]}}'

run() { # run ФАЙЛ_ТРАНСКРИПТА [stop_hook_active]
  printf '{"transcript_path":"%s","stop_hook_active":%s}' "$1" "${2:-false}" | node "$HOOK"
}

# 1. Пришло из Telegram, ответа не было — ход разворачиваем.
printf '%s\n%s\n' "$inbound" "$work" >"$TMP/a.jsonl"
out=$(run "$TMP/a.jsonl")
check "молчание после входящего блокируется" "block" "$(echo "$out" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{process.stdout.write(JSON.parse(s).decision||"")}catch(e){process.stdout.write("")}})')"

# 2. Ответ отправлен — не мешаем.
printf '%s\n%s\n' "$inbound" "$reply" >"$TMP/b.jsonl"
check "после reply ход проходит" "" "$(run "$TMP/b.jsonl")"

# 3. Задача из терминала, не из Telegram — не наше дело.
printf '%s\n%s\n' "$local_msg" "$work" >"$TMP/c.jsonl"
check "локальный запрос не трогаем" "" "$(run "$TMP/c.jsonl")"

# 4. Reply был на ПРОШЛОЕ сообщение, на новое входящее — нет.
printf '%s\n%s\n%s\n%s\n' "$inbound" "$reply" "$inbound" "$work" >"$TMP/d.jsonl"
out=$(run "$TMP/d.jsonl")
check "старый reply не засчитывается новому сообщению" "block" "$(echo "$out" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{process.stdout.write(JSON.parse(s).decision||"")}catch(e){process.stdout.write("")}})')"

# 5. Второй заход подряд — отпускаем, иначе бесконечный круг при недоступном Telegram.
check "stop_hook_active снимает блокировку" "" "$(run "$TMP/a.jsonl" true)"

# 6. Битый транскрипт не должен ронять ход.
echo 'не json' >"$TMP/e.jsonl"
check "битый транскрипт не мешает" "" "$(run "$TMP/e.jsonl")"

if [ "$fails" -eq 0 ]; then echo "ALL PASS"; exit 0; fi
echo "$fails CHECK(S) FAILED"; exit 1
