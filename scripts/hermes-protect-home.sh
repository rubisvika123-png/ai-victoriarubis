#!/usr/bin/env python3
"""pre_tool_call хук: не даёт агенту Эрмеса снести собственный дом.

Агент Эрмеса умеет выполнять команды в терминале. Одной командой вида
`rm -rf ~/.hermes` он стирает сам себя: настройки, характер (SOUL.md),
доступ к боту (.env), вход в аккаунт (auth.json) и всю память (state.db).
Так уже случалось на боевом сервере — восстановить удалось не всё.

Хук получает на stdin JSON вида
    {"tool_name": "terminal", "tool_input": {"command": "..."}, "cwd": "..."}
и, если команда рекурсивно удаляет что-то в доме Эрмеса или трогает ключевой
файл профиля, печатает {"decision": "block", "reason": ...} — Эрмес отменяет
вызов и показывает агенту причину.

Разрешено то, что безопасно и нужно в работе: чистка кэшей, логов и песочниц,
удаление отдельных обычных файлов, любые операции вне дома Эрмеса.

Проверка: bash scripts/test_hermes_protect_home.sh
"""
import json
import os
import re
import sys

# Дом Эрмеса у каждого свой: у профиля — своя папка, у Mac — свой /Users/...
# Берём его из окружения гейтвея, а на всякий случай ловим и запись через ~.
HOME = os.path.abspath(os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"))
HOME_PATTERNS = (re.escape(HOME), r"~/\.hermes", r"\$HERMES_HOME", r"\$\{HERMES_HOME\}")
HOME_ALT = "|".join(HOME_PATTERNS)
HOME_RE = re.compile(HOME_ALT)

# Чистка этих подпапок — обычная работа, не катастрофа.
SAFE_SUBDIRS = re.compile(
    r"(" + HOME_ALT + r")"
    r"(/profiles/[^/\s]+)?/(cache|logs|sandboxes|audio_cache|image_cache|pending_messages)\b"
)

# Рекурсивное удаление в любом виде, включая питоний rmtree из execute_code.
RECURSIVE_DELETE = re.compile(
    r"\brm\b[^|;&\n]*?\s-[a-zA-Z]*[rR]"      # rm -r, rm -rf, rm -fr, rm -Rf
    r"|shutil\.rmtree|\brmtree\s*\("            # python
    r"|os\.removedirs"
    r"|\bfind\b[^|;&\n]*-delete"
    r"|\bfind\b[^|;&\n]*-exec\s+rm"
)

# Файлы, без которых профиль не поднимется: доступ к боту, характер, настройки,
# вход в аккаунт, память. Их нельзя ни удалить, ни обнулить — даже по одному.
KEY_FILE = re.compile(
    r"(" + HOME_ALT + r")[^\s'\"]*/"
    r"(\.env|SOUL\.md|config\.yaml|auth\.json|state\.db)\b"
)
KEY_FILE_HARM = re.compile(
    r"\brm\b|\bmv\b|\btruncate\b|\bshred\b|>\s*[^>|]*(\.env|SOUL\.md|config\.yaml|auth\.json|state\.db)"
    r"|os\.remove|os\.unlink|\bunlink\s*\("
)

REASON = (
    "Заблокировано защитой: это удаляет или обнуляет файлы дома Эрмеса "
    "({what}). Так агент стирает сам себя — вместе с памятью, характером и "
    "доступом к боту, и восстановить это можно только из резервной копии. "
    "Если удаление действительно нужно — попроси владельца сделать это "
    "руками, обходить запрет нельзя."
)


def verdict(command: str, cwd: str) -> str:
    """Вернуть текст причины блокировки или '' если команда безопасна."""
    if not command.strip():
        return ""

    touches_home = bool(HOME_RE.search(command))
    # Относительный путь в доме Эрмеса опаснее абсолютного: `rm -rf .` из
    # рабочей папки профиля выглядит безобидно и сносит всё. Именно в таком
    # положении (рабочая папка внутри дома) работает гейтвей.
    cwd = os.path.abspath(cwd) if cwd else ""
    in_home = bool(cwd) and (cwd == HOME or cwd.startswith(HOME + "/") or "/.hermes" in cwd)

    if RECURSIVE_DELETE.search(command):
        if SAFE_SUBDIRS.search(command):
            return ""
        if touches_home:
            return REASON.format(what="рекурсивное удаление")
        if in_home:
            return REASON.format(
                what="рекурсивное удаление по относительному пути, "
                     "рабочая папка — внутри дома Эрмеса"
            )

    if KEY_FILE.search(command) and KEY_FILE_HARM.search(command):
        return REASON.format(what="ключевой файл профиля")

    return ""


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return  # непонятный вход — молчим, хук не имеет права ломать агента

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    # terminal кладёт команду в command, execute_code — код в code/script.
    command = " ".join(
        str(tool_input.get(k, "")) for k in ("command", "code", "script", "input")
    )
    reason = verdict(command, str(payload.get("cwd") or ""))
    if reason:
        json.dump({"decision": "block", "reason": reason}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
