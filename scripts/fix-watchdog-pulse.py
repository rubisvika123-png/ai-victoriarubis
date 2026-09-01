#!/usr/bin/env python3
"""Учит уже установленного сторожа новой версии Claude.

Claude 2.1.252 перенёс мост в Telegram внутрь своего процесса: отдельного
`bun server.ts` и файла bot.pid больше нет. Сторож, поставленный раньше,
считает такого живого агента мёртвым и убивает его раз в несколько минут —
агент выглядит как «постоянно падает».

Скрипт заменяет в agent-watchdog.sh функцию pulse() на версию, которая
признаёт оба вида моста. Остальное в файле (список агентов, пути) не трогает,
рядом кладёт копию .bak.

    python3 fix-watchdog-pulse.py /root/ai-victoriarubis/scripts/agent-watchdog.sh
    python3 fix-watchdog-pulse.py --self-test
"""
import re
import shutil
import sys
import time

NEW_PULSE = '''pulse() {
  local name="$1" now last pane_pid
  tmux has-session -t "$name" 2>/dev/null || return 0
  now=$(date +%s); last=${LAST_RESTART[$name]:-0}
  (( now - last < GRACE )) && return 0
  pane_pid=$(tmux list-panes -t "$name" -F '#{pane_pid}' 2>/dev/null | head -1)
  [ -n "$pane_pid" ] || return 0
  # Живой мост бывает двух видов: старый отдельный процесс `bun server.ts` и
  # (Claude 2.1.252+) канал внутри самого процесса claude. Проверка только
  # первого принимала здорового агента за мёртвого и перезапускала его по кругу.
  if ! pgrep -P "$pane_pid" -f "server.ts" >/dev/null 2>&1 \\
     && ! ps -o args= -p "$pane_pid" 2>/dev/null | grep -q "dangerously-load-development-channels"; then
    log "NO PULSE for $name (Telegram bridge dead) — restarting"
    LAST_RESTART[$name]=$now
    tmux kill-session -t "$name" 2>/dev/null
  fi
}
'''

PULSE_RE = re.compile(r'^pulse\(\) \{.*?^\}\n', re.S | re.M)
MARK = 'dangerously-load-development-channels'


def patch(text):
    """Возвращает текст сторожа с новой pulse(). Бросает ValueError, если её нет."""
    if not PULSE_RE.search(text):
        raise ValueError('в файле нет функции pulse() — это не сторож или он сильно переписан')
    return PULSE_RE.sub(lambda _: NEW_PULSE, text, count=1)


def self_test():
    old_separate = 'A=1\npulse() {\n  pgrep -P "$p" -f "server.ts" || kill\n}\nB=2\n'
    old_botpid = 'A=1\npulse() {\n  bot_pid_file=x\n  if [ -f "$bot_pid_file" ]; then\n    return 0\n  fi\n}\nB=2\n'
    for name, src in (('старый мост', old_separate), ('вариант с bot.pid', old_botpid)):
        out = patch(src)
        assert MARK in out, name
        assert out.startswith('A=1\n') and out.endswith('B=2\n'), name
        assert out.count('pulse() {') == 1, name
    assert MARK in patch(patch(old_botpid)), 'повторный запуск должен быть безопасным'
    try:
        patch('ничего похожего\n')
    except ValueError:
        pass
    else:
        raise AssertionError('файл без pulse() должен отклоняться')
    print('self-test OK')


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    if sys.argv[1] == '--self-test':
        return self_test()
    path = sys.argv[1]
    text = open(path).read()
    if MARK in text and 'server.ts' in text:
        print('Сторож уже знает новую версию Claude — менять нечего.')
        return
    shutil.copy2(path, '%s.bak-%d' % (path, int(time.time())))
    open(path, 'w').write(patch(text))
    print('Готово: pulse() обновлён, старый файл рядом с пометкой .bak')
    print('Теперь перезапустите сторожа: systemctl restart agent-watchdog agent')


if __name__ == '__main__':
    main()
