"""
Автозагрузка видео в TikTok через tiktok-uploader.

Использование:
    python -m publish.tiktok_upload --video video.mp4 --description "текст"
    python -m publish.tiktok_upload --queue   (загружает всё из очереди бота)
"""

import argparse
import sys
import time
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))


COOKIES_PATH = Path(__file__).parent.parent / "cookies.txt"


def upload_one(video_path: str, description: str = "") -> bool:
    """
    Загружает одно видео в TikTok.
    Возвращает True если успешно.
    """
    from tiktok_uploader.upload import upload_video

    if not Path(video_path).exists():
        logger.error(f"Файл не найден: {video_path}")
        return False

    if not COOKIES_PATH.exists():
        logger.error(
            f"Файл cookies не найден: {COOKIES_PATH}\n"
            "Инструкция:\n"
            "  1. Установи расширение 'Get cookies.txt LOCALLY' в Chrome\n"
            "  2. Зайди на tiktok.com под своим аккаунтом\n"
            "  3. Нажми на расширение → Export → сохрани как cookies.txt\n"
            "  4. Положи файл в папку проекта"
        )
        return False

    logger.info(f"Загружаю: {Path(video_path).name}")
    try:
        upload_video(
            video_path,
            description=description,
            cookies=str(COOKIES_PATH),
        )
        logger.info(f"✅ Опубликовано: {Path(video_path).name}")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка загрузки: {e}")
        return False


def upload_queue(delay_minutes: int = 30) -> None:
    """
    Загружает все pending видео из очереди Telegram бота.
    Между постами ждёт delay_minutes минут.
    """
    from bot.queue_db import init_db, get_next_pending, update_status

    init_db()
    count = 0

    while True:
        item = get_next_pending()
        if not item:
            logger.info("Очередь пуста — всё загружено!")
            break

        success = upload_one(item.video_path, item.caption)

        if success:
            update_status(item.id, "sent")
            count += 1
            logger.info(f"Загружено {count} видео. Жду {delay_minutes} мин до следующего...")
            time.sleep(delay_minutes * 60)
        else:
            update_status(item.id, "failed")
            logger.warning("Пропускаю видео с ошибкой, перехожу к следующему...")
            time.sleep(60)

    logger.info(f"Готово! Загружено: {count} видео")


def main():
    parser = argparse.ArgumentParser(description="TikTok автозагрузка")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--video",  help="Путь к видео файлу")
    group.add_argument("--queue",  action="store_true", help="Загрузить всё из очереди бота")
    parser.add_argument("--description", default="", help="Описание видео")
    parser.add_argument("--delay", type=int, default=30, help="Минут между постами (default: 30)")
    args = parser.parse_args()

    if args.video:
        upload_one(args.video, args.description)
    elif args.queue:
        upload_queue(delay_minutes=args.delay)


if __name__ == "__main__":
    main()
