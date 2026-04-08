"""
Тесты для AntiDetect.

Запуск unit тестов:
    pytest tests/test_antidetect.py -v

Интеграционный тест (реальная обработка чанков):
    pytest tests/test_antidetect.py -v -m integration
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from models.schemas import RawClip, ProcessedClip
from pipeline.antidetect import AntiDetect


# ---------------------------------------------------------------------------
# Фикстура
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def restore_settings():
    from config.settings import settings
    original_output_dir = settings.output_dir
    yield
    settings.output_dir = original_output_dir


@pytest.fixture
def antidetect():
    return AntiDetect()


@pytest.fixture
def sample_clip(tmp_path):
    """Фейковый RawClip с реальным файлом."""
    clip_file = tmp_path / "chunk_01.mp4"
    clip_file.write_bytes(b"fake video" * 1000)
    return RawClip(
        video_id="avatar01",
        scene_index=0,
        clip_path=str(clip_file),
        start=0.0,
        end=120.0,
        duration=120.0,
    )


# ---------------------------------------------------------------------------
# _get_variation
# ---------------------------------------------------------------------------

class TestGetVariation:

    def test_returns_value_in_range(self, antidetect):
        """Вариация всегда в диапазоне [-1.0, 1.0]."""
        for i in range(20):
            v = antidetect._get_variation(f"/fake/path_{i}.mp4", i)
            assert -1.0 <= v <= 1.0

    def test_different_clips_get_different_variations(self, antidetect):
        """Разные клипы → разные вариации."""
        v1 = antidetect._get_variation("/fake/chunk_01.mp4", 0)
        v2 = antidetect._get_variation("/fake/chunk_02.mp4", 0)
        assert v1 != v2

    def test_same_clip_same_variation(self, antidetect):
        """Один и тот же клип → одна и та же вариация (детерминировано)."""
        v1 = antidetect._get_variation("/fake/chunk_01.mp4", 0)
        v2 = antidetect._get_variation("/fake/chunk_01.mp4", 0)
        assert v1 == v2


# ---------------------------------------------------------------------------
# _build_filter_complex
# ---------------------------------------------------------------------------

class TestBuildFilterComplex:

    def test_returns_valid_filter_string(self, antidetect):
        """Строка фильтров содержит все нужные компоненты."""
        vf, speed = antidetect._build_filter_complex("/fake/chunk_01.mp4", 1280, 720)
        assert "crop=" in vf
        assert "scale=" in vf
        assert "setpts=" in vf
        assert "eq=saturation=" in vf
        assert "noise=" in vf       # видеошум для сдвига perceptual hash

    def test_speed_in_valid_range(self, antidetect):
        """Скорость в диапазоне ±5.5% — порог ломания аудиофингерпринта."""
        for i in range(20):
            _, speed = antidetect._build_filter_complex(f"/fake/chunk_{i:02d}.mp4", 1280, 720)
            assert 0.944 <= speed <= 1.056, f"Скорость вышла за диапазон: {speed}"

    def test_crop_dimensions_smaller_than_original(self, antidetect):
        """Кроп всегда меньше оригинала."""
        vf, _ = antidetect._build_filter_complex("/fake/chunk_01.mp4", 1280, 720)
        # Парсим crop=W:H:X:Y
        crop_part = [p for p in vf.split(",") if p.startswith("crop=")][0]
        _, params = crop_part.split("=")
        w, h, x, y = map(int, params.split(":"))
        assert w < 1280
        assert h < 720

    def test_scale_restores_original_size(self, antidetect):
        """Scale возвращает к оригинальным размерам после кропа."""
        vf, _ = antidetect._build_filter_complex("/fake/chunk_01.mp4", 1280, 720)
        scale_part = [p for p in vf.split(",") if p.startswith("scale=")][0]
        _, params = scale_part.split("=")
        w, h = map(int, params.split(":"))
        assert w == 1280
        assert h == 720

    def test_different_clips_get_different_filters(self, antidetect):
        """Разные клипы → разные фильтры."""
        vf1, speed1 = antidetect._build_filter_complex("/fake/chunk_01.mp4", 1280, 720)
        vf2, speed2 = antidetect._build_filter_complex("/fake/chunk_02.mp4", 1280, 720)
        assert vf1 != vf2 or speed1 != speed2


# ---------------------------------------------------------------------------
# process_clip
# ---------------------------------------------------------------------------

class TestProcessClip:

    def test_raises_if_clip_not_found(self, antidetect):
        clip = RawClip(
            video_id="x", scene_index=0,
            clip_path="/nonexistent/chunk.mp4",
            start=0.0, end=120.0, duration=120.0,
        )
        with pytest.raises(FileNotFoundError):
            antidetect.process_clip(clip)

    def test_returns_processed_clip(self, antidetect, sample_clip, tmp_path):
        """process_clip возвращает ProcessedClip с правильными полями."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        # Мокаем ffprobe и ffmpeg
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = mock_result

            # Создаём выходной файл вручную (ffmpeg замокан)
            output_path = Path(sample_clip.clip_path).parent / "chunk_01_ad.mp4"
            output_path.write_bytes(b"processed video" * 1000)

            result = antidetect.process_clip(sample_clip)

        assert isinstance(result, ProcessedClip)
        assert result.video_id == "avatar01"
        assert result.scene_index == 0
        assert result.raw_clip_path == sample_clip.clip_path
        assert "zoom_crop" in result.filters_applied
        assert "speed_variation" in result.filters_applied
        assert "audio_tempo" in result.filters_applied
        assert "video_noise" in result.filters_applied
        assert "metadata_strip" in result.filters_applied

    def test_output_path_has_ad_suffix(self, antidetect, sample_clip):
        """Выходной файл получает суффикс _ad."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = mock_result

            output_path = Path(sample_clip.clip_path).parent / "chunk_01_ad.mp4"
            output_path.write_bytes(b"x" * 1000)

            result = antidetect.process_clip(sample_clip)

        assert "_ad" in result.processed_clip_path

    def test_raises_on_ffmpeg_error(self, antidetect, sample_clip):
        """При ошибке ffmpeg выбрасывает RuntimeError."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "ffmpeg: invalid filter"

        with patch('subprocess.run', return_value=mock_result):
            with pytest.raises(RuntimeError, match="AntiDetect не удался"):
                antidetect.process_clip(sample_clip)


# ---------------------------------------------------------------------------
# process (все клипы)
# ---------------------------------------------------------------------------

class TestProcess:

    def test_processes_all_clips(self, antidetect, tmp_path):
        """process() возвращает столько же ProcessedClip сколько получил RawClip."""
        clips = []
        for i in range(3):
            f = tmp_path / f"chunk_{i:02d}.mp4"
            f.write_bytes(b"x" * 1000)
            clips.append(RawClip(
                video_id="test", scene_index=i,
                clip_path=str(f),
                start=i * 120.0, end=(i + 1) * 120.0, duration=120.0,
            ))

        def fake_process(clip):
            out = Path(clip.clip_path).parent / f"{Path(clip.clip_path).stem}_ad.mp4"
            out.write_bytes(b"x" * 1000)
            return ProcessedClip(
                video_id=clip.video_id,
                scene_index=clip.scene_index,
                raw_clip_path=clip.clip_path,
                processed_clip_path=str(out),
                filters_applied=["zoom_crop"],
            )

        with patch.object(antidetect, 'process_clip', side_effect=fake_process):
            results = antidetect.process(clips)

        assert len(results) == 3


# ---------------------------------------------------------------------------
# Integration тест
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_real_antidetect():
    """
    Реальная обработка чанков из output/avatar01/.
    Запуск: pytest tests/test_antidetect.py -v -m integration

    Требует: готовые чанки из integration теста chunker
    """
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не найден в PATH")

    from config.settings import settings

    clips_dir = settings.output_dir / "avatar01"
    if not clips_dir.exists():
        pytest.skip("Сначала запусти integration тест chunker")

    chunk_files = sorted(clips_dir.glob("chunk_*.mp4"))
    # Берём только первые 2 чанка чтобы не ждать долго
    chunk_files = [f for f in chunk_files if "_ad" not in f.name][:2]

    if not chunk_files:
        pytest.skip(f"Чанки не найдены в {clips_dir}")

    clips = [
        RawClip(
            video_id="avatar01",
            scene_index=i,
            clip_path=str(f),
            start=0.0, end=120.0, duration=120.0,
        )
        for i, f in enumerate(chunk_files)
    ]

    antidetect = AntiDetect()
    results = antidetect.process(clips)

    assert len(results) == len(clips)

    for result in results:
        out = Path(result.processed_clip_path)
        assert out.exists(), f"Файл не создан: {out}"
        assert out.stat().st_size > 0
        assert "_ad" in out.name

    print(f"\n✅ AntiDetect успешен: {len(results)} клипов обработано")
    for r in results:
        size_mb = Path(r.processed_clip_path).stat().st_size / 1024 / 1024
        print(f"  {Path(r.processed_clip_path).name}: {size_mb:.1f} MB")
