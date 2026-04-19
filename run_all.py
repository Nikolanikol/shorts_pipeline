"""
Пакетная обработка всех видео из папки inbox/.

Использование:
    run_all.bat                        # обработать всё в inbox/
    run_all.bat --publish              # обработать + авто-публикация в TikTok
    run_all.bat --platforms tiktok reels

Порядок работы:
    1. Сканирует папку inbox/ на наличие .mp4 / .mkv / .avi файлов
    2. Обрабатывает каждый файл через пайплайн по очереди
    3. После обработки ВСЕХ файлов — публикует в TikTok (если --publish)
    4. Обработанные файлы перемещает в inbox/done/
"""

import argparse
import shutil
import sys
from pathlib import Path

from loguru import logger

INBOX_DIR  = Path(__file__).parent / "inbox"
DONE_DIR   = INBOX_DIR / "done"
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov"}


def find_videos() -> list[Path]:
    """Возвращает список видео из inbox/ (не из done/)."""
    if not INBOX_DIR.exists():
        INBOX_DIR.mkdir(parents=True)
        logger.info(f"Создана папка: {INBOX_DIR}")
        return []

    videos = sorted([
        f for f in INBOX_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS
    ])
    return videos


def main():
    parser = argparse.ArgumentParser(description="Пакетная обработка видео из inbox/")
    parser.add_argument(
        "--platforms", nargs="+",
        default=["tiktok"],
        choices=["youtube_shorts", "tiktok", "tiktok_long", "reels"],
    )
    parser.add_argument("--no-subtitles", action="store_true")
    parser.add_argument("--publish", action="store_true",
                        help="Авто-публикация в TikTok после обработки всех видео")
    parser.add_argument("--skip-antidetect", action="store_true")
    args = parser.parse_args()

    # Настройка логов
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        level="INFO",
    )

    videos = find_videos()

    if not videos:
        logger.info(f"Папка inbox/ пуста. Положи видео в:")
        logger.info(f"  {INBOX_DIR}")
        return

    logger.info(f"Найдено {len(videos)} видео в inbox/:")
    for i, v in enumerate(videos, 1):
        logger.info(f"  {i}. {v.name}")
    logger.info("")

    # Обрабатываем каждое видео (без --publish чтобы не публиковать после каждого)
    from main import run_pipeline, _notify
    _notify(f"▶ Начинаю пакетную обработку: {len(videos)} видео")

    done_dir = DONE_DIR
    done_dir.mkdir(parents=True, exist_ok=True)

    processed = 0
    for i, video in enumerate(videos, 1):
        logger.info(f"")
        logger.info(f"━━━ [{i}/{len(videos)}] {video.name} ━━━")
        logger.info(f"")

        try:
            run_pipeline(
                video_path=str(video),
                platforms=args.platforms,
                skip_antidetect=args.skip_antidetect,
                no_subtitles=args.no_subtitles,
                auto_publish=False,  # публикуем один раз в конце
            )
            # Перемещаем в done/
            shutil.move(str(video), done_dir / video.name)
            logger.info(f"✓ Перемещено в inbox/done/: {video.name}")
            processed += 1

        except Exception as e:
            logger.error(f"❌ Ошибка при обработке {video.name}: {e}")
            logger.warning("Продолжаю со следующим видео...")

    logger.info(f"")
    logger.info(f"━━━ Обработка завершена: {processed}/{len(videos)} видео ━━━")
    _notify(f"✅ Обработано {processed}/{len(videos)} видео. Начинаю публикацию...")

    # Публикуем всё накопленное в TikTok
    if args.publish:
        logger.info("🚀 Запускаю планировщик публикации (2 поста/день)...")
        from publish.tiktok_upload import upload_scheduler
        upload_scheduler(delay_minutes=30, max_per_day=2)
    else:
        logger.info("💡 Для публикации запусти:")
        logger.info("   run_all.bat --publish")


if __name__ == "__main__":
    main()
