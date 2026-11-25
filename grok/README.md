# Агенты на Grok (через подписку Cursor)

Набор для курса: поставить команду агентов сразу на Grok или перенести уже готовых агентов с Claude на Grok.
Все шаги — в уроках курса (режим «Я на Grok»). Коротко, на сервере под root:

```bash
git clone https://github.com/rubisvika123-png/ai-victoriarubis.git /root/grok-src && cp -r /root/grok-src/grok /root/grok-kit
cd /root/grok-kit
python3 claude-to-grok.py setup                                   # один раз: Cursor, мост, обновление правил
NO_OPEN_BROWSER=1 /root/.local/bin/agent login                   # вход в Cursor по ссылке
python3 claude-to-grok.py new techspec ТОКЕН_БОТА ТВОЙ_TELEGRAM_ID  # первый агент
python3 claude-to-grok.py tz Asia/Bangkok                         # свой часовой пояс, если не Москва
python3 claude-to-grok.py github                                  # подключить GitHub (токен вводится невидимо)
python3 claude-to-grok.py google                                  # почта Gmail и календарь (пароль приложения)
```

Перенос агентов, которые уже работали на Claude: `claude-to-grok.py plan / apply / switch / check / back`.
