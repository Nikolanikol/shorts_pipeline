"""
Централизованные настройки проекта через .env файл.
Использование: from config.settings import settings
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator
from typing import Any


BASE_DIR = Path(__file__).parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------------------------------------------------------------------------
    # Claude API (legacy, опционально)
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

    # ---------------------------------------------------------------------------
    # Groq API (бесплатный, основной)
    # ---------------------------------------------------------------------------
    groq_api_key: str = Field(
        default="",
        description="Ключ Groq API (бесплатно: console.groq.com)"
    )
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Модель Groq: llama-3.3-70b-versatile или llama-3.1-8b-instant"
    )
    groq_max_retries: int = Field(
        default=3,
        description="Количество попыток при ошибке Groq API"
    )

    # ---------------------------------------------------------------------------
    # Выбор сцен
    # ---------------------------------------------------------------------------
    scene_selector_backend: str = Field(
        default="groq",
        description="Бэкенд выбора сцен: groq | none (none = нарезка по тишине)"
    )
    scenes_to_select: int = Field(
        default=5,
        description="Сколько топ-сцен выбирать из видео"
    )
    scene_max_tokens_per_chunk: int = Field(
        default=20000,
        description="Макс. токенов транскрипта в одном запросе к LLM (для длинных интервью)"
    )

    # ---------------------------------------------------------------------------
    # Автоопределение железа
    # ---------------------------------------------------------------------------
    auto_detect_hardware: bool = Field(
        default=True,
        description="Автоматически определять устройство и выбирать настройки Whisper"
    )

    # ---------------------------------------------------------------------------
    # Транскрибация (faster-whisper)
    # Значения ниже используются только если auto_detect_hardware=false
    # ---------------------------------------------------------------------------
    whisper_model_size: str = Field(
        default="medium",
        description="Размер модели Whisper: tiny, base, small, medium, large-v3"
    )
    whisper_device: str = Field(
        default="cpu",
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

    @model_validator(mode="after")
    def apply_hardware_detection(self) -> "Settings":
        if not self.auto_detect_hardware:
            return self
        from config.hardware import detect_hardware, log_hardware_profile
        profile = detect_hardware()
        log_hardware_profile(profile)
        self.whisper_model_size = profile.model_size
        self.whisper_device = profile.device
        self.whisper_compute_type = profile.compute_type
        return self

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
        default=150,
        description="Целевая длина чанка в секундах (2.5 минуты — укладывается в TikTok лимит 3 мин)"
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
    # Публикация TikTok
    # ---------------------------------------------------------------------------
    tiktok_kmotors_url: str = Field(
        default="https://kmotors.ru",
        description="Ссылка на сайт kmotors для описания поста"
    )
    tiktok_account_email: str = Field(default="", description="Email аккаунта TikTok")
    tiktok_account_password: str = Field(default="", description="Пароль аккаунта TikTok")

    # ---------------------------------------------------------------------------
    # Замена звука на фоновую музыку
    # ---------------------------------------------------------------------------
    music_dir: Path = Field(
        default=BASE_DIR / "music",
        description="Папка с MP3-файлами для фоновой музыки"
    )
    music_volume: float = Field(
        default=0.3,
        description="Громкость фоновой музыки (0.0-1.0)"
    )

    # ---------------------------------------------------------------------------
    # Пути
    # ---------------------------------------------------------------------------
    output_dir: Path = Field(
        default=BASE_DIR / "ready" / "pending",
        description="Папка для готовых шортсов (ожидают публикации)"
    )
    posted_dir: Path = Field(
        default=BASE_DIR / "ready" / "posted",
        description="Папка для опубликованных шортсов"
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
        for d in [self.output_dir, self.posted_dir, self.checkpoint_dir, self.temp_dir]:
            d.mkdir(parents=True, exist_ok=True)


# Синглтон — импортировать отовсюду
settings = Settings()
