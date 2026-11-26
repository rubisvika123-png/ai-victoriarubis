#!/usr/bin/env python3
"""Распознавание длинного ролика GigaAM по кускам.

GigaAM за раз берёт короткий фрагмент (на 36 с ругается «Too long wav file»),
поэтому режем аудио по тишине на куски до ~25 с, распознаём каждый отдельно и
складываем слова обратно в одну шкалу времени исходника.
"""
import json
import subprocess
import sys
from pathlib import Path

import gigaam

src = Path(sys.argv[1])
out = Path(sys.argv[2])
bounds = [float(x) for x in sys.argv[3].split(",")]   # точки реза, в секундах

dur = float(subprocess.check_output(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
     "-of", "default=nw=1:nk=1", str(src)], text=True).strip())
edges = [0.0] + bounds + [dur]

model = gigaam.load_model("v3_e2e_rnnt")
words, texts = [], []
for i, (a, b) in enumerate(zip(edges, edges[1:])):
    part = src.parent / f"chunk{i}.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src),
                    "-ss", f"{a:.3f}", "-to", f"{b:.3f}",
                    "-c:a", "pcm_s16le", str(part)], check=True)
    res = model.transcribe(str(part), word_timestamps=True)
    text = res.text.strip() if hasattr(res, "text") else str(res).strip()
    texts.append(text)
    for w in (getattr(res, "words", []) or []):
        words.append({"start": a + float(w.start), "end": a + float(w.end),
                      "text": str(w.text).strip()})
    print(f"[{a:6.2f}-{b:6.2f}] {text}", flush=True)

out.write_text(json.dumps({"model": "v3_e2e_rnnt", "duration": dur,
                           "text": " ".join(texts), "words": words},
                          ensure_ascii=False, indent=2), encoding="utf-8")
print(out)
