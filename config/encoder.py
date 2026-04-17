"""
Автовыбор видеоэнкодера: h264_nvenc (GPU) или libx264 (CPU).

Использование:
    from config.encoder import get_video_encoder
    enc = get_video_encoder()
    cmd = ["ffmpeg", "-i", input, *enc.args(), "-y", output]
"""

import subprocess
from dataclasses import dataclass
from functools import lru_cache

from loguru import logger


@dataclass
class VideoEncoder:
    name: str            # h264_nvenc | libx264
    quality_flag: str    # -cq (nvenc) | -crf (libx264)
    quality_value: int   # 23
    preset: str          # p4 (nvenc) | fast (libx264)

    def args(self, quality: int = None) -> list[str]:
        """Возвращает список ffmpeg аргументов для кодирования."""
        q = str(quality) if quality is not None else str(self.quality_value)
        return [
            "-c:v", self.name,
            "-preset", self.preset,
            self.quality_flag, q,
        ]


@lru_cache(maxsize=1)
def get_video_encoder() -> VideoEncoder:
    """
    Определяет лучший доступный энкодер.
    Результат кэшируется — определение происходит один раз.
    """
    try:
        # Проверяем что h264_nvenc числится среди энкодеров
        result = subprocess.run(
            ["ffmpeg", "-encoders", "-v", "quiet"],
            capture_output=True, text=True, timeout=10,
        )
        if "h264_nvenc" not in result.stdout:
            logger.info("Энкодер: libx264 (CPU) — h264_nvenc не найден")
            return _cpu_encoder()

        # Проверяем что nvenc реально работает
        test = subprocess.run(
            [
                "ffmpeg", "-f", "lavfi", "-i", "nullsrc=s=64x64:d=1",
                "-c:v", "h264_nvenc", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if test.returncode == 0:
            logger.info("Энкодер: h264_nvenc (GPU) — ускорение в 5-8x")
            return VideoEncoder(
                name="h264_nvenc",
                quality_flag="-cq",
                quality_value=23,
                preset="p4",
            )
        else:
            logger.warning(f"NVENC тест не прошёл: {test.stderr[:200]}")

    except subprocess.TimeoutExpired:
        logger.warning("NVENC тест timeout — переключаемся на CPU")
    except Exception as e:
        logger.warning(f"Ошибка при тестировании NVENC: {e}")

    return _cpu_encoder()


def _cpu_encoder() -> VideoEncoder:
    logger.info("Энкодер: libx264 (CPU)")
    return VideoEncoder(
        name="libx264",
        quality_flag="-crf",
        quality_value=23,
        preset="fast",
    )
