#!/usr/bin/env python3
"""Keep the old tmux «room» of a Claude agent alive after its move to Grok (25.11, Denis's emergency move).

Many cron scripts wake the Claude agent by typing into its room: `tmux send-keys -t ROOM -l "ЗАДАЧА…"` + Enter.
On Grok there is no room, so those tasks would vanish silently. Run this in a tmux session with the SAME name:
every line typed into it becomes a scheduled task in the bridge queue of the Grok agent.

  tmux new-session -d -s coder-denis "python3 room_forwarder.py denis"
"""
import json, os, sys

STATE_ROOT = os.environ.get("GROK_BRIDGE_DIR", os.path.dirname(os.path.abspath(__file__)))


def forward(agent, line, root=STATE_ROOT):
    text = line.strip()
    if not text:
        return False
    path = os.path.join(root, f"state-{agent}", "inbox.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "schedule", "id": "комната", "text": text}, ensure_ascii=False) + "\n")
    return True


def main(agent):
    print(f"Комната агента {agent}: всё, что сюда печатают, уходит ему на Grok.", flush=True)
    for line in sys.stdin:
        if forward(agent, line):
            print("→ передано", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
