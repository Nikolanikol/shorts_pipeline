"""
Шаг 1: Транскрибация видео через faster-whisper.

Принимает путь к видео → возвращает Transcript с тайм-кодами.
Результат сохраняется в checkpoints/{video_id}_transcript.json.
"""

import uuid
import json
import subprocess
from pathlib import Path

from loguru import logger

from config.settings import settings
from models.schemas import Transcript, TranscriptSegment


class Transcriber:
    """
    Транскрибирует видео через faster-whisper (CUDA).
    Перед транскрибацией нормализует видео в единый формат,
    заменяя имя файла на UUID (решает проблему кириллицы и спецсимволов).
    """

    def __init__(self):
        self.model = None  # Ленивая загрузка модели при первом вызове

    def _load_model(self):
        """Загружает модель Whisper (только один раз)."""
        if self.model is None:
            from faster_whisper import WhisperModel
            logger.info(
                f"Загрузка Whisper модели: {settings.whisper_model_size} "
                f"/ {settings.whisper_device} / {settings.whisper_compute_type}"
            )
            self.model = WhisperModel(
                settings.whisper_model_size,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
            )
            logger.info("Модель загружена")

    def _normalize_video(self, video_path: Path, video_id: str) -> Path:
        """
        Конвертирует входное видео в единый формат mp4 (h264/aac).
        Имя файла заменяется на UUID — решает проблему кириллицы.
        Если нормализованный файл уже существует — возвращает его.
        """
        settings.ensure_dirs()
        normalized_path = settings.temp_dir / f"{video_id}.mp4"

        if normalized_path.exists():
            # Проверяем что файл не повреждён (duration > 0)
            duration = self._get_video_duration(normalized_path)
            if duration > 0:
                logger.info(f"Нормализованное видео найдено: {normalized_path}")
                return normalized_path
            else:
                logger.warning(f"Нормализованный файл повреждён (duration=0), пересоздаём: {normalized_path}")
                normalized_path.unlink()

        logger.info(f"Нормализация видео → {normalized_path.name}")
        cmd = [
            "ffmpeg", "-i", str(video_path),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-ar", "44100",
            "-y",  # перезаписать если есть
            str(normalized_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"ffmpeg ошибка:\n{result.stderr}")
            raise RuntimeError(f"Нормализация видео не удалась: {video_path}")

        if not normalized_path.exists() or normalized_path.stat().st_size == 0:
            raise RuntimeError(f"Нормализованный файл пустой или не создан: {normalized_path}")

        logger.info(f"Нормализация завершена: {normalized_path.stat().st_size / 1024 / 1024:.1f} MB")
        return normalized_path

    def _validate_transcript(self, transcript: Transcript) -> bool:
        """
        Базовая валидация транскрипта перед отправкой в LLM.
        Защита от галлюцинаций faster-whisper.
        """
        if not transcript.segments:
            logger.warning("Транскрипт пустой — нет сегментов")
            return False

        if len(transcript.segments) < 5:
            logger.warning(f"Подозрительно мало сегментов: {len(transcript.segments)}")

        # Проверяем что тайм-коды не съехали
        for seg in transcript.segments:
            if seg.start < 0 or seg.end <= seg.start:
                logger.warning(f"Неверные тайм-коды: [{seg.start} - {seg.end}] '{seg.text[:30]}'")
                return False
            if seg.end > transcript.duration + 5:
                logger.warning(f"Тайм-код выходит за длительность видео: {seg.end} > {transcript.duration}")

        total_text = " ".join(s.text for s in transcript.segments)
        if len(total_text) < 100:
            logger.warning(f"Слишком короткий текст транскрипта: {len(total_text)} символов")
            return False

        return True

    def _get_video_duration(self, video_path: Path) -> float:
        """Получает длительность видео через ffprobe."""
        cmd = [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        try:
            return float(result.stdout.strip())
        except ValueError:
            return 0.0

    def process(self, video_path: str | Path, video_id: str = None) -> Transcript:
        """
        Основной метод. Транскрибирует видео и возвращает Transcript.

        Args:
            video_path: путь к исходному видео
            video_id: UUID (если None — генерируется автоматически)

        Returns:
            Transcript с тайм-кодами всех сегментов
        """
        video_path = Path(video_path).resolve()
        if not video_path.exists():
            raise FileNotFoundError(f"Видео не найдено: {video_path}")

        if video_id is None:
            video_id = str(uuid.uuid4())[:8]

        # Проверяем checkpoint
        checkpoint_path = settings.checkpoint_dir / f"{video_id}_transcript.json"
        if checkpoint_path.exists():
            logger.info(f"Загружаем транскрипт из checkpoint: {checkpoint_path}")
            return Transcript.model_validate_json(checkpoint_path.read_text())

        # Нормализация видео
        normalized_path = self._normalize_video(video_path, video_id)
        duration = self._get_video_duration(normalized_path)

        # Загружаем модель и транскрибируем
        self._load_model()

        logger.info(f"Начало транскрибации ({duration / 60:.1f} мин видео)...")
        segments_iter, info = self.model.transcribe(
            str(normalized_path),
            language=settings.whisper_language,
            beam_size=5,
            vad_filter=True,           # фильтр тишины
            vad_parameters=dict(
                min_silence_duration_ms=500,
            ),
            word_timestamps=False,
        )

        logger.info(f"Язык определён: {info.language} (уверенность: {info.language_probability:.2f})")

        segments = []
        for seg in segments_iter:
            segments.append(TranscriptSegment(
                start=round(seg.start, 2),
                end=round(seg.end, 2),
                text=seg.text.strip(),
            ))
            # Прогресс каждые 50 сегментов
            if len(segments) % 50 == 0:
                logger.debug(f"  обработано сегментов: {len(segments)} ({seg.end / 60:.1f} мин)")

        transcript = Transcript(
            video_id=video_id,
            video_path=str(normalized_path),
            duration=duration,
            language=info.language,
            segments=segments,
        )

        # Валидация
        if not self._validate_transcript(transcript):
            logger.warning("Транскрипт не прошёл валидацию — проверь качество видео/аудио")

        logger.info(
            f"Транскрибация завершена: {len(segments)} сегментов, "
            f"{len(transcript.full_text)} символов"
        )

        # Сохраняем checkpoint
        settings.ensure_dirs()
        checkpoint_path.write_text(transcript.model_dump_json(indent=2))
        logger.info(f"Checkpoint сохранён: {checkpoint_path}")

        return transcript
