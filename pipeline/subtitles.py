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


def _build_srt(transcript: Transcript, start_offset: float, end_offset: float) -> str:
    """
    Генерирует SRT-строку для отрезка видео [start_offset, end_offset].
    Тайм-коды пересчитываются относительно начала клипа.
    """
    lines = []
    index = 1

    for seg in transcript.segments:
        # Берём только сегменты, которые попадают в окно клипа
        if seg.end <= start_offset or seg.start >= end_offset:
            continue

        seg_start = max(seg.start - start_offset, 0.0)
        seg_end = min(seg.end - start_offset, end_offset - start_offset)

        if seg_end <= seg_start:
            continue

        def fmt(seconds: float) -> str:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds % 1) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        lines.append(str(index))
        lines.append(f"{fmt(seg_start)} --> {fmt(seg_end)}")
        lines.append(seg.text.strip())
        lines.append("")
        index += 1

    return "\n".join(lines)


def burn_subtitles(
    video_path: str,
    output_path: str,
    transcript: Transcript,
    start_offset: float = 0.0,
    end_offset: float = None,
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

    srt_content = _build_srt(transcript, start_offset, end_offset)

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

        # Стиль: жирный белый текст, чёрная обводка, по центру снизу
        style = (
            "FontName=Arial,"
            "FontSize=18,"
            "Bold=1,"
            "PrimaryColour=&H00FFFFFF,"   # белый
            "OutlineColour=&H00000000,"   # чёрная обводка
            "Outline=2,"
            "Shadow=1,"
            "Alignment=2,"                # снизу по центру
            "MarginV=60"                  # отступ от низа
        )

        vf = f"subtitles='{srt_escaped}':force_style='{style}'"

        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "copy",
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
