"""
Тесты для Formatter.

Запуск unit тестов:
    pytest tests/test_formatter.py -v

Интеграционный тест (реальное форматирование):
    pytest tests/test_formatter.py -v -m integration
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from models.schemas import ProcessedClip, FinalShort
from processor.formatter import Formatter, PLATFORMS


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def restore_settings():
    from config.settings import settings
    original_output_dir = settings.output_dir
    yield
    settings.output_dir = original_output_dir


@pytest.fixture
def formatter():
    return Formatter()


@pytest.fixture
def sample_clip(tmp_path):
    """Фейковый ProcessedClip с реальным файлом."""
    clip_file = tmp_path / "chunk_01_ad.mp4"
    clip_file.write_bytes(b"fake video" * 1000)
    return ProcessedClip(
        video_id="avatar01",
        scene_index=0,
        raw_clip_path=str(tmp_path / "chunk_01.mp4"),
        processed_clip_path=str(clip_file),
        filters_applied=["zoom_crop", "speed_variation"],
    )


# ---------------------------------------------------------------------------
# Платформы
# ---------------------------------------------------------------------------

class TestPlatforms:

    def test_all_platforms_defined(self):
        """Все три платформы присутствуют."""
        assert "youtube_shorts" in PLATFORMS
        assert "tiktok" in PLATFORMS
        assert "reels" in PLATFORMS

    def test_all_platforms_are_vertical(self):
        """Все платформы вертикальные 9:16."""
        for name, p in PLATFORMS.items():
            assert p.width == 1080, f"{name}: ширина должна быть 1080"
            assert p.height == 1920, f"{name}: высота должна быть 1920"

    def test_youtube_shorts_max_duration(self):
        assert PLATFORMS["youtube_shorts"].max_duration == 60

    def test_reels_longer_than_shorts(self):
        assert PLATFORMS["reels"].max_duration > PLATFORMS["youtube_shorts"].max_duration


# ---------------------------------------------------------------------------
# _build_vertical_filter
# ---------------------------------------------------------------------------

class TestBuildVerticalFilter:

    def test_horizontal_video_gets_blur_bg(self, formatter):
        """Горизонтальное видео (16:9) получает blur-фон фильтр."""
        vf = formatter._build_vertical_filter(1280, 720, 1080, 1920, 30)
        assert "boxblur" in vf
        assert "overlay" in vf
        assert "scale" in vf

    def test_filter_sets_correct_fps(self, formatter):
        """FPS в фильтре соответствует заданному."""
        vf = formatter._build_vertical_filter(1280, 720, 1080, 1920, 30)
        assert "fps=30" in vf

    def test_vertical_video_also_gets_blur_bg(self, formatter):
        """Вертикальное видео тоже получает фон."""
        vf = formatter._build_vertical_filter(720, 1280, 1080, 1920, 30)
        assert "boxblur" in vf

    def test_output_dimensions_in_filter(self, formatter):
        """Финальные размеры в фильтре соответствуют целевым."""
        vf = formatter._build_vertical_filter(1280, 720, 1080, 1920, 30)
        # scale фона должен содержать целевую ширину
        assert "1080" in vf
        assert "1920" in vf

    def test_filter_is_string(self, formatter):
        vf = formatter._build_vertical_filter(1280, 720, 1080, 1920, 30)
        assert isinstance(vf, str)
        assert len(vf) > 0


# ---------------------------------------------------------------------------
# format_clip
# ---------------------------------------------------------------------------

class TestFormatClip:

    def test_raises_on_unknown_platform(self, formatter, sample_clip):
        with pytest.raises(ValueError, match="Неизвестная платформа"):
            formatter.format_clip(sample_clip, "instagram_feed")

    def test_raises_if_clip_not_found(self, formatter):
        clip = ProcessedClip(
            video_id="x", scene_index=0,
            raw_clip_path="/fake/raw.mp4",
            processed_clip_path="/nonexistent/clip.mp4",
            filters_applied=[],
        )
        with pytest.raises(FileNotFoundError):
            formatter.format_clip(clip, "youtube_shorts")

    def test_returns_final_short(self, formatter, sample_clip, tmp_path):
        """format_clip возвращает FinalShort с правильными полями."""
        from config.settings import settings
        settings.output_dir = tmp_path

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch('subprocess.run', return_value=mock_result), \
             patch.object(formatter, '_get_video_info', return_value=(1280, 720, 45.0)):

            # Создаём выходной файл вручную
            out_dir = tmp_path / "avatar01" / "youtube_shorts"
            out_dir.mkdir(parents=True)
            out_file = out_dir / "chunk_01_youtube_shorts.mp4"
            out_file.write_bytes(b"x" * 1000)

            result = formatter.format_clip(sample_clip, "youtube_shorts")

        assert isinstance(result, FinalShort)
        assert result.video_id == "avatar01"
        assert result.platform == "youtube_shorts"
        assert result.scene_index == 0

    def test_duration_capped_at_platform_max(self, formatter, sample_clip, tmp_path):
        """Длительность в FinalShort не превышает лимит платформы."""
        from config.settings import settings
        settings.output_dir = tmp_path

        mock_result = MagicMock()
        mock_result.returncode = 0

        # Клип 90с > лимит YouTube Shorts 60с
        with patch('subprocess.run', return_value=mock_result), \
             patch.object(formatter, '_get_video_info', return_value=(1280, 720, 90.0)):

            out_dir = tmp_path / "avatar01" / "youtube_shorts"
            out_dir.mkdir(parents=True)
            out_file = out_dir / "chunk_01_youtube_shorts.mp4"
            out_file.write_bytes(b"x" * 1000)

            result = formatter.format_clip(sample_clip, "youtube_shorts")

        assert result.duration == 60  # обрезан до лимита


# ---------------------------------------------------------------------------
# process (все клипы)
# ---------------------------------------------------------------------------

class TestProcess:

    def test_processes_all_clips_single_platform(self, formatter, tmp_path):
        """process() с одной платформой возвращает len(clips) результатов."""
        from config.settings import settings
        settings.output_dir = tmp_path

        clips = []
        for i in range(3):
            f = tmp_path / f"chunk_{i:02d}_ad.mp4"
            f.write_bytes(b"x" * 1000)
            clips.append(ProcessedClip(
                video_id="test", scene_index=i,
                raw_clip_path=str(tmp_path / f"chunk_{i:02d}.mp4"),
                processed_clip_path=str(f),
                filters_applied=[],
            ))

        def fake_format(clip, platform_name):
            out_dir = tmp_path / clip.video_id / platform_name
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"chunk_{clip.scene_index:02d}_{platform_name}.mp4"
            out.write_bytes(b"x" * 1000)
            return FinalShort(
                video_id=clip.video_id,
                scene_index=clip.scene_index,
                platform=platform_name,
                output_path=str(out),
                duration=60.0,
            )

        with patch.object(formatter, 'format_clip', side_effect=fake_format):
            results = formatter.process(clips, platforms=["youtube_shorts"])

        assert len(results) == 3

    def test_processes_multiple_platforms(self, formatter, tmp_path):
        """process() с двумя платформами возвращает len(clips)*2 результатов."""
        from config.settings import settings
        settings.output_dir = tmp_path

        clips = []
        for i in range(2):
            f = tmp_path / f"chunk_{i:02d}_ad.mp4"
            f.write_bytes(b"x" * 1000)
            clips.append(ProcessedClip(
                video_id="test", scene_index=i,
                raw_clip_path=str(tmp_path / f"chunk_{i:02d}.mp4"),
                processed_clip_path=str(f),
                filters_applied=[],
            ))

        def fake_format(clip, platform_name):
            out_dir = tmp_path / clip.video_id / platform_name
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"out_{platform_name}.mp4"
            out.write_bytes(b"x" * 1000)
            return FinalShort(
                video_id=clip.video_id, scene_index=clip.scene_index,
                platform=platform_name, output_path=str(out), duration=60.0,
            )

        with patch.object(formatter, 'format_clip', side_effect=fake_format):
            results = formatter.process(clips, platforms=["youtube_shorts", "tiktok"])

        assert len(results) == 4  # 2 клипа × 2 платформы

    def test_default_platform_is_youtube_shorts(self, formatter, tmp_path):
        """Без явного указания платформы используется youtube_shorts."""
        from config.settings import settings
        settings.output_dir = tmp_path

        f = tmp_path / "chunk_01_ad.mp4"
        f.write_bytes(b"x" * 1000)
        clip = ProcessedClip(
            video_id="test", scene_index=0,
            raw_clip_path="/fake/raw.mp4",
            processed_clip_path=str(f),
            filters_applied=[],
        )

        calls = []

        def fake_format(c, platform_name):
            calls.append(platform_name)
            out = tmp_path / f"out_{platform_name}.mp4"
            out.write_bytes(b"x" * 1000)
            return FinalShort(
                video_id="test", scene_index=0,
                platform=platform_name, output_path=str(out), duration=60.0,
            )

        with patch.object(formatter, 'format_clip', side_effect=fake_format):
            formatter.process([clip])

        assert calls == ["youtube_shorts"]


# ---------------------------------------------------------------------------
# Integration тест
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_real_formatting():
    """
    Реальное форматирование первых 2 _ad клипов в вертикальный формат.
    Запуск: pytest tests/test_formatter.py -v -m integration

    Требует: готовые _ad файлы из integration теста antidetect
    """
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не найден в PATH")

    from config.settings import settings

    clips_dir = settings.output_dir / "avatar01"
    if not clips_dir.exists():
        pytest.skip("Сначала запусти integration тест antidetect")

    ad_files = sorted(clips_dir.glob("chunk_*_ad.mp4"))[:2]
    if not ad_files:
        pytest.skip(f"_ad файлы не найдены в {clips_dir}")

    clips = [
        ProcessedClip(
            video_id="avatar01",
            scene_index=i,
            raw_clip_path=str(f).replace("_ad.mp4", ".mp4"),
            processed_clip_path=str(f),
            filters_applied=["zoom_crop", "speed_variation"],
        )
        for i, f in enumerate(ad_files)
    ]

    formatter = Formatter()
    results = formatter.process(clips, platforms=["youtube_shorts"])

    assert len(results) == len(clips)

    for final in results:
        out = Path(final.output_path)
        assert out.exists(), f"Файл не создан: {out}"
        assert out.stat().st_size > 0

    print(f"\n✅ Formatter успешен: {len(results)} шортсов")
    for r in results:
        size_mb = Path(r.output_path).stat().st_size / 1024 / 1024
        print(f"  {Path(r.output_path).name}: {r.duration:.0f}с, {size_mb:.1f} MB")
