"""
Замена оригинального звука фильма на фоновую музыку.

Зачем: TikTok ContentID распознаёт оригинальный звук фильма → страйк.
Решение: убираем оригинальный звук, накладываем нейтральную музыку.

Музыкальные файлы кладёшь в папку music/ (MP3, WAV, любой формат).
Файл выбирается детерминировано по хешу клипа — разные клипы получают разные треки.
"""

import hashlib
import subprocess
from pathlib import Path

from loguru import logger

from config.settings import settings


def _pick_music_file(clip_path: str) -> Path | None:
    """Выбирает музыкальный файл детерминировано по хешу клипа."""
    music_dir = settings.music_dir
    if not music_dir.exists():
        return None

    music_files = sorted(
        list(music_dir.glob("*.mp3")) +
        list(music_dir.glob("*.wav")) +
        list(music_dir.glob("*.m4a"))
    )
    if not music_files:
        return None

    h = int(hashlib.md5(clip_path.encode()).hexdigest(), 16)
    return music_files[h % len(music_files)]


def replace_audio(video_path: str, output_path: str) -> str:
    """
    Заменяет оригинальный звук на фоновую музыку.

    Args:
        video_path: входное видео
        output_path: выходное видео с новым звуком

    Returns:
        путь к обработанному видео (или оригинал если музыка не найдена)
    """
    music_file = _pick_music_file(video_path)

    if music_file is None:
        logger.debug(f"Папка music/ пуста или не существует, звук не заменяем")
        return video_path

    volume = settings.music_volume
    logger.debug(f"Заменяем звук: {music_file.name} (громкость: {volume})")

    # Накладываем музыку поверх видео:
    # - amix: смешиваем два аудиопотока
    # - duration=first: обрезаем музыку по длине видео
    # - weights: громкость оригинала=0, музыки=volume
    # Оригинальный звук убираем полностью (weight=0)
    af = f"[0:a]volume=0[orig];[1:a]volume={volume}[music];[orig][music]amix=inputs=2:duration=first"

    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-stream_loop", "-1",  # музыка зациклена если короче видео
        "-i", str(music_file),
        "-filter_complex", af,
        "-c:v", "copy",
        "-c:a", "aac", "-ar", "44100",
        "-map", "0:v",
        "-shortest",
        "-y",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"Ошибка замены звука:\n{result.stderr[-300:]}")
        logger.warning("Возвращаем видео с оригинальным звуком")
        return video_path

    logger.info(f"Звук заменён на: {music_file.name}")
    return output_path
