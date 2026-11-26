#!/usr/bin/env python3
"""Субтитры, хук и кодовое слово для рилса — файл subs.ass для ffmpeg.

Это рабочий набор монтажёра автора курса, на котором он монтирует свои ролики
каждый день. Логика и пропорции проверены на десятках роликов; всё, что
меняется от ролика к ролику и от владельца к владельцу, вынесено в два файла:

  style.json  — фирменный стиль владельца (цвета, шрифт, размер субтитров,
                кодовое слово). Лежит рядом с навыком, заполняется ОДИН раз.
  job.json    — этот ролик: хук, где стоят тексты (из text_zones.py),
                когда уходит хук, исправления распознавателя. Лежит в папке задания.

    python3 build_ass.py ПАПКА_ЗАДАНИЯ          # читает ПАПКА/job.json и ПАПКА/work/tb.json
    python3 build_ass.py --selftest

Слова и их время — только из расшифровки (work/tb.json, transcribe_chunks.py).
Текст не придумывается: правятся лишь слова, которые распознаватель слышит неверно.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

CHAR_W, LINE_H = 0.484, 1.2
SAFE_W, HOOK_W = 800, 860

DEFAULT_STYLE = {
    "font": "Montserrat Black",
    "main_colour": "#F3E7CE",       # цвет хука и кодового слова
    "key_colour": "#E91E8C",        # цвет ключевых слов хука
    "code_outline": "#B01B3B",      # обводка кодового слова
    "sub_colour": "#FFFFFF",        # сказанные слова
    "sub_next_colour": "#A0A0A0",   # следующие слова (подсветка караоке)
    "sub_size": 62,
    "code_word": "",
    "code_triggers": ["слово", "комментах", "комментариях", "пиши", "напиши"],
}
DEFAULT_JOB = {
    "hook": "",            # «ПЕРВАЯ СТРОКА|ВТОРАЯ СТРОКА» — капслок, строки через |
    "hook_keys": [],       # части хука, которые красятся key_colour
    "hook_end": 4.0,       # секунды: когда хук уходит
    "hook_x": 540, "hook_y": 270, "hook_band": 225,
    "sub_x": 540, "sub_y": 1400,
    "code_y": 272, "code_band": 150,
    "sub_safe_w": 640,
    "phrases": [],         # [["и","агент"], "ИИ-АГЕНТ"] — склейки слов распознавателя
    "replace": {},         # {"тред": "THREADS"} — замены одиночных слов
}
# Общие исправления распознавателя русской речи (ИИ, бренды) — проверены на роликах автора.
BASE_PHRASES = [
    (("и-и-и", "агентов"), "ИИ-АГЕНТОВ"), (("и", "агентов"), "ИИ-АГЕНТОВ"),
    (("и-и-и", "агент"), "ИИ-АГЕНТ"), (("и", "агент"), "ИИ-АГЕНТ"),
    (("и-и-и", "маркетолог"), "ИИ-МАРКЕТОЛОГ"), (("и-и-и", "маркетолога"), "ИИ-МАРКЕТОЛОГА"),
    (("youtube", "шорт"), "YOUTUBE SHORTS"), (("youtube", "и", "шорт"), "YOUTUBE И SHORTS"),
]
BASE_REPL = {"тред": "THREADS", "треда": "THREADS", "трэц": "THREADS", "трец": "THREADS",
             "трэс": "THREADS", "шорт": "SHORTS", "шорты": "SHORTS", "рюсов": "РИЛСОВ"}


def ass_colour(hex_rgb):
    """#RRGGBB -> ASS &HBBGGRR&."""
    h = hex_rgb.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def load(path, default):
    data = dict(default)
    if os.path.isfile(path):
        data.update(json.load(open(path, encoding="utf-8")))
    return data


def fit(lines, band, width):
    longest = max(len(l) for l in lines)
    return int(min(band / (len(lines) * LINE_H), width / (longest * CHAR_W)))


def layout_hook(text, band, width):
    parts = [p.split() for p in text.split("|")]
    return parts, fit([" ".join(p) for p in parts], band, width)


def colour_lines(parts, keys, key_c, main_c):
    flat = [w for p in parts for w in p]
    hit = set()
    for key in keys:
        kw = key.split()
        for i in range(len(flat) - len(kw) + 1):
            if flat[i:i + len(kw)] == kw:
                hit |= set(range(i, i + len(kw)))
                break
        else:
            raise SystemExit(f"не нашёл в хуке ключевую часть: {key!r}")
    out, idx = [], 0
    for part in parts:
        chunks, cur, buf = [], None, []
        for w in part:
            k = idx in hit
            if cur is None or k == cur:
                buf.append(w)
            else:
                chunks.append((cur, buf)); buf = [w]
            cur = k; idx += 1
        if buf:
            chunks.append((cur, buf))
        out.append("".join(f"{{\\c{key_c if k else main_c}}}" + " ".join(b)
                           + (" " if n < len(chunks) - 1 else "")
                           for n, (k, b) in enumerate(chunks)))
    return "\\N".join(out)


def cs(t):
    t = max(t, 0)
    h = int(t // 3600); t -= h * 3600
    m = int(t // 60); t -= m * 60
    s = int(t); c = int(round((t - s) * 100))
    if c == 100:
        s += 1; c = 0
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def tail_punct(raw):
    tail = ""
    for ch in reversed(raw):
        if ch in ".,!?…»":
            tail = ch + tail
        else:
            break
    return tail.replace("»", "")


def fix_words(words, phrases, repl):
    merged, i = [], 0
    phrases = sorted(phrases, key=lambda pr: -len(pr[0]))  # длинные склейки — первыми
    while i < len(words):
        hit = False
        for phrase, rep in phrases:
            n = len(phrase)
            if i + n <= len(words) and tuple(
                    words[i + k]["text"].lower().strip(",.!?«»") for k in range(n)) == tuple(phrase):
                merged.append({"text": rep + tail_punct(words[i + n - 1]["text"]),
                               "fs": words[i]["fs"], "fe": words[i + n - 1]["fe"]})
                i += n; hit = True; break
        if hit:
            continue
        w = dict(words[i])
        clean = w["text"].lower().strip(",.!?«»")
        if clean in repl:
            w["text"] = repl[clean] + tail_punct(w["text"])
        merged.append(w); i += 1
    return merged


def blocks_of(words, sub_size, sub_safe_w):
    """Блоки субтитров по правилам автора: 2–4 слова, точка закрывает блок, «не» — со своим глаголом."""
    max_chars = int(sub_safe_w / (sub_size * CHAR_W))
    blocks, cur = [], []
    for w in words:
        if cur:
            p = cur[-1]; gap = w["fs"] - p["fe"]
            line = " ".join(x["text"] for x in cur + [w])
            if p["text"].rstrip("»").endswith((".", "!", "?", "…")):
                blocks.append(cur); cur = []          # конец предложения закрывает блок
            elif len(line) > max_chars:
                blocks.append(cur); cur = []          # строка шире безопасной ширины
            elif w["text"].lower().strip("«».,!?") in ("не", "ни") and len(cur) >= 2:
                blocks.append(cur); cur = []          # отрицание начинает новый блок
            elif gap > 0.40 or len(cur) >= 4:
                blocks.append(cur); cur = []
            elif (len(cur) >= 2 and gap > 0.18
                  and cur[0]["text"].lower().strip("«».,!?") not in ("не", "ни")):
                blocks.append(cur); cur = []
        cur.append(w)
    if cur:
        blocks.append(cur)
    for bi in range(len(blocks) - 1):                 # «не» последним — переносим к глаголу
        b, nxt = blocks[bi], blocks[bi + 1]
        if len(b) >= 2 and b[-1]["text"].lower().strip("«»") in ("не", "ни") \
                and len(" ".join(x["text"] for x in [b[-1]] + nxt)) <= max_chars:
            nxt.insert(0, b.pop())
    return blocks


def build(job_dir, style_path=None):
    style = load(style_path or os.path.join(SKILL, "style.json"), DEFAULT_STYLE)
    job = load(os.path.join(job_dir, "job.json"), DEFAULT_JOB)
    if not job["hook"]:
        raise SystemExit("в job.json нет хука (поле hook) — сначала выбери хук с владельцем")
    words = [{"text": w["text"], "fs": w["start"], "fe": w["end"]}
             for w in json.load(open(os.path.join(job_dir, "work", "tb.json"), encoding="utf-8"))["words"]
             if w["text"]]
    phrases = BASE_PHRASES + [(tuple(p), r) for p, r in job["phrases"]]
    repl = dict(BASE_REPL, **{k.lower(): v for k, v in job["replace"].items()})
    words = fix_words(words, phrases, repl)
    blocks = blocks_of(words, style["sub_size"], job["sub_safe_w"])

    main_c, key_c = ass_colour(style["main_colour"]), ass_colour(style["key_colour"])
    hook_lines, hook_size = layout_hook(job["hook"], job["hook_band"], HOOK_W)
    code = style["code_word"].upper()
    code_size = fit([code], job["code_band"], SAFE_W) if code else 0
    a = lambda c: "&H00" + ass_colour(c)[2:-1]  # noqa: E731  style colours with alpha
    font = style["font"]
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},{style['sub_size']},{a(style['sub_colour'])},{a(style['sub_next_colour'])},&H00101010,&H64000000,-1,0,0,0,100,100,0.8,0,1,4,2,5,0,0,0,1
Style: Hook,{font},{hook_size},{a(style['main_colour'])},{a(style['main_colour'])},&H00101010,&H00000000,-1,0,0,0,100,100,0.6,0,1,6,3,8,60,60,0,1
Style: Code,{font},{code_size or 10},{a(style['main_colour'])},{a(style['main_colour'])},{a(style['code_outline'])},&H00000000,-1,0,0,0,100,100,1,0,1,9,0,8,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [head]
    for idx, blk in enumerate(blocks):
        start, end = blk[0]["fs"], blk[-1]["fe"] + 0.40
        if idx < len(blocks) - 1:
            end = min(end, blocks[idx + 1][0]["fs"])
        toks = []
        for j, w in enumerate(blk):
            d = (blk[j + 1]["fs"] - w["fs"]) if j < len(blk) - 1 else (w["fe"] - w["fs"])
            toks.append(f"{{\\k{max(1, int(round(d * 100)))}}}{w['text'].upper()} ")
        lines.append(f"Dialogue: 0,{cs(start)},{cs(end)},Sub,,0,0,0,,"
                     f"{{\\an2\\pos({job['sub_x']},{job['sub_y']})}}" + "".join(toks).rstrip())
    lines.append(f"Dialogue: 1,{cs(0.15)},{cs(job['hook_end'])},Hook,,0,0,0,,"
                 f"{{\\pos({job['hook_x']},{job['hook_y']})\\fad(180,180)}}"
                 + colour_lines(hook_lines, job["hook_keys"], key_c, main_c))
    spots = []
    if code:  # кодовое слово — только там, где это ПРИЗЫВ («пиши слово …»), а не по ходу речи
        for i, w in enumerate(words):
            clean = w["text"].lower().strip(",.!?«»")
            prev = words[i - 1]["text"].lower().strip(",.!?«»") if i else ""
            if clean == code.lower() and prev in style["code_triggers"]:
                spots.append(w)
        for spot in spots:
            lines.append(f"Dialogue: 1,{cs(spot['fs'] - 0.15)},{cs(spot['fe'] + 2.0)},Code,,0,0,0,,"
                         f"{{\\pos(540,{job['code_y']})\\fad(150,220)}}{code}")
    open(os.path.join(job_dir, "subs.ass"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return {"hook_px": hook_size, "sub_px": style["sub_size"], "code_px": code_size,
            "blocks": len(blocks), "code_spots": [round(s["fs"], 2) for s in spots]}


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        os.makedirs(os.path.join(t, "work"))
        ws = ["Я", "не", "пишу", "посты.", "Мой", "и", "агент", "пишет.", "Пиши", "слово", "агент"]
        json.dump({"words": [{"text": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(ws)]},
                  open(os.path.join(t, "work", "tb.json"), "w"))
        json.dump({"hook": "ПОСТЫ ПИШУ|НЕ Я", "hook_keys": ["НЕ Я"]}, open(os.path.join(t, "job.json"), "w"))
        st = os.path.join(t, "style.json")
        json.dump({"code_word": "агент"}, open(st, "w"))
        r = build(t, st)
        ass = open(os.path.join(t, "subs.ass"), encoding="utf-8").read()
        assert "ИИ-АГЕНТ" in ass and "ПОСТЫ ПИШУ" in ass, ass
        assert r["code_spots"] == [3.0], r            # «Пиши слово агент» — призыв; «и агент» — нет
        assert "ПОСТЫ. МОЙ" not in ass                 # точка закрывает блок
        assert ass_colour("#E91E8C") == "&H8C1EE9&"
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        print(build(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")))
