"""
Pydantic схемы для передачи данных между модулями пайплайна.
Каждый модуль принимает и возвращает эти типы.
"""

from dataclasses import dataclass, field
from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Шаг 1: Transcriber
# ---------------------------------------------------------------------------

class TranscriptSegment(BaseModel):
    """Один сегмент транскрипта (фраза с тайм-кодами)."""
    start: float = Field(..., description="Начало сегмента в секундах")
    end: float = Field(..., description="Конец сегмента в секундах")
    text: str = Field(..., description="Текст фразы")
    speaker: Optional[str] = Field(None, description="Спикер (если определён)")


class Transcript(BaseModel):
    """Полный транскрипт видео."""
    video_id: str = Field(..., description="UUID исходного видео")
    video_path: str = Field(..., description="Путь к нормализованному видео")
    duration: float = Field(..., description="Длительность видео в секундах")
    language: str = Field(default="ru", description="Язык транскрипта")
    segments: list[TranscriptSegment] = Field(default_factory=list)

    @property
    def full_text(self) -> str:
        """Полный текст для отправки в LLM."""
        return "\n".join(
            f"[{s.start:.1f}s - {s.end:.1f}s] {s.text}"
            for s in self.segments
        )


# ---------------------------------------------------------------------------
# Шаг 2: SceneSelector
# ---------------------------------------------------------------------------

class Scene(BaseModel):
    """Интересный момент, выбранный Claude."""
    start: float = Field(..., description="Начало сцены в секундах")
    end: float = Field(..., description="Конец сцены в секундах")
    reason: str = Field(..., description="Почему Claude выбрал этот момент")
    score: float = Field(..., ge=0.0, le=1.0, description="Оценка интересности 0–1")
    title: Optional[str] = Field(None, description="Короткий заголовок для шортса")

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def start_buffered(self) -> float:
        """Начало с буфером -3 секунды (не меньше 0)."""
        return max(0.0, self.start - 3.0)

    @property
    def end_buffered(self) -> float:
        """Конец с буфером +3 секунды."""
        return self.end + 3.0


class SceneSelection(BaseModel):
    """Результат работы SceneSelector."""
    video_id: str
    scenes: list[Scene] = Field(default_factory=list)
    model_used: str = Field(default="claude-haiku-4-5-20251001")


# ---------------------------------------------------------------------------
# Шаг 3: Cutter
# ---------------------------------------------------------------------------

class RawClip(BaseModel):
    """Нарезанный клип до обработки."""
    video_id: str
    scene_index: int = Field(..., description="Индекс сцены из SceneSelection")
    clip_path: str = Field(..., description="Путь к файлу клипа")
    start: float
    end: float
    duration: float


# ---------------------------------------------------------------------------
# Шаг 4: AntiDetect
# ---------------------------------------------------------------------------

class ProcessedClip(BaseModel):
    """Клип после анти-бан обработки."""
    video_id: str
    scene_index: int
    raw_clip_path: str
    processed_clip_path: str
    filters_applied: list[str] = Field(default_factory=list)
    start: float = Field(default=0.0, description="Начало клипа в исходном видео (сек)")
    end: float = Field(default=0.0, description="Конец клипа в исходном видео (сек)")


# ---------------------------------------------------------------------------
# Шаг 5: Formatter
# ---------------------------------------------------------------------------

class Platform(BaseModel):
    """Настройки платформы для финального форматирования."""
    name: str                          # "youtube_shorts", "tiktok", "reels"
    width: int = 1080
    height: int = 1920
    fps: int = 30
    max_duration: int = 60             # секунд


class FinalShort(BaseModel):
    """Готовый шортс."""
    video_id: str
    scene_index: int
    platform: str
    output_path: str
    duration: float
    title: Optional[str] = None


# ---------------------------------------------------------------------------
# Checkpoint система
# ---------------------------------------------------------------------------

class PipelineState(BaseModel):
    """Состояние пайплайна для checkpoint системы."""
    video_id: str
    original_path: str
    normalized_path: Optional[str] = None
    transcript_done: bool = False
    scenes_done: bool = False
    cuts_done: bool = False
    antidetect_done: bool = False
    format_done: bool = False
    final_shorts: list[FinalShort] = Field(default_factory=list)
