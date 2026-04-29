"""
downloader/youtube.py — загрузка видео с YouTube через yt-dlp.

Использование как модуль:
    from downloader.youtube import download
    path = download("https://youtube.com/watch?v=...", output_dir="inbox")

Использование через CLI:
    python controller.py download https://youtube.com/watch?v=...
    python controller.py download https://youtube.com/watch?v=... --process

Зависимости:
    pip install yt-dlp

Обновление yt-dlp (YouTube меняет формат каждые 2-4 недели):
    pip install -U yt-dlp
"""

import subprocess
import sys
from pathlib import Path

from loguru import logger


def download(url: str, output_dir: str | Path = "inbox") -> Path:
    """
    Скачивает видео с YouTube в папку output_dir.

    Выбирает лучшее качество до 1080p (не 4K — экономим место).
    Мержит видео + аудио через ffmpeg.
    Возвращает путь к скачанному файлу.

    Args:
        url: ссылка на YouTube видео или плейлист
        output_dir: куда сохранять (по умолчанию inbox/)

    Returns:
        Path к скачанному файлу

    Raises:
        RuntimeError: если yt-dlp вернул ошибку
        FileNotFoundError: если файл не создался после скачивания
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Шаблон имени: Title [youtube_id].mp4
    output_template = str(output_dir / "%(title)s [%(id)s].%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        # Лучшее качество до 1080p: mp4 предпочтительно, иначе любое с мержем
        "--format", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "--merge-output-format", "mp4",
        "--output", output_template,
        # Не скачивать если файл уже есть
        "--no-overwrites",
        # Прогресс в лог
        "--newline",
        # Встраиваем субтитры не нужны — у нас свой Whisper
        "--no-write-subs",
        # Убираем лишние файлы
        "--no-write-thumbnail",
        "--no-write-info-json",
        url,
    ]

    logger.info(f"Скачиваю: {url}")
    logger.debug(f"Папка: {output_dir.resolve()}")

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")

    if result.returncode != 0:
        error_msg = result.stderr[-500:] if result.stderr else result.stdout[-500:]
        logger.error(f"yt-dlp ошибка:\n{error_msg}")
        raise RuntimeError(f"Не удалось скачать видео: {url}\n{error_msg}")

    # Находим скачанный файл — ищем самый свежий mp4 в папке
    mp4_files = sorted(output_dir.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not mp4_files:
        raise FileNotFoundError(f"Файл не найден в {output_dir} после скачивания")

    downloaded = mp4_files[0]
    size_mb = downloaded.stat().st_size / 1024 / 1024
    logger.info(f"Скачано: {downloaded.name} ({size_mb:.1f} MB)")

    return downloaded


def get_info(url: str) -> dict:
    """
    Возвращает метаданные видео без скачивания.
    Полезно для предпросмотра (название, длительность, автор).

    Args:
        url: ссылка на YouTube видео

    Returns:
        dict с полями: title, duration, uploader, view_count, description
    """
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--dump-json",
        "--no-playlist",
        url,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")

    if result.returncode != 0:
        logger.warning(f"Не удалось получить инфо о видео: {url}")
        return {}

    import json
    try:
        data = json.loads(result.stdout)
        return {
            "title": data.get("title", ""),
            "duration": data.get("duration", 0),
            "uploader": data.get("uploader", ""),
            "view_count": data.get("view_count", 0),
            "description": (data.get("description") or "")[:500],
        }
    except json.JSONDecodeError:
        return {}
