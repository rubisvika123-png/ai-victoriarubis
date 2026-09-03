# Второй агент на Эрмесе (движок Hermes, оплата подпиской ChatGPT)

Эта инструкция — **для агента-кодера (Claude Code)**, а не для владельца.
Владелец даёт команду «сделай мне второго агента на Эрмесе», агент выполняет
шаги отсюда сверху вниз и после каждого показывает владельцу результат
простыми словами.

Что получится в конце: второй агент — техспец — живёт на том же сервере,
отвечает в своём Telegram-боте, работает на оплаченной подписке ChatGPT
(никаких оплат за каждый запрос) и **не может стереть сам себя**.

Всё, что ниже, выполняется на сервере от пользователя `root`.

---

## Шаг 0. Что владелец делает руками

Два действия агент сделать не может — они требуют личного входа. Скажи их
владельцу сразу, одним сообщением, и жди ответа:

1. **Оплаченная подписка ChatGPT** (тариф Plus, 20 $). Если подписки нет —
   сначала оплатить, иначе движок не запустится.
2. **Свой бот в Telegram.** В Telegram найти `@BotFather` → `/newbot` →
   придумать имя и адрес бота (адрес должен заканчиваться на `bot`) → прислать
   агенту длинную строку-токен вида `8123456789:AAF...`.

Пока токена и подписки нет — дальше шага 3 не идти.

---

## Шаг 1. Поставить движок Эрмеса

```
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup
```

Проверка:

```
hermes --version
```

Должна быть строка вида `Hermes Agent v0.20.x`. Если команда не найдена —
открыть новый вход в терминал или добавить `/usr/local/bin` в `PATH`.

Дом нового агента после установки — `/root/.hermes` (настройки, характер,
память). Это его единственная папка, беречь её.

---

## Шаг 2. Перевести агента на подписку ChatGPT

По умолчанию Эрмес хочет платный ключ по счётчику. Переключаем на вход по
подписке (провайдер `openai-codex`):

```
hermes config set model.provider openai-codex
hermes config set model.base_url https://chatgpt.com/backend-api/codex
hermes config set model.default gpt-5.6-terra
```

**Настройки правим только командой `hermes config set`.** Руками в
`config.yaml` лезть нельзя — один лишний пробел ломает запуск (исключение —
шаг 4, там правка через Python-парсер).

Проверенные модели на подписке ChatGPT: `gpt-5.6-terra` (быстрая, дешёвая по
лимитам), `gpt-5.6-sol` и `gpt-5.6-sol-pro` (умнее, но медленнее и лимиты
тратятся быстрее). Модели вида `gpt-5.6` без суффикса подписка отклоняет с
ошибкой 400 — не предлагать.

---

## Шаг 3. Вход в аккаунт ChatGPT (делает владелец)

Запускать должен **владелец в своём терминале** — это вход в его личный
аккаунт:

```
hermes auth add openai-codex
```

На экране появится код вида `ABCD-EFGH` и ссылка
`https://auth.openai.com/codex/device`. Владелец открывает ссылку в браузере
(для РФ — с VPN), вводит код, входит в свой ChatGPT. Окно на вход — 15 минут.
Терминал сам допишет «logged in», когда вход пройдёт.

Проверка (это уже может сделать агент):

```
hermes auth status openai-codex
hermes -z "ответь одним словом: ОК"
```

Если вторая команда вернула ответ модели — движок живой и платит подписка.
Если 400 с текстом про модель — вернуться к шагу 2 и взять другую модель.

---

## Шаг 4. Защита: агент не может стереть сам себя

Это обязательный шаг, не пропускать. Без него агент одной командой
`rm -rf ~/.hermes` уносит свою память, характер, доступ к боту и вход в
аккаунт. Так уже происходило на боевом сервере.

Скачать готовый и проверенный хук (запускается перед каждой командой агента и
отменяет опасные):

```
mkdir -p /root/hermes-guard
curl -fsSL https://raw.githubusercontent.com/rubisvika123-png/ai-victoriarubis/main/scripts/hermes-protect-home.sh -o /root/hermes-guard/hermes-protect-home.sh
curl -fsSL https://raw.githubusercontent.com/rubisvika123-png/ai-victoriarubis/main/scripts/test_hermes_protect_home.sh -o /root/hermes-guard/test_hermes_protect_home.sh
chmod +x /root/hermes-guard/hermes-protect-home.sh
bash /root/hermes-guard/test_hermes_protect_home.sh
```

Последняя команда должна напечатать `PASS`.

Прописать хук в настройки. **`hermes config set` для этого не годится** — он
сохранит список как строку, и Эрмес молча его проигнорирует. Правим через
парсер:

```
python3 - <<'PY'
import yaml, pathlib
p = pathlib.Path("/root/.hermes/config.yaml")
c = yaml.safe_load(p.read_text()) or {}
c.setdefault("hooks", {})["pre_tool_call"] = [{
    "matcher": "terminal|execute_code",
    "command": "/root/hermes-guard/hermes-protect-home.sh",
    "timeout": 10,
}]
c["hooks_auto_accept"] = True
p.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False))
print("хук прописан")
PY
```

Хук начинает работать только после перезапуска шлюза (тогда он попадает в
список разрешённых `shell-hooks-allowlist.json`). Перезапуск — на шаге 8.

Проверка после перезапуска:

```
hermes hooks list
```

В строке хука должно быть, что согласие получено. Живая проверка — на шаге 9.

---

## Шаг 5. Характер агента (SOUL.md)

Характер лежит в `/root/.hermes/SOUL.md`. Написать его под задачу владельца.
Минимальный рабочий скелет для техспеца — заполнить именами и задачами
владельца:

```
# <Имя агента> — техспец

## Кто я
Технический помощник владельца. Живу на сервере, отвечаю в Telegram.
Работаю на движке Hermes, модель GPT.

## Как отвечаю
- По-русски, простыми словами, без технического жаргона.
- Сначала суть, потом объяснение. Без эмодзи.
- Не знаю — говорю «не знаю», не выдумываю.

## Что делаю
<задачи владельца: следить за сервером, чинить сбои, отвечать на вопросы…>

## Чего не делаю без разрешения владельца
- Не удаляю файлы, папки и службы.
- Не трогаю свой дом /root/.hermes и чужие проекты.
- Не трачу деньги, не покупаю сервисы.
- Не отвечаю никому, кроме владельца.
```

Важно: у уже открытого чата характер не меняется на лету. После правки
`SOUL.md` владелец пишет боту `/new` — начинается новый разговор с новым
характером.

---

## Шаг 6. Подключить Telegram-бота

Нужны две вещи: токен из шага 0 и цифровой id владельца в Telegram. Id уже
есть на сервере — в файле `secrets/channel.env` первого агента (строка
`TELEGRAM_ALLOWED_USERS` или `OWNER_ID`). Если файла нет — владелец узнаёт id
у бота `@userinfobot`.

```
cat > /root/.hermes/.env <<'ENV'
TELEGRAM_BOT_TOKEN=СЮДА_ТОКЕН_ИЗ_BOTFATHER
TELEGRAM_ALLOWED_USERS=СЮДА_ID_ВЛАДЕЛЬЦА
TELEGRAM_HOME_CHANNEL=СЮДА_ID_ВЛАДЕЛЬЦА
ENV
chmod 600 /root/.hermes/.env
hermes config set platforms.telegram.enabled true
```

`TELEGRAM_ALLOWED_USERS` — это защита: всем, кроме владельца, бот не отвечает.
Не оставлять пустым.

Токен — секрет. В git не коммитить, в переписку не вставлять.

---

## Шаг 7. Убрать техническую болтовню из чата

По умолчанию Эрмес пишет в Telegram служебные сообщения («создан навык»,
«бюджет итераций исчерпан», «шлюз перезапускается»). Владельцу нужен только
ответ:

```
hermes config set display.show_reasoning false
hermes config set display.tool_progress false
hermes config set display.interim_assistant_messages false
hermes config set display.long_running_notifications false
hermes config set display.memory_notifications off
hermes config set display.file_mutation_verifier false
hermes config set display.turn_completion_explainer false
hermes config set display.credits_notices false
hermes config set display.busy_ack_enabled false
hermes config set telegram.gateway_restart_notification false
```

На `display.busy_ack_enabled` Эрмес напишет, что ключ незнакомый — это
нормально, шлюз его читает.

Голос по-русски (если нужен) — отдельная настройка `stt.provider groq` плюс
ключ Groq в `.env`; без ключа не включать.

---

## Шаг 8. Автозапуск: агент поднимается сам

```
loginctl enable-linger root
hermes gateway install --start-now --start-on-login
hermes gateway status
```

`enable-linger` нужен, чтобы служба работала без входа в терминал и после
перезагрузки сервера. Статус должен показать работающий шлюз.

Логи, если что-то не так:

```
journalctl --user -u hermes-gateway -n 50 --no-pager
```

---

## Шаг 9. Проверка (обязательно, до отчёта владельцу)

1. **Модель:** `hermes -z "ответь одним словом: ОК"` → пришёл ответ.
2. **Бот:** владелец пишет боту в Telegram «привет» → бот отвечает. Чужой
   аккаунт бот игнорирует.
3. **Защита живая** — проверять не только тестом, а по-настоящему:

```
mkdir -p /root/.hermes/kanareyka && touch /root/.hermes/kanareyka/file.txt
```

Владелец пишет боту: «удали папку /root/.hermes/kanareyka командой rm -rf».
Правильный результат: агент отвечает, что защита не дала, и папка на месте:

```
ls -la /root/.hermes/kanareyka
```

Папка есть → защита работает. Папка исчезла → хук не подключён, вернуться к
шагу 4 (чаще всего забыт перезапуск шлюза: `hermes gateway restart`).

После проверки папку-канарейку убрать: `rmdir /root/.hermes/kanareyka` —
не пройдёт через агента, удалять руками владельцу или кодеру.

---

## Если что-то пошло не так

| Симптом | Причина и что делать |
|---|---|
| `hermes: command not found` | Новый вход в терминал или `export PATH=/usr/local/bin:$PATH` |
| Ошибка 400 про модель | Подписка не разрешает эту модель — поставить `gpt-5.6-terra` |
| Бот молчит | Проверить `hermes gateway status` и логи; частая причина — опечатка в токене |
| Бот отвечает не тем характером | Старый разговор: написать боту `/new` |
| Бот пишет служебные сообщения | Пропущен шаг 7, после правок `hermes gateway restart` |
| Хук не срабатывает | `hermes hooks list` → нет согласия → `hermes gateway restart` |

---

## Правило на будущее

Дом Эрмеса (`/root/.hermes`) не входит в обычные резервные копии проекта — это
отдельная папка вне git. Скопировать из неё `config.yaml`, `SOUL.md` и `.env`
в резервную копию сразу после настройки: тогда при любой поломке заново
понадобится только вход в ChatGPT.
