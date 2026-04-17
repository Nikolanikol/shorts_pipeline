"""
Шаг 4: Форматирование клипов под платформы (YouTube Shorts / TikTok / Reels).

Что делает:
  - Конвертирует в вертикальный формат 9:16 (1080x1920)
  - Горизонтальное видео → blur фон + видео по центру (без чёрных полос)
  - Нормализует FPS до 30

Пресеты платформ:
  youtube_shorts  — 1080x1920, 30fps, до 60 сек
  tiktok          — 1080x1920, 30fps, до 60 сек
  reels           — 1080x1920, 30fps, до 90 сек
"""

import subprocess
from pathlib import Path

from loguru import logger

from config.settings import settings
from models.schemas import ProcessedClip, FinalShort, Platform, Transcript


# ---------------------------------------------------------------------------
# Пресеты платформ
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, Platform] = {
    "youtube_shorts": Platform(
        name="youtube_shorts",
        width=1080, height=1920,
        fps=30, max_duration=60,       # YouTube Shorts: строго до 60 сек
    ),
    "tiktok": Platform(
        name="tiktok",
        width=1080, height=1920,
        fps=30, max_duration=180,      # TikTok: до 3 мин — оптимум охвата
    ),
    "tiktok_long": Platform(
        name="tiktok_long",
        width=1080, height=1920,
        fps=30, max_duration=600,      # TikTok длинные: до 10 мин
    ),
    "reels": Platform(
        name="reels",
        width=1080, height=1920,
        fps=30, max_duration=90,       # Instagram Reels: до 90 сек
    ),
}


class Formatter:
    """
    Форматирует обработанные клипы под целевые платформы.
    Горизонтальное видео превращает в вертикальное через blur-фон технику.
    """

    def _get_video_info(self, clip_path: str) -> tuple[int, int, float]:
        """Возвращает (width, height, duration) через ffprobe."""
        cmd = [
            "ffprobe", "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "csv=p=0",
            clip_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        try:
            parts = result.stdout.strip().split(",")
            return int(parts[0]), int(parts[1]), float(parts[2])
        except (ValueError, IndexError):
            logger.warning(f"Не удалось получить инфо о видео: {clip_path}")
            return 1280, 720, 120.0

    def _build_vertical_filter(
        self,
        src_w: int, src_h: int,
        target_w: int, target_h: int,
        fps: int,
    ) -> str:
        """
        Строит ffmpeg filtergraph для конвертации в вертикальный формат.

        Техника blur-фон:
          1. Фоновый слой: исходное видео масштабируется на всю высоту 1920px,
             размывается (boxblur) и обрезается до 1080x1920
          2. Основной слой: исходное видео вписывается в 1080px по ширине
             с сохранением пропорций
          3. Оба слоя накладываются: фон снизу, основное видео по центру

        Результат: красивое вертикальное видео без чёрных полос.
        """
        src_ratio = src_w / src_h
        target_ratio = target_w / target_h

        if src_ratio > target_ratio:
            # Горизонтальное видео (широкое) — классический случай для сериалов
            # Основное: масштабируем по ширине target_w
            main_w = target_w
            main_h = int(target_w / src_ratio)
            main_h = main_h - (main_h % 2)  # чётное число для кодека

            # Фон: масштабируем по высоте target_h, потом кропаем по ширине
            bg_h = target_h
            bg_w = int(target_h * src_ratio)
            bg_w = bg_w - (bg_w % 2)

            bg_x = (bg_w - target_w) // 2  # центрируем по горизонтали
            main_y = (target_h - main_h) // 2  # центрируем по вертикали

            filtergraph = (
                f"[0:v]scale={bg_w}:{bg_h},crop={target_w}:{target_h}:{bg_x}:0,"
                f"boxblur=20:5[bg];"
                f"[0:v]scale={main_w}:{main_h}[main];"
                f"[bg][main]overlay=(W-w)/2:{main_y},"
                f"fps={fps}[v]"
            )
        else:
            # Вертикальное или квадратное — просто масштабируем и добавляем фон
            main_h = target_h
            main_w = int(target_h * src_ratio)
            main_w = main_w - (main_w % 2)

            bg_w = target_w
            bg_h = int(target_w / src_ratio)
            bg_h = bg_h - (bg_h % 2)
            bg_y = (target_h - bg_h) // 2
            main_x = (target_w - main_w) // 2

            filtergraph = (
                f"[0:v]scale={bg_w}:{bg_h},"
                f"pad={target_w}:{target_h}:0:{bg_y}:black,"
                f"boxblur=20:5[bg];"
                f"[0:v]scale={main_w}:{main_h}[main];"
                f"[bg][main]overlay={main_x}:(H-h)/2,"
                f"fps={fps}[v]"
            )

        return filtergraph

    def _trim_to_max_duration(
        self,
        clip_path: str,
        output_path: Path,
        max_duration: int,
        current_duration: float,
    ) -> str:
        """Обрезает клип если он длиннее max_duration платформы."""
        if current_duration <= max_duration:
            return clip_path  # обрезка не нужна

        logger.info(
            f"  Клип {current_duration:.0f}с > лимит {max_duration}с, обрезаем"
        )
        trimmed = output_path.parent / f"{output_path.stem}_trim.mp4"
        cmd = [
            "ffmpeg", "-i", clip_path,
            "-t", str(max_duration),
            "-c", "copy", "-y",
            str(trimmed),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning("Не удалось обрезать клип, используем оригинал")
            return clip_path
        return str(trimmed)

    def format_clip(
        self,
        clip: ProcessedClip,
        platform_name: str = "youtube_shorts",
        transcript: Transcript = None,
    ) -> FinalShort:
        """
        Форматирует один клип под платформу.

        Args:
            clip: ProcessedClip после AntiDetect
            platform_name: название платформы

        Returns:
            FinalShort с путём к готовому вертикальному видео
        """
        platform = PLATFORMS.get(platform_name)
        if not platform:
            raise ValueError(f"Неизвестная платформа: {platform_name}. Доступные: {list(PLATFORMS)}")

        input_path = Path(clip.processed_clip_path)
        if not input_path.exists():
            raise FileNotFoundError(f"Клип не найден: {input_path}")

        # Выходная папка: output/{video_id}/{platform}/
        out_dir = settings.output_dir / clip.video_id / platform_name
        out_dir.mkdir(parents=True, exist_ok=True)

        # Имя файла без суффикса _ad
        stem = input_path.stem.replace("_ad", "")
        output_path = out_dir / f"{stem}_{platform_name}.mp4"

        # Получаем инфо о клипе
        src_w, src_h, duration = self._get_video_info(str(input_path))
        logger.info(
            f"  {input_path.name}: {src_w}x{src_h} {duration:.0f}с → "
            f"{platform.width}x{platform.height} ({platform_name})"
        )

        # Обрезаем если нужно
        input_for_format = self._trim_to_max_duration(
            str(input_path), output_path, platform.max_duration, duration
        )
        actual_duration = min(duration, platform.max_duration)

        # Строим фильтр вертикализации
        filtergraph = self._build_vertical_filter(
            src_w, src_h,
            platform.width, platform.height,
            platform.fps,
        )

        cmd = [
            "ffmpeg",
            "-i", input_for_format,
            "-filter_complex", filtergraph,
            "-map", "[v]",
            "-map", "0:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-ar", "44100",
            "-movflags", "+faststart",
            "-y",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"ffmpeg ошибка:\n{result.stderr[-500:]}")
            raise RuntimeError(f"Форматирование не удалось: {input_path.name}")

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(f"Выходной файл пустой: {output_path}")

        # Чистим временный trim-файл если создавался
        trimmed_path = output_path.parent / f"{output_path.stem}_trim.mp4"
        if trimmed_path.exists():
            trimmed_path.unlink()

        # Сжигаем субтитры если есть транскрипт
        if transcript is not None:
            from pipeline.subtitles import burn_subtitles
            sub_output = out_dir / f"{stem}_{platform_name}_sub.mp4"
            result_path = burn_subtitles(
                video_path=str(output_path),
                output_path=str(sub_output),
                transcript=transcript,
                start_offset=clip.start,
                end_offset=clip.end,
            )
            if result_path == str(sub_output) and sub_output.exists():
                output_path.unlink()
                output_path = sub_output

        size_mb = output_path.stat().st_size / 1024 / 1024
        logger.info(f"  ✓ {output_path.name}: {size_mb:.1f} MB")

        return FinalShort(
            video_id=clip.video_id,
            scene_index=clip.scene_index,
            platform=platform_name,
            output_path=str(output_path),
            duration=actual_duration,
        )

    def process(
        self,
        clips: list[ProcessedClip],
        platforms: list[str] = None,
        transcript: Transcript = None,
    ) -> list[FinalShort]:
        """
        Форматирует все клипы под одну или несколько платформ.

        Args:
            clips: список ProcessedClip из AntiDetect
            platforms: список платформ (по умолчанию ["youtube_shorts"])

        Returns:
            список FinalShort
        """
        if platforms is None:
            platforms = ["youtube_shorts"]

        total = len(clips) * len(platforms)
        logger.info(f"Formatter: {len(clips)} клипов × {len(platforms)} платформ = {total} файлов")

        finals = []
        counter = 0
        for clip in clips:
            for platform_name in platforms:
                counter += 1
                logger.info(f"[{counter}/{total}] {Path(clip.processed_clip_path).name} → {platform_name}")
                final = self.format_clip(clip, platform_name, transcript=transcript)
                finals.append(final)

        logger.info(f"Formatter завершён: {len(finals)} готовых шортсов")
        return finals
