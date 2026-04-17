"""
Расписание авто-публикации.

Добавляет видео в очередь и публикует по расписанию (1-2 поста в день).

Использование:
    # Добавить видео в очередь
    python -m publish.scheduler add --video-id abc123 --movie "Форсаж 9" --genre action

    # Запустить публикацию по расписанию (оставить работать в фоне)
    python -m publish.scheduler run --posts-per-day 2

    # Посмотреть очередь
    python -m publish.scheduler status
"""

import argparse
import json
import time
import random
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from config.settings import settings
from publish.manual import HASHTAGS


QUEUE_FILE = settings.checkpoint_dir / "publish_queue.json"


def _load_queue() -> list[dict]:
    if QUEUE_FILE.exists():
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    return []


def _save_queue(queue: list[dict]) -> None:
    settings.ensure_dirs()
    QUEUE_FILE.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


def add_to_queue(video_id: str, movie_name: str = "", genre: str = "default") -> None:
    """Добавляет все клипы из video_id в очередь публикации."""
    queue = _load_queue()

    video_dir = settings.output_dir / video_id / "tiktok"
    if not video_dir.exists():
        logger.error(f"Папка не найдена: {video_dir}")
        return

    videos = sorted(video_dir.glob("*.mp4"))
    added = 0
    for v in videos:
        # Проверяем что уже не в очереди
        if any(item["video_path"] == str(v) for item in queue):
            continue
        queue.append({
            "video_path": str(v),
            "video_id": video_id,
            "movie_name": movie_name,
            "genre": genre,
            "status": "pending",
            "added_at": datetime.now().isoformat(),
            "published_at": None,
        })
        added += 1

    _save_queue(queue)
    logger.info(f"Добавлено в очередь: {added} видео (всего в очереди: {len(queue)})")


def show_status() -> None:
    """Показывает состояние очереди."""
    queue = _load_queue()
    pending = [i for i in queue if i["status"] == "pending"]
    done = [i for i in queue if i["status"] == "published"]
    failed = [i for i in queue if i["status"] == "failed"]

    logger.info(f"\n📋 Очередь публикации:")
    logger.info(f"   Ожидает:    {len(pending)}")
    logger.info(f"   Опубликовано: {len(done)}")
    logger.info(f"   Ошибки:     {len(failed)}")
    logger.info(f"   Всего:      {len(queue)}")

    if pending:
        logger.info(f"\nСледующие к публикации:")
        for item in pending[:5]:
            name = Path(item["video_path"]).name
            movie = item.get("movie_name", "")
            logger.info(f"  • {name}" + (f" ({movie})" if movie else ""))


def run_scheduler(posts_per_day: int = 2) -> None:
    """
    Запускает публикацию по расписанию.
    posts_per_day: сколько постов в день (равномерно распределяет).
    """
    from publish.auto_tiktok import auto_publish

    interval_hours = 24 / posts_per_day
    interval_sec = int(interval_hours * 3600)
    # Добавляем случайный джиттер ±15 минут (выглядит как человек)
    jitter = 15 * 60

    logger.info(f"Расписание: {posts_per_day} поста/день, интервал ~{interval_hours:.1f}ч")
    logger.info("Нажми Ctrl+C для остановки\n")

    while True:
        queue = _load_queue()
        pending = [i for i in queue if i["status"] == "pending"]

        if not pending:
            logger.info("Очередь пуста. Жду пока появятся новые видео...")
            time.sleep(300)
            continue

        item = pending[0]
        video_path = item["video_path"]
        movie_name = item.get("movie_name", "")
        genre = item.get("genre", "default")

        logger.info(f"Публикуем: {Path(video_path).name}")

        try:
            from publish.auto_tiktok import _get_driver, _login, _upload_video
            from publish.manual import _generate_caption

            caption = _generate_caption(0, movie_name, genre, settings.tiktok_kmotors_url)
            driver = _get_driver()

            try:
                if _login(driver, settings.tiktok_account_email, settings.tiktok_account_password):
                    success = _upload_video(driver, video_path, caption)
                    item["status"] = "published" if success else "failed"
                    item["published_at"] = datetime.now().isoformat()
                else:
                    item["status"] = "failed"
            finally:
                driver.quit()

        except Exception as e:
            logger.error(f"Ошибка: {e}")
            item["status"] = "failed"

        _save_queue(queue)

        # Ждём до следующего поста
        wait = interval_sec + random.randint(-jitter, jitter)
        next_time = datetime.now() + timedelta(seconds=wait)
        logger.info(f"Следующий пост: {next_time.strftime('%H:%M:%S')} (через {wait // 60} мин)")
        time.sleep(wait)


def main():
    parser = argparse.ArgumentParser(description="Очередь и расписание публикаций TikTok")
    sub = parser.add_subparsers(dest="command")

    # add
    add_p = sub.add_parser("add", help="Добавить видео в очередь")
    add_p.add_argument("--video-id", required=True)
    add_p.add_argument("--movie", default="")
    add_p.add_argument("--genre", default="default", choices=list(HASHTAGS.keys()))

    # run
    run_p = sub.add_parser("run", help="Запустить публикацию по расписанию")
    run_p.add_argument("--posts-per-day", type=int, default=2)

    # status
    sub.add_parser("status", help="Показать очередь")

    args = parser.parse_args()

    if args.command == "add":
        add_to_queue(args.video_id, args.movie, args.genre)
    elif args.command == "run":
        run_scheduler(args.posts_per_day)
    elif args.command == "status":
        show_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
