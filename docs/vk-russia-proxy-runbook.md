# ВКонтакте с российского адреса: браузер у тебя, российский сервер — только проход

Инструкция **для техспеца (Claude Code)**. Владелец даёт команду из урока
«Готовый навык: 6 соцсетей на автомате» (часть про ВКонтакте).

**С 13.11.2026 основной путь урока — «Вариант Б» (личный российский прокси, ниже):**
владелец присылает IP, порт, логин, пароль и тип прокси. Шаги 1–3 тогда пропускаешь.
Шаги 1–3 — для тех, у кого уже есть свой российский сервер (адрес и пароль).

Зачем так: ВК публикует фото, видео и клипы только после входа владельца как
человека, а заграничный адрес встречает плохо. Поэтому браузер для ВК живёт
**здесь, на основном сервере**, а весь его интернет идёт через российский сервер.
Российскому серверу почти ничего не нужно — хватает самого дешёвого тарифа
(512 МБ). Браузер на сам российский сервер **не ставь**: ему там мало памяти.

Всё ниже выполняется на основном сервере от `root`.

## Шаг 1. Ключ для входа на российский сервер

```
[ -f /root/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -N "" -f /root/.ssh/id_ed25519
apt-get install -y -qq sshpass
SSHPASS='<пароль российского сервера>' sshpass -e ssh-copy-id -o StrictHostKeyChecking=accept-new root@<IP>
ssh -o BatchMode=yes root@<IP> 'echo ok'
```

Должно напечатать `ok`. Пароль в чат и в файлы не пиши.

## Шаг 2. Защита российского сервера от подбора пароля

```
ssh root@<IP> 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fail2ban && systemctl enable --now fail2ban'
```

## Шаг 3. Постоянный проход через российский сервер

Создай `/etc/systemd/system/ru-socks.service`:

```
[Unit]
Description=Проход в интернет через российский сервер (для ВКонтакте)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/ssh -N -D 127.0.0.1:1081 -o BatchMode=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes root@<IP>
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```
systemctl daemon-reload && systemctl enable --now ru-socks
sleep 3; curl -s --socks5-hostname 127.0.0.1:1081 https://ifconfig.me; echo
```

Должен напечататься **адрес российского сервера**. Если пусто — `systemctl status ru-socks --no-pager`.

## Шаг 4. Отдельный браузер только для ВКонтакте

Основной браузер агентов (из урока про браузер) не трогаем — Инстаграм, Threads и
остальное видят прежний адрес. Ставим второй, через проход:

```
AGENT_BROWSER_DIR=/root/vk-browser AGENT_BROWSER_DISPLAY=98 AGENT_BROWSER_PORT=9226 \
AGENT_BROWSER_UNIT=vk-browser AGENT_BROWSER_VNCPORT=5901 AGENT_BROWSER_WEBPORT=6081 \
AGENT_BROWSER_PROXY=socks5://127.0.0.1:1081 AGENT_BROWSER_AFTER_UNIT=ru-socks.service \
AGENT_BROWSER_MCP_NAME=vk-browser bash scripts/setup-browser.sh
```

Скрипт — из заготовки курса (`scripts/setup-browser.sh`). Если в нём нет строки
`AGENT_BROWSER_PROXY` — обнови папку из заготовки (`rubisvika123-png/ai-victoriarubis`).
Агенты получат новый инструмент `vk-browser`; основной `playwright` остаётся как был.

Проверка, что браузер жив:

```
curl -s http://127.0.0.1:9226/json/version | head -3
```

## Шаг 5. Владелец один раз входит в ВК

```
/root/vk-browser/share-browser.sh
```

Пришли владельцу ссылку и одноразовый пароль. Он открывает vk.com и входит в свой
аккаунт сам — ты пароль не видишь. Когда он напишет, что вошёл:

```
/root/vk-browser/stop-share.sh
```

## Шаг 6. Как публиковать

- **Текстовые посты** — как раньше, ключом сообщества.
- **Фото, видео, клипы** — через инструмент `vk-browser` (не `playwright`!).
- **Видео в сообщество** у некоторых групп ВК не даёт загрузить напрямую (ошибка прав).
  Тогда загрузи видео на личную страницу владельца, дождись конца обработки, не
  закрывая вкладку, и опубликуй пост от сообщества с этим видео.
- Если ВК показал «Проверяем, что вы не робот» — **сам не проходи**. Остановись,
  запусти `share-browser.sh` и попроси владельца нажать самому.

## Вариант Б: российский прокси вместо сервера (если сервера «нет в наличии»)

12.11.2026 дешёвые российские серверы закончились у RUVDS и FirstVDS. Тогда владелец
покупает не сервер, а **личный российский прокси** (например, Proxy6: страна Россия,
IPv4, «в одни руки», на 1 месяц) и присылает тебе из кабинета: IP, порт, логин, пароль
и тип (SOCKS5 или HTTPS). Шаги 1–3 выше тогда **пропускаешь**, вместо них:

Браузер не умеет сам вводить логин и пароль прокси, поэтому ставим маленькую
программу-посредник gost: она слушает 127.0.0.1:1081 без пароля и отправляет всё
в прокси владельца с паролем. Для браузера всё выглядит так же, как с сервером.

```
cd /tmp && curl -fsSLO https://github.com/go-gost/gost/releases/download/v3.3.3/gost_3.3.3_linux_amd64.tar.gz
tar xzf gost_3.3.3_linux_amd64.tar.gz gost && install -m 755 gost /usr/local/bin/gost && gost -V
```

Создай `/etc/systemd/system/ru-socks.service` (имя то же, что в шаге 3, чтобы шаг 4 не менять).
Подставь данные владельца; если тип прокси HTTPS — вместо `socks5://` пиши `http://`:

```
[Unit]
Description=Проход в интернет через российский прокси (для ВКонтакте)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/gost -L socks5://127.0.0.1:1081 -F socks5://ЛОГИН:ПАРОЛЬ@IP:ПОРТ
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```
chmod 600 /etc/systemd/system/ru-socks.service
systemctl daemon-reload && systemctl enable --now ru-socks
sleep 3; curl -s --socks5-hostname 127.0.0.1:1081 https://ifconfig.me; echo
```

Должен напечататься **адрес прокси** (тот IP, что прислал владелец). Дальше — шаги 4–6
без изменений. VPN «из России» в этом варианте не ставится: у прокси нет своего сервера.

## VPN «из России» на том же сервере

Ставится на российский сервер как раньше (AmneziaWG или XRay — по приложению
владельца). Он почти не нагружает сервер, 512 МБ хватает.
