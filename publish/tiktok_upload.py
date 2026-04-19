"""
Автозагрузка видео в TikTok через tiktok-uploader.

Использование:
    python -m publish.tiktok_upload --video video.mp4 --description "текст"
    python -m publish.tiktok_upload --queue   (загружает всё из очереди бота)
"""

import argparse
import json
import random
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv(Path(__file__).parent.parent / ".env")

COOKIES_PATH = Path(__file__).parent.parent / "cookies.txt"


def _notify(text: str) -> None:
    """Отправляет уведомление в Telegram через urllib (без asyncio)."""
    try:
        import os
        token    = os.getenv("TELEGRAM_TOKEN")
        owner_id = os.getenv("TELEGRAM_OWNER_ID")
        if not token or not owner_id:
            return
        data = json.dumps({"chat_id": int(owner_id), "text": text}).encode()
        req  = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def upload_one(video_path: str, description: str = "", retries: int = 2) -> bool:
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

    name = Path(video_path).name
    for attempt in range(1, retries + 1):
        logger.info(f"Загружаю: {name} (попытка {attempt}/{retries})")
        try:
            results = upload_video(
                video_path,
                description=description,
                cookies=str(COOKIES_PATH),
            )
            # upload_video возвращает список результатов
            # проверяем что нет ошибок
            if results and hasattr(results[0], 'ok') and not results[0].ok:
                raise RuntimeError(f"upload_video вернул ошибку: {results[0]}")

            logger.info(f"✅ Опубликовано: {name}")
            return True

        except Exception as e:
            logger.error(f"❌ Ошибка (попытка {attempt}): {e}")
            if attempt < retries:
                wait = 30 * attempt
                logger.info(f"Жду {wait} сек перед повтором...")
                time.sleep(wait)

    return False


SAFE_HOURS = range(9, 23)   # постим только с 9:00 до 23:00
MAX_PER_DAY = 2             # максимум постов в день на аккаунт


def _wait_until_safe_hour() -> None:
    """Ждёт если сейчас ночное время (23:00 — 9:00)."""
    now = datetime.now().hour
    if now not in SAFE_HOURS:
        # Считаем сколько ждать до 9:00
        if now >= 23:
            wait_hours = 24 - now + 9
        else:
            wait_hours = 9 - now
        logger.info(f"🌙 Сейчас {now}:00 — ночное время. Жду до 9:00 ({wait_hours} ч)...")
        time.sleep(wait_hours * 3600)


def _random_delay(base_minutes: int) -> None:
    """
    Случайная задержка ±30% от базового значения.
    base=30 мин → ждём от 21 до 39 мин — выглядит как человек.
    """
    low  = int(base_minutes * 0.7)
    high = int(base_minutes * 1.3)
    wait = random.randint(low, high)
    logger.info(f"⏳ Жду {wait} мин до следующего поста...")
    time.sleep(wait * 60)


def upload_queue(delay_minutes: int = 30, max_per_day: int = MAX_PER_DAY) -> None:
    """
    Загружает pending видео из очереди с защитой от бана:
    - Только в безопасные часы (9:00–23:00)
    - Максимум max_per_day постов в день
    - Случайная задержка ±30% между постами
    """
    from bot.queue_db import init_db, get_next_pending, update_status

    init_db()
    count = 0

    while True:
        # Проверяем безопасное время
        _wait_until_safe_hour()

        # Лимит в день
        if count >= max_per_day:
            logger.info(f"🛑 Лимит {max_per_day} постов/день достигнут. Запусти завтра.")
            break

        item = get_next_pending()
        if not item:
            logger.info("✅ Очередь пуста — всё загружено!")
            break

        success = upload_one(item.video_path, item.caption)

        if success:
            update_status(item.id, "sent")
            count += 1
            logger.info(f"📊 Опубликовано сегодня: {count}/{max_per_day}")
            if count < max_per_day:
                _random_delay(delay_minutes)
        else:
            update_status(item.id, "failed")
            logger.warning("Пропускаю видео с ошибкой...")
            time.sleep(60)

    logger.info(f"Готово! Загружено: {count} видео")


def upload_scheduler(delay_minutes: int = 30, max_per_day: int = MAX_PER_DAY) -> None:
    """
    Запускается один раз — постит по max_per_day видео в день
    пока очередь не опустеет. Между днями ждёт до 9:00.
    """
    from bot.queue_db import init_db, get_next_pending, update_status, get_stats

    init_db()
    total_posted = 0

    logger.info("🤖 Планировщик запущен. Работает пока очередь не опустеет.")
    logger.info(f"   Режим: {max_per_day} поста/день, пауза ~{delay_minutes} мин")

    while True:
        stats = get_stats()
        if stats["pending"] == 0:
            logger.info(f"🏁 Очередь пуста! Всего опубликовано: {total_posted}")
            _notify(f"🏁 Публикация завершена! Всего опубликовано: {total_posted} видео")
            break

        logger.info(f"📅 Новый день. В очереди: {stats['pending']} видео")
        posted_today = 0

        # Постим до лимита в день
        while posted_today < max_per_day:
            _wait_until_safe_hour()

            item = get_next_pending()
            if not item:
                break

            success = upload_one(item.video_path, item.caption)

            if success:
                update_status(item.id, "sent")
                posted_today += 1
                total_posted += 1
                logger.info(f"📊 Сегодня: {posted_today}/{max_per_day} | Всего: {total_posted}")
                _notify(f"✅ Опубликовано {total_posted} видео\n{Path(item.video_path).name}")

                if posted_today < max_per_day:
                    _random_delay(delay_minutes)
            else:
                update_status(item.id, "failed")
                logger.warning("Ошибка — пропускаю...")
                time.sleep(60)

        # Ждём следующий день (до 9:00 следующего дня)
        if get_stats()["pending"] > 0:
            now = datetime.now()
            wait_hours = 24 - now.hour + 9
            logger.info(f"💤 Лимит дня выполнен ({posted_today} постов). Следующий пост через ~{wait_hours} ч")
            _notify(f"💤 Лимит дня: {posted_today} постов. Продолжу завтра в 9:00")
            time.sleep(wait_hours * 3600)


def main():
    parser = argparse.ArgumentParser(description="TikTok автозагрузка")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--video",     help="Путь к видео файлу")
    group.add_argument("--queue",     action="store_true", help="Загрузить сегодняшний лимит из очереди")
    group.add_argument("--scheduler", action="store_true", help="Запустить планировщик (постит каждый день)")
    parser.add_argument("--description", default="", help="Описание видео")
    parser.add_argument("--delay", type=int, default=30, help="Минут между постами (default: 30)")
    parser.add_argument("--max-per-day", type=int, default=MAX_PER_DAY, help="Постов в день (default: 2)")
    args = parser.parse_args()

    if args.video:
        upload_one(args.video, args.description)
    elif args.queue:
        upload_queue(delay_minutes=args.delay, max_per_day=args.max_per_day)
    elif args.scheduler:
        upload_scheduler(delay_minutes=args.delay, max_per_day=args.max_per_day)


if __name__ == "__main__":
    main()
