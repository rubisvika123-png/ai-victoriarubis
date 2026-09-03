#!/usr/bin/env bash
# Проверка hermes-protect-home.sh: блокирует команды, которыми агент стирает
# сам себя, и не мешает обычной работе. Хук читает JSON со stdin и печатает
# {"decision": "block", ...} только когда команду надо остановить.
#
# Запуск:  bash scripts/test_hermes_protect_home.sh
set -uo pipefail
HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/hermes-protect-home.sh"

fail=0
HOME_DIR=${HERMES_HOME_UNDER_TEST:-/root/.hermes}

# check EXPECT("block"|"allow") CWD COMMAND DESCRIPTION
check() {
  local expect="$1" cwd="$2" cmd="$3" what="$4" out
  out=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": "terminal", "cwd": sys.argv[1], "tool_input": {"command": sys.argv[2]}}))
' "$cwd" "$cmd" | HERMES_HOME="$HOME_DIR" python3 "$HOOK")
  if [ "$expect" = "block" ]; then
    echo "$out" | grep -q '"block"' || { echo "FAIL: пропустил — $what: $cmd"; fail=1; }
  else
    echo "$out" | grep -q '"block"' && { echo "FAIL: заблокировал лишнее — $what: $cmd"; fail=1; }
  fi
  return 0
}

# --- то, что должно блокироваться -------------------------------------------
check block /root  'rm -rf /root/.hermes'                       "снос всего дома"
check block /root  'rm -rf /root/.hermes/profiles/agent2'       "снос второго профиля"
check block /root  'rm -rf ~/.hermes/profiles'                  "снос всех профилей через ~"
check block /root  'rm -rf "$HERMES_HOME"'                      "снос через переменную"
check block /root  'python3 -c "import shutil; shutil.rmtree(\"/root/.hermes/profiles/agent2\")"' "rmtree из кода"
check block /root  'find /root/.hermes -type f -delete'         "find -delete"
check block /root  'find /root/.hermes -name "*" -exec rm {} +' "find -exec rm"
check block /root/.hermes/profiles/agent2 'rm -rf .'            "относительный путь из папки профиля"
check block /root/.hermes               'rm -rf ./profiles'     "относительный путь из дома"
check block /root  'rm /root/.hermes/profiles/agent2/.env'      "удаление доступа к боту"
check block /root  'rm -f /root/.hermes/profiles/agent2/SOUL.md' "удаление характера"
check block /root  'mv /root/.hermes/config.yaml /tmp/'         "унос настроек"
check block /root  'truncate -s 0 /root/.hermes/.env'           "обнуление доступов"
check block /root  'rm /root/.hermes/auth.json'                 "удаление входа в аккаунт"

# --- то, что должно проходить ------------------------------------------------
check allow /root  'ls -la /root/.hermes/profiles/agent2'       "просмотр"
check allow /root  'cat /root/.hermes/profiles/agent2/SOUL.md'  "чтение характера"
check allow /root  'rm -rf /root/.hermes/cache'                 "чистка кэша"
check allow /root  'rm -rf /root/.hermes/profiles/agent2/logs'  "чистка логов профиля"
check allow /root  'rm -rf /tmp/build'                          "удаление вне дома Эрмеса"
check allow /root  'rm -rf /root/ai-victoriarubis/node_modules' "удаление в чужом проекте"
check allow /root  'rm /root/.hermes/notes/2026-09-03.md'       "удаление обычного файла внутри дома"
check allow /root  'systemctl --user restart hermes-gateway.service' "перезапуск службы"
check allow /root  'echo "разговор про rm -rf и .env, просто текст"' "текст про удаление, а не удаление"
check allow /root/.hermes/profiles/agent2 'ls -la'              "обычная работа из папки профиля"

# --- дом не обязан лежать в /root: Mac, обычный пользователь, свой профиль ---
HOME_DIR=/home/uchenik/.hermes
check block /home/uchenik 'rm -rf /home/uchenik/.hermes'        "снос дома у обычного пользователя"
check block /home/uchenik/.hermes 'rm -rf .'                    "относительный путь из чужого дома"
check allow /home/uchenik 'rm -rf /home/uchenik/.hermes/cache'  "чистка кэша у обычного пользователя"
check allow /home/uchenik 'rm -rf /home/uchenik/proekt'         "удаление вне дома"

[ "$fail" -eq 0 ] && echo "PASS"
exit "$fail"
