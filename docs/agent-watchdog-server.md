# Сторож для агента на сервере (Урок для тех, кто переехал на сервер)

Это инструкция для твоего технического специалиста (человека или ИИ-агента),
который настраивает твоего личного агента на сервере. Отдай ему этот файл
целиком — там всё, что нужно: что ставим, зачем, как проверить, что работает.

Актуально ТОЛЬКО если твой агент уже живёт на сервере (не на твоём личном
компьютере) — там, где терминал не закрывается, когда ты выключаешь свой ноутбук.

---

## Зачем это вообще нужно

Твой агент — это программа `claude`, запущенная внутри терминала. Если сервер
перезагрузился, программа упала с ошибкой, или у неё закончился 5-часовой лимит
подписки Claude — агент замолкает. Без присмотра никто этого не заметит, пока
ты сам не напишешь ему и не увидишь тишину.

«Сторож» — отдельная маленькая программа, которая крутится РЯДОМ с агентом
(не внутри него) и каждые несколько секунд проверяет: жив ли агент. Если
что-то не так — чинит сама, без участия человека. Это ровно то же самое,
что уже работает у нашей команды на своём сервере — просто здесь описано
так, чтобы поставить это для ОДНОГО твоего агента.

Сторож ловит четыре разные поломки:

1. **Агент полностью упал** (окно терминала/сессия исчезла) — сторож
   запускает его заново.
2. **Агент вроде жив, но канал с Telegram отвалился** (бывает: сам процесс
   `claude` работает, а его «мост» к Telegram — нет, и сообщения перестают
   доходить) — сторож перезапускает весь агент целиком.
3. **Завис лишний процесс-двойник**, который тоже пытается слушать Telegram
   (бывает после неудачного перезапуска) — из-за этого Telegram начинает
   отдавать сообщения то одному, то другому процессу вразнобой, и часть
   сообщений теряется. Сторож находит и убивает только лишние копии,
   живую не трогает.
4. **У агента кончился 5-часовой лимит Claude** — агент технически жив, но
   на экране всплывает меню с вопросом «What do you want to do?» и двумя
   пунктами («Stop and wait for limit to reset» / «Upgrade your plan»), и
   claude замирает, ждёт нажатия клавиши. Раньше это меню приходилось
   закрывать руками — заходить на сервер и жать Enter. Теперь **сторож
   отвечает на это меню сам** (выбирает «ждать сброса лимита») и следом
   **сам пишет тебе в Telegram напрямую от бота этого агента** (сам агент
   в этот момент недоступен, поэтому написать должен кто-то другой) —
   с примерным временем, когда лимит освободится. Когда лимит проходит —
   сторож ещё раз «тыкает» агента, чтобы он ожил. Тебе вообще не нужно
   заходить на сервер руками.

---

## Что понадобится

- Linux-сервер (Ubuntu/Debian и похожие), с правами root или sudo.
- `tmux` — программа, которая держит терминал агента открытым, даже когда
  ты отключился от сервера. Проверить: `tmux -V`. Если нет — поставить:
  `apt-get install -y tmux`.
- Агент уже настроен и запускается командой `./start.sh` из своей папки
  (так же, как в Уроке 1 — просто теперь эта папка на сервере, а не на
  твоём компьютере).

---

## Шаг 1. Положить скрипт сторожа

Создай файл `scripts/agent-watchdog.sh` в папке агента на сервере со
следующим содержимым. Единственное, что нужно поменять под себя —
блок `AGENTS` в начале: вписать имя твоей tmux-сессии и путь к папке агента.

```bash
#!/usr/bin/env bash
# agent-watchdog.sh — держит агента живым: перезапускает при падении,
# чистит зависшие копии-дубликаты, замечает исчерпание лимита Claude
# и пишет владельцу напрямую, когда агент сам ответить не может.
set -uo pipefail
export HOME="${HOME:-/root}"
export PATH="$HOME/.bun/bin:$PATH"

LOG="${WATCHDOG_LOG:-$HOME/watchdog.log}"
INTERVAL="${WATCHDOG_INTERVAL:-5}"
COOLDOWN="${WATCHDOG_COOLDOWN:-30}"
GRACE="${WATCHDOG_GRACE:-60}"

# ЗАПОЛНИ ПОД СЕБЯ: имя tmux-сессии -> папка агента (там, где start.sh)
declare -A AGENTS=(
  [my-agent]="/path/to/your/agent"
)

declare -A LAST_RESTART=()
declare -A LIMIT_NOTIFIED=()

log() { echo "$(date '+%F %T') $*" >>"$LOG" 2>/dev/null || true; }

confirm_startup() {
  local name="$1" i scr
  for i in $(seq 1 40); do
    sleep 2
    tmux has-session -t "$name" 2>/dev/null || return 0
    scr=$(tmux capture-pane -t "$name" -p 2>/dev/null)
    if echo "$scr" | grep -q "bypass permissions on"; then
      log "startup complete for $name"; return 0
    elif echo "$scr" | grep -q "Bypass Permissions mode"; then
      tmux send-keys -t "$name" Down; sleep 0.5; tmux send-keys -t "$name" Enter
      log "accepted bypass-permissions dialog for $name"
    elif echo "$scr" | grep -q "for local development"; then
      tmux send-keys -t "$name" Enter
      log "confirmed dev-channels dialog for $name"
    fi
  done
}

ensure() {
  local name="$1" dir="$2" now last
  tmux has-session -t "$name" 2>/dev/null && return 0

  now=$(date +%s)
  last=${LAST_RESTART[$name]:-0}
  if (( now - last < COOLDOWN )); then
    return 0
  fi
  LAST_RESTART[$name]=$now

  if [ -x "$dir/start.sh" ]; then
    tmux new-session -d -s "$name" -c "$dir" "./start.sh"
    confirm_startup "$name" &
    log "restarted $name (dir $dir)"
  else
    log "ERROR $name: no executable start.sh in $dir"
  fi
}

pulse() {
  local name="$1" now last pane_pid
  tmux has-session -t "$name" 2>/dev/null || return 0
  now=$(date +%s); last=${LAST_RESTART[$name]:-0}
  (( now - last < GRACE )) && return 0
  pane_pid=$(tmux list-panes -t "$name" -F '#{pane_pid}' 2>/dev/null | head -1)
  [ -n "$pane_pid" ] || return 0
  if ! pgrep -P "$pane_pid" -f "server.ts" >/dev/null 2>&1; then
    log "NO PULSE for $name (Telegram bridge dead) — restarting"
    LAST_RESTART[$name]=$now
    tmux kill-session -t "$name" 2>/dev/null
  fi
}

sweep() {
  local name="$1" dir="$2" live_pid pid pcwd
  live_pid=$(tmux list-panes -t "$name" -F '#{pane_pid}' 2>/dev/null | head -1)
  [ -n "$live_pid" ] || return 0
  for pid in $(pgrep -f 'server:dashi-channel' 2>/dev/null); do
    [ "$pid" = "$live_pid" ] && continue
    pcwd=$(readlink "/proc/$pid/cwd" 2>/dev/null) || continue
    case "$pcwd" in
      "$dir"|"$dir"/*) kill "$pid" 2>/dev/null && log "swept orphan claude $pid for $name (cwd $pcwd)" ;;
    esac
  done
}

# Пишет владельцу напрямую от бота ЭТОГО агента (сам агент недоступен —
# писать должен кто-то другой). Токен и Telegram ID владельца берутся из
# secrets/channel.env агента — тех же, что и сам агент использует.
notify_owner() {
  local dir="$1" msg="$2" env_file token uid
  env_file="$dir/secrets/channel.env"
  token=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$env_file" 2>/dev/null | cut -d= -f2-)
  uid=$(grep -m1 '^TELEGRAM_ALLOWED_USER_IDS=' "$env_file" 2>/dev/null | cut -d= -f2- | cut -d, -f1)
  if [ -z "$token" ] || [ -z "$uid" ]; then
    log "notify_owner: missing token/user id in $env_file"
    return 1
  fi
  curl -s -m 10 "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${uid}" --data-urlencode "text=${msg}" >/dev/null
}

# Ловит момент "кончился лимит Claude" в две стадии.
#
# Стадия 1 — МЕНЮ. Реальный экран лимита (проверено на скриншоте Вики
# 2026-08-20) — это не баннер, а интерактивное меню:
#     What do you want to do?
#   > 1. Stop and wait for limit to reset
#     2. Upgrade your plan
# Пока оно на экране, claude ждёт нажатия клавиши и НЕ продолжит сам, даже
# когда лимит освободится — раньше это приходилось нажимать руками. Пункт 1
# выбран по умолчанию, поэтому просто Enter отвечает на него правильно.
#
# Стадия 2 — уведомление с точным временем сброса. Формулировка баннера
# "usage limit reached ... resets at ..." и формат времени пока не пойманы
# вживую — это лучшее предположение. Если после реального случая окажется,
# что сторож не поймал момент, подправь регэксп ниже под точный текст с
# экрана, саму проверку не убирай.
check_limit() {
  local name="$1" dir="$2" pane reset_time msg menu=""
  tmux has-session -t "$name" 2>/dev/null || return 0
  pane=$(tmux capture-pane -t "$name" -p 2>/dev/null)

  if echo "$pane" | grep -qiF 'Stop and wait for limit to reset' \
     && echo "$pane" | grep -qiF 'Upgrade your plan' \
     && echo "$pane" | grep -qiF 'What do you want to do?'; then
    menu=1
    tmux send-keys -t "$name" Enter 2>/dev/null
    log "limit prompt answered for $name (chose: stop and wait for reset)"
  fi

  if echo "$pane" | grep -vE '\||grep' | grep -qiE 'usage limit reached|limit reached.*resets?'; then
    [ -n "${LIMIT_NOTIFIED[$name]:-}" ] && return 0
    reset_time=$(echo "$pane" | grep -oiE 'resets?[^0-9]{0,8}[0-9]{1,2}(:[0-9]{2})?\s*(am|pm)?' | tail -1 \
      | grep -oiE '[0-9]{1,2}(:[0-9]{2})?\s*(am|pm)?')
    if [ -n "$reset_time" ]; then
      msg="${name} сейчас недоступен — кончился лимит Claude, освободится примерно в ${reset_time}."
    else
      msg="${name} сейчас недоступен — кончился лимит Claude. Точное время на экране не разобрал."
    fi
    notify_owner "$dir" "$msg" && LIMIT_NOTIFIED[$name]=1
    log "usage-limit hit for $name (reset: ${reset_time:-unknown}), notified owner"
  elif [ -n "${LIMIT_NOTIFIED[$name]:-}" ] && [ -z "$menu" ]; then
    LIMIT_NOTIFIED[$name]=""
    tmux send-keys -t "$name" Enter 2>/dev/null
    log "usage-limit cleared for $name, nudged"
  fi
}

main() {
  log "watchdog started (pid $$)"
  while true; do
    for name in "${!AGENTS[@]}"; do
      ensure "$name" "${AGENTS[$name]}"
      sweep  "$name" "${AGENTS[$name]}"
      pulse  "$name"
      check_limit "$name" "${AGENTS[$name]}"
    done
    sleep "$INTERVAL"
  done
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main
fi
```

Сделай файл исполняемым:

```bash
chmod +x scripts/agent-watchdog.sh
```

---

## Шаг 2. Заставить сторожа работать 24/7 сам по себе

Сторожу самому тоже нужен присмотр — если сервер перезагрузится, сторож
должен подняться сам. Для этого используем `systemd` (стандартный
«диспетчер программ» в Linux).

Создай файл `/etc/systemd/system/agent-watchdog.service`:

```ini
[Unit]
Description=Watchdog для агента
After=network.target

[Service]
Type=simple
ExecStart=/bin/bash /path/to/your/agent/scripts/agent-watchdog.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Замени `/path/to/your/agent/` на реальный путь. Дальше:

```bash
systemctl daemon-reload
systemctl enable --now agent-watchdog.service
```

---

## Шаг 3. Проверить, что работает

```bash
systemctl status agent-watchdog.service   # должен быть "active (running)"
tail -f ~/watchdog.log                    # смотрим лог в реальном времени
```

Проверка «поймает ли падение»: убей сессию агента командой
`tmux kill-session -t my-agent` и подожди ~10 секунд — в логе должна
появиться строка `restarted my-agent`, и агент должен снова отвечать
в Telegram.

Проверку на «лимит закончился» вживую не устроить по щелчку (нужно
дождаться реального момента, когда у агента кончится подписка на 5 часов).
Но когда это случится в первый раз — открой `tmux attach -t my-agent` СРАЗУ,
как заметишь тишину: меню «What do you want to do?» должно исчезнуть само
за несколько секунд (сторож уже нажал Enter за тебя), а в `watchdog.log`
появится строка `limit prompt answered`. Если уведомление в Telegram не
пришло, хотя агент явно молчал из-за лимита, — посмотри, что именно
написано на экране в этот момент, и поправь регэксп в `check_limit()` под
точный текст (только формулировку баннера с временем сброса, само меню уже
проверено вживую и трогать не нужно).

---

## Итог

После этих трёх шагов: если агент падает — сам поднимается за секунды;
если у него кончается лимит — сторож сам закрывает всплывшее меню, тебе
сразу приходит сообщение с объяснением и примерным временем, а когда лимит
пройдёт — агент сам разбудится. Руками лезть на сервер, жать Enter и
разбираться «почему молчит» больше не нужно вообще.
