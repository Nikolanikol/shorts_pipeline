"""
Сжигание русских субтитров в видео через ffmpeg.

Стиль TikTok: жирный белый текст с чёрной обводкой, по центру снизу.
Субтитры берутся из транскрипта Whisper — тайм-коды уже есть.
"""

import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from models.schemas import Transcript, FinalShort


MAX_LINE_CHARS = 42  # максимум символов в строке субтитра для 1080px


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
    return "\n".join(lines)


def _fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _build_srt(transcript: Transcript, start_offset: float, end_offset: float, speed: float = 1.0) -> str:
    """
    Генерирует SRT-строку для отрезка видео [start_offset, end_offset].
    Тайм-коды пересчитываются относительно начала клипа с учётом speed.

    Если antidetect применил speed=1.038, видео стало короче:
    кадр, который был на секунде T, теперь на T/speed.
    """
    lines = []
    index = 1

    for seg in transcript.segments:
        if seg.end <= start_offset or seg.start >= end_offset:
            continue

        seg_start = max(seg.start - start_offset, 0.0)
        seg_end = min(seg.end - start_offset, end_offset - start_offset)

        if seg_end <= seg_start:
            continue

        # Корректируем тайм-код под изменённую скорость видео
        seg_start = seg_start / speed
        seg_end = seg_end / speed

        text = _wrap_text(seg.text.strip())

        lines.append(str(index))
        lines.append(f"{_fmt_time(seg_start)} --> {_fmt_time(seg_end)}")
        lines.append(text)
        lines.append("")
        index += 1

    return "\n".join(lines)


def burn_subtitles(
    video_path: str,
    output_path: str,
    transcript: Transcript,
    start_offset: float = 0.0,
    end_offset: float = None,
    speed: float = 1.0,
) -> str:
    """
    Сжигает субтитры в видео.

    Args:
        video_path: путь к входному видео
        output_path: путь к выходному видео
        transcript: транскрипт из Whisper
        start_offset: начало клипа в исходном видео (сек)
        end_offset: конец клипа в исходном видео (сек)

    Returns:
        путь к видео с субтитрами
    """
    if end_offset is None:
        end_offset = transcript.duration

    srt_content = _build_srt(transcript, start_offset, end_offset, speed=speed)

    if not srt_content.strip():
        logger.warning(f"Нет субтитров для отрезка {start_offset:.1f}—{end_offset:.1f}с, пропускаем")
        return video_path

    # Пишем SRT во временный файл
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".srt", encoding="utf-8", delete=False
    ) as f:
        f.write(srt_content)
        srt_path = f.name

    try:
        # Экранируем путь для ffmpeg subtitles фильтра (Windows: \ → \\)
        srt_escaped = srt_path.replace("\\", "\\\\").replace(":", "\\:")

        # Стиль TikTok: жирный белый текст, чёрная обводка, по центру снизу
        style = (
            "FontName=Arial,"
            "FontSize=26,"                # 26px — хорошо читается на 1080x1920
            "Bold=1,"
            "PrimaryColour=&H00FFFFFF,"   # белый текст
            "OutlineColour=&H00000000,"   # чёрная обводка
            "Outline=3,"                  # толщина обводки
            "Shadow=1,"
            "Alignment=2,"                # снизу по центру
            "MarginV=80"                  # отступ от низа
        )

        vf = f"subtitles='{srt_escaped}':force_style='{style}'"

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
        Path(srt_path).unlink(missing_ok=True)
