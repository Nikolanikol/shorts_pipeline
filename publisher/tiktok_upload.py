"""
Автозагрузка видео в TikTok через tiktok-uploader.

Использование:
    python -m publish.tiktok_upload --video video.mp4 --description "текст"
    python -m publish.tiktok_upload --queue   (загружает всё из очереди бота)
"""

import argparse
import json
import random
import shutil
import subprocess
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


RETRY_COUNT    = 3      # попыток на одно видео
RETRY_PAUSE    = 300    # секунд между попытками (5 минут)


def _check_cookies() -> bool:
    """
    Проверяет что файл cookies.txt существует и не пустой.
    Возвращает False с понятной инструкцией если что-то не так.
    """
    if not COOKIES_PATH.exists():
        logger.error(
            f"Файл cookies.txt не найден: {COOKIES_PATH}\n"
            "  Инструкция:\n"
            "    1. Установи расширение 'Get cookies.txt LOCALLY' в Chrome\n"
            "    2. Зайди на tiktok.com под своим аккаунтом\n"
            "    3. Нажми на расширение → Export → сохрани как cookies.txt\n"
            "    4. Положи файл в корень проекта (рядом с controller.py)"
        )
        return False

    if COOKIES_PATH.stat().st_size < 100:
        logger.error(f"Файл cookies.txt пустой или повреждён: {COOKIES_PATH}")
        return False

    # Проверяем что есть строка с tiktok.com
    content = COOKIES_PATH.read_text(encoding="utf-8", errors="ignore")
    if "tiktok" not in content.lower():
        logger.error("cookies.txt не содержит данных TikTok — экспортируй заново")
        return False

    logger.debug("cookies.txt — OK")
    return True


def _upload_single(video_path: str, description: str) -> bool:
    """
    Выполняет ОДНУ попытку загрузки в отдельном subprocess.

    Зачем subprocess: Playwright Sync API конфликтует с asyncio event loop
    который остаётся в памяти после первой загрузки. Каждый subprocess
    получает чистый Python-интерпретатор без остаточного event loop.

    Returns:
        True если загрузка прошла успешно (returncode == 0)
    """
    import os
    env = {**os.environ, "PYTHONUTF8": "1"}
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--_raw",                   # внутренняя команда: один upload без retry
            video_path,
            "--description", description,
        ],
        cwd=str(Path(__file__).parent.parent),
        env=env,
    )
    return result.returncode == 0


def upload_one(video_path: str, description: str = "", retries: int = RETRY_COUNT) -> bool:
    """
    Загружает одно видео в TikTok.

    Логика retry:
      - 3 попытки с паузой 5 мин между ними
      - Каждая попытка запускается в отдельном subprocess (обход asyncio конфликта)
      - Возвращает True только если загрузка прошла успешно

    Returns:
        True если видео успешно опубликовано
    """
    name = Path(video_path).name

    if not Path(video_path).exists():
        logger.error(f"Файл не найден: {video_path}")
        return False

    if not _check_cookies():
        return False

    for attempt in range(1, retries + 1):
        logger.info(f"📤 Загружаю: {name} (попытка {attempt}/{retries})")
        try:
            success = _upload_single(video_path, description)
            if success:
                logger.info(f"✅ Опубликовано: {name}")
                return True
            else:
                logger.error(f"❌ Загрузка не удалась (попытка {attempt}/{retries})")
        except Exception as e:
            logger.error(f"❌ Исключение (попытка {attempt}/{retries}): {e}")

        if attempt < retries:
            logger.info(f"⏳ Жду {RETRY_PAUSE // 60} мин перед повтором...")
            time.sleep(RETRY_PAUSE)

    logger.error(f"💀 Все {retries} попытки провалились: {name}")
    _notify(f"⚠️ Не удалось опубликовать после {retries} попыток:\n{name}")
    return False


SAFE_HOURS = range(9, 23)   # постим только с 9:00 до 23:00
MAX_PER_DAY = 2             # максимум постов в день на аккаунт


def _move_to_posted(video_path: str) -> None:
    """Перемещает опубликованное видео из ready/pending/ в ready/posted/."""
    try:
        from config.settings import settings
        src = Path(video_path)
        if not src.exists():
            return
        dest_dir = settings.posted_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Если файл с таким именем уже есть — добавляем timestamp
        dest = dest_dir / src.name
        if dest.exists():
            ts = datetime.now().strftime("%H%M%S")
            dest = dest_dir / f"{src.stem}_{ts}{src.suffix}"
        shutil.move(str(src), dest)
        logger.debug(f"📦 Перемещено в posted/: {dest.name}")
    except Exception as e:
        logger.warning(f"Не удалось переместить в posted/: {e}")


def _wait_until_safe_hour(force: bool = False) -> None:
    """Ждёт если сейчас ночное время (23:00 — 9:00). force=True — пропустить проверку."""
    if force:
        return
    now = datetime.now().hour
    if now not in SAFE_HOURS:
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


def upload_queue(delay_minutes: int = 30, max_per_day: int = MAX_PER_DAY, force: bool = False) -> None:
    """
    Загружает pending видео из очереди с защитой от бана:
    - Только в безопасные часы (9:00–23:00)  [отключается через force=True]
    - Максимум max_per_day постов в день
    - Случайная задержка ±30% между постами
    """
    from publisher.queue_db import init_db, get_next_pending, update_status

    init_db()
    count = 0

    while True:
        # Проверяем безопасное время
        _wait_until_safe_hour(force=force)

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
            _move_to_posted(item.video_path)
            count += 1
            logger.info(f"📊 Опубликовано сегодня: {count}/{max_per_day}")
            if count < max_per_day:
                _random_delay(delay_minutes)
        else:
            # СТОП — не пропускаем, чтобы сохранить хронологию.
            # Помечаем как failed и останавливаем очередь.
            update_status(item.id, "failed")
            logger.error(
                f"🛑 Публикация остановлена после ошибки: {Path(item.video_path).name}\n"
                f"   Хронология важна — не публикуем следующие части пока эта не выйдет.\n"
                f"   Исправь проблему и запусти: run.bat retry"
            )
            _notify(
                f"🛑 Публикация остановлена!\n"
                f"Не удалось опубликовать: {Path(item.video_path).name}\n"
                f"Запусти run.bat retry после исправления."
            )
            break

    logger.info(f"Готово! Загружено: {count} видео")


def upload_scheduler(delay_minutes: int = 30, max_per_day: int = MAX_PER_DAY, force: bool = False) -> None:
    """
    Запускается один раз — постит по max_per_day видео в день
    пока очередь не опустеет. Между днями ждёт до 9:00.
    force=True — отключает проверку времени (для тестов).
    """
    from publisher.queue_db import init_db, get_next_pending, update_status, get_stats

    init_db()
    total_posted = 0

    logger.info("🤖 Планировщик запущен. Работает пока очередь не опустеет.")
    logger.info(f"   Режим: {max_per_day} поста/день, пауза ~{delay_minutes} мин")
    if force:
        logger.info("   ⚡ --force: проверка времени отключена")

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
            _wait_until_safe_hour(force=force)

            item = get_next_pending()
            if not item:
                break

            success = upload_one(item.video_path, item.caption)

            if success:
                update_status(item.id, "sent")
                _move_to_posted(item.video_path)
                posted_today += 1
                total_posted += 1
                logger.info(f"📊 Сегодня: {posted_today}/{max_per_day} | Всего: {total_posted}")
                _notify(f"✅ Опубликовано {total_posted} видео\n{Path(item.video_path).name}")

                if posted_today < max_per_day:
                    _random_delay(delay_minutes)
            else:
                # СТОП — не пропускаем следующую часть, хронология важна.
                update_status(item.id, "failed")
                logger.error(
                    f"🛑 Публикация остановлена после ошибки: {Path(item.video_path).name}\n"
                    f"   Следующие части не будут опубликованы пока эта не выйдет.\n"
                    f"   Исправь проблему и запусти: run.bat retry"
                )
                _notify(
                    f"🛑 Публикация остановлена!\n"
                    f"Не удалось: {Path(item.video_path).name}\n"
                    f"Запусти run.bat retry после исправления."
                )
                return   # выходим полностью, не ждём следующего дня

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
    group.add_argument("--_raw",      help="[внутренняя] Одна попытка upload без retry (subprocess)")
    parser.add_argument("--description", default="", help="Описание видео")
    parser.add_argument("--delay", type=int, default=30, help="Минут между постами (default: 30)")
    parser.add_argument("--max-per-day", type=int, default=MAX_PER_DAY, help="Постов в день (default: 2)")
    args = parser.parse_args()

    if args._raw:
        # Внутренняя команда: сырая загрузка без retry, вызывается из _upload_single()
        from tiktok_uploader.upload import upload_video
        if not _check_cookies():
            sys.exit(1)
        try:
            failed = upload_video(
                args._raw,
                description=args.description,
                cookies=str(COOKIES_PATH),
                headless=False,
            )
            sys.exit(0 if not failed else 1)
        except Exception as e:
            logger.error(f"❌ upload_video исключение: {e}")
            sys.exit(1)
    elif args.video:
        upload_one(args.video, args.description)
    elif args.queue:
        upload_queue(delay_minutes=args.delay, max_per_day=args.max_per_day)
    elif args.scheduler:
        upload_scheduler(delay_minutes=args.delay, max_per_day=args.max_per_day)


if __name__ == "__main__":
    main()
