"""
controller.py — главная точка входа пайплайна.

Использование:
    python controller.py download https://youtube.com/watch?v=...          # скачать с YouTube
    python controller.py download https://... --process                    # скачать + обработать
    python controller.py process video.mp4                                 # обработать файл
    python controller.py process --inbox                                   # обработать папку inbox/
    python controller.py publish                                           # опубликовать в TikTok
    python controller.py start                                             # inbox/ + публикация
    python controller.py status
    python controller.py logs [--n 20]

Флаги:
    --platforms youtube_shorts tiktok reels
    --selector auto|groq|none   (auto=Groq если есть ключ, none=нарезка по тишине)
    --skip-antidetect
    --no-subtitles
    --delay 30          (минут между постами, default 30)
    --max-per-day 2     (постов в день, default 2)
"""

# Добавляем CUDA DLL пути ДО любых других импортов
import os as _os
import sys as _sys
from pathlib import Path as _Path

_nvidia_dirs = []
for _sp in _sys.path:
    if "site-packages" in _sp:
        for _pkg in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_runtime/bin"):
            _dll = _Path(_sp) / _pkg
            if _dll.is_dir():
                _nvidia_dirs.append(str(_dll))
                try:
                    _os.add_dll_directory(str(_dll))
                except Exception:
                    pass

# Добавляем в PATH — ctranslate2 ищет DLL через PATH, не через add_dll_directory
if _nvidia_dirs:
    _os.environ["PATH"] = _os.pathsep.join(_nvidia_dirs) + _os.pathsep + _os.environ.get("PATH", "")

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).parent / ".env")

from config.settings import settings

BASE_DIR   = Path(__file__).parent
INBOX_DIR  = BASE_DIR / "inbox"
DONE_DIR   = INBOX_DIR / "done"
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov"}


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def _check_dependencies() -> None:
    """Проверяет наличие ffmpeg и ffprobe перед запуском."""
    for tool in ("ffmpeg", "ffprobe"):
        try:
            result = subprocess.run([tool, "-version"], capture_output=True, timeout=10)
            if result.returncode != 0:
                raise FileNotFoundError
        except (FileNotFoundError, OSError):
            logger.error(
                f"'{tool}' не найден. Установи ffmpeg: https://ffmpeg.org/download.html"
            )
            sys.exit(1)
    logger.debug("ffmpeg / ffprobe — OK")


def _notify(text: str) -> None:
    """Отправляет уведомление в Telegram через urllib (без asyncio)."""
    try:
        import os, urllib.request
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


def _video_id(video_path: Path) -> str:
    """
    ID видео = очищенное имя файла без расширения.
    Запрещённые символы заменяются на "_".
    Обрезается до 80 UTF-8 байт.
    """
    stem = video_path.stem
    for ch in r'\/:*?"<>|':
        stem = stem.replace(ch, "_")
    return stem.encode("utf-8")[:80].decode("utf-8", errors="ignore")


def _setup_logging() -> None:
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        level="INFO",
    )
    settings.ensure_dirs()
    logger.add(
        settings.checkpoint_dir / "pipeline.log",
        rotation="10 MB",
        level="DEBUG",
    )


# ---------------------------------------------------------------------------
# Команда: process
# ---------------------------------------------------------------------------

def process_video(
    video_path: Path,
    platforms: list[str] = None,
    skip_antidetect: bool = False,
    no_subtitles: bool = False,
    selector: str = "auto",
) -> int:
    """
    Запускает полный пайплайн для одного видео.
    Возвращает количество добавленных в очередь шортсов.

    Args:
        selector: "auto" — Groq если ключ есть, иначе Chunker;
                  "groq"  — только Groq (ошибка если нет ключа);
                  "none"  — всегда Chunker (нарезка по тишине)
    """
    from models.schemas import PipelineState, Transcript, RawClip, ProcessedClip, SceneSelection
    from processor.transcriber import Transcriber
    from processor.chunker import Chunker
    from processor.scene_selector import SceneSelector
    from processor.antidetect import AntiDetect
    from processor.formatter import Formatter
    from processor.captions import make_caption
    from publisher.queue_db import init_db, add_video

    if platforms is None:
        platforms = ["tiktok"]

    if not video_path.exists():
        logger.error(f"Файл не найден: {video_path}")
        return 0

    settings.ensure_dirs()
    video_id = _video_id(video_path)

    # Checkpoint
    checkpoint_path = settings.checkpoint_dir / f"{video_id}_state.json"
    if checkpoint_path.exists():
        state = PipelineState.model_validate_json(checkpoint_path.read_text())
        logger.info(f"Найден checkpoint: {checkpoint_path.name}")
    else:
        state = PipelineState(video_id=video_id, original_path=str(video_path))

    def save_state():
        checkpoint_path.write_text(state.model_dump_json(indent=2))

    # Определяем эффективный режим нарезки
    effective_selector = selector
    if selector == "auto":
        from config.settings import settings as _s
        effective_selector = "groq" if _s.groq_api_key else "none"

    logger.info("")
    logger.info("▶  Shorts Pipeline")
    logger.info(f"   Файл:      {video_path.name}")
    logger.info(f"   ID:        {video_id}")
    logger.info(f"   Платформы: {', '.join(platforms)}")
    logger.info(f"   Нарезка:   {effective_selector}")
    _notify(f"▶ Начал обработку: {video_path.name}")
    logger.info("")

    # ------------------------------------------------------------------
    # Шаг 1: Транскрибация
    # ------------------------------------------------------------------
    transcript_path = settings.checkpoint_dir / f"{video_id}_transcript.json"
    transcript = None

    if state.transcript_done and transcript_path.exists():
        transcript = Transcript.model_validate_json(transcript_path.read_text())
        logger.info(f"✅ Шаг 1: Транскрибация (из checkpoint, {len(transcript.segments)} сегментов)")
    else:
        logger.info("📝 Шаг 1: Транскрибация...")
        transcript = Transcriber().process(str(video_path), video_id=video_id)
        state.transcript_done = True
        state.normalized_path = transcript.video_path
        save_state()
        logger.info(f"   ✓ {len(transcript.segments)} сегментов, {transcript.duration / 60:.1f} мин")

    # ------------------------------------------------------------------
    # Шаг 2: Выбор сцен через LLM (если включён)
    # ------------------------------------------------------------------
    scenes_checkpoint = settings.checkpoint_dir / f"{video_id}_scenes.json"
    scene_selection = None

    if effective_selector != "none":
        if state.scenes_done and scenes_checkpoint.exists():
            scene_selection = SceneSelection.model_validate_json(scenes_checkpoint.read_text())
            logger.info(f"✅ Шаг 2: Выбор сцен (из checkpoint, {len(scene_selection.scenes)} сцен)")
        else:
            logger.info(f"🎯 Шаг 2: Выбор интересных моментов ({effective_selector})...")
            scene_selection = SceneSelector().process(transcript)
            if scene_selection:
                scenes_checkpoint.write_text(scene_selection.model_dump_json(indent=2))
                state.scenes_done = True
                save_state()
                logger.info(f"   ✓ {len(scene_selection.scenes)} сцен выбрано")
            else:
                logger.warning("   ⚠ SceneSelector вернул None — переключаемся на нарезку по тишине")
    else:
        logger.info("⏭️  Шаг 2: Выбор сцен пропущен (selector=none)")

    # ------------------------------------------------------------------
    # Шаг 3: Нарезка на клипы
    # ------------------------------------------------------------------
    clips_checkpoint = settings.checkpoint_dir / f"{video_id}_clips.json"

    if state.cuts_done and clips_checkpoint.exists():
        raw_clips = [RawClip(**c) for c in json.loads(clips_checkpoint.read_text())]
        logger.info(f"✅ Шаг 3: Нарезка (из checkpoint, {len(raw_clips)} клипов)")
    else:
        chunker = Chunker()
        if scene_selection and scene_selection.scenes:
            logger.info(f"✂️  Шаг 3: Нарезка по сценам ({len(scene_selection.scenes)} моментов)...")
            raw_clips = chunker.process_scenes(transcript, scene_selection)
        else:
            logger.info("✂️  Шаг 3: Нарезка по тишине...")
            raw_clips = chunker.process(transcript)
        clips_checkpoint.write_text(json.dumps([c.model_dump() for c in raw_clips], indent=2))
        state.cuts_done = True
        save_state()
        logger.info(f"   ✓ {len(raw_clips)} клипов")

    # ------------------------------------------------------------------
    # Шаг 4: Анти-бан
    # ------------------------------------------------------------------
    antidetect_checkpoint = settings.checkpoint_dir / f"{video_id}_antidetect.json"

    if skip_antidetect:
        logger.info("⏭️  Шаг 4: Анти-бан пропущен")
        processed_clips = [
            ProcessedClip(
                video_id=c.video_id,
                scene_index=c.scene_index,
                raw_clip_path=c.clip_path,
                processed_clip_path=c.clip_path,
                filters_applied=[],
                start=c.start,
                end=c.end,
            )
            for c in raw_clips
        ]
    elif state.antidetect_done and antidetect_checkpoint.exists():
        processed_clips = [ProcessedClip(**c) for c in json.loads(antidetect_checkpoint.read_text())]
        logger.info(f"✅ Шаг 4: Анти-бан (из checkpoint, {len(processed_clips)} клипов)")
    else:
        logger.info("🛡️  Шаг 4: Анти-бан обработка...")
        processed_clips = AntiDetect().process(raw_clips)
        antidetect_checkpoint.write_text(json.dumps([c.model_dump() for c in processed_clips], indent=2))
        state.antidetect_done = True
        save_state()
        logger.info(f"   ✓ {len(processed_clips)} клипов обработано")

    # ------------------------------------------------------------------
    # Шаг 5: Форматирование
    # ------------------------------------------------------------------
    if state.format_done:
        finals = state.final_shorts
        logger.info(f"✅ Шаг 5: Форматирование (из checkpoint, {len(finals)} шортсов)")
    else:
        logger.info(f"📱 Шаг 5: Форматирование → {', '.join(platforms)}...")
        finals = Formatter().process(
            clips=processed_clips,
            platforms=platforms,
            transcript=None if no_subtitles else transcript,
        )
        state.format_done = True
        state.final_shorts = finals
        save_state()
        logger.info(f"   ✓ {len(finals)} шортсов готово")


    # ------------------------------------------------------------------
    # Итог
    # ------------------------------------------------------------------
    logger.info("")
    logger.info("🏁 Готово!")
    logger.info(f"   Шортсы: {settings.output_dir / video_id}")
    logger.info("")
    for final in finals:
        out = Path(final.output_path)
        size_mb = out.stat().st_size / 1024 / 1024 if out.exists() else 0
        logger.info(f"   📹 {out.parent.name}/{out.name}  ({final.duration:.0f}с, {size_mb:.1f} MB)")

    # ------------------------------------------------------------------
    # Добавляем в очередь с нормальными подписями
    # ------------------------------------------------------------------
    init_db()
    added = 0
    for final in finals:
        out = Path(final.output_path)
        if out.exists():
            part_number = final.scene_index + 1
            caption = make_caption(video_id, part_number)
            add_video(str(out), caption=caption)
            added += 1
            logger.info(f"   📬 В очереди: {Path(final.output_path).name}")
            logger.debug(f"      Подпись: {caption[:60]}...")

    if added:
        _notify(f"✅ Готово! {added} видео в очереди.\n{video_path.name}")

    return added


def cmd_download(args) -> None:
    """Скачивает видео с YouTube в папку inbox/."""
    _setup_logging()
    from downloader.youtube import download, get_info

    url = args.url

    # Показываем инфо о видео перед скачиванием
    logger.info(f"Получаю инфо о видео...")
    info = get_info(url)
    if info:
        duration_min = info.get("duration", 0) / 60
        logger.info(f"   Название:  {info.get('title', '?')}")
        logger.info(f"   Канал:     {info.get('uploader', '?')}")
        logger.info(f"   Длина:     {duration_min:.1f} мин")

    try:
        video_path = download(url, output_dir=INBOX_DIR)
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        return

    if args.process:
        logger.info(f"")
        logger.info(f"▶ Запускаю обработку скачанного видео...")
        process_video(
            video_path=video_path,
            platforms=args.platforms,
            skip_antidetect=args.skip_antidetect,
            no_subtitles=args.no_subtitles,
            selector=args.selector,
        )


def cmd_process(args) -> None:
    """Обрабатывает одно видео или всю папку inbox/."""
    _setup_logging()
    _check_dependencies()

    if args.inbox:
        # Пакетная обработка inbox/
        INBOX_DIR.mkdir(parents=True, exist_ok=True)
        videos = sorted([
            f for f in INBOX_DIR.iterdir()
            if f.is_file() and f.suffix.lower() in VIDEO_EXTS
        ])
        if not videos:
            logger.info(f"Папка inbox/ пуста. Положи видео в: {INBOX_DIR}")
            return

        logger.info(f"Найдено {len(videos)} видео в inbox/:")
        for i, v in enumerate(videos, 1):
            logger.info(f"  {i}. {v.name}")
        _notify(f"▶ Пакетная обработка: {len(videos)} видео")

        DONE_DIR.mkdir(parents=True, exist_ok=True)
        processed = 0
        for i, video in enumerate(videos, 1):
            logger.info(f"")
            logger.info(f"━━━ [{i}/{len(videos)}] {video.name} ━━━")
            logger.info(f"")
            try:
                process_video(
                    video_path=video,
                    platforms=args.platforms,
                    skip_antidetect=args.skip_antidetect,
                    no_subtitles=args.no_subtitles,
                    selector=getattr(args, "selector", "auto"),
                )
                shutil.move(str(video), DONE_DIR / video.name)
                logger.info(f"✓ Перемещено в inbox/done/: {video.name}")
                processed += 1
            except Exception as e:
                logger.error(f"❌ Ошибка при обработке {video.name}: {e}")
                logger.warning("Продолжаю со следующим видео...")

        logger.info(f"")
        logger.info(f"━━━ Обработка завершена: {processed}/{len(videos)} ━━━")
        _notify(f"✅ Обработано {processed}/{len(videos)} видео")

    else:
        # Одно видео
        video = Path(args.video)
        process_video(
            video_path=video,
            platforms=args.platforms,
            skip_antidetect=args.skip_antidetect,
            no_subtitles=args.no_subtitles,
            selector=getattr(args, "selector", "auto"),
        )


def cmd_publish(args) -> None:
    """Запускает планировщик публикации TikTok."""
    _setup_logging()
    from publisher.tiktok_upload import upload_scheduler
    upload_scheduler(
        delay_minutes=args.delay,
        max_per_day=args.max_per_day,
        force=getattr(args, "force", False),
    )


def cmd_start(args) -> None:
    """Обрабатывает inbox/ и затем запускает публикацию."""
    _setup_logging()
    _check_dependencies()

    # process --inbox
    args.inbox = True
    cmd_process(args)

    # publish
    logger.info("")
    logger.info("🚀 Запускаю планировщик публикации...")
    _notify("🚀 Начинаю публикацию в TikTok...")
    from publisher.tiktok_upload import upload_scheduler
    upload_scheduler(
        delay_minutes=args.delay,
        max_per_day=args.max_per_day,
    )


def cmd_retry(args) -> None:
    """Сбрасывает failed видео обратно в pending и сразу запускает публикацию."""
    _setup_logging()
    from publisher.queue_db import init_db, reset_failed, get_stats
    init_db()

    reset_count, skipped_count = reset_failed()

    if reset_count > 0:
        print(f"  🔄 Сброшено в pending: {reset_count} видео")
    if skipped_count > 0:
        print(f"  ⏭  Пропущено (файл не найден / уже опубликован): {skipped_count} видео")

    stats = get_stats()
    pending = stats['pending']

    if pending == 0:
        print("  ✅ Очередь пуста — нечего публиковать.")
        return

    print(f"  ⏳ В очереди: {pending} видео — запускаю публикацию...")
    print()

    from publisher.tiktok_upload import upload_scheduler
    upload_scheduler(
        delay_minutes=getattr(args, "delay", 30),
        max_per_day=getattr(args, "max_per_day", 2),
        force=getattr(args, "force", False),
    )


def cmd_status(_args) -> None:
    """Показывает статус очереди + историю публикаций по дням."""
    _setup_logging()
    from publisher.queue_db import init_db, get_stats, get_db
    init_db()

    stats = get_stats()
    total = sum(stats.values())

    print()
    print("  📊 СТАТУС ОЧЕРЕДИ")
    print("  " + "─" * 36)
    print(f"  ⏳ Ожидают публикации : {stats['pending']}")
    print(f"  ✅ Опубликовано        : {stats['sent']}")
    print(f"  ❌ Ошибки              : {stats['failed']}")
    print(f"  ⏭  Пропущено           : {stats['skipped']}")
    print(f"  {'─' * 36}")
    print(f"  📦 Всего               : {total}")

    # История публикаций по дням
    with get_db() as db:
        rows = db.execute("""
            SELECT
                date(sent_at) as day,
                COUNT(*) as count,
                GROUP_CONCAT(
                    substr(video_path, instr(video_path, 'chunk'), 10), ', '
                ) as videos
            FROM queue
            WHERE status = 'sent' AND sent_at IS NOT NULL
            GROUP BY day
            ORDER BY day DESC
            LIMIT 14
        """).fetchall()

    if rows:
        print()
        print("  📅 ИСТОРИЯ ПУБЛИКАЦИЙ (по дням)")
        print("  " + "─" * 36)
        for row in rows:
            print(f"  {row['day']}  —  {row['count']} видео  ({row['videos']})")
    print()


def cmd_logs(args) -> None:
    """Показывает историю публикаций в виде таблицы."""
    _setup_logging()
    from publisher.queue_db import init_db, get_recent
    init_db()
    items = get_recent(args.n)
    if not items:
        print("  Очередь пуста.")
        return

    icons = {"pending": "⏳", "sent": "✅", "skipped": "⏭ ", "failed": "❌"}

    print()
    print(f"  {'#':<4} {'':2} {'Опубликовано':<17} {'Добавлено':<17} {'Файл':<30} {'Подпись'}")
    print("  " + "─" * 100)

    for item in reversed(items):
        icon     = icons.get(item.status, "❓")
        name     = Path(item.video_path).name[:28]
        added    = item.created_at[:16] if item.created_at else "—"
        sent     = item.sent_at[:16]    if item.sent_at    else "—"
        caption  = item.caption.split("\n")[0][:40] if item.caption else "—"
        print(f"  {item.id:<4} {icon} {sent:<17} {added:<17} {name:<30} {caption}")

    print()
    sent_count = sum(1 for i in items if i.status == "sent")
    print(f"  Показано: {len(items)} | Опубликовано: {sent_count} | Pending: {sum(1 for i in items if i.status == 'pending')}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Shorts Pipeline — автонарезка и публикация шортсов",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- Общие флаги для process / start / download --process ---
    def add_process_args(p):
        p.add_argument(
            "--platforms", nargs="+",
            default=["tiktok"],
            choices=["youtube_shorts", "tiktok", "tiktok_long", "reels"],
            help="Платформы (default: tiktok)",
        )
        p.add_argument("--skip-antidetect", action="store_true")
        p.add_argument("--no-subtitles", action="store_true")
        p.add_argument(
            "--selector",
            default="auto",
            choices=["auto", "groq", "none"],
            help="Режим нарезки: auto=Groq если есть ключ, groq=только Groq, none=по тишине (default: auto)",
        )

    def add_publish_args(p):
        p.add_argument("--delay", type=int, default=30,
                       help="Минут между постами (default: 30)")
        p.add_argument("--max-per-day", type=int, default=2,
                       help="Постов в день (default: 2)")
        p.add_argument("--force", action="store_true",
                       help="Не ждать безопасного времени (для тестов)")

    # download
    p_download = sub.add_parser("download", help="Скачать видео с YouTube в inbox/")
    p_download.add_argument("url", help="Ссылка на YouTube видео")
    p_download.add_argument("--process", action="store_true",
                             help="После скачивания сразу обработать")
    add_process_args(p_download)

    # process
    p_process = sub.add_parser("process", help="Обработать видео")
    p_process.add_argument("video", nargs="?", help="Путь к видеофайлу")
    p_process.add_argument("--inbox", action="store_true",
                            help="Обработать все видео из папки inbox/")
    add_process_args(p_process)

    # publish
    p_publish = sub.add_parser("publish", help="Запустить публикацию в TikTok")
    add_publish_args(p_publish)

    # start
    p_start = sub.add_parser("start", help="Обработать inbox/ + опубликовать")
    add_process_args(p_start)
    add_publish_args(p_start)

    # status
    sub.add_parser("status", help="Показать статус очереди")

    # logs
    p_logs = sub.add_parser("logs", help="Показать последние записи очереди")
    p_logs.add_argument("--n", type=int, default=20, help="Количество записей (default: 20)")

    # retry
    p_retry = sub.add_parser("retry", help="Сбросить failed видео в pending и возобновить публикацию")
    add_publish_args(p_retry)

    args = parser.parse_args()

    # Валидация
    if args.command == "process" and not args.inbox and not args.video:
        parser.error("process требует либо --inbox, либо путь к видео")

    dispatch = {
        "download": cmd_download,
        "process": cmd_process,
        "publish": cmd_publish,
        "start":   cmd_start,
        "status":  cmd_status,
        "logs":    cmd_logs,
        "retry":   cmd_retry,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
