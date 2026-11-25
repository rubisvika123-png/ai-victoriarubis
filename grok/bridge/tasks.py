#!/usr/bin/env python3
"""Hand a task to another agent, and scheduled tasks, for the bridged (Grok/GLM) agents. Vika 24.11.

Live Claude agents do this with tmux send-keys + cron. Bridged agents have no tmux pane:
the bridge of the target agent picks the task up from its inbox file and runs it as a turn.

  tasks.py handoff --from marketer --to montazher --text "смонтируй ролик по сценарию ..."
  tasks.py schedule add --agent assistant --cron "0 10 * * 1" --tz Asia/Bangkok --text "пришли план недели"
  tasks.py schedule list --agent assistant
  tasks.py schedule cancel --agent assistant --id 2

Anti ping-pong (same rule as the live team): a task may be handed on ONLY from a turn that came
from Vika herself (or her schedule). A turn that is itself a handed task or a report cannot hand on.
"""
import argparse
OWNER_NOM, OWNER_GEN, OWNER_DAT = (__import__("os").environ.get("OWNER_FORMS", "Виктория|Виктории|Виктории").split("|") + ["", ""])[:3]
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

BASE = os.environ.get("GROK_BRIDGE_DIR") or os.path.dirname(os.path.abspath(__file__))  
NAMES = {"techspec": "техспец", "assistant": "ассистент", "marketer": "маркетолог", "montazher": "монтажёр", "kurator": "куратор Оля"}


def sdir(agent):
    return os.path.join(BASE, f"state-{agent}")


def append(path, item):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def take_inbox(agent):
    """Atomically take all queued tasks for this agent's bridge."""
    p = os.path.join(sdir(agent), "inbox.jsonl")
    tmp = p + ".taking"
    try:
        os.replace(p, tmp)
    except OSError:
        return []
    with open(tmp, encoding="utf-8") as f:
        items = [json.loads(l) for l in f if l.strip()]
    os.remove(tmp)
    return items


def origin(agent):
    try:
        with open(os.path.join(sdir(agent), "origin.txt"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def handoff(src, dst, text):
    if src == dst:
        return "нельзя передать задачу самому себе"
    if not os.path.isdir(sdir(dst)):
        return f"нет такого агента-получателя: {dst}"
    if origin(src) in ("handoff", "report"):
        return ("ОТКАЗ: эта задача сама пришла от другого агента — дальше по цепочке не передаю "
                f"(правило команды: передаём только по просьбе {OWNER_GEN}). Сделай сам или ответь {OWNER_DAT}.")
    append(os.path.join(sdir(dst), "inbox.jsonl"), {"kind": "handoff", "from": src, "text": text})
    return f"передал ({NAMES.get(dst, dst)}): он сделает и ответит {OWNER_DAT} из своего бота, тебе придёт отчёт"


# --- cron ----------------------------------------------------------------------
def _field(spec, value, lo, hi):
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = map(int, part.split("-"))
        else:
            a = b = int(part)
        if a <= value <= b and (value - a) % step == 0:
            return True
    return False


def cron_match(expr, t):
    """5-field cron, t = local datetime. Sunday is 0 or 7."""
    mi, h, dom, mon, dow = expr.split()
    wd = (t.weekday() + 1) % 7
    return (_field(mi, t.minute, 0, 59) and _field(h, t.hour, 0, 23) and _field(dom, t.day, 1, 31)
            and _field(mon, t.month, 1, 12) and (_field(dow, wd, 0, 7) or (wd == 0 and _field(dow, 7, 0, 7))))


def schedules_path(agent):
    return os.path.join(sdir(agent), "schedules.jsonl")


def load_schedules(agent):
    try:
        with open(schedules_path(agent), encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    except OSError:
        return []


def save_schedules(agent, items):
    p = schedules_path(agent)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    os.replace(p + ".tmp", p)


def due_schedules(agent, now_utc):
    """Schedules whose cron matches this minute (bridge calls once per minute)."""
    out = []
    for it in load_schedules(agent):
        local = now_utc.astimezone(ZoneInfo(it.get("tz", "Asia/Bangkok"))).replace(second=0, microsecond=0)
        if cron_match(it["cron"], local):
            out.append(it)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("handoff")
    h.add_argument("--from", dest="src", required=True)
    h.add_argument("--to", dest="dst", required=True)
    h.add_argument("--text", required=True)
    s = sub.add_parser("schedule")
    s.add_argument("action", choices=["add", "list", "cancel"])
    s.add_argument("--agent", required=True)
    s.add_argument("--cron")
    s.add_argument("--tz", default="Asia/Bangkok")
    s.add_argument("--text")
    s.add_argument("--id", type=int)
    a = ap.parse_args(argv)
    if a.cmd == "handoff":
        print(handoff(a.src, a.dst, a.text))
        return
    items = load_schedules(a.agent)
    if a.action == "add":
        if not a.cron or len(a.cron.split()) != 5 or not a.text:
            sys.exit("нужно --cron \"мин час день месяц день_недели\" и --text")
        cron_match(a.cron, dt.datetime(2026, 1, 1))  # validates syntax
        rid = max([it["id"] for it in items] + [0]) + 1
        items.append({"id": rid, "cron": a.cron, "tz": a.tz, "text": a.text})
        save_schedules(a.agent, items)
        print(f"OK расписание #{rid}: «{a.cron}» ({a.tz}) — {a.text}")
    elif a.action == "list":
        for it in items:
            print(f"#{it['id']} «{it['cron']}» ({it['tz']}) — {it['text']}")
        if not items:
            print("расписаний нет")
    else:
        save_schedules(a.agent, [it for it in items if it["id"] != a.id])
        print(f"отменено расписание #{a.id}")


if __name__ == "__main__":
    main()
