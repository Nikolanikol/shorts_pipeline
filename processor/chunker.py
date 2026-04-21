"""
Шаг 2 (chunks режим): нарезка видео на равные куски по тишине.

Алгоритм:
  1. Берём транскрипт — знаем где паузы между фразами
  2. Каждые ~CHUNK_DURATION секунд ищем ближайшую паузу в окне ±CHUNK_SEARCH_WINDOW
  3. Режем в середине этой паузы
  4. Следующий чанк начинается за CHUNK_OVERLAP секунд до точки реза

Пример при chunk_duration=120, overlap=10:
  Чанк 1: 0:00 — 2:04   (пауза нашлась на 2:04)
  Чанк 2: 1:54 — 4:11   (начало = 2:04 - 10сек)
  Чанк 3: 4:01 — ...
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from config.settings import settings
from models.schemas import Transcript, TranscriptSegment, RawClip


@dataclass
class ChunkBoundary:
    """Граница между чанками — точка реза по тишине."""
    position: float        # время реза в секундах
    silence_duration: float  # длина паузы в этой точке
    target: float          # изначальная целевая позиция


class Chunker:
    """
    Нарезает видео на чанки по ~2 минуты, разрезая в паузах речи.
    Использует готовый транскрипт — ffprobe не нужен.
    """

    def find_silences(self, transcript: Transcript) -> list[tuple[float, float]]:
        """
        Извлекает паузы из транскрипта.
        Пауза — промежуток между концом одного сегмента и началом следующего.

        Returns:
            Список (start_of_silence, duration) отсортированный по времени
        """
        silences = []
        segments = transcript.segments

        for i in range(len(segments) - 1):
            gap_start = segments[i].end
            gap_end = segments[i + 1].start
            gap_duration = gap_end - gap_start

            if gap_duration >= settings.chunk_min_silence:
                silences.append((gap_start, gap_duration))

        logger.debug(f"Найдено пауз: {len(silences)} (мин. длина: {settings.chunk_min_silence}с)")
        return silences

    def find_best_cut(
        self,
        target: float,
        silences: list[tuple[float, float]],
        video_duration: float,
    ) -> ChunkBoundary:
        """
        Находит лучшую точку реза рядом с целевой позицией.
        Ищет в окне [target - window, target + window].
        Из всех пауз в окне выбирает самую длинную.
        Если пауз нет — режет точно в target.

        Args:
            target: целевая позиция реза (секунды)
            silences: список всех пауз (start, duration)
            video_duration: длительность видео

        Returns:
            ChunkBoundary с позицией реза
        """
        window = settings.chunk_search_window

        # Собираем паузы в окне поиска
        candidates = [
            (pos, dur) for pos, dur in silences
            if target - window <= pos <= target + window
        ]

        if not candidates:
            # Пауз нет — режем точно по target
            logger.debug(f"Пауз не найдено у {target:.1f}с, режем точно")
            return ChunkBoundary(
                position=min(target, video_duration),
                silence_duration=0.0,
                target=target,
            )

        # Выбираем самую длинную паузу в окне
        best_pos, best_dur = max(candidates, key=lambda x: x[1])

        # Режем в середине паузы
        cut_point = best_pos + best_dur / 2

        logger.debug(
            f"Цель: {target:.1f}с → пауза {best_pos:.1f}с "
            f"(длина: {best_dur:.2f}с) → рез: {cut_point:.1f}с"
        )

        return ChunkBoundary(
            position=cut_point,
            silence_duration=best_dur,
            target=target,
        )

    def calculate_boundaries(self, transcript: Transcript) -> list[ChunkBoundary]:
        """
        Рассчитывает все точки реза для видео.

        Returns:
            Список границ чанков (без начала и конца видео)
        """
        silences = self.find_silences(transcript)
        duration = transcript.duration
        boundaries = []

        target = float(settings.chunk_duration)
        while target < duration - settings.chunk_overlap:
            boundary = self.find_best_cut(target, silences, duration)
            boundaries.append(boundary)
            # Следующая цель — фиксированный шаг от предыдущей цели (не от реза)
            # Иначе паузы до target накапливают дрейф и дают лишние чанки
            target += settings.chunk_duration

        logger.info(f"Рассчитано границ: {len(boundaries)} → {len(boundaries) + 1} чанков")
        return boundaries

    def _cut_clip(
        self,
        video_path: str,
        output_path: Path,
        start: float,
        end: float,
    ) -> None:
        """Нарезает один клип через ffmpeg."""
        duration = end - start
        from config.encoder import get_video_encoder
        enc = get_video_encoder()
        cmd = [
            "ffmpeg",
            # ВАЖНО: -ss ПОСЛЕ -i = точный seek (медленнее, но без сдвига keyframe).
            # -ss до -i ищет ближайший keyframe и даёт смещение тайм-кодов ~0.5-2с,
            # из-за чего субтитры появляются раньше речи.
            "-i", str(video_path),
            "-ss", str(start),
            "-t", str(duration),
            *enc.args(quality=18),
            "-c:a", "aac",
            "-avoid_negative_ts", "make_zero",
            "-y",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"ffmpeg ошибка:\n{result.stderr[-500:]}")
            raise RuntimeError(f"Нарезка не удалась: {output_path.name}")

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(f"Клип пустой или не создан: {output_path}")

    def process(self, transcript: Transcript) -> list[RawClip]:
        """
        Основной метод. Нарезает видео на чанки по тишине.

        Args:
            transcript: готовый транскрипт из Transcriber

        Returns:
            Список RawClip с путями к нарезанным файлам
        """
        settings.ensure_dirs()

        video_path = transcript.video_path
        video_id = transcript.video_id
        duration = transcript.duration

        if duration == 0:
            raise ValueError("Длительность видео = 0, проверь транскрипт")

        # Рассчитываем границы
        boundaries = self.calculate_boundaries(transcript)

        # Строим список чанков: [(start, end), ...]
        cut_points = [b.position for b in boundaries]
        starts = [0.0] + [p - settings.chunk_overlap for p in cut_points]
        ends = cut_points + [duration]

        # Убираем отрицательные старты
        starts = [max(0.0, s) for s in starts]

        chunks = list(zip(starts, ends))
        logger.info(f"Нарезаем {len(chunks)} чанков из {duration / 60:.1f} мин видео")

        clips = []
        # Промежуточные файлы (сырые чанки) — в temp/, не в ready/pending/
        clips_dir = settings.temp_dir / video_id
        clips_dir.mkdir(parents=True, exist_ok=True)

        for i, (start, end) in enumerate(chunks):
            clip_duration = end - start
            output_path = clips_dir / f"chunk_{i + 1:02d}.mp4"

            logger.info(
                f"  Чанк {i + 1}/{len(chunks)}: "
                f"{start / 60:.1f}м — {end / 60:.1f}м "
                f"({clip_duration:.0f}с)"
            )

            self._cut_clip(video_path, output_path, start, end)

            clips.append(RawClip(
                video_id=video_id,
                scene_index=i,
                clip_path=str(output_path),
                start=start,
                end=end,
                duration=clip_duration,
            ))

        logger.info(f"Нарезка завершена: {len(clips)} чанков в {clips_dir}")
        return clips
