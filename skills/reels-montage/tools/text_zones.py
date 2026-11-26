#!/usr/bin/env python3
"""Где в ЭТОМ ролике можно ставить хук и субтитры, чтобы не попасть на лицо.

Кадрирование у Вики меняется от ролика к ролику: где-то она ближе, где-то
дальше. Поэтому фиксированные числа не годятся — 16.09.2026 хук четыре раза
подряд оказывался у неё на лбу. Скрипт сам находит лицо по всему ролику и
считает две свободные полосы:

  хук      — выше лба (по волосам идти можно), но ниже плашек Instagram;
  субтитры — ниже подбородка, но не впритык к нижнему краю.

Берётся самое высокое положение головы и самый низкий подбородок за весь ролик,
поэтому текст не наедет на лицо ни в одном кадре, а не только в проверенном.

    /root/reels-montage/venv/bin/python /root/reels-montage/tools/ text_zones.py видео.mp4   # JSON с координатами
    python3 text_zones.py --selftest             # проверка расчёта, без видео
"""

import argparse
import json
import sys

# Верхние плашки Instagram Reels. Доля высоты кадра: 270 px при 1920 и 180 при
# 1280 — ровно верх безопасной зоны из инструкции Вики. Замерено по её
# скриншоту реального Reels 08.10.2026: заголовок «Reels» и иконки стоят до
# высоты кадра 0.115, хук на 180 на них наезжал. 0.1406 — это с запасом.
IG_TOP_SHARE = 0.140625
# Низ безопасной зоны Reels — доля высоты кадра. 0.65 — это ровно инструкция
# Вики: y<=1248 при 1920 и y<=832 при 1280. Раньше здесь был запас всего 130 px
# от нижнего края, и субтитры уезжали под плашки соцсети: 08.10.2026 Вика
# написала «почему так низко основные субтитры», и это был уже не первый раз.
SAFE_BOTTOM = 0.65
# Зазор между текстом и лицом и высота надписи — доли высоты кадра, а не
# фиксированные пиксели: один и тот же ролик считается и в 1280, и в 1920.
GAP_SHARE = 0.02
SUB_H_SHARE = 0.035
# Хук занимает ВСЮ свободную полосу над лицом, а не фиксированные 140 px:
# 08.10.2026 Вика написала «хук должен быть крупнее, на то он и хук», и фикс
# высоты как раз и делал его мелким. Поэтому zones() отдаёт hook_h — высоту
# свободной полосы, под которую монтажёр подбирает кегль. Меньше этой доли
# полоса бесполезна: туда не влезет и одна крупная строка.
HOOK_MIN_SHARE = 0.06
# Хуку можно на столько зайти за верх рамки лица: там ещё волосы, а не лицо.
HOOK_TOL = 20
# Лицо меньше этой доли от самого крупного найденного — это не Вика, а аватарка
# на показанном экране или портрет на стене.
FACE_MIN_SHARE = 0.25
# Насколько низко субтитры стоят в свободной полосе под подбородком.
SUB_LOW = 0.72


def zones(head_top, chin, height):
    """Считает hook_y (верх надписи, \\an8) и sub_y (низ надписи, \\an2).

    Чистая функция без OpenCV: её и проверяет --selftest.
    """
    gap, sub_h = GAP_SHARE * height, SUB_H_SHARE * height

    ig_top = IG_TOP_SHARE * height
    hook_floor = head_top + HOOK_TOL
    hook_y = max(ig_top, 0)
    hook_h = hook_floor - hook_y
    hook_fits = hook_h >= HOOK_MIN_SHARE * height

    # Субтитры стоят в нижней части свободного места между подбородком и НИЗОМ
    # БЕЗОПАСНОЙ ЗОНЫ (не нижним краем кадра). Доля, а не фиксированный зазор:
    # на крупном плане они опускаются, на общем поднимаются сами.
    floor = SAFE_BOTTOM * height
    sub_y = chin + SUB_LOW * (floor - chin)
    sub_y = min(max(sub_y, chin + gap + sub_h), floor)
    sub_fits = sub_y - sub_h - gap >= chin

    return {
        "hook_y": int(round(hook_y)),
        "hook_h": int(round(max(hook_h, 0))),
        "sub_y": int(round(sub_y)),
        "head_top": int(round(head_top)),
        "chin": int(round(chin)),
        "hook_fits": bool(hook_fits),
        "sub_fits": bool(sub_fits),
    }


def measure(video, samples=14, t_from=None, t_to=None):
    """Самое высокое положение головы и самый низкий подбородок за ролик."""
    import cv2

    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"не открывается: {video}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    model = __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "face_detection_yunet_2023mar.onnx")
    detector = cv2.FaceDetectorYN.create(model, "", (width, height), 0.75, 0.3, 5000)

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    first = int((t_from or 0) * fps)
    last = int(t_to * fps) if t_to else total
    last = max(last, first + 1)

    found = []
    for i in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, first + int((last - first) * (i + 0.5) / samples))
        ok, frame = cap.read()
        if not ok:
            continue
        _, faces = detector.detect(frame)
        if faces is None or len(faces) == 0:
            continue
        # Самое крупное лицо в кадре — это Вика, а не портрет на стене.
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])[:4]
        found.append((y, y + h, w * h))
    cap.release()

    if not found:
        raise SystemExit("лицо не найдено ни в одном кадре — ставить текст вслепую нельзя")

    # Мелкие «лица» — не Вика. На ролике с показом экрана YuNet принимает за
    # лица аватарки в чатах, и они уводили замеры: 08.10.2026 голова «поднялась»
    # с 394 на 184 и хук перестал опознаваться, 09.10.2026 подбородок «опустился»
    # до 1483 и нормальные субтитры объявлялись браком. Берём только те кадры,
    # где лицо сопоставимо с самым крупным найденным.
    biggest = max(a for _, _, a in found)
    real = [(t, c) for t, c, a in found if a >= FACE_MIN_SHARE * biggest]
    tops = [t for t, _ in real]
    chins = [c for _, c in real]
    return min(tops), max(chins), height, len(real), samples


def face_boxes(video, times):
    """Самое крупное лицо в каждый из моментов: (верх, подбородок, площадь).

    Если лица нет, в списке стоит (None, None, 0.0).

    Нужна там, где надо отличить кадр с Викой от кадра с показом экрана:
    на записи экрана YuNet видит аватарки в чатах, но они в разы мельче лица.
    """
    import cv2

    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"не открывается: {video}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    model = __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "face_detection_yunet_2023mar.onnx")
    detector = cv2.FaceDetectorYN.create(model, "", (width, height), 0.75, 0.3, 5000)

    out = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok:
            out.append((None, None, 0.0)); continue
        _, faces = detector.detect(frame)
        if faces is None or len(faces) == 0:
            out.append((None, None, 0.0)); continue
        f = max(faces, key=lambda f: f[2] * f[3])
        out.append((int(f[1]), int(f[1] + f[3]), float(f[2] * f[3])))
    cap.release()
    return out


def selftest():
    fails = 0
    # Кадр 720×1280, замеры ролика 08.10.2026 (пересчитаны из 1080×1920): рамка
    # лица с 263, подбородок на 748. Полосы — те, что записаны в инструкции
    # Вики: хук выше лба, субтитры x=43–634, y=700–827.
    got = zones(263, 748, 1280)
    if got["hook_y"] != int(round(IG_TOP_SHARE * 1280)):
        print(f"FAIL: хук не от верхней плашки: {got['hook_y']}")
        fails = 1
    # Полоса под хук должна быть реальной высоты, а не «140 px всегда».
    if got["hook_h"] < 0.06 * 1280:
        print(f"FAIL: полоса под хук мельче минимума: {got['hook_h']}")
        fails = 1
    if not (700 <= got["sub_y"] <= 832):
        print(f"FAIL: субтитры вне полосы инструкции: {got['sub_y']}")
        fails = 1

    if not got["hook_fits"] or not got["sub_fits"]:
        print(f"FAIL: нормальный кадр признан тесным: {got}")
        fails = 1

    # Близкий план 16.09.2026 (подбородок 805 при 1280): субтитры ниже
    # подбородка в безопасную зону уже не помещаются. Тогда их поставили на
    # 1025 — под плашки соцсети, и 08.10.2026 Вика на это пожаловалась.
    # Теперь это честно считается теснотой: ставить вслепую нельзя, надо
    # спросить Вику, что важнее.
    close = zones(256, 805, 1280)
    if close["sub_fits"]:
        print(f"FAIL: субтитры под плашками признаны нормой: {close}")
        fails = 1

    # Лицо крупно и почти во весь кадр — места нет, и это надо сказать вслух.
    tight = zones(60, 1200, 1280)
    if tight["hook_fits"] or tight["sub_fits"]:
        print(f"FAIL: нехватка места не распознана: {tight}")
        fails = 1

    # Хук никогда не заходит под верхние плашки Instagram.
    high = zones(200, 700, 1280)
    if high["hook_y"] < IG_TOP_SHARE * 1280:
        print(f"FAIL: хук залез под плашки: {high['hook_y']}")
        fails = 1

    # Субтитры никогда не прижимаются к самому нижнему краю.
    low = zones(300, 1250, 1280)
    if low["sub_y"] > SAFE_BOTTOM * 1280:
        print(f"FAIL: субтитры у самого края: {low['sub_y']}")
        fails = 1

    if fails:
        return 1
    print("ALL PASS")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("video", nargs="?")
    p.add_argument("--samples", type=int, default=14)
    p.add_argument("--from", dest="t_from", type=float)
    p.add_argument("--to", dest="t_to", type=float)
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()
    if not a.video:
        p.error("нужен файл видео")

    head_top, chin, height, seen, samples = measure(a.video, a.samples, a.t_from, a.t_to)
    out = zones(head_top, chin, height)
    out["frames_with_face"] = f"{seen}/{samples}"
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
