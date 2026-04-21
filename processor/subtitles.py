"""
Сжигание русских субтитров в видео через ffmpeg.

Стиль TikTok: жирный белый текст с чёрной обводкой, по центру снизу.
Субтитры берутся из транскрипта Whisper — тайм-коды уже есть.

Формат ASS (Advanced SubStation Alpha) с явным PlayResX/PlayResY=1080x1920
чтобы FontSize задавался в реальных пикселях, а не в единицах libass (288p).
"""

import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from models.schemas import Transcript, FinalShort


# ---------------------------------------------------------------------------
# Настройки стиля
# ---------------------------------------------------------------------------

MAX_LINE_CHARS = 22      # короткие строки лучше читаются на вертикальном экране
FONT_NAME      = "Arial"
FONT_SIZE      = 72      # пикселей при PlayResY=1920 — оптимально для TikTok
FONT_BOLD      = 1
TEXT_COLOR     = "&H00FFFFFF"   # белый (AABBGGRR в ASS — A=00 прозрачность)
OUTLINE_COLOR  = "&H00000000"   # чёрная обводка
OUTLINE_WIDTH  = 4              # толщина обводки
SHADOW         = 1
MARGIN_BOTTOM  = 120            # отступ от низа экрана (px)

# Whisper слегка опережает реальное начало речи (известный bias модели).
# Этот сдвиг компенсирует это — субтитры появятся на 0.2с позже.
# Увеличь до 0.4-0.5 если субтитры всё ещё опережают.
SUBTITLE_DELAY = 0.2     # секунд (в координатах финального видео)


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def _wrap_text(text: str, max_chars: int = MAX_LINE_CHARS) -> str:
    """
    Переносит длинный текст на несколько строк.
    Разбивает по словам, не более max_chars в строке.
    """
    words = text.strip().split()
    lines = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 <= max_chars:
            current = f"{current} {word}".strip()
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\\N".join(lines)   # \\N — перенос строки в ASS формате


def _fmt_time_ass(seconds: float) -> str:
    """Формат времени для ASS: H:MM:SS.cc (сотые доли секунды)."""
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    cs = int((seconds % 1) * 100)   # centiseconds
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ---------------------------------------------------------------------------
# Генерация ASS файла
# ---------------------------------------------------------------------------

def _build_ass(
    transcript: Transcript,
    start_offset: float,
    end_offset: float,
    speed: float = 1.0,
    target_w: int = 1080,
    target_h: int = 1920,
) -> str:
    """
    Генерирует содержимое .ass файла для отрезка видео [start_offset, end_offset].

    PlayResX/PlayResY явно задаём = размер финального видео (1080x1920),
    тогда FontSize — это ровно то количество пикселей которое мы хотим.
    """
    # Заголовок скрипта
    header = f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: {target_w}
PlayResY: {target_h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT_NAME},{FONT_SIZE},{TEXT_COLOR},&H000000FF,{OUTLINE_COLOR},&H00000000,{FONT_BOLD},0,0,0,100,100,0,0,1,{OUTLINE_WIDTH},{SHADOW},2,10,10,{MARGIN_BOTTOM},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    dialogues = []
    for seg in transcript.segments:
        if seg.end <= start_offset or seg.start >= end_offset:
            continue

        seg_start = max(seg.start - start_offset, 0.0)
        seg_end   = min(seg.end   - start_offset, end_offset - start_offset)

        if seg_end <= seg_start:
            continue

        # Корректируем тайм-код под изменённую скорость видео (antidetect)
        seg_start /= speed
        seg_end   /= speed

        # Сдвигаем чуть вперёд — Whisper чуть опережает реальное начало речи
        seg_start = max(seg_start + SUBTITLE_DELAY, 0.0)
        seg_end   = seg_end + SUBTITLE_DELAY

        text = _wrap_text(seg.text.strip())

        dialogues.append(
            f"Dialogue: 0,{_fmt_time_ass(seg_start)},{_fmt_time_ass(seg_end)},"
            f"Default,,0,0,0,,{text}"
        )

    return header + "\n".join(dialogues)


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def burn_subtitles(
    video_path: str,
    output_path: str,
    transcript: Transcript,
    start_offset: float = 0.0,
    end_offset: float = None,
    speed: float = 1.0,
    target_w: int = 1080,
    target_h: int = 1920,
) -> str:
    """
    Сжигает субтитры в видео через ASS формат.

    Args:
        video_path:   путь к входному видео
        output_path:  путь к выходному видео
        transcript:   транскрипт из Whisper
        start_offset: начало клипа в исходном видео (сек)
        end_offset:   конец клипа в исходном видео (сек)
        speed:        скорость antidetect (для точных тайм-кодов)
        target_w/h:   разрешение финального видео (для PlayRes)

    Returns:
        путь к видео с субтитрами (или исходный если ошибка)
    """
    if end_offset is None:
        end_offset = transcript.duration

    ass_content = _build_ass(transcript, start_offset, end_offset, speed, target_w, target_h)

    # Проверяем что есть хоть одна строка Dialogue
    if "Dialogue:" not in ass_content:
        logger.warning(f"Нет субтитров для отрезка {start_offset:.1f}—{end_offset:.1f}с, пропускаем")
        return video_path

    # Пишем ASS во временный файл
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ass", encoding="utf-8", delete=False
    ) as f:
        f.write(ass_content)
        ass_path = f.name

    try:
        # Экранируем путь для ffmpeg subtitles фильтра (Windows: \ → \\, : → \:)
        ass_escaped = ass_path.replace("\\", "\\\\").replace(":", "\\:")

        vf = f"ass='{ass_escaped}'"

        from config.encoder import get_video_encoder
        enc = get_video_encoder()
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-vf", vf,
            *enc.args(),
            "-c:a", "copy",
            "-threads", "4",
            "-y",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"Ошибка сжигания субтитров:\n{result.stderr[-500:]}")
            logger.warning("Возвращаем видео без субтитров")
            return video_path

        logger.debug(f"Субтитры сожжены: {Path(output_path).name}")
        return output_path

    finally:
        Path(ass_path).unlink(missing_ok=True)
