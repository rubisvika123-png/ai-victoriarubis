#!/usr/bin/env python3
"""Keep Claude Code's conversation history for at least 90 days (Vika 15.11.2026).

By default Claude Code deletes session logs older than 30 days. If this agent ever
has to move to GPT, the converter builds its task recipes from that history — so
we keep three months. Only raises the value, never touches other settings, and
leaves a settings file it cannot parse alone.
"""
import json
import os

DAYS = 90
path = os.path.expanduser("~/.claude/settings.json")
data = {}
if os.path.exists(path):
    try:
        data = json.load(open(path, encoding="utf-8"))
    except ValueError:
        raise SystemExit(0)  # not ours to repair
if int(data.get("cleanupPeriodDays") or 0) < DAYS:
    data["cleanupPeriodDays"] = DAYS
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
