"""
Shorts Pipeline — точка входа.

Использование:
    python main.py ../avatar0101.mp4
    python main.py ../avatar0101.mp4 --platform tiktok
    python main.py ../avatar0101.mp4 --platforms youtube_shorts tiktok
    python main.py ../avatar0101.mp4 --skip-antidetect
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from loguru import logger

from config.settings import settings


def _check_dependencies() -> None:
    """Проверяет наличие ffmpeg и ffprobe перед запуском."""
    for tool in ("ffmpeg", "ffprobe"):
        try:
            result = subprocess.run(
                [tool, "-version"], capture_output=True, timeout=10
            )
            if result.returncode != 0:
                raise FileNotFoundError
        except (FileNotFoundError, OSError):
            logger.error(
                f"'{tool}' не найден. Установи ffmpeg: https://ffmpeg.org/download.html\n"
                f"Или запускай через run.bat"
            )
            sys.exit(1)
    logger.debug("ffmpeg / ffprobe — OK")
from models.schemas import PipelineState, Transcript
from pipeline.transcriber import Transcriber
from pipeline.chunker import Chunker
from pipeline.antidetect import AntiDetect
from pipeline.formatter import Formatter


def _video_id(video_path: Path) -> str:
    """
    ID видео = очищенное имя файла без расширения.
    Пример: "Аватар_Легенда_об_Аанге_1_сезон_-_1_серия.mp4" → "Аватар_Легенда_об_Аанге_1_сезон_-_1_серия"
    Запрещённые символы заменяются на "_".
    """
    stem = video_path.stem  # имя без расширения
    # Убираем символы запрещённые в Windows путях
    for ch in r'\/:*?"<>|':
        stem = stem.replace(ch, "_")
    # Обрезаем до 80 символов чтобы путь не был слишком длинным
    return stem[:80]


def load_checkpoint(video_id: str) -> PipelineState | None:
    checkpoint_path = settings.checkpoint_dir / f"{video_id}_state.json"
    if checkpoint_path.exists():
        logger.info(f"Найден checkpoint: {checkpoint_path.name}")
        return PipelineState.model_validate_json(checkpoint_path.read_text())
    return None


def save_checkpoint(state: PipelineState) -> None:
    checkpoint_path = settings.checkpoint_dir / f"{state.video_id}_state.json"
    checkpoint_path.write_text(state.model_dump_json(indent=2))
    logger.debug(f"Checkpoint сохранён: {checkpoint_path.name}")


def load_transcript(video_id: str) -> Transcript | None:
    path = settings.checkpoint_dir / f"{video_id}_transcript.json"
    if path.exists():
        return Transcript.model_validate_json(path.read_text())
    return None


def run_pipeline(
    video_path: str,
    platforms: list[str] = None,
    skip_antidetect: bool = False,
) -> None:
    """
    Запускает полный пайплайн для одного видео.
    Каждый шаг пропускается если уже выполнен (checkpoint).
    """
    settings.ensure_dirs()

    if platforms is None:
        platforms = ["youtube_shorts"]

    video_path = Path(video_path).resolve()
    if not video_path.exists():
        logger.error(f"Файл не найден: {video_path}")
        sys.exit(1)

    video_id = _video_id(video_path)

    # Загружаем или создаём state
    state = load_checkpoint(video_id) or PipelineState(
        video_id=video_id,
        original_path=str(video_path),
    )

    logger.info(f"")
    logger.info(f"▶  Shorts Pipeline")
    logger.info(f"   Файл:      {video_path.name}")
    logger.info(f"   ID:        {video_id}")
    logger.info(f"   Платформы: {', '.join(platforms)}")
    logger.info(f"")

    # ------------------------------------------------------------------
    # Шаг 1: Транскрибация
    # ------------------------------------------------------------------
    transcript = load_transcript(video_id)

    if not state.transcript_done or transcript is None:
        logger.info("📝 Шаг 1: Транскрибация...")
        transcript = Transcriber().process(str(video_path), video_id=video_id)
        state.transcript_done = True
        state.normalized_path = transcript.video_path
        save_checkpoint(state)
        logger.info(f"   ✓ {len(transcript.segments)} сегментов, {transcript.duration / 60:.1f} мин")
    else:
        logger.info(f"✅ Шаг 1: Транскрибация (из checkpoint, {len(transcript.segments)} сегментов)")

    # ------------------------------------------------------------------
    # Шаг 2: Нарезка на чанки по тишине
    # ------------------------------------------------------------------
    clips_checkpoint = settings.checkpoint_dir / f"{video_id}_clips.json"

    if not state.cuts_done or not clips_checkpoint.exists():
        logger.info("✂️  Шаг 2: Нарезка по тишине...")
        chunker = Chunker()
        raw_clips = chunker.process(transcript)
        # Сохраняем список клипов
        clips_checkpoint.write_text(
            json.dumps([c.model_dump() for c in raw_clips], indent=2)
        )
        state.cuts_done = True
        save_checkpoint(state)
        logger.info(f"   ✓ {len(raw_clips)} чанков")
    else:
        from models.schemas import RawClip
        raw_clips = [
            RawClip(**c) for c in json.loads(clips_checkpoint.read_text())
        ]
        logger.info(f"✅ Шаг 2: Нарезка (из checkpoint, {len(raw_clips)} чанков)")

    # ------------------------------------------------------------------
    # Шаг 3: Анти-бан
    # ------------------------------------------------------------------
    antidetect_checkpoint = settings.checkpoint_dir / f"{video_id}_antidetect.json"

    if skip_antidetect:
        logger.info("⏭️  Шаг 3: Анти-бан пропущен (--skip-antidetect)")
        # Используем raw_clips как "processed" без изменений
        from models.schemas import ProcessedClip
        processed_clips = [
            ProcessedClip(
                video_id=c.video_id,
                scene_index=c.scene_index,
                raw_clip_path=c.clip_path,
                processed_clip_path=c.clip_path,
                filters_applied=[],
            )
            for c in raw_clips
        ]
    elif not state.antidetect_done or not antidetect_checkpoint.exists():
        logger.info("🛡️  Шаг 3: Анти-бан обработка...")
        processed_clips = AntiDetect().process(raw_clips)
        antidetect_checkpoint.write_text(
            json.dumps([c.model_dump() for c in processed_clips], indent=2)
        )
        state.antidetect_done = True
        save_checkpoint(state)
        logger.info(f"   ✓ {len(processed_clips)} клипов обработано")
    else:
        from models.schemas import ProcessedClip
        processed_clips = [
            ProcessedClip(**c) for c in json.loads(antidetect_checkpoint.read_text())
        ]
        logger.info(f"✅ Шаг 3: Анти-бан (из checkpoint, {len(processed_clips)} клипов)")

    # ------------------------------------------------------------------
    # Шаг 4: Форматирование под платформы
    # ------------------------------------------------------------------
    if not state.format_done:
        logger.info(f"📱 Шаг 4: Форматирование → {', '.join(platforms)}...")
        formatter = Formatter()
        finals = formatter.process(
            clips=processed_clips,
            platforms=platforms,
            transcript=transcript,
        )
        state.format_done = True
        state.final_shorts = finals
        save_checkpoint(state)
        logger.info(f"   ✓ {len(finals)} шортсов готово")
    else:
        finals = state.final_shorts
        logger.info(f"✅ Шаг 4: Форматирование (из checkpoint, {len(finals)} шортсов)")

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


def main():
    parser = argparse.ArgumentParser(
        description="Shorts Pipeline — автоматическая нарезка шортсов из видео"
    )
    parser.add_argument("video", help="Путь к входному видео файлу")
    parser.add_argument(
        "--platforms", nargs="+",
        default=["tiktok"],
        choices=["youtube_shorts", "tiktok", "tiktok_long", "reels"],
        help="Целевые платформы (по умолчанию: tiktok)"
    )
    parser.add_argument(
        "--skip-antidetect", action="store_true",
        help="Пропустить анти-бан обработку"
    )
    args = parser.parse_args()

    # Настройка логирования
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

    _check_dependencies()

    run_pipeline(
        video_path=args.video,
        platforms=args.platforms,
        skip_antidetect=args.skip_antidetect,
    )


if __name__ == "__main__":
    main()
