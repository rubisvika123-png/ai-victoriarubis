#!/usr/bin/env python3
"""Короткое имя для учеников, у которых агенты сразу на Grok (курс «Я на Grok», 26.11.2026):
`python3 grok.py setup | new | tz | github | google | group | mcp-share …` — то же, что claude-to-grok.py."""
import importlib.util
import os
import sys

spec = importlib.util.spec_from_file_location(
    "claude_to_grok", os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude-to-grok.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

if __name__ == "__main__":
    sys.exit(mod.main(sys.argv[1:]))
