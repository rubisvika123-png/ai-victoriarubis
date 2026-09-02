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
# Запускать от root на сервере, из папки агента:
#   bash scripts/setup-browser.sh
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
MCP=${AGENT_BROWSER_MCP:-$PWD/dashi-plugin-claude-code/plugin/.mcp.json}

say() { echo "→ $*"; }
die() { echo "ОШИБКА: $*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "запусти от root (это сервер, ты уже root)"

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

# ── 5. Подключаем браузер агенту ─────────────────────────────────────
if [ -f "$MCP" ]; then
  python3 - "$MCP" "$PORT" <<'PY' || die "не получилось прописать браузер в $MCP"
import json, sys
path, port = sys.argv[1], sys.argv[2]
cfg = json.load(open(path))
cfg.setdefault("mcpServers", {})["playwright"] = {
    "command": "npx",
    "args": ["-y", "@playwright/mcp@latest", "--cdp-endpoint", f"http://127.0.0.1:{port}"],
}
json.dump(cfg, open(path, "w"), indent=2, ensure_ascii=False)
PY
  say "браузер прописан агенту: $MCP"
  MCPDONE=1
else
  MCPDONE=0
fi

echo
echo "ГОТОВО. Браузер работает и поднимается сам после перезагрузки."
echo
if [ "$MCPDONE" = "1" ]; then
  echo "Осталось перезапустить агента, чтобы он увидел браузер:"
  echo "  systemctl restart agent-assistant"
else
  echo "Файл настроек агента не найден по пути:"
  echo "  $MCP"
  echo "Запусти меня ещё раз из папки агента (там, где лежит его CLAUDE.md)."
fi
echo
echo "Разовый вход владельца на сайт:  $DIR/share-browser.sh"
echo "Выключить показ:                 $DIR/stop-share.sh"
