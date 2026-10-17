#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# setup-browser.sh — даёт агенту постоянный браузер на сервере.
#
# Что ставит:
#   1. Chromium (через playwright) на невидимом экране Xvfb;
#   2. постоянный профиль — сайты помнят вход после перезагрузки;
#   3. автозапуск (systemd), чтобы браузер поднимался сам;
#   4. share-browser.sh — разовый показ браузера владельцу, чтобы он
#      САМ вошёл в личный кабинет; пароль агент при этом не видит.
#
# Запускать от root на сервере, из любой папки:
#   bash scripts/setup-browser.sh
# Браузер один на сервер, а подключается ко ВСЕМ агентам в /root/*/
# (техспец, маркетолог, ассистент): каждый видит те же сохранённые входы.
#
# Повторный запуск безопасен: уже сделанное пропускается.
# ─────────────────────────────────────────────────────────────────────
set -uo pipefail

# Значения по умолчанию — те же у всех учеников. Переменные нужны
# только чтобы прогнать установку на тестовом экземпляре рядом с рабочим.
DIR=${AGENT_BROWSER_DIR:-/root/agent-browser}
DISP=${AGENT_BROWSER_DISPLAY:-99}
PORT=${AGENT_BROWSER_PORT:-9222}
UNIT=${AGENT_BROWSER_UNIT:-agent-browser}
VNCPORT=${AGENT_BROWSER_VNCPORT:-5900}
WEBPORT=${AGENT_BROWSER_WEBPORT:-6080}
# Где искать агентов: /root/<папка>/dashi-plugin-claude-code/plugin/.mcp.json
AGENTS_ROOT=${AGENT_BROWSER_AGENTS_ROOT:-/root}

say() { echo "→ $*"; }
die() { echo "ОШИБКА: $*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "запусти от root (это сервер, ты уже root)"

# Только подключить уже стоящий браузер к агентам (для проверки скрипта).
if [ "${AGENT_BROWSER_CONNECT_ONLY:-}" != 1 ]; then

# ── 1. Программы ─────────────────────────────────────────────────────
say "ставлю программы (xvfb, x11vnc, novnc, websockify)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq xvfb x11vnc novnc websockify curl >/dev/null 2>&1 ||
  die "не поставились программы — покажи владельцу вывод apt-get install xvfb x11vnc novnc websockify"

if ! command -v cloudflared >/dev/null 2>&1; then
  say "ставлю cloudflared (им браузер показывается владельцу по ссылке)"
  case "$(uname -m)" in
    x86_64) ARCH=amd64 ;;
    aarch64 | arm64) ARCH=arm64 ;;
    *) die "неизвестный тип сервера $(uname -m) — напиши владельцу" ;;
  esac
  curl -fsSL -o /tmp/cloudflared.deb \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb" ||
    die "не скачался cloudflared"
  dpkg -i /tmp/cloudflared.deb >/dev/null 2>&1 || die "не установился cloudflared"
  rm -f /tmp/cloudflared.deb
fi

# ── 2. Сам браузер ───────────────────────────────────────────────────
find_chrome() { ls /root/.cache/ms-playwright/chromium-*/chrome-linux64/chrome 2>/dev/null | head -1; }
CHROME=$(find_chrome)
if [ -z "$CHROME" ]; then
  command -v npx >/dev/null 2>&1 || die "нет npx (node) — сначала поставь node, потом запусти меня снова"
  say "скачиваю Chromium (это пара минут)"
  npx -y playwright install --with-deps chromium >/dev/null 2>&1
  CHROME=$(find_chrome)
  [ -n "$CHROME" ] || die "Chromium не скачался — повтори: npx -y playwright install --with-deps chromium"
fi

mkdir -p "$DIR/profile"

# ── 3. Скрипты ───────────────────────────────────────────────────────
cat >"$DIR/start-browser.sh" <<EOF
#!/usr/bin/env bash
# Постоянный браузер агента: невидимый экран :$DISP, профиль с
# сохранёнными входами, управление по 127.0.0.1:$PORT (только изнутри
# сервера). Живёт под systemd ($UNIT.service).
set -uo pipefail
export HOME=/root
export DISPLAY=":$DISP"
PROFILE=$DIR/profile
CHROME=\$(ls /root/.cache/ms-playwright/chromium-*/chrome-linux64/chrome 2>/dev/null | head -1)
mkdir -p "\$PROFILE"
[ -n "\$CHROME" ] || { echo "нет Chromium в ~/.cache/ms-playwright" >&2; exit 1; }
pgrep -f "Xvfb :$DISP" >/dev/null || { Xvfb :$DISP -screen 0 1360x900x24 -nolisten tcp & sleep 1; }
exec "\$CHROME" \\
  --user-data-dir="\$PROFILE" \\
  --remote-debugging-port=$PORT \\
  --remote-debugging-address=127.0.0.1 \\
  --no-sandbox --no-first-run --no-default-browser-check \\
  --disable-gpu --disable-dev-shm-usage \\
  --window-position=0,0 --window-size=1360,900 \\
  --display=:$DISP \\
  about:blank
EOF

cat >"$DIR/share-browser.sh" <<EOF
#!/usr/bin/env bash
# Разовый показ браузера владельцу, чтобы он сам вошёл на сайт.
# Печатает ссылку и одноразовый пароль. Выключается stop-share.sh —
# постоянный браузер при этом продолжает работать.
set -uo pipefail
export HOME=/root
DIR=$DIR
CFLOG="\$DIR/cloudflared.log"
"\$DIR/stop-share.sh" >/dev/null 2>&1
PW=\$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 8)
x11vnc -storepasswd "\$PW" "\$DIR/.vncpass" >/dev/null 2>&1
x11vnc -display :$DISP -rfbauth "\$DIR/.vncpass" -localhost -forever -shared \\
       -rfbport $VNCPORT -bg -o "\$DIR/x11vnc.log" >/dev/null 2>&1
websockify --web=/usr/share/novnc $WEBPORT localhost:$VNCPORT >"\$DIR/websockify.log" 2>&1 &
: >"\$CFLOG"
cloudflared tunnel --no-autoupdate --url http://localhost:$WEBPORT >"\$CFLOG" 2>&1 &
URL=""
for i in \$(seq 1 20); do
  sleep 1
  URL=\$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "\$CFLOG" | head -1)
  [ -n "\$URL" ] && break
done
[ -n "\$URL" ] || { echo "ссылка не поднялась — смотри \$CFLOG" >&2; exit 1; }
echo "LINK: \${URL}/vnc.html"
echo "PASSWORD: \${PW}"
EOF

cat >"$DIR/stop-share.sh" <<EOF
#!/usr/bin/env bash
# Выключает показ. Сам браузер и сохранённые входы остаются.
pkill -f 'cloudflared tunnel .*localhost:$WEBPORT' 2>/dev/null
pkill -f 'websockify .*$WEBPORT localhost:$VNCPORT' 2>/dev/null
pkill -f 'x11vnc -display :$DISP' 2>/dev/null
exit 0
EOF

chmod +x "$DIR"/*.sh

# ── 4. Автозапуск ────────────────────────────────────────────────────
cat >"/etc/systemd/system/$UNIT.service" <<EOF
[Unit]
Description=Постоянный браузер агента (профиль с входами, CDP на 127.0.0.1:$PORT)
After=network.target

[Service]
Type=simple
ExecStart=$DIR/start-browser.sh
Restart=always
RestartSec=3
Environment=HOME=/root

[Install]
WantedBy=multi-user.target
EOF

say "включаю автозапуск браузера"
systemctl daemon-reload
systemctl enable --now "$UNIT" >/dev/null 2>&1 || die "не запустился $UNIT.service — покажи владельцу systemctl status $UNIT"

for i in $(seq 1 15); do
  sleep 1
  curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1 && break
done
curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1 ||
  die "браузер не отвечает на 127.0.0.1:$PORT — покажи владельцу systemctl status $UNIT"

fi # AGENT_BROWSER_CONNECT_ONLY

# ── 5. Подключаем браузер всем агентам ───────────────────────────────
# 17.10.2026: раньше браузер прописывался только ассистенту, и маркетолог
# честно отвечал «не могу зайти в соцсети». Теперь — каждому агенту.
UNITS=()
FOUND=0
for MCP in "$AGENTS_ROOT"/*/dashi-plugin-claude-code/plugin/.mcp.json; do
  [ -f "$MCP" ] || continue
  FOUND=1
  PLUGIN=$(dirname "$MCP")
  AGENTDIR=$(dirname "$(dirname "$PLUGIN")")
  python3 - "$MCP" "$PORT" "$PLUGIN/.claude/settings.local.json" <<'PY' || die "не получилось прописать браузер в $MCP"
import json, os, sys
path, port, settings = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.load(open(path))
cfg.setdefault("mcpServers", {})["playwright"] = {
    "command": "npx",
    "args": ["-y", "@playwright/mcp@latest", "--cdp-endpoint", f"http://127.0.0.1:{port}"],
}
json.dump(cfg, open(path, "w"), indent=2, ensure_ascii=False)
# A "playwright" entry in disabledMcpjsonServers hides the browser from the
# agent while everything else looks healthy — drop it.
if os.path.exists(settings):
    st = json.load(open(settings))
    off = st.get("disabledMcpjsonServers")
    if isinstance(off, list) and "playwright" in off:
        st["disabledMcpjsonServers"] = [x for x in off if x != "playwright"]
        json.dump(st, open(settings, "w"), indent=2, ensure_ascii=False)
PY
  say "браузер прописан агенту: $AGENTDIR"
  # Автозапуск этого агента: юнит с WorkingDirectory=<его папка>. Не «любое
  # упоминание папки» — его есть и у agent-watchdog.service, а тот по
  # алфавиту идёт раньше agent.service.
  UNITFILE=$(grep -lsx -- "WorkingDirectory=$AGENTDIR/\?" /etc/systemd/system/*.service 2>/dev/null |
    grep -v "/$UNIT.service" | head -1)
  [ -n "$UNITFILE" ] && UNITS+=("$(basename "$UNITFILE" .service)")
done

[ "$FOUND" = 1 ] || die "не нашёл ни одного агента в $AGENTS_ROOT/*/dashi-plugin-claude-code — покажи владельцу ls $AGENTS_ROOT"

echo
echo "ГОТОВО. Браузер работает и поднимается сам после перезагрузки."
echo
echo "Разовый вход владельца на сайт:  $DIR/share-browser.sh"
echo "Выключить показ:                 $DIR/stop-share.sh"
echo
if [ "${#UNITS[@]}" -gt 0 ]; then
  echo "Перезапускаю агентов, чтобы они увидели браузер: ${UNITS[*]}"
  echo "Через полминуты они снова на связи."
  # --no-block и в самом конце: если команду запустил сам агент, его
  # перезапуск не оборвёт скрипт на середине.
  systemctl restart --no-block "${UNITS[@]}"
else
  echo "Автозапуск агентов не нашёл — перезапусти их сам (список: systemctl list-unit-files 'agent*')."
fi
