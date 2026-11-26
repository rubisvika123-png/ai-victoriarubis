#!/usr/bin/env python3
"""Move a student's Claude Code agents onto Grok (Cursor CLI, by subscription) — Vika 24.11.

Everything we learned on 24.11 running our own team on Grok is built in (see ../README.md):
the agent keeps its bot, rules, memory, skills, MCP connections, schedules and reminders,
plus what Claude gave it implicitly and Grok does not: the memory index, the skills list and
the owner's rules (feedback memories) as a binding section, refreshed hourly by grok-sync.py.

Student-layout knowledge (memory folders, allowlist, schedules, MCP, history export, watchdog
rooms) is reused from claude-to-gpt.py — one source of truth for both escape hatches.

Run as root on the student's server, from this kit folder:

  claude-to-grok.py setup                         # once: Cursor CLI, bridge, hourly sync
  claude-to-grok.py login                         # once: how to get the Cursor login link to the owner
  claude-to-grok.py plan   AGENT_DIR NAME         # dry run: what will move
  claude-to-grok.py apply  AGENT_DIR NAME         # build the Grok agent (Claude agent keeps running)
  claude-to-grok.py switch NAME                   # stop the Claude agent, start the Grok one on the same bot
  claude-to-grok.py check  NAME                   # bot, bridge, Grok answers, rules complete
  claude-to-grok.py back   NAME                   # undo: stop Grok, start the Claude agent again
  claude-to-grok.py tz     ЗОНА                    owner's time zone for the WHOLE team (e.g. Asia/Bangkok, Europe/Moscow)
  claude-to-grok.py google                        # lesson «Chatplace и Google» on Grok: app password + iCal address
  claude-to-grok.py github                        # lesson «Подключаем GitHub» on Grok: token typed invisibly → Cursor MCP
  claude-to-grok.py new    NAME BOT_TOKEN OWNER_ID [ИМЯ_АГЕНТА]
                                                  # course «agent from scratch on Grok» (25.11): no Claude at all —
                                                  # course starter → /root/ai-NAME, then apply + switch + check

NAME: short latin name of the agent (techspec, marketer, assistant, montazher, …).
"""
from zoneinfo import ZoneInfo
import argparse
import datetime as dt
import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

KIT = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("GROK_HOME", "/root")
BRIDGE = os.environ.get("GROK_BRIDGE_DIR", "/root/grok-bridge")
TEAM = os.environ.get("GROK_TEAM_DIR", "/root/grok-team")
CONFIG = os.environ.get("GROK_TEAM_CONFIG", "/etc/grok-team.json")
UNIT_DIR = os.environ.get("C2G_UNIT_DIR", "/etc/systemd/system")
AGENT_BIN = os.path.join(HOME, ".local/bin/agent")
SVC = os.environ.get("GROK_UNIT", "grok-bridge")  # systemd template name (tests on our server use another one)
RUN_AS = os.environ.get("GROK_USER", "root")  # students: root, like their Claude agents (full access to their server)
COPY_EXCLUDES = ["secrets/", ".git/", "node_modules/", "dashi-plugin-claude-code/", "*.env", "*.key", "*.pem",
                 "state/telegram/", "venv/", ".venv/", "__pycache__/", "backups/"]
UNIT = """[Unit]
Description=Telegram <-> Grok (Cursor) bridge for %i
After=network-online.target

[Service]
User={user}
WorkingDirectory={bridge}
Environment=HOME={home}
Environment=PATH={home}/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=GROK_BRIDGE_DIR={bridge}
ExecStart=/usr/bin/python3 {bridge}/grok_bridge.py {bridge}/%i.env
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def c2g():
    """claude-to-gpt.py from this kit: memory, allowlist, MCP, schedules, history — the student layout lessons."""
    spec = importlib.util.spec_from_file_location("c2g", os.path.join(KIT, "claude-to-gpt.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sh(cmd, **kw):
    # Cursor keeps its login under $HOME: run everything with the agents' home, not the caller's
    kw.setdefault("env", dict(os.environ, HOME=HOME, PATH=f"{HOME}/.local/bin:" + os.environ.get("PATH", "")))
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, **kw)


def load_cfg():
    if os.path.isfile(CONFIG):
        return json.load(open(CONFIG, encoding="utf-8"))
    return {"user": RUN_AS, "team": TEAM, "owner": "ВЛАДЕЛЬЦА", "shared": [], "agents": {}}


def save_cfg(cfg):
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CONFIG)


# --- setup ----------------------------------------------------------------------
def setup():
    for tool in ("rsync", "curl", "python3"):
        if not shutil.which(tool):
            sys.exit(f"ОШИБКА: нет программы {tool}: apt install -y {tool}")
    try:
        import yaml  # noqa: F401  (claude-to-gpt.py needs it)
    except ImportError:
        print("ставлю python3-yaml…", "ок" if sh("apt-get install -y python3-yaml").returncode == 0 else "НЕ ВЫШЛО")
    if not os.path.isfile(AGENT_BIN):
        print("ставлю Cursor CLI…")
        r = sh("curl https://cursor.com/install -fsS | bash")
        if not os.path.isfile(AGENT_BIN):
            sys.exit("ОШИБКА: Cursor CLI не поставился:\n" + r.stdout[-800:] + r.stderr[-800:])
    os.makedirs(BRIDGE, exist_ok=True)
    os.makedirs(TEAM, exist_ok=True)
    for f in ("grok_bridge.py", "remind.py", "tasks.py", "test_grok_bridge.py", "google_mail.py", "room_forwarder.py"):
        shutil.copy2(os.path.join(KIT, "bridge", f), os.path.join(BRIDGE, f))
    for f in ("grok-sync.py", "claude-to-gpt.py", "claude-to-grok.py"):
        shutil.copy2(os.path.join(KIT, f), os.path.join(BRIDGE, f))
    shutil.copytree(os.path.join(KIT, "templates"), os.path.join(BRIDGE, "templates"), dirs_exist_ok=True)
    shutil.copy2(os.path.join(KIT, "templates/student/GROK-COMMON.md"), os.path.join(TEAM, "GROK-COMMON.md"))
    t = sh([sys.executable, os.path.join(BRIDGE, "test_grok_bridge.py")], cwd=BRIDGE)
    if not t.stderr.rstrip().endswith("OK"):
        sys.exit("ОШИБКА: проверки моста не прошли:\n" + t.stderr[-1500:])
    with open(os.path.join(UNIT_DIR, f"{SVC}@.service"), "w") as f:
        f.write(UNIT.format(bridge=BRIDGE, home=HOME, user=RUN_AS))
    sh("systemctl daemon-reload")
    cfg = load_cfg()
    # the team's shared memory (course: ~/ai-shared-brain or *brain*) — copied hourly, Grok's own notes kept
    for d in sorted(glob.glob(os.path.join(HOME, "*brain*"))):
        if os.path.isdir(d) and not any(s == d for s, _ in cfg["shared"]):
            cfg["shared"].append([d, os.path.join(TEAM, "shared", os.path.basename(d))])
    save_cfg(cfg)
    cron = sh("crontab -l").stdout
    line = f"17 * * * * GROK_TEAM_CONFIG={CONFIG} /usr/bin/python3 {BRIDGE}/grok-sync.py >> {TEAM}/grok-sync.log 2>&1  # grok-team"
    if CONFIG not in cron:
        subprocess.run(["crontab", "-"], input=cron.rstrip("\n") + "\n" + line + "\n", text=True)
    print(f"Готово: Cursor CLI {AGENT_BIN}, мост {BRIDGE}, папка команды {TEAM}, обновление раз в час.")
    print(f"Дальше: вход в Cursor — NO_OPEN_BROWSER=1 {AGENT_BIN} login")


CMD = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0].endswith(".py") else "claude-to-grok.py"
FRESH = False  # `new`: the agent never ran on Claude — no migration words for the student (26.11 прохождение курса)


def say(text):
    """Migration-only line: hidden on a fresh Grok install."""
    if not FRESH:
        print(text)


def logged_in():
    out = sh(f"{AGENT_BIN} status")
    text = (out.stdout + out.stderr).lower()
    return "logged in" in text and "not logged" not in text


def login():
    if logged_in():
        print("Cursor: вход уже выполнен.")
        return
    print("Запусти в отдельном окне и отдай ссылку владельцу — он откроет её в браузере, где вошёл в Cursor/Grok, и подтвердит:")
    print(f"  NO_OPEN_BROWSER=1 {AGENT_BIN} login")
    print(f"После входа: python3 {CMD} check-login")


# --- plan / apply ---------------------------------------------------------------
def gather(agent_dir, name):
    m = c2g()
    agent_dir = os.path.abspath(agent_dir)
    if not os.path.isfile(os.path.join(agent_dir, "CLAUDE.md")):
        sys.exit(f"ОШИБКА: в {agent_dir} нет CLAUDE.md — это не папка агента")
    env = m.read_env(os.path.join(agent_dir, "secrets/channel.env"))
    mem = m.find_memory_dirs(agent_dir, HOME)
    tz = "Asia/Bangkok" if m.owner_tz(agent_dir, mem) == 7 else "Europe/Moscow"
    tz = load_cfg().get("owner_tz") or tz  # team-wide zone set by `tz` (25.11: new agents assumed Moscow)
    return {
        "m": m, "agent_dir": agent_dir, "name": name, "copy": os.path.join(TEAM, name),
        "token": env.get("TELEGRAM_BOT_TOKEN", ""), "allowed": m.allowed_ids(agent_dir, env),
        "groq": env.get("GROQ_API_KEY", ""), "memory": mem, "tz": tz,
        "skills": m.skill_dirs(agent_dir, HOME), "mcp": m.find_mcp(agent_dir, HOME),
        "schedules": m.find_schedules(agent_dir), "manual": m.find_manual_schedules(agent_dir),
        "rooms": sorted(m.agent_rooms(agent_dir)),
    }


def show(p):
    if FRESH:
        print(f"Агент «{p['name']}»: бот {'есть' if p['token'] else 'НЕТ'}, отвечает: {', '.join(p['allowed']) or 'НИКОМУ'}, "
              f"часовой пояс: {p['tz']}")
        return [] if p["token"] and p["allowed"] else ["нет токена бота или ID владельца"]
    print(f"Агент: {p['agent_dir']} → Grok «{p['name']}» ({p['copy']})")
    print(f"  бот: {'есть' if p['token'] else 'НЕТ'}; отвечает: {', '.join(p['allowed']) or 'НИКОМУ'}; "
          f"голосовые: {'есть' if p['groq'] else 'нет ключа Groq'}; часовой пояс владельца: {p['tz']}")
    print(f"  память: {', '.join(p['memory']) or 'НЕТ'}")
    print(f"  навыки: {', '.join(p['skills']) or 'нет'}; подключения (MCP): {', '.join(p['mcp']) or 'нет'}")
    print(f"  комнаты Claude: {', '.join(p['rooms']) or 'не нашёл'}; расписаний: {len(p['schedules'])}")
    for s, t in p["schedules"]:
        print(f"    {s} — {t[:70]}")
    if p["manual"]:
        print(f"  ВНИМАНИЕ: {len(p['manual'])} расписаний собраны сложным скриптом — перенести руками (tasks.py schedule add):")
        for line in p["manual"]:
            print(f"    {line[:120]}")
    if not any(re.search(r"chrome|browser|playwright|cdp", json.dumps(v)) for v in p["mcp"].values()):
        print("  ВНИМАНИЕ: у агента нет браузера (MCP chrome/playwright) — поиск цен и сайтов на Grok будет слабее (Google с сервера даёт капчу)")
    problems = []
    if not p["token"]:
        problems.append("нет TELEGRAM_BOT_TOKEN в secrets/channel.env")
    if not p["allowed"]:
        problems.append("не нашёл, кому агент отвечает")
    if len(p["allowed"]) > 1:
        print(f"  ВНИМАНИЕ: агент отвечал нескольким чатам ({', '.join(p['allowed'])}); мост на Grok отвечает одному — "
              f"беру первый ({p['allowed'][0]}). Если владелец другой — поправить ALLOWED_CHAT в {BRIDGE}/{p['name']}.env")
    return problems


def fill(tpl, **kw):
    text = open(os.path.join(KIT, "templates/student", tpl), encoding="utf-8").read()
    for k, v in kw.items():
        text = text.replace("{" + k + "}", v)
    return text


def apply(p):
    m, name, copy = p["m"], p["name"], p["copy"]
    os.makedirs(copy, exist_ok=True)
    ex = sum((["--exclude", e] for e in COPY_EXCLUDES), [])
    subprocess.run(["rsync", "-a", *ex, p["agent_dir"] + "/", copy + "/"], check=True)
    # skills outside the agent folder (~/.claude/skills, course /root/ai-video/skills…) — the rules index only
    # sees what is inside the copy, so bring them in (19.11 ученица: montage skills lived outside)
    for sd in p["skills"]:
        if not os.path.realpath(sd).startswith(os.path.realpath(p["agent_dir"]) + os.sep):
            dst = os.path.join(copy, "skills-shared", re.sub(r"[^A-Za-z0-9]+", "-", sd).strip("-"))
            subprocess.run(["rsync", "-a", "--exclude", "everything-claude-code/", sd + "/", dst + "/"], check=False,
                           capture_output=True)  # an unreadable file in a shared skill must not scare the student
    # 19.11 lesson (ученица, GPT move): the moved agent must see its past — one clean dialog file
    dialog = os.path.join(copy, "state", "claude-dialog.md")
    n = m.export_claude_dialog(p["agent_dir"], HOME, dialog)
    hist = ""
    if n:
        hist = (f"\n## Моя работа до переезда (не начинаю с чистого листа)\nВся прежняя переписка с владельцем — {dialog} "
                f"(по датам, старые сверху). Владелец ссылается на прошлое («как мы делали», «помнишь») — сначала "
                f"`grep -n -i '<слово>' {dialog}` и читаю найденное, а не переспрашиваю.")
    cfg = load_cfg()
    cfg["agents"][name] = {"live": p["agent_dir"], "memory": p["memory"]}
    save_cfg(cfg)
    team_list = ", ".join(sorted(cfg["agents"]))
    with open(os.path.join(copy, "GROK.md"), "w", encoding="utf-8") as f:
        f.write(fill("GROK.md.tpl", DATE=dt.date.today().strftime("%d.%m.%Y"), COPY=copy, TZ=p["tz"], NAME=name, LIVE=p["agent_dir"], KIT=KIT,
                     BRIDGE=BRIDGE, HISTORY=hist))
    for other in cfg["agents"]:  # every agent's handoff rule must list the whole team, the new one too
        od = os.path.join(cfg["team"], other, "grok-overrides")
        os.makedirs(od, exist_ok=True)
        with open(os.path.join(od, "02-handoff.md"), "w", encoding="utf-8") as f:
            f.write(fill("02-handoff.md.tpl", TEAM_LIST=team_list, BRIDGE=BRIDGE, NAME=other, TZ=p["tz"]))
    # MCP connections → Cursor (~/.cursor/mcp.json); OAuth ones need `agent mcp login <name>` once
    if p["mcp"]:
        path = os.path.join(HOME, ".cursor/mcp.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = json.load(open(path)) if os.path.isfile(path) else {}
        data.setdefault("mcpServers", {}).update(p["mcp"])
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.chmod(path, 0o600)  # holds tokens (GitHub PAT headers)
        for srv in p["mcp"]:  # Cursor loads a server only after approval (25.11 test: «needs approval»)
            sh([AGENT_BIN, "mcp", "enable", srv])
    state = os.path.join(BRIDGE, f"state-{name}")
    os.makedirs(state, exist_ok=True)
    env = os.path.join(BRIDGE, f"{name}.env")
    if not p["groq"] and os.path.isfile(env):  # keep a voice key the agent/owner added later (25.11 test)
        p["groq"] = c2g().read_env(env).get("GROQ_API_KEY", "")
    with open(env, "w") as f:
        f.write(f"TELEGRAM_BOT_TOKEN={p['token']}\nALLOWED_CHAT={p['allowed'][0]}\nAGENT_DIR={copy}\nBACKEND=cursor\n"
                f"MODEL=grok-4.7-high\nGROQ_API_KEY={p['groq']}\nSTATE_DIR={state}\nOWNER_FORMS=\"владелец|владельца|владельцу\"\n")
    os.chmod(env, 0o600)
    # Claude wake-ups stay in the crontab: after `switch` the agent's tmux room becomes a forwarder into the
    # bridge queue (25.11) — copying them into bridge schedules too would deliver every task TWICE.
    # Only agents without a room (nothing would type into it) get bridge schedules.
    tasks = os.path.join(BRIDGE, "tasks.py")
    if not m.agent_rooms(p["agent_dir"]):
        have = sh([sys.executable, tasks, "schedule", "list", "--agent", name]).stdout
        for sched, text in p["schedules"]:
            if text[:60] not in have:
                sh([sys.executable, tasks, "schedule", "add", "--agent", name, "--cron", sched, "--tz", m.server_tz(), "--text", text])
    moved = 0
    now = dt.datetime.now(dt.timezone.utc)
    for path in glob.glob(os.path.join(p["agent_dir"], "**/reminders.tsv"), recursive=True):
        for line in open(path, encoding="utf-8", errors="ignore"):
            if "\t" not in line:
                continue
            when, text = line.rstrip("\n").split("\t", 1)
            try:
                t = dt.datetime.fromisoformat(when.strip())
            except ValueError:
                continue
            t = (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)
            if t > now:
                # in the owner's zone, so «remind.py list» shows his own clock (25.11 student test)
                loc = t.astimezone(ZoneInfo(p["tz"]))
                sh([sys.executable, os.path.join(BRIDGE, "remind.py"), "add", "--agent", name,
                    "--at", loc.strftime("%Y-%m-%d %H:%M"), "--tz", p["tz"], "--text", text])
                moved += 1
    r = sh([sys.executable, os.path.join(BRIDGE, "grok-sync.py"), "--config", CONFIG])
    if r.returncode:
        sys.exit("ОШИБКА сборки правил:\n" + r.stderr[-1500:])
    say(r.stdout.strip())
    say(f"Собран: {copy}. Прежняя переписка: {n or 0} эпизодов. Напоминаний перенесено: {moved}. "
          f"Расписаний: {len(p['schedules'])}.")
    if p["mcp"]:
        say(f"Подключения: {', '.join(p['mcp'])} → {HOME}/.cursor/mcp.json. Для тех, что с входом: {AGENT_BIN} mcp login <имя> "
              "(ссылку — владельцу, живёт ~5 минут, делать быстро).")
    say(f"Агент на Claude ещё работает. Переключить на Grok: {CMD} switch {name}")


# --- switch / back / check ------------------------------------------------------
def claude_units(agent_dir):
    out = []
    for uf in glob.glob(os.path.join(UNIT_DIR, "*.service")):
        try:
            text = open(uf, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        wd = re.search(r"^WorkingDirectory=(\S+)", text, re.M)
        if wd and os.path.realpath(wd.group(1)) == os.path.realpath(agent_dir) and SVC not in uf:
            out.append(os.path.basename(uf))
    return out


def watchdog_toggle(agent_dir, off=True):
    """Course watchdog revives the Claude agent by its room — comment its entry out (or back in)."""
    changed = []
    agent = os.path.realpath(agent_dir)
    for uf in sorted(glob.glob(os.path.join(UNIT_DIR, "agent-watchdog*.service"))):
        mm = re.search(r"^ExecStart=.*?(/\S*watchdog\S*\.sh)", open(uf, encoding="utf-8").read(), re.M)
        if not mm or not os.path.isfile(mm.group(1)):
            continue
        wd = mm.group(1)
        s = open(wd, encoding="utf-8").read()
        if off:
            new = re.sub(r'^(\s*)\[([^\]]+)\]="([^"]+)"',
                         lambda x: (f'{x.group(1)}# [{x.group(2)}]="{x.group(3)}"  # на Grok'
                                    if os.path.realpath(x.group(3)) == agent else x.group(0)),
                         s, flags=re.M)
        else:
            new = re.sub(r'^(\s*)# \[([^\]]+)\]="([^"]+)"  # на Grok', r'\1[\2]="\3"', s, flags=re.M)
        if new != s:
            shutil.copy2(wd, wd + ".bak-claude-to-grok")
            with open(wd, "w", encoding="utf-8") as f:
                f.write(new)
            if sh(["bash", "-n", wd]).returncode:
                shutil.copy2(wd + ".bak-claude-to-grok", wd)
                continue
            sh(["systemctl", "restart", os.path.basename(uf)])
            changed.append(wd)
    return changed


def switch(name):
    cfg = load_cfg()
    if name not in cfg["agents"]:
        sys.exit(f"ОШИБКА: агента {name} нет — сначала apply")
    if not logged_in():
        sys.exit(f"ОШИБКА: Cursor не вошёл — сначала NO_OPEN_BROWSER=1 {AGENT_BIN} login")
    agent_dir = cfg["agents"][name]["live"]
    wd = watchdog_toggle(agent_dir, off=True)
    units = claude_units(agent_dir)
    for u in units:
        sh(["systemctl", "disable", "--now", u])
    rooms = sorted(c2g().agent_rooms(agent_dir))
    for room in rooms:
        sh(["tmux", "kill-session", "-t", room])  # one bot token can't be polled by two engines
    r = sh(["systemctl", "enable", "--now", f"{SVC}@{name}"])
    # cron scripts that typed tasks into the Claude room keep working: the room becomes a forwarder (25.11)
    for room in rooms:
        sh(["tmux", "new-session", "-d", "-s", room,
            f"GROK_BRIDGE_DIR={BRIDGE} python3 {os.path.join(BRIDGE, 'room_forwarder.py')} {name}"])
    if rooms:
        print(f"Комнаты {', '.join(rooms)} теперь переходники: задачи, которые туда печатают по расписанию, идут агенту на Grok.")
    say(f"Сторож: {', '.join(wd) or 'не нашёл'}; остановил Claude: {', '.join(units) or 'служб не нашёл'}.")
    print("Мост на Grok запущен." if r.returncode == 0 else "ОШИБКА запуска моста:\n" + r.stderr)
    say(f"Проверка: {CMD} check {name}, потом владелец пишет боту «проверка».")


def back(name):
    cfg = load_cfg()
    agent_dir = cfg["agents"][name]["live"]
    sh(["systemctl", "disable", "--now", f"{SVC}@{name}"])
    for room in c2g().agent_rooms(agent_dir):  # drop the forwarder rooms so Claude can take its rooms back
        sh(["tmux", "kill-session", "-t", room])
    wd = watchdog_toggle(agent_dir, off=False)
    units = claude_units(agent_dir)
    for u in units:
        sh(["systemctl", "enable", "--now", u])
    print(f"Grok выключен, Claude включён обратно: {', '.join(units) or 'служб не нашёл — запусти как было'}; "
          f"сторож: {', '.join(wd) or '-'}.")


def check(name):
    cfg = load_cfg()
    copy = os.path.join(cfg["team"], name)
    env = c2g().read_env(os.path.join(BRIDGE, f"{name}.env"))
    ok = True

    def line(good, text):
        nonlocal ok
        ok = ok and bool(good)
        print(("ок   " if good else "НЕТ  ") + text)
    try:
        me = json.load(urllib.request.urlopen(
            f"https://api.telegram.org/bot{env.get('TELEGRAM_BOT_TOKEN', '')}/getMe", timeout=20))
        line(me.get("ok"), f"бот @{me['result']['username']} отвечает Телеграму")
    except Exception as e:
        line(False, f"бот: {type(e).__name__}")  # never print the token-bearing URL
    line(sh(["systemctl", "is-active", f"{SVC}@{name}"]).stdout.strip() == "active", "мост работает")
    line(logged_in(), "Cursor: вход выполнен")
    path = os.path.join(copy, "CLAUDE.md")
    rules = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
    for must, what in (("## Как я работаю", "общий порядок работы"), ("## Я на Grok", "правила моста"),
                       ("tasks.py handoff", "передача задач"), ("tasks.py schedule add", "расписания"),
                       ("remind.py add", "напоминания"), ("## Оглавление моей памяти", "оглавление памяти")):
        line(must in rules, f"в правилах: {what}")
    r = sh(f"cd {copy} && timeout 300 {AGENT_BIN} -p --trust --force --model grok-4.7-high --output-format text "
           "'Служебная проверка: ответь одним словом — готов'")
    line("готов" in r.stdout.lower(), f"Grok отвечает в папке агента ({r.stdout.strip()[:60] or r.stderr.strip()[:120]})")
    say("ПЕРЕНОС ГОТОВ" if ok else "ПЕРЕНОС НЕ ГОТОВ — исправить строки «НЕТ»")
    return ok


STARTER = os.environ.get("GROK_STARTER", "https://github.com/rubisvika123-png/ai-victoriarubis.git")
ROLE_TEXT = {  # what the course lessons make of each starter copy
    "techspec": "Ты технический специалист владельца: держишь сервер, агентов и подключения в порядке, чинишь сбои, ставишь новое.",
    "marketer": "Ты маркетолог владельца: пишешь посты и сценарии рилс в его стиле, ведёшь соцсети и контент-план.",
    "assistant": "Ты личный ассистент владельца: дела, напоминания, встречи, почта, поиск.",
}


def new_agent(name, token, owner, persona=None, base="/root"):
    """Course route «агент сразу на Grok»: the agent never ran on Claude. Lay the course starter out exactly as
    the Claude lessons would (so every later lesson's paths stay true), then reuse the move: apply + switch."""
    agent_dir = os.path.join(base, f"ai-{name}")
    if not os.path.isdir(agent_dir):
        r = sh(["git", "clone", "-q", STARTER, agent_dir])
        if r.returncode:
            sys.exit("ОШИБКА: не скачал стартовую папку агента:\n" + r.stderr[-500:])
    os.makedirs(os.path.join(agent_dir, "secrets"), exist_ok=True)
    env = os.path.join(agent_dir, "secrets", "channel.env")
    with open(env, "w") as f:
        f.write(f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_ALLOWED_USER_IDS={owner}\nAGENT_ID={name}\n")
    os.chmod(env, 0o600)
    cfgp = os.path.join(agent_dir, "dashi-plugin-claude-code/plugin/state/telegram/config.json")
    os.makedirs(os.path.dirname(cfgp), exist_ok=True)
    with open(cfgp, "w") as f:
        json.dump({"allowed_chat_ids": [int(owner)]}, f)
    claude_md = os.path.join(agent_dir, "CLAUDE.md")
    text = open(claude_md, encoding="utf-8").read()
    if "{{ИМЯ_АГЕНТА}}" in text:  # lesson «личность и память» renames later; no raw placeholders for the model now
        text = (text.replace("{{ИМЯ_АГЕНТА}}", persona or {"techspec": "Техспец", "marketer": "Маркетолог",
                                                         "assistant": "Ассистент"}.get(name, name))
                    .replace("{{КАК_ОБРАЩАТЬСЯ}}", "по имени, когда владелец его назовёт").replace("{{...}}", ""))
        if name in ROLE_TEXT and "## Моя роль" not in text:
            text += f"\n\n## Моя роль\n{ROLE_TEXT[name]}\n"
        with open(claude_md, "w", encoding="utf-8") as f:
            f.write(text)
    return agent_dir


def connect_github(token):
    """Lesson «Подключаем GitHub» on Grok (25.11): GitHub's own MCP server, token in ~/.cursor/mcp.json (600),
    approved for Cursor — every agent of the owner gets it (one Cursor login for the team)."""
    if not re.fullmatch(r"(github_pat_|ghp_)[A-Za-z0-9_]{20,}", token):
        sys.exit("ОШИБКА: это не похоже на токен GitHub (он начинается с github_pat_). Скопируйте его заново.")
    path = os.path.join(HOME, ".cursor/mcp.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = json.load(open(path)) if os.path.isfile(path) else {}
    data.setdefault("mcpServers", {})["github"] = {"url": "https://api.githubcopilot.com/mcp/",
                                                   "headers": {"Authorization": f"Bearer {token}"}}
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.chmod(path, 0o600)
    sh([AGENT_BIN, "mcp", "enable", "github"])
    ok = "ready" in sh([AGENT_BIN, "mcp", "list"]).stdout
    print("GitHub подключён ✅ Напишите агенту /new и попросите: «назови три моих репозитория на GitHub»." if ok
          else "GitHub записан, но не отвечает — проверьте, что токен скопирован целиком и не просрочен.")
    return ok


def connect_google(address, app_password, ics_url, tz):
    """Lesson «Chatplace и Google» on Grok (25.11, Vika chose variant 1): no claude.ai connectors here, so mail goes
    over IMAP/SMTP with a Google app password and the calendar is read from its secret iCal address."""
    path = os.path.join(HOME, ".grok-google.env")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(f"GMAIL_ADDRESS={address}\nGMAIL_APP_PASSWORD={app_password.replace(' ', '')}\n"
                f"GCAL_ICS_URL={ics_url}\nOWNER_TZ={tz}\n")
    tool = os.path.join(BRIDGE, "google_mail.py")
    mail = sh([sys.executable, tool, "list", "1"])
    cal = sh([sys.executable, tool, "calendar", "1"]) if ics_url else None
    print("Почта:", "подключена ✅" if mail.returncode == 0 else "НЕ подключилась — проверьте адрес и пароль приложения ("
          + (mail.stderr.strip().splitlines() or ["?"])[-1][:120] + ")")
    if cal is not None:
        print("Календарь:", "подключён ✅" if cal.returncode == 0 else "НЕ подключился — проверьте секретный адрес iCal")
    return mail.returncode == 0


def share_mcp_logins(home=None):
    """26.11 test: Cursor keeps an MCP login (Chatplace) per agent FOLDER (~/.cursor/projects/<folder>/mcp-auth.json).
    The owner logs in once (usually via the techspec) — copy that login to every other team agent, so the
    marketer who builds the funnel (lesson «Автоворонка») sees Chatplace too. Never overwrites a login an agent has."""
    home = home or HOME
    cfg = load_cfg()
    proj = os.path.join(home, ".cursor", "projects")
    key = lambda d: os.path.abspath(d).strip("/").replace("/", "-")  # noqa: E731  Cursor's folder naming
    dirs = [os.path.join(cfg["team"], n) for n in cfg["agents"]]
    have = {}
    for d in dirs:
        try:
            for srv, val in json.load(open(os.path.join(proj, key(d), "mcp-auth.json"))).items():
                if isinstance(val, dict) and val.get("tokens"):
                    have.setdefault(srv, val)
        except (OSError, ValueError):
            pass
    shared = []
    for d in dirs:
        path = os.path.join(proj, key(d), "mcp-auth.json")
        try:
            data = json.load(open(path))
        except (OSError, ValueError):
            data = {}
        changed = False
        for srv, val in have.items():
            if not (isinstance(data.get(srv), dict) and data[srv].get("tokens")):
                data[srv] = val
                changed = True
                shared.append(f"{srv} → {os.path.basename(d)}")
        if changed:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(data, f)
            os.chmod(path, 0o600)
    print("Вход в подключения роздан: " + (", ".join(shared) if shared else "все агенты уже подключены"))
    return shared


def set_group(name, chat_id):
    """Lesson «ИИ-куратор» on Grok (26.11): the curator's bridge also answers in the course group.
    Writes GROUP_CHATS and the bot's @username (for owner @mentions) into the bridge env and restarts it."""
    env = os.path.join(BRIDGE, f"{name}.env")
    if not os.path.isfile(env):
        sys.exit(f"ОШИБКА: нет агента «{name}» ({env}). Сначала: python3 {CMD} new {name} ТОКЕН_БОТА ТВОЙ_ID")
    if not re.fullmatch(r"-100\d{5,}", chat_id):
        sys.exit("ОШИБКА: id группы начинается с -100 (например -1001234567890). Его покажет бот @getidsbot в группе.")
    allines = open(env, encoding="utf-8").readlines()
    vals = dict(l.rstrip("\n").split("=", 1) for l in allines if "=" in l)
    lines = [l for l in allines if not l.startswith(("GROUP_CHATS=", "BOT_USERNAME="))]
    token = vals.get("TELEGRAM_BOT_TOKEN", "").strip('"')
    try:
        me = json.load(urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=20))["result"]
    except Exception as e:
        sys.exit(f"ОШИБКА: не смог спросить у Telegram имя бота ({e}). Проверь токен.")
    if me.get("can_read_all_group_messages") is False:
        print("ВНИМАНИЕ: у бота включён режим приватности — в группе он видит только команды и упоминания. "
              "В @BotFather: /mybots → бот → Bot Settings → Group Privacy → Turn off, потом удали бота из группы и добавь снова.")
    groups = sorted({g for g in vals.get("GROUP_CHATS", "").split(",") if g} | {chat_id})
    with open(env, "w", encoding="utf-8") as f:
        f.writelines(lines + [f"GROUP_CHATS={','.join(groups)}\n", f"BOT_USERNAME={me['username']}\n"])
    os.chmod(env, 0o600)
    sh(["systemctl", "restart", f"{SVC}@{name}"])
    print(f"Куратор «{name}» (@{me['username']}) теперь отвечает и в группе {chat_id}: ученикам — сам, владельцу — по @упоминанию.")


def set_owner_tz(zone):
    """25.11 test: an agent created later assumed Moscow while the owner lived in Thailand. One team-wide zone:
    stored in the team config, used by every next `apply`/`new`, and the existing agents are rebuilt."""
    try:
        ZoneInfo(zone)
    except Exception:
        sys.exit(f"ОШИБКА: не знаю часовой пояс «{zone}». Примеры: Europe/Moscow, Asia/Bangkok, Asia/Almaty")
    cfg = load_cfg()
    cfg["owner_tz"] = zone
    save_cfg(cfg)
    for name, a in cfg["agents"].items():
        p = gather(a["live"], name)
        apply(p)
    gpath = os.path.join(HOME, ".grok-google.env")
    if os.path.isfile(gpath):
        lines = [l for l in open(gpath, encoding="utf-8") if not l.startswith("OWNER_TZ=")]
        with open(gpath, "w", encoding="utf-8") as f:
            f.writelines(lines + [f"OWNER_TZ={zone}\n"])
    print(f"Часовой пояс владельца для всей команды: {zone}. Агенты пересобраны; новые напоминания и время — по нему.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["setup", "login", "check-login", "plan", "apply", "switch", "back", "check", "new", "github", "google", "tz", "group", "mcp-share"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    if a.cmd == "setup":
        return setup()
    if a.cmd == "login":
        return login()
    if a.cmd == "check-login":
        return print("Cursor: вход выполнен" if logged_in() else "Cursor: НЕ вошёл")
    if a.cmd == "mcp-share":
        return share_mcp_logins()
    if a.cmd == "group":
        if len(a.args) != 2:
            sys.exit("нужно: group ИМЯ_АГЕНТА ID_ГРУППЫ, например group kurator -1001234567890")
        return set_group(*a.args)
    if a.cmd == "tz":
        if len(a.args) != 1:
            sys.exit("нужно: tz ЗОНА, например tz Asia/Bangkok")
        return set_owner_tz(a.args[0])
    if a.cmd == "google":
        import getpass
        addr = input("Адрес Gmail: ").strip()
        pw = getpass.getpass("Пароль приложения Google (16 букв; на экране не появится): ").strip()
        ics = input("Секретный адрес календаря в формате iCal (Enter — пропустить): ").strip()
        tz = input("Ваш часовой пояс (Enter — Москва): ").strip() or "Europe/Moscow"
        return connect_google(addr, pw, ics, tz)
    if a.cmd == "github":
        import getpass
        return connect_github(getpass.getpass("Вставьте токен GitHub (на экране он не появится) и нажмите Enter: ").strip())
    if a.cmd == "new":
        if len(a.args) not in (3, 4) or not re.fullmatch(r"[a-z0-9]+", a.args[0]) or not a.args[2].isdigit() \
                or not re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,}", a.args[1]):
            sys.exit("нужно: new NAME BOT_TOKEN OWNER_ID [ИМЯ_АГЕНТА] (имя латиницей, токен от BotFather, ID — цифры)")
        if not logged_in():
            sys.exit(f"ОШИБКА: Cursor не вошёл — сначала python3 {CMD} setup и NO_OPEN_BROWSER=1 {AGENT_BIN} login")
        name = a.args[0]
        global FRESH
        FRESH = True
        agent_dir = new_agent(name, a.args[1], a.args[2], a.args[3] if len(a.args) == 4 else None)
        p = gather(agent_dir, name)
        problems = show(p)
        if problems:
            sys.exit("ОШИБКА: " + "; ".join(problems))
        apply(p)
        switch(name)
        ok = check(name)
        print(f"АГЕНТ {name} ГОТОВ — напишите боту «проверка»." if ok else f"АГЕНТ {name} НЕ ГОТОВ — см. строки «НЕТ» выше.")
        return ok
    if a.cmd in ("plan", "apply"):
        if len(a.args) != 2 or not re.fullmatch(r"[a-z0-9]+", a.args[1]):
            sys.exit("нужно: AGENT_DIR NAME (имя — маленькие латинские буквы и цифры)")
        p = gather(*a.args)
        problems = show(p)
        if problems:
            sys.exit("ОШИБКА: " + "; ".join(problems))
        if a.cmd == "apply":
            apply(p)
        else:
            print(f"Пробный прогон: ничего не записано. Запись: {CMD} apply …")
        return
    if len(a.args) != 1:
        sys.exit("нужно: NAME")
    return {"switch": switch, "back": back, "check": check}[a.cmd](a.args[0])


if __name__ == "__main__":
    main()
