#!/usr/bin/env python3
"""Keep the Grok copies of agents as smart as the live Claude agents (Vika 24.11).

Claude Code gives a live agent things automatically on every turn: its CLAUDE.md,
the auto-memory index (MEMORY.md), the skills list. Cursor/Grok gets none of that
unless it is written into the copy's CLAUDE.md. This script rebuilds each copy's
CLAUDE.md from the LIVE agent and refreshes its knowledge folders, hourly (cron):

  CLAUDE.md = live CLAUDE.md (+USER.md, rules.md; paths rewritten; Claude-only lines dropped;
              copy-only section overrides from <copy>/grok-overrides/*.md)
            + GROK-COMMON.md (shared work discipline)
            + <copy>/GROK.md (copy-specific rules: bridge, reminders, handoff, schedules…)
            + skills index + the owner's rules (feedback memories, BINDING) + memory index

Works for our server (built-in config) and for a student's server: the migrator
(claude-to-grok.py) writes /etc/grok-team.json with the student's agents.

Usage: grok-sync.py [--agent NAME] [--dry-run] [--config PATH]
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

CONFIG_PATH = os.environ.get("GROK_TEAM_CONFIG", "/etc/grok-team.json")
MEM = "/root/.claude/projects/{}/memory"
DEFAULT = {  # no built-in team: a student server always has /etc/grok-team.json (written by setup)
    "user": "root", "team": "/root/grok-team", "owner": "ВЛАДЕЛЬЦА", "shared": [], "agents": {},
}
# kept for the tests and old callers
TEAM = DEFAULT["team"]
AGENTS = {k: (v["live"], os.path.basename(os.path.dirname(v["memory"][0]))) for k, v in DEFAULT["agents"].items()}

# knowledge folders refreshed from the live agent (only if present on both sides)
FOLDERS = ["core", "course-errors", "skills", ".claude/skills", ".agents/skills", "lessons", "docs", "knowledge", "kb", "materials"]
EXCLUDES = ["secrets/", ".git/", "node_modules/", "state/", "*.env", "*.key", "*.pem", "*.mp4", "*.MOV", "*.mov", "*.zip"]
RULE_FILES = ["CLAUDE.md", "USER.md", "core/USER.md", "rules.md"]  # what the live agent reads as its rules
# Lines that only make sense inside Claude Code (reply tool, permission prompts) — the bridge answers by itself.
# Same list as claude-to-gpt.py CLAUDE_ONLY (students' GPT move, 30.10/05.11).
CLAUDE_ONLY = re.compile(r"dashi-channel|вызовом reply|Do you want to allow|Ход не считается завершённым|"
                         r"Мой обычный текст|Модель:\s*Claude|вызов\w* `?reply`?")


def load_config(path=None):
    path = path or CONFIG_PATH
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT


def rewrite_paths(text, live, copy, shared=None):
    text = text.replace(live + "/", copy + "/").replace(live + "`", copy + "`")
    for src, dst in (shared if shared is not None else DEFAULT["shared"]):
        text = text.replace(src, dst)
    return text


REPLY_SECTION = re.compile(r"^#{1,3}\s.*(Как я отвечаю|правило ответа|Как отвечать)", re.I)


def drop_reply_sections(text):
    """Course starter (25.11 test): «## Как я отвечаю» is wholly about the Claude reply tool — line-dropping left
    half-sentences («…инструмент reply из» / «ответа.»). Drop the whole section when it is about reply/dashi."""
    parts = re.split(r"(?m)^(?=#{1,3}\s)", text)
    keep = [p for p in parts if not (REPLY_SECTION.match(p) and CLAUDE_ONLY.search(p))]
    return "".join(keep)


def strip_claude_only(text):
    text = drop_reply_sections(text)
    out = []
    for line in text.splitlines():
        if CLAUDE_ONLY.search(line):
            continue
        out.append(re.sub(r"через (инструмент )?`?reply`?", "в Telegram", line))
    return "\n".join(out)


def apply_overrides(text, override_files):
    """Replace a live '## Heading…' section with the copy's version that starts with the same heading."""
    for path in override_files:
        new = open(path, encoding="utf-8").read().rstrip() + "\n"
        head = new.splitlines()[0]
        base = re.split(r"\s+[—-]\s+", head, maxsplit=1)[0]  # '## Напоминания (будильник)'
        m = re.search(r"^" + re.escape(base) + r".*$", text, re.M)
        if not m:  # course starter has no such section (25.11 student test: hand-off rule was lost) — add it
            text = text.rstrip() + "\n\n" + new
            continue
        nxt = re.search(r"^## ", text[m.end():], re.M)
        end = m.end() + nxt.start() if nxt else len(text)
        text = text[:m.start()] + new + "\n" + text[end:]
    return text


REMIND_HEAD = re.compile(r"^(#+)\s.*(Напоминани|будильник)", re.I)
REMIND_BODY = re.compile(r"remind|БУДИЛЬНИК|reminders\.tsv|tmux", re.I)
REMIND_POINTER = ("Напоминания у меня работают через мост — команда и порядок в разделе «Напоминания — РАБОТАЮТ» "
                  "ниже. Старый будильник Claude (скрипт *remind-add*, «СРАБОТАЛ БУДИЛЬНИК», reminders.tsv) "
                  "здесь НЕ работает — им не пользуюсь и владельца к нему не отправляю.")


def replace_claude_reminders(text):
    """24.11: the old Claude alarm-clock section (ana-remind-add.sh, wake-up via tmux) stayed in the copy's
    rules next to the bridge's remind.py — the assistant refused reminders and sent Vika «к живой Ане».
    Any reminders section that talks about the Claude alarm clock becomes a pointer to the working one."""
    lines, out, i = text.splitlines(), [], 0
    while i < len(lines):
        m = REMIND_HEAD.match(lines[i])
        if not m or "РАБОТАЮТ" in lines[i] or "В ЭТОЙ КОПИИ" in lines[i]:
            out.append(lines[i])
            i += 1
            continue
        level = len(m.group(1))
        j = i + 1
        while j < len(lines) and not re.match(r"^#{1,%d}\s" % level, lines[j]):
            j += 1
        if REMIND_BODY.search("\n".join(lines[i + 1:j])):
            out += [lines[i], "", REMIND_POINTER, ""]
        else:
            out += lines[i:j]
        i = j
    return "\n".join(out)


# Skills installed for the whole server: `npx skills add … -g --agent cursor` puts them in ~/.agents/skills (kie.ai,
# lesson «Генерация картинок и видео», 26.11) — outside every agent folder, so the index must look there too.
SHARED_SKILLS = [os.path.expanduser("~/.agents/skills")]


def skills_index(copy, shared=None):
    rows, seen = [], set()
    found = []  # os.walk, not glob: glob's ** skips hidden folders, and skills live in .claude/skills
    for top in [copy, *(SHARED_SKILLS if shared is None else shared)]:
        for root, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "everything-claude-code")]
            if "SKILL.md" in files:
                found.append(os.path.join(root, "SKILL.md"))
    for f in sorted(found):
        # generic dev packs (887 skills) drown our own know-how; deleted/backup copies are stale
        if any(x in f for x in ("/node_modules/", "/backup", "everything-claude-code", "deleted-")):
            continue
        head = open(f, encoding="utf-8", errors="ignore").read(3000)
        name = re.search(r"^name:\s*(.+)$", head, re.M)
        desc = re.search(r"^description:\s*([>|][-+]?\s*\n((?:[ \t]+.+\n?)+)|.+)$", head, re.M)
        if desc and desc.group(2):  # YAML folded/literal block: join the indented lines
            desc = re.match(r"(.*)", " ".join(l.strip() for l in desc.group(2).splitlines()))
        key = (name.group(1).strip() if name else f)
        if desc and key not in seen:
            seen.add(key)
            shown = os.path.relpath(f, copy) if f.startswith(copy.rstrip("/") + "/") else f
            rows.append(f"- `{shown}` — {(name.group(1) if name else '').strip()}: {desc.group(1).strip()[:220]}")
    return rows


FULL_RULES_LIMIT = 70_000  # bytes: below this, the owner's rules go in word for word; above, one line each


def feedback_rules(memdirs, owner="ВИКТОРИИ"):
    """The owner's standing rules (type: feedback memories) as a BINDING section.

    24.11: the montazher copy saw «3 хука на выбор до монтажа» only as a line in the memory
    index — and skipped it. Rules must read as rules, not as a reference list."""
    if isinstance(memdirs, str):
        memdirs = [memdirs]
    files = []
    for d in memdirs:
        for f in sorted(glob.glob(d + "/*.md")):
            text = open(f, encoding="utf-8", errors="ignore").read()
            if re.search(r"^\s*type:\s*feedback\s*$", text, re.M) and not CLAUDE_ONLY.search(text):
                files.append((f, text))  # Claude-only rules (reply tool…) would contradict the bridge
    if not files:
        return ""
    total = sum(len(t.encode()) for _, t in files)
    out = [f"## ПРАВИЛА {owner} — ОБЯЗАТЕЛЬНЫ (это не справка). Перед задачей ищу здесь всё, что касается её вида, и делаю так"]
    for f, text in files:
        desc = re.search(r"^description:\s*(.+)$", text, re.M)
        if total <= FULL_RULES_LIMIT:
            body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S).strip()
            out.append(f"### {os.path.basename(f)}\n{body}")
        elif desc:
            out.append(f"- {desc.group(1).strip().strip(chr(34))} (`agent-memory/{os.path.basename(f)}`)")
    return "\n\n".join(out) if total <= FULL_RULES_LIMIT else "\n".join(out)


def memory_index(memdirs):
    """MEMORY.md of each memory folder; folders without one (course core/memory/) get a built index."""
    parts = []
    for d in memdirs:
        idx = os.path.join(d, "MEMORY.md")
        if os.path.isfile(idx):
            parts.append(open(idx, encoding="utf-8").read().strip())
            continue
        lines = []
        for f in sorted(glob.glob(os.path.join(d, "*.md"))):
            text = open(f, encoding="utf-8", errors="ignore").read()
            m = re.search(r"^description:\s*(.+)$", text, re.M) or re.search(r"^#+\s*(.+)$", text, re.M)
            lines.append(f"- [{os.path.basename(f)}]({os.path.basename(f)}) — {(m.group(1).strip().strip(chr(34)) if m else '')[:160]}")
        if lines:
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def build(agent, dry=False, cfg=None):
    cfg = cfg or load_config()
    a = cfg["agents"][agent]
    live, memdirs = a["live"], [d for d in a.get("memory", []) if os.path.isdir(d)]
    team = cfg["team"]
    copy = f"{team}/{agent}"
    shared = cfg.get("shared", [])
    rules = []
    for name in RULE_FILES:
        p = os.path.join(live, name)
        if os.path.isfile(p):
            rules.append(open(p, encoding="utf-8").read().rstrip())
    if not rules:
        sys.exit(f"ОШИБКА: в {live} нет CLAUDE.md — это не папка агента")
    text = apply_overrides("\n\n".join(rules), sorted(glob.glob(f"{copy}/grok-overrides/*.md")))
    text = replace_claude_reminders(strip_claude_only(rewrite_paths(text, live, copy, shared)))
    head = [open(f"{team}/GROK-COMMON.md", encoding="utf-8").read().rstrip()]
    if os.path.exists(f"{copy}/GROK.md"):
        head.append(open(f"{copy}/GROK.md", encoding="utf-8").read().rstrip())
    # GLM (24.11) skipped the team's records more than Grok: its work rules go FIRST, before the live rules
    parts = head + [text.rstrip()] if cfg.get("rules_first") else [text.rstrip()] + head
    for rel in a.get("inject", []):  # small key files the model must see every turn (e.g. error base index)
        p = os.path.join(copy, rel)
        if os.path.isfile(p):
            parts.append(f"## {rel} — всегда перед глазами (полный текст карточек — в той же папке)\n\n"
                         + open(p, encoding="utf-8").read().strip())
    skills = skills_index(copy)
    if skills:
        parts.append("## Мои навыки (открываю SKILL.md, когда задача подходит)\n\n" + "\n".join(skills))
    own = os.path.join(copy, "agent-memory")  # live memory + what the Grok agent learned itself (sync_knowledge)
    owner_rules = feedback_rules([own] if os.path.isdir(own) else memdirs, cfg.get("owner", "ВЛАДЕЛЬЦА"))
    if owner_rules:
        parts.append(owner_rules)
    idx = memory_index([own] if os.path.isdir(own) else memdirs)
    if idx:
        parts.append("## Оглавление моей памяти (файлы в agent-memory/)\n\n" + idx)
    out = "\n\n".join(parts) + "\n"
    if dry:
        return out
    sync_knowledge(live, copy, memdirs)
    out = build(agent, dry=True, cfg=cfg)  # rebuild: the merged memory may have new notes
    rules_file = cfg.get("rules_file", "CLAUDE.md")  # Cursor reads CLAUDE.md; OpenClaw (GLM) injects AGENTS.md
    tmp = f"{copy}/{rules_file}.tmp"
    open(tmp, "w", encoding="utf-8").write(out)
    os.replace(tmp, f"{copy}/{rules_file}")
    subprocess.run(["chown", "-R", f"{cfg['user']}:{cfg['user']}", copy], check=True)
    return out


def sync_knowledge(live, copy, memdirs):
    """Live → copy, WITHOUT erasing what the Grok agent wrote itself (24.11 audit: after a student's move the
    Claude folder is frozen, and an hourly plain rsync would overwrite every new note of the Grok agent).
    --update keeps files the copy changed later; MEMORY.md is merged line by line."""
    ex = sum((["--exclude", e] for e in EXCLUDES), [])
    for f in FOLDERS:
        if os.path.isdir(f"{live}/{f}") and os.path.isdir(f"{copy}/{f}"):
            subprocess.run(["rsync", "-a", "--update", *ex, f"{live}/{f}/", f"{copy}/{f}/"], check=True)
    own = f"{copy}/agent-memory"
    os.makedirs(own, exist_ok=True)
    for d in memdirs:
        subprocess.run(["rsync", "-a", "--update", "--exclude", "MEMORY.md", d + "/", own + "/"], check=True)
        live_idx, mine = os.path.join(d, "MEMORY.md"), os.path.join(own, "MEMORY.md")
        if os.path.isfile(live_idx):
            live_text = open(live_idx, encoding="utf-8").read()
            my_text = open(mine, encoding="utf-8").read() if os.path.isfile(mine) else ""
            with open(mine, "w", encoding="utf-8") as f:
                f.write(merge_text(live_text, my_text) if my_text else live_text)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    for agent in [a.agent] if a.agent else cfg["agents"]:
        out = build(agent, a.dry_run, cfg)
        print(f"{agent}: {len(out)} chars")
    if not a.dry_run and not a.agent:
        ensure_rooms(cfg)
        for src, dst in cfg.get("shared", []):
            if os.path.isdir(src):
                sync_shared(src, dst)
                subprocess.run(["chown", "-R", f"{cfg['user']}:{cfg['user']}", dst], check=False)


def ensure_rooms(cfg, run=subprocess.run):
    """Forwarder rooms (scripts that typed tasks into a Claude tmux room now reach the Grok agent). Stopping a Claude
    unit can take the whole tmux server down with it — 26.11 Татьяна lost 3 of 5 rooms right after the move and her
    morning calendar would have gone silent. The hourly sync puts back any room that is missing."""
    bridge = os.path.dirname(os.path.abspath(__file__))
    back = []
    for name, info in cfg.get("agents", {}).items():
        for room in info.get("rooms", []):
            if run(["tmux", "has-session", "-t", room], capture_output=True).returncode != 0:
                run(["tmux", "new-session", "-d", "-s", room,
                     f"GROK_BRIDGE_DIR={bridge} python3 {os.path.join(bridge, 'room_forwarder.py')} {name}"])
                back.append(room)
    if back:
        print("вернул комнаты-переходники:", ", ".join(back))
    return back


def merge_text(live, copy):
    """Live version + lines only the copy has (Grok agents' own notes, e.g. their task-board rows)."""
    have = set(live.splitlines())
    extra = [l for l in copy.splitlines() if l.strip() and l not in have]
    return live.rstrip("\n") + ("\n" + "\n".join(extra) if extra else "") + "\n"


def sync_shared(src, dst):
    """Team memory: live files flow in hourly, but what the Grok agents wrote themselves is kept."""
    merged = {}
    for root, _, files in os.walk(dst):
        for f in files:
            p = os.path.join(root, f)
            s = os.path.join(src, os.path.relpath(p, dst))
            if f.endswith((".md", ".tsv", ".txt")) and os.path.exists(s):
                live = open(s, encoding="utf-8", errors="ignore").read()
                mine = open(p, encoding="utf-8", errors="ignore").read()
                if live != mine:
                    merged[p] = merge_text(live, mine)
    os.makedirs(dst, exist_ok=True)
    subprocess.run(["rsync", "-a", "--exclude", ".git/", src + "/", dst + "/"], check=False)
    for p, text in merged.items():
        open(p, "w", encoding="utf-8").write(text)


if __name__ == "__main__":
    sys.exit(main())
