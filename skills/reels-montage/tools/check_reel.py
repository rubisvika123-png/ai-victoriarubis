#!/usr/bin/env python3
"""Приёмка готового ролика: пускать нельзя, пока не прошёл проверку.

Зачем это вообще есть. У монтажёра на Клоде правила встроены в инструмент, а у
монтажёра на Эрмесе они жили как текст, который он должен помнить, — и 19.09.2026
Вика получила ролик, в котором разом: две секунды тишины в начале, подписи на
чёрной плашке посреди кадра, хука нет вообще. Все правила при этом были записаны.
Правило, которое никто не проверяет, — это не правило. Поэтому проверяет машина.

Скрипт смотрит на ГОТОВЫЙ файл, тот самый, который собираются отправить, и
отвечает одним словом: принято или брак. При браке говорит, что именно не так.

Проверяет три вещи: вырезана ли тишина в начале, есть ли хук и не лезет ли он на
лицо, ниже ли подбородка субтитры. Чёрную плашку под подписями машинно отличить
от тёмных волос надёжно не вышло (проверка либо пропускала плашку, либо браковала
утверждённый ролик), поэтому такой проверки здесь СОЗНАТЕЛЬНО нет — ложная
тревога хуже её отсутствия. Плашку смотрит человек на кадрах приёмки.

    /root/reels-montage/venv/bin/python /root/reels-montage/tools/ check_reel.py финал.mp4
    python3 check_reel.py --selftest        # проверка логики вердикта, без видео

Код возврата: 0 — принято, 1 — брак, и отправлять нельзя.
"""

import argparse
import json
import re
import subprocess
import sys

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from text_zones import FACE_MIN_SHARE, HOOK_TOL, face_boxes, measure, zones  # noqa: E402

# Сколько тишины в начале допустимо. Вика: «начало, где я молчу, режется».
# 0.35 с — это запас на вдох перед первым словом, а не пауза.
LEAD_MAX = 0.35
# Порог тишины: ниже этого уровня считаем, что звука нет.
SILENCE_DB = -35
# Хук ищем в этом окне: он должен быть виден сразу, а не «когда-нибудь».
HOOK_WINDOW = (0.6, 4.0)
# Порог «светлого пикселя» для поиска вшитого текста. Был 225 — под чисто белые
# субтитры. 10.10.2026 из-за него ролик уходил в ложный брак «хука нет»: хук
# написан кремовым из палитры Вики (243,231,206), после кодирования это ~222, и
# ни один пиксель порог не проходил. 210 ловит и кремовый, и белый, а кожа
# (150–200) и волосы по-прежнему мимо; светлая стена отсекается не яркостью, а
# длиной светлого участка (см. text_rows).
BRIGHT = 210


def lead_silence(video):
    """Сколько секунд ролик молчит в самом начале."""
    out = subprocess.run(
        ["ffmpeg", "-i", video, "-af", f"silencedetect=n={SILENCE_DB}dB:d=0.2", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    starts = [float(m) for m in re.findall(r"silence_start: ([-0-9.]+)", out)]
    ends = [float(m) for m in re.findall(r"silence_end: ([-0-9.]+)", out)]
    # Тишина «в начале» — только та, что начинается практически с нуля.
    if not starts or not ends or min(starts) > 0.25:
        return 0.0
    return ends[0]


def text_rows(gray):
    """Строки кадра, в которых есть вшитый текст.

    Два признака уже не сработали, поэтому оба описаны здесь, чтобы не пробовать
    их заново. «Светлый пиксель рядом с тёмным» (обводка) не увидел белый хук на
    светлой стене. Счёт резких границ (Canny) наоборот увидел текст там, где его
    нет: у лица и волос границ не меньше, чем у букв.

    Рабочий признак — ДЛИНА светлых участков. Буква даёт короткий штрих, и таких
    штрихов в строке много. Белая кофта и стена дают один длинный участок на всю
    ширину, волосы — ни одного светлого. Поэтому считаем именно короткие светлые
    отрезки, а не яркость и не границы.
    """
    import numpy as np

    bright = (gray > BRIGHT).astype(np.int8)
    scores = np.zeros(gray.shape[0], dtype=np.int32)
    for y in range(gray.shape[0]):
        edges = np.flatnonzero(np.diff(np.concatenate(([0], bright[y], [0]))))
        if edges.size == 0:
            continue
        starts, ends = edges[0::2], edges[1::2]
        lengths = ends - starts
        scores[y] = int(((lengths >= 2) & (lengths <= 45)).sum())
    return scores


def text_bands(gray, min_score=6):
    """Непрерывные полосы строк с текстом: [(верх, низ), ...]."""
    scores = text_rows(gray)
    bands, start = [], None
    for y, s in enumerate(scores):
        if s >= min_score and start is None:
            start = y
        elif s < min_score and start is not None:
            if y - start >= 8:  # одиночные строки — это шум, а не надпись
                bands.append((start, y))
            start = None
    if start is not None and len(scores) - start >= 8:
        bands.append((start, len(scores)))
    return bands


def frames_at(video, times):
    import cv2

    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    out = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if ok:
            out.append((t, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)))
    cap.release()
    return out


def duration(video):
    import cv2

    cap = cv2.VideoCapture(video)
    d = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1) / (cap.get(cv2.CAP_PROP_FPS) or 30.0)
    cap.release()
    return d


def analyze(video):
    """Собирает факты о готовом файле. Решение принимает verdict()."""
    head_top, chin, height, seen, samples = measure(video)
    # measure() отдаёт координаты из OpenCV как float32: и в JSON они не
    # сериализуются, и в отчёте выглядят как «подбородок на 813.3552246».
    head_top, chin = int(round(float(head_top))), int(round(float(chin)))
    z = zones(head_top, chin, height)

    # Лицо для хука меряем в окне самого хука, а не по всему ролику. На ролике
    # из трёх частей (08.10.2026) показ экрана дал ложные лица — YuNet принял
    # аватарки в чатах за лица, глобальный head_top уехал с 394 на 184, и хук,
    # стоявший правильно над головой, перестал опознаваться как хук.
    hook_head_top = int(round(float(measure(video, t_to=HOOK_WINDOW[1] + 2)[0])))

    # Лицо для хука сверяем ПОКАДРОВО, а не по самому высокому положению за первые
    # секунды. 13.10.2026: Вика на 2,8 с наклонилась к камере, лоб поднялся на 291,
    # и правильный хук, который к этому моменту уже погас, объявлялся налезающим на
    # лицо. Текст и лицо могут спорить только в одном и том же кадре.
    hook_times = [HOOK_WINDOW[0], 1.5, 2.5, HOOK_WINDOW[1]]
    boxes = face_boxes(video, hook_times)
    ref = max([b[2] for b in boxes] or [0.0])
    hook_seen, hook_low = False, None
    for (_, gray), box in zip(frames_at(video, hook_times), boxes):
        top = box[0] if box[0] is not None and box[2] >= FACE_MIN_SHARE * ref else hook_head_top
        for y0, y1 in text_bands(gray):
            if y1 <= top + HOOK_TOL + 40:             # надпись над лицом — это хук
                hook_seen = True
            elif y0 < top and y1 < chin:              # верхний текст, но лезет на лицо
                hook_low = y0 if hook_low is None else min(hook_low, y0)

    # Субтитры — САМАЯ НИЖНЯЯ надпись в кадре. Брать «самую верхнюю ниже
    # середины» нельзя: на хорошем ролике так за субтитры принимался блик на
    # вороте, и утверждённый Викой ролик объявлялся браком.
    sub_top = None
    dur = duration(video)
    # Первые секунды смотрим отдельно: 19.09.2026 чёрная плашка была именно там,
    # а по долям длительности туда не попадаешь и брак проходит мимо.
    # Кадров берём с запасом: в субтитрах уже сказанные слова белые, а следующие
    # приглушённо-серые, и на первом слове блока белого текста слишком мало,
    # чтобы его опознать. 12.10.2026 из-за двух неудачно попавших кадров ролик
    # с нормальными субтитрами получил «субтитры не найдены».
    times = [0.8, 1.2, 1.6, 2.2, 3.0] + [dur * m for m in (0.25, 0.4, 0.55, 0.7, 0.85, 0.95)]
    # Субтитры ищем только в кадрах, где в кадре Вика. На части с показом экрана
    # надписи интерфейса — не субтитры, и подбородок к ним отношения не имеет:
    # 12.10.2026 из-за этого ролик с записью экрана ушёл в ложный брак
    # «субтитры заходят на лицо» (нижняя надпись интерфейса на 1166).
    # Опорное лицо берём из первых секунд: по инструкции ролик всегда
    # начинается с лица Вики.
    areas = [b[2] for b in face_boxes(video, times)]
    sub_ref = max(areas[:2]) or max(areas) or 0.0
    times = [t for t, a in zip(times, areas) if a >= FACE_MIN_SHARE * sub_ref] or times[:2]
    for _, gray in frames_at(video, times):
        lower = [b for b in text_bands(gray) if height * 0.4 <= b[0] <= height - 60]
        if not lower:
            continue
        band = max(lower, key=lambda b: b[0])
        sub_top = band[0] if sub_top is None else min(sub_top, band[0])

    return {
        "height": height,
        "head_top": head_top,
        "hook_head_top": hook_head_top,
        "chin": chin,
        "faces": f"{seen}/{samples}",
        "lead_silence": round(lead_silence(video), 2),
        "hook_seen": hook_seen,
        "hook_on_face": hook_low,
        "sub_top": sub_top,
        "want_hook_y": zones(hook_head_top, chin, height)["hook_y"],
        "want_sub_y": z["sub_y"],
    }


def verdict(f):
    """Чистая функция: по фактам — список претензий. Её и проверяет --selftest."""
    bad = []
    if f["lead_silence"] > LEAD_MAX:
        bad.append(
            f"тишина в начале {f['lead_silence']} с — не вырезана (допустимо {LEAD_MAX})"
        )
    if not f["hook_seen"]:
        bad.append("хука нет в первых секундах")
    if f["hook_on_face"] is not None:
        bad.append(
            f"хук налезает на лицо (начинается на {f['hook_on_face']},"
            f" лицо с {f.get('hook_head_top', f['head_top'])})"
        )
    if f["sub_top"] is None:
        bad.append("субтитры не найдены")
    elif f["sub_top"] < f["chin"]:
        bad.append(
            f"субтитры заходят на лицо: текст с {f['sub_top']}, подбородок на {f['chin']}"
            f" — должны стоять около {f['want_sub_y']}"
        )
    return bad


def selftest():
    fails = 0
    good = {
        "height": 1280, "head_top": 256, "chin": 805, "faces": "14/14",
        "lead_silence": 0.0, "hook_seen": True, "hook_on_face": None,
        "sub_top": 1000, "want_hook_y": 130, "want_sub_y": 1025,
    }
    if verdict(good):
        print(f"FAIL: хороший ролик забракован: {verdict(good)}")
        fails = 1

    # Ровно тот брак, который Вика получила 19.09.2026, — все четыре претензии.
    bad = dict(good, lead_silence=1.79, hook_seen=False, sub_top=700)
    got = verdict(bad)
    if len(got) != 3:
        print(f"FAIL: брак 19.09 распознан не полностью: {got}")
        fails = 1

    # Граница: полсекунды вдоха — это ещё не «не вырезал начало».
    if verdict(dict(good, lead_silence=0.3)):
        print("FAIL: вдох перед первым словом засчитан за тишину")
        fails = 1
    if not verdict(dict(good, lead_silence=0.4)):
        print("FAIL: лишняя тишина пропущена")
        fails = 1

    # Хук, залезающий на лицо, — брак.
    if not verdict(dict(good, hook_on_face=200)):
        print("FAIL: хук на лице пропущен")
        fails = 1

    if fails:
        return 1
    print("ALL PASS")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("video", nargs="?")
    p.add_argument("--json", action="store_true")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()
    if not a.video:
        p.error("нужен файл видео")

    facts = analyze(a.video)
    bad = verdict(facts)
    if a.json:
        print(json.dumps({"facts": facts, "problems": bad}, ensure_ascii=False, indent=2))
    elif bad:
        print("БРАК — отправлять нельзя:")
        for b in bad:
            print(f"  · {b}")
    else:
        print("ПРИНЯТО: тишина вырезана, хук на месте и не на лице, "
              "субтитры ниже подбородка.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
