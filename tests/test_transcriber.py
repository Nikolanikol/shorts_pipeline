"""
Тесты для Transcriber.

Запуск:
    cd shorts_pipeline
    pytest tests/test_transcriber.py -v

Стратегия:
- Unit тесты: мокаем faster-whisper, проверяем логику класса
- Integration тест: реальный запуск на тестовом видео (помечен @pytest.mark.integration)
  Запуск интеграционных тестов: pytest tests/test_transcriber.py -v -m integration
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from models.schemas import Transcript, TranscriptSegment
from processor.transcriber import Transcriber


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def restore_settings():
    """Сохраняет и восстанавливает settings после каждого теста."""
    from config.settings import settings
    original_checkpoint_dir = settings.checkpoint_dir
    original_temp_dir = settings.temp_dir
    original_output_dir = settings.output_dir
    yield
    settings.checkpoint_dir = original_checkpoint_dir
    settings.temp_dir = original_temp_dir
    settings.output_dir = original_output_dir


@pytest.fixture
def transcriber():
    return Transcriber()


@pytest.fixture
def sample_transcript():
    """Готовый транскрипт для тестов."""
    return Transcript(
        video_id="test001",
        video_path="/tmp/test001.mp4",
        duration=1420.0,  # ~23 минуты
        language="ru",
        segments=[
            TranscriptSegment(start=0.0, end=3.5, text="Давным-давно в далёком королевстве"),
            TranscriptSegment(start=3.5, end=7.2, text="жил был молодой принц"),
            TranscriptSegment(start=7.2, end=11.0, text="который мечтал о великих приключениях"),
            TranscriptSegment(start=11.0, end=15.5, text="Однажды случилось нечто необычное"),
            TranscriptSegment(start=15.5, end=20.0, text="что изменило его жизнь навсегда"),
        ] * 20  # 100 сегментов итого
    )


# ---------------------------------------------------------------------------
# Unit тесты: валидация транскрипта
# ---------------------------------------------------------------------------

class TestValidateTranscript:

    def test_valid_transcript_passes(self, transcriber, sample_transcript):
        assert transcriber._validate_transcript(sample_transcript) is True

    def test_empty_segments_fails(self, transcriber):
        transcript = Transcript(
            video_id="x", video_path="/tmp/x.mp4",
            duration=100.0, language="ru", segments=[]
        )
        assert transcriber._validate_transcript(transcript) is False

    def test_negative_start_fails(self, transcriber):
        transcript = Transcript(
            video_id="x", video_path="/tmp/x.mp4",
            duration=100.0, language="ru",
            segments=[TranscriptSegment(start=-1.0, end=5.0, text="текст " * 20)]
        )
        assert transcriber._validate_transcript(transcript) is False

    def test_end_before_start_fails(self, transcriber):
        transcript = Transcript(
            video_id="x", video_path="/tmp/x.mp4",
            duration=100.0, language="ru",
            segments=[TranscriptSegment(start=10.0, end=5.0, text="текст " * 20)]
        )
        assert transcriber._validate_transcript(transcript) is False

    def test_too_short_text_fails(self, transcriber):
        transcript = Transcript(
            video_id="x", video_path="/tmp/x.mp4",
            duration=100.0, language="ru",
            segments=[TranscriptSegment(start=0.0, end=5.0, text="короткий")]
        )
        assert transcriber._validate_transcript(transcript) is False


# ---------------------------------------------------------------------------
# Unit тесты: checkpoint система
# ---------------------------------------------------------------------------

class TestCheckpoint:

    def test_loads_from_checkpoint_if_exists(self, transcriber, sample_transcript, tmp_path):
        """Если checkpoint есть — не вызывает faster-whisper."""
        # Настраиваем checkpoint директорию
        from config.settings import settings
        original_dir = settings.checkpoint_dir
        settings.checkpoint_dir = tmp_path

        # Сохраняем checkpoint
        checkpoint_path = tmp_path / "test001_transcript.json"
        checkpoint_path.write_text(sample_transcript.model_dump_json())

        with patch.object(transcriber, '_load_model') as mock_load, \
             patch.object(transcriber, '_normalize_video') as mock_norm:

            # Мокаем видео файл
            fake_video = tmp_path / "test001.mp4"
            fake_video.touch()

            result = transcriber.process(str(fake_video), video_id="test001")

            # Модель НЕ должна загружаться — берём из checkpoint
            mock_load.assert_not_called()
            mock_norm.assert_not_called()

        assert result.video_id == "test001"
        assert len(result.segments) == 100

        settings.checkpoint_dir = original_dir

    def test_saves_checkpoint_after_transcription(self, transcriber, tmp_path):
        """После транскрибации checkpoint сохраняется на диск."""
        from config.settings import settings
        settings.checkpoint_dir = tmp_path
        settings.temp_dir = tmp_path

        fake_video = tmp_path / "video.mp4"
        fake_video.touch()

        # Мокаем все внешние вызовы
        fake_normalized = tmp_path / "abc12345.mp4"
        fake_normalized.touch()

        mock_segment = MagicMock()
        mock_segment.start = 0.0
        mock_segment.end = 5.0
        mock_segment.text = "  тестовый текст эпизода  "

        mock_info = MagicMock()
        mock_info.language = "ru"
        mock_info.language_probability = 0.99

        with patch.object(transcriber, '_normalize_video', return_value=fake_normalized), \
             patch.object(transcriber, '_get_video_duration', return_value=300.0), \
             patch.object(transcriber, '_load_model'), \
             patch.object(transcriber, '_validate_transcript', return_value=True):

            transcriber.model = MagicMock()
            transcriber.model.transcribe.return_value = ([mock_segment] * 10, mock_info)

            result = transcriber.process(str(fake_video), video_id="abc12345")

        # Checkpoint должен существовать
        checkpoint = tmp_path / "abc12345_transcript.json"
        assert checkpoint.exists()

        # Содержимое должно быть валидным
        data = json.loads(checkpoint.read_text())
        assert data["video_id"] == "abc12345"
        assert len(data["segments"]) == 10


# ---------------------------------------------------------------------------
# Unit тесты: нормализация видео
# ---------------------------------------------------------------------------

class TestNormalizeVideo:

    def test_skips_normalization_if_already_exists(self, transcriber, tmp_path):
        """Если нормализованный файл уже есть — ffmpeg не вызывается."""
        from config.settings import settings
        settings.temp_dir = tmp_path

        normalized = tmp_path / "abc12345.mp4"
        normalized.write_bytes(b"fake video data")

        with patch('subprocess.run') as mock_run:
            result = transcriber._normalize_video(Path("/fake/video.mp4"), "abc12345")
            mock_run.assert_not_called()

        assert result == normalized

    def test_raises_on_ffmpeg_error(self, transcriber, tmp_path):
        """При ошибке ffmpeg выбрасывает RuntimeError."""
        from config.settings import settings
        settings.temp_dir = tmp_path

        fake_video = tmp_path / "input.mp4"
        fake_video.touch()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "ffmpeg error: invalid codec"

        with patch('subprocess.run', return_value=mock_result):
            with pytest.raises(RuntimeError, match="Нормализация видео не удалась"):
                transcriber._normalize_video(fake_video, "newid123")


# ---------------------------------------------------------------------------
# Integration тест (запускается вручную с реальным видео)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_real_transcription():
    """
    Реальный тест на avatar0101.mp4.
    Запуск: pytest tests/test_transcriber.py -v -m integration

    Требует: faster-whisper установлен, CUDA доступна (или WHISPER_DEVICE=cpu в .env)
    """
    import subprocess as sp
    import shutil

    # Проверяем ffmpeg
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не найден в PATH. Установи: brew install ffmpeg")

    # Проверяем faster-whisper
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        pytest.skip("faster-whisper не установлен: pip install faster-whisper")

    video_path = Path(__file__).parent.parent.parent / "avatar0101.mp4"
    if not video_path.exists():
        pytest.skip(f"Тестовое видео не найдено: {video_path}")

    transcriber = Transcriber()
    transcript = transcriber.process(str(video_path), video_id="avatar01")

    # Базовые проверки
    assert transcript.video_id == "avatar01"
    assert transcript.duration > 0
    assert len(transcript.segments) > 50, "Ожидаем много сегментов для 23-минутного видео"
    assert transcript.language in ("ru", "en")

    # Проверяем тайм-коды
    for seg in transcript.segments:
        assert seg.start >= 0
        assert seg.end > seg.start
        assert seg.text.strip() != ""

    # Проверяем что full_text работает
    full = transcript.full_text
    assert "[" in full and "s]" in full  # формат тайм-кодов

    print(f"\n✅ Транскрибация успешна:")
    print(f"   Сегментов: {len(transcript.segments)}")
    print(f"   Длительность: {transcript.duration / 60:.1f} мин")
    print(f"   Язык: {transcript.language}")
    print(f"   Первые 3 сегмента:")
    for seg in transcript.segments[:3]:
        print(f"   [{seg.start:.1f}s - {seg.end:.1f}s] {seg.text}")
