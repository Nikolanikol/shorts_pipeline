"""
Централизованные настройки проекта через .env файл.
Использование: from config.settings import settings
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


BASE_DIR = Path(__file__).parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------------------------------------------------------------------------
    # Claude API
    # ---------------------------------------------------------------------------
    anthropic_api_key: str = Field(
        default="",
        description="Ключ Anthropic API (заполнить в .env)"
    )
    claude_model: str = Field(
        default="claude-haiku-4-5-20251001",
        description="Модель Claude для выбора сцен"
    )
    claude_max_retries: int = Field(
        default=3,
        description="Количество попыток при ошибке API"
    )
    scenes_to_select: int = Field(
        default=5,
        description="Сколько топ-сцен выбирать из видео"
    )

    # ---------------------------------------------------------------------------
    # Транскрибация (faster-whisper)
    # ---------------------------------------------------------------------------
    whisper_model_size: str = Field(
        default="large-v3",
        description="Размер модели Whisper: tiny, base, small, medium, large-v3"
    )
    whisper_device: str = Field(
        default="cuda",
        description="Устройство для Whisper: cuda / cpu"
    )
    whisper_compute_type: str = Field(
        default="int8",
        description="Тип вычислений: int8, float16, float32"
    )
    whisper_language: str = Field(
        default="ru",
        description="Язык транскрибации (ru, en, ...)"
    )

    # ---------------------------------------------------------------------------
    # ffmpeg / нарезка
    # ---------------------------------------------------------------------------
    min_clip_duration: int = Field(
        default=30,
        description="Минимальная длина клипа в секундах"
    )
    max_clip_duration: int = Field(
        default=90,
        description="Максимальная длина клипа в секундах"
    )
    scene_buffer_seconds: float = Field(
        default=3.0,
        description="Буфер вокруг тайм-кодов сцены (±сек)"
    )

    # ---------------------------------------------------------------------------
    # Chunk режим (нарезка по тишине)
    # ---------------------------------------------------------------------------
    chunk_duration: int = Field(
        default=120,
        description="Целевая длина чанка в секундах (2 минуты)"
    )
    chunk_search_window: int = Field(
        default=20,
        description="Окно поиска паузы вокруг границы чанка (±сек)"
    )
    chunk_overlap: int = Field(
        default=10,
        description="Перекрытие между чанками в секундах"
    )
    chunk_min_silence: float = Field(
        default=0.3,
        description="Минимальная длина паузы для точки реза (сек)"
    )

    # ---------------------------------------------------------------------------
    # Анти-бан
    # ---------------------------------------------------------------------------
    antidetect_speed_variation: float = Field(
        default=0.055,
        description="Вариация скорости ±% (0.055 = ±5.5%) — порог ломания аудиофингерпринта"
    )
    antidetect_zoom_crop: float = Field(
        default=0.04,
        description="Zoom crop % (0.04 = 4%) — асимметричный кроп для сдвига perceptual hash"
    )
    antidetect_noise_level: int = Field(
        default=8,
        description="Уровень видеошума 0-20 (8 = визуально незаметно, но сдвигает hash)"
    )

    # ---------------------------------------------------------------------------
    # Пути
    # ---------------------------------------------------------------------------
    output_dir: Path = Field(
        default=BASE_DIR / "output",
        description="Папка для готовых шортсов"
    )
    checkpoint_dir: Path = Field(
        default=BASE_DIR / "checkpoints",
        description="Папка для checkpoint файлов"
    )
    temp_dir: Path = Field(
        default=BASE_DIR / "temp",
        description="Временные файлы (нормализованное видео и т.д.)"
    )

    def ensure_dirs(self) -> None:
        """Создаёт все необходимые директории если их нет."""
        for d in [self.output_dir, self.checkpoint_dir, self.temp_dir]:
            d.mkdir(parents=True, exist_ok=True)


# Синглтон — импортировать отовсюду
settings = Settings()
