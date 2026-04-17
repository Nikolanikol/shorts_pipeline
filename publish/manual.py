"""
Ручной режим публикации.

Создаёт папку publish/{video_id}/ с готовыми видео и текстовым файлом
для каждого клипа: описание, хэштеги, ссылка на kmotors.

Использование:
    python -m publish.manual --video-id abc123
    python -m publish.manual --video-id abc123 --movie "Форсаж 9" --genre action
"""

import argparse
import json
import random
import shutil
from pathlib import Path

from loguru import logger

from config.settings import settings


# Хэштеги по жанрам
HASHTAGS = {
    "action":  ["#боевик", "#экшен", "#кино", "#фильм", "#сцена", "#голливуд"],
    "comedy":  ["#комедия", "#смешно", "#юмор", "#кино", "#фильм"],
    "drama":   ["#драма", "#кино", "#фильм", "#сцена", "#кинематограф"],
    "series":  ["#сериал", "#кино", "#сцена", "#бестсериал", "#топсериал"],
    "default": ["#кино", "#фильм", "#сцена", "#shorts", "#тикток", "#кинематограф"],
}

TIKTOK_HASHTAGS = ["#tiktok", "#viral", "#fyp", "#foryou", "#кино2024"]


def _generate_caption(
    clip_index: int,
    movie_name: str = "",
    genre: str = "default",
    kmotors_url: str = "",
) -> str:
    """Генерирует описание поста для TikTok."""
    genre_tags = HASHTAGS.get(genre, HASHTAGS["default"])
    # Берём 4 рандомных из жанровых + 2 TikTok-тега
    selected = random.sample(genre_tags, min(4, len(genre_tags)))
    selected += random.sample(TIKTOK_HASHTAGS, 2)
    tags = " ".join(selected)

    movie_part = f" | {movie_name}" if movie_name else ""
    kmotors_part = f"\n\n🚗 Крутые авто: {kmotors_url}" if kmotors_url else ""

    return f"Лучшие сцены{movie_part} 🎬 часть {clip_index + 1}\n\n{tags}{kmotors_part}"


def prepare_manual_publish(
    video_id: str,
    movie_name: str = "",
    genre: str = "default",
    platform: str = "tiktok",
) -> Path:
    """
    Собирает папку для ручной публикации.

    Returns:
        путь к папке с готовыми материалами
    """
    # Ищем готовые шортсы
    source_dir = settings.output_dir / video_id / platform
    if not source_dir.exists():
        raise FileNotFoundError(
            f"Папка с готовыми шортсами не найдена: {source_dir}\n"
            f"Сначала запусти: python main.py video.mp4 --platforms {platform}"
        )

    video_files = sorted(source_dir.glob("*.mp4"))
    if not video_files:
        raise FileNotFoundError(f"Нет MP4-файлов в {source_dir}")

    # Создаём папку для публикации
    publish_dir = settings.output_dir / video_id / "publish_manual"
    publish_dir.mkdir(parents=True, exist_ok=True)

    kmotors_url = settings.tiktok_kmotors_url
    manifest = []

    for i, video_file in enumerate(video_files):
        # Копируем видео с простым именем
        dest_video = publish_dir / f"post_{i + 1:02d}.mp4"
        shutil.copy2(video_file, dest_video)

        # Генерируем описание
        caption = _generate_caption(i, movie_name, genre, kmotors_url)
        caption_file = publish_dir / f"post_{i + 1:02d}_caption.txt"
        caption_file.write_text(caption, encoding="utf-8")

        manifest.append({
            "index": i + 1,
            "video": dest_video.name,
            "caption_file": caption_file.name,
            "caption_preview": caption[:80] + "...",
        })

        logger.info(f"  [{i + 1}/{len(video_files)}] {dest_video.name}")

    # Сохраняем манифест
    manifest_path = publish_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(f"\n✅ Готово! Папка для публикации: {publish_dir}")
    logger.info(f"   Видео: {len(video_files)} штук")
    logger.info(f"   Ссылка kmotors в каждом посте: {kmotors_url}")

    return publish_dir


def main():
    parser = argparse.ArgumentParser(description="Подготовить материалы для ручной публикации")
    parser.add_argument("--video-id", required=True, help="ID видео (из имени папки в output/)")
    parser.add_argument("--movie", default="", help="Название фильма/сериала")
    parser.add_argument(
        "--genre", default="default",
        choices=list(HASHTAGS.keys()),
        help="Жанр для хэштегов"
    )
    parser.add_argument(
        "--platform", default="tiktok",
        choices=["tiktok", "youtube_shorts", "reels"],
    )
    args = parser.parse_args()

    prepare_manual_publish(
        video_id=args.video_id,
        movie_name=args.movie,
        genre=args.genre,
        platform=args.platform,
    )


if __name__ == "__main__":
    main()
