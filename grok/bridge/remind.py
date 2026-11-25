#!/usr/bin/env python3
"""Reminders for the Grok agents (Vika 24.11: «будильник не поставился»).

The agent schedules; its bridge delivers at the due time (the bridge is the only
thing that can write to Telegram first).

  remind.py add --agent assistant --at "2026-11-25 10:00" --tz Asia/Bangkok --text "оплатить сервер"
  remind.py list --agent assistant
  remind.py cancel --agent assistant --id 3

File: ~/bridge/state-<agent>/reminders.jsonl, one JSON per line:
  {"id": 1, "due_utc": "2026-11-25T03:00:00+00:00", "text": "...", "done": false}
"""
import re
import argparse
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

BASE = os.environ.get("GROK_BRIDGE_DIR") or os.path.dirname(os.path.abspath(__file__))  


def path(agent):
    return os.path.join(BASE, f"state-{agent}", "reminders.jsonl")


def load(agent):
    try:
        return [json.loads(l) for l in open(path(agent), encoding="utf-8") if l.strip()]
    except OSError:
        return []


def save(agent, items):
    p = path(agent)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    os.replace(tmp, p)


def due(agent, now=None):
    """Reminders that are due and not yet sent."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return [it for it in load(agent) if not it["done"] and dt.datetime.fromisoformat(it["due_utc"]) <= now]


def mark_done(agent, rid):
    items = load(agent)
    for it in items:
        if it["id"] == rid:
            it["done"] = True
    save(agent, items)


def same_thing(a, b):
    """Same reminder reworded («оплатить сервер» / «Оплатить сервер VDSINA»): most words of the shorter one match."""
    wa, wb = (set(re.findall(r"\w{3,}", x.lower())) for x in (a, b))
    small = min(wa, wb, key=len)
    return bool(small) and len(wa & wb) / len(small) >= 0.6


ZONE_RU = {"Asia/Bangkok": "Таиланд", "Europe/Moscow": "МСК", "UTC": "UTC"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["add", "list", "cancel"])
    ap.add_argument("--agent", required=True)
    ap.add_argument("--at")
    ap.add_argument("--tz", default="Asia/Bangkok")
    ap.add_argument("--text")
    ap.add_argument("--id", type=int)
    a = ap.parse_args(argv)
    items = load(a.agent)
    if a.cmd == "add":
        local = dt.datetime.strptime(a.at, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(a.tz))
        utc = local.astimezone(dt.timezone.utc)
        if utc <= dt.datetime.now(dt.timezone.utc):
            sys.exit(f"время уже прошло: {local:%d.%m %H:%M} {a.tz}")
        # Same moment already scheduled (±10 min) -> update it, don't send Vika two (25.11).
        # ...but only the SAME reminder: two different things at 10:00 both stay (25.11 student test: «оплатить
        # сервер» from the techspec silently replaced «позвонить в школу» at the same time).
        twin = next((it for it in items if not it["done"] and same_thing(it["text"], a.text) and
                     abs((dt.datetime.fromisoformat(it["due_utc"]) - utc).total_seconds()) <= 600), None)
        if twin:
            twin["text"], twin["tz"], rid = a.text, a.tz, twin["id"]
        else:
            rid = max([it["id"] for it in items] + [0]) + 1
            items.append({"id": rid, "due_utc": utc.isoformat(), "text": a.text, "done": False, "tz": a.tz})
        save(a.agent, items)
        msk = utc.astimezone(ZoneInfo("Europe/Moscow"))
        print(f"OK #{rid}: {local:%d.%m %H:%M} ({a.tz}) = {msk:%d.%m %H:%M} МСК — {a.text}")
    elif a.cmd == "list":
        live = os.path.expanduser(f"~/team/{a.agent}/state/live-reminders.tsv")
        if os.path.exists(live):  # what the live Claude agent already has scheduled (read-only mirror)
            now = dt.datetime.now(dt.timezone.utc)
            for line in open(live, encoding="utf-8"):
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 2:
                    try:
                        when = dt.datetime.fromisoformat(parts[0])
                    except ValueError:
                        continue
                    if when > now:
                        loc = when.astimezone(ZoneInfo("Asia/Bangkok"))
                        print(f"[живая Аня] {loc:%d.%m.%Y %H:%M} (Таиланд) — {parts[1]}")
        for it in items:
            if not it["done"]:
                # shown in the owner's own zone (25.11 student test: a Moscow student saw «(Таиланд)»)
                tz = it.get("tz") or "Asia/Bangkok"
                loc = dt.datetime.fromisoformat(it["due_utc"]).astimezone(ZoneInfo(tz))
                print(f"#{it['id']} {loc:%d.%m %H:%M} ({ZONE_RU.get(tz, tz)}) — {it['text']}")
    elif a.cmd == "cancel":
        save(a.agent, [it for it in items if it["id"] != a.id])
        print(f"отменено #{a.id}")


if __name__ == "__main__":
    main()
