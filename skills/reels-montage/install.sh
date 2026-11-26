#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# install.sh — ставит на сервер набор монтажа рилсов (урок «ИИ-монтажёр»).
#
# Это тот же набор, на котором монтирует монтажёр автора курса:
#   • нарезка пауз и дублей ПО ЗВУКУ (montage-pipeline, roughcut);
#   • расшифровка русской речи с таймкодами слов (GigaAM) — для субтитров;
#   • где поставить хук и субтитры — по лицу в кадре (YuNet, text_zones.py);
#   • субтитры с подсветкой слов, плашка хука, кодовое слово (build_ass.py);
#   • приёмка готового ролика: «ПРИНЯТО» или «БРАК» (check_reel.py).
#
# Запуск от root (агент запускает сам):
#   bash /root/<папка агента>/skills/reels-montage/install.sh
# Повторный запуск безопасен: готовое пропускается.
# Работает для агентов на Claude Code, GPT (Hermes) и Grok (Cursor).
# ─────────────────────────────────────────────────────────────────────
set -uo pipefail
SRC=$(cd "$(dirname "$0")" && pwd)
DST=${REELS_DIR:-/root/reels-montage}
say() { echo "→ $*"; }
die() { echo "ОШИБКА: $*" >&2; exit 1; }

say "программы: ffmpeg, git, python3-venv, шрифты"
if ! command -v ffmpeg >/dev/null || ! command -v git >/dev/null || ! python3 -c "import venv, ensurepip" 2>/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y ffmpeg git python3-venv fontconfig >/dev/null 2>&1 \
    || { apt-get update >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y ffmpeg git python3-venv fontconfig >/dev/null 2>&1; } \
    || die "не поставились ffmpeg/git/python3-venv — покажи владельцу вывод: apt-get install ffmpeg git python3-venv"
fi

mkdir -p "$DST" && cp -r "$SRC/tools" "$DST/" && cp "$SRC/SKILL.md" "$DST/"
[ -f "$DST/style.json" ] || cp "$SRC/style.json" "$DST/style.json"   # стиль владельца не затираем

say "шрифт Montserrat"
mkdir -p /usr/local/share/fonts/reels && cp "$SRC"/tools/fonts/*.ttf /usr/local/share/fonts/reels/ && fc-cache -f >/dev/null 2>&1

say "поиск лица (YuNet)"
M="$DST/tools/face_detection_yunet_2023mar.onnx"
[ -s "$M" ] || curl -fsSL -m 120 -o "$M" \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx \
  || die "не скачалась модель лица — повтори запуск"

say "окружение для лица и приёмки (opencv)"
[ -x "$DST/venv/bin/python" ] || python3 -m venv "$DST/venv" || die "не создалось окружение python"
"$DST/venv/bin/python" -c "import cv2, numpy" 2>/dev/null || \
  "$DST/venv/bin/pip" install -q opencv-python-headless numpy || die "не поставился opencv"

say "распознавание речи GigaAM (один раз, ~2 ГБ, несколько минут)"
[ -d "$DST/GigaAM" ] || git clone -q --depth 1 https://github.com/salute-developers/GigaAM.git "$DST/GigaAM" \
  || die "не скачался GigaAM"
[ -x "$DST/gigaam-venv/bin/python" ] || python3 -m venv "$DST/gigaam-venv"
"$DST/gigaam-venv/bin/python" -c "import gigaam" 2>/dev/null || {
  "$DST/gigaam-venv/bin/pip" install -q torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
    && "$DST/gigaam-venv/bin/pip" install -q -e "$DST/GigaAM"; } || die "не поставился GigaAM"

say "нарезка по звуку (montage-pipeline)"
[ -d "$DST/montage-pipeline" ] || git clone -q --depth 1 https://github.com/qwwiwi/montage-pipeline.git "$DST/montage-pipeline" \
  || die "не скачался montage-pipeline"
cat > "$DST/roughcut.sh" <<'EOF'
#!/usr/bin/env bash
# Нарезка по звуку. Ключ Groq берём из окружения или из настроек агента (урок «навыки и голосовые»).
set -euo pipefail
if [ -z "${GROQ_API_KEY:-}" ]; then
  for f in /root/grok-bridge/*.env /root/ai-*/secrets/*.env /root/*/secrets/*.env /root/.hermes/.env /root/.hermes/profiles/*/.env; do
    [ -f "$f" ] || continue
    k=$(grep -m1 '^GROQ_API_KEY=' "$f" 2>/dev/null | cut -d= -f2- | tr -d '"')
    [ -n "$k" ] && { export GROQ_API_KEY="$k"; break; }
  done
fi
exec python3 -I "$(dirname "$0")/montage-pipeline/roughcut.py" "$@"
EOF
chmod +x "$DST/roughcut.sh"

say "навык — в папки навыков агентов"
for d in /root/.claude/skills /root/.hermes/skills; do
  [ -d "$(dirname "$d")" ] || continue
  mkdir -p "$d" && ln -sfn "$DST" "$d/reels-montage"
done

say "самопроверка"
"$DST/venv/bin/python" "$DST/tools/build_ass.py" --selftest >/dev/null || die "build_ass не прошёл самопроверку"
"$DST/venv/bin/python" "$DST/tools/check_reel.py" --selftest >/dev/null || die "check_reel не прошёл самопроверку"
"$DST/gigaam-venv/bin/python" -c "import gigaam" || die "GigaAM не импортируется"
echo
echo "ГОТОВО. Набор монтажа: $DST (инструкция агенту — $DST/SKILL.md)."
echo "Дальше один раз заполни стиль владельца: $DST/style.json"
