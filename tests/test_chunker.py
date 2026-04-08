"""
Тесты для Chunker.

Запуск всех тестов:
    pytest tests/test_chunker.py -v

Интеграционный тест (реальная нарезка видео):
    pytest tests/test_chunker.py -v -m integration
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from models.schemas import Transcript, TranscriptSegment, RawClip
from pipeline.chunker import Chunker, ChunkBoundary


# ---------------------------------------------------------------------------
# Фикстура: восстанавливает settings после каждого теста
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def restore_settings():
    """Сохраняет и восстанавливает settings.output_dir после каждого теста."""
    from config.settings import settings
    original_output_dir = settings.output_dir
    original_checkpoint_dir = settings.checkpoint_dir
    original_temp_dir = settings.temp_dir
    yield
    settings.output_dir = original_output_dir
    settings.checkpoint_dir = original_checkpoint_dir
    settings.temp_dir = original_temp_dir


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def make_transcript(segments: list[tuple[float, float, str]], duration: float = None) -> Transcript:
    """Создаёт транскрипт из списка (start, end, text)."""
    segs = [TranscriptSegment(start=s, end=e, text=t) for s, e, t in segments]
    return Transcript(
        video_id="test001",
        video_path="/tmp/test001.mp4",
        duration=duration or (segs[-1].end + 5.0 if segs else 0.0),
        language="ru",
        segments=segs,
    )


# ---------------------------------------------------------------------------
# find_silences
# ---------------------------------------------------------------------------

class TestFindSilences:

    def test_finds_gap_between_segments(self):
        transcript = make_transcript([
            (0.0, 5.0, "фраза один"),
            (8.0, 12.0, "фраза два"),   # пауза 3.0с
            (12.5, 16.0, "фраза три"),  # пауза 0.5с
        ])
        chunker = Chunker()
        silences = chunker.find_silences(transcript)

        assert len(silences) == 2
        assert silences[0] == (5.0, 3.0)   # старт паузы, длина
        assert silences[1] == (12.0, 0.5)

    def test_ignores_short_gaps(self):
        """Паузы короче chunk_min_silence игнорируются."""
        transcript = make_transcript([
            (0.0, 5.0, "фраза один"),
            (5.1, 10.0, "фраза два"),   # пауза 0.1с — слишком короткая
            (12.0, 16.0, "фраза три"), # пауза 2.0с — берём
        ])
        chunker = Chunker()
        silences = chunker.find_silences(transcript)

        # только пауза 2.0с должна попасть (0.1 < min_silence=0.3)
        assert len(silences) == 1
        assert silences[0][0] == 10.0

    def test_empty_segments_returns_empty(self):
        transcript = Transcript(
            video_id="x", video_path="/tmp/x.mp4",
            duration=100.0, language="ru", segments=[]
        )
        chunker = Chunker()
        assert chunker.find_silences(transcript) == []

    def test_no_gaps_returns_empty(self):
        """Если сегменты идут без пауз — пусто."""
        transcript = make_transcript([
            (0.0, 5.0, "раз"),
            (5.0, 10.0, "два"),
            (10.0, 15.0, "три"),
        ])
        chunker = Chunker()
        assert chunker.find_silences(transcript) == []


# ---------------------------------------------------------------------------
# find_best_cut
# ---------------------------------------------------------------------------

class TestFindBestCut:

    def test_picks_longest_silence_in_window(self):
        """Из нескольких пауз в окне выбирается самая длинная."""
        silences = [
            (115.0, 0.5),   # близко к цели, но короткая
            (118.0, 2.5),   # длиннее — должна выиграть
            (122.0, 1.0),
        ]
        chunker = Chunker()
        boundary = chunker.find_best_cut(target=120.0, silences=silences, video_duration=600.0)

        # Рез в середине паузы 118.0 + 2.5/2 = 119.25
        assert boundary.position == pytest.approx(119.25)
        assert boundary.silence_duration == 2.5
        assert boundary.target == 120.0

    def test_cuts_at_target_when_no_silences(self):
        """Если пауз нет — режет точно в target."""
        chunker = Chunker()
        boundary = chunker.find_best_cut(target=120.0, silences=[], video_duration=600.0)

        assert boundary.position == 120.0
        assert boundary.silence_duration == 0.0

    def test_ignores_silences_outside_window(self):
        """Паузы за пределами окна не учитываются."""
        silences = [
            (50.0, 5.0),    # далеко от target=120
            (200.0, 5.0),   # тоже далеко
        ]
        chunker = Chunker()
        boundary = chunker.find_best_cut(target=120.0, silences=silences, video_duration=600.0)

        assert boundary.position == 120.0  # нет кандидатов → режем точно

    def test_does_not_exceed_video_duration(self):
        """Точка реза не выходит за длительность видео."""
        chunker = Chunker()
        boundary = chunker.find_best_cut(target=600.0, silences=[], video_duration=500.0)

        assert boundary.position <= 500.0


# ---------------------------------------------------------------------------
# calculate_boundaries
# ---------------------------------------------------------------------------

class TestCalculateBoundaries:

    def test_correct_number_of_boundaries(self):
        """Для 10-минутного видео с chunk=120с должно быть ~4 границы."""
        # 10 мин = 600с, chunk=120с → границы примерно на 120,240,360,480 → 4 границы → 5 чанков
        segments = []
        for i in range(60):
            segments.append((i * 10.0, i * 10.0 + 8.0, f"фраза {i}"))

        transcript = make_transcript(segments, duration=600.0)
        chunker = Chunker()
        boundaries = chunker.calculate_boundaries(transcript)

        assert len(boundaries) == 4

    def test_boundaries_are_sorted(self):
        """Границы идут в хронологическом порядке."""
        segments = [(i * 5.0, i * 5.0 + 4.0, f"фраза {i}") for i in range(100)]
        transcript = make_transcript(segments, duration=600.0)
        chunker = Chunker()
        boundaries = chunker.calculate_boundaries(transcript)

        positions = [b.position for b in boundaries]
        assert positions == sorted(positions)

    def test_short_video_no_boundaries(self):
        """Видео короче chunk_duration → 0 границ → 1 чанк."""
        transcript = make_transcript(
            [(0.0, 5.0, "короткое видео")],
            duration=60.0,  # 1 минута < chunk_duration=120с
        )
        chunker = Chunker()
        boundaries = chunker.calculate_boundaries(transcript)
        assert len(boundaries) == 0


# ---------------------------------------------------------------------------
# process (с моком ffmpeg)
# ---------------------------------------------------------------------------

class TestProcess:

    def test_creates_correct_number_of_clips(self, tmp_path):
        """process() возвращает правильное количество RawClip."""
        from config.settings import settings
        settings.output_dir = tmp_path

        # 5-минутное видео с паузами
        segments = [(i * 8.0, i * 8.0 + 7.0, f"фраза {i}") for i in range(40)]
        transcript = make_transcript(segments, duration=300.0)
        transcript.video_id = "test_chunks"
        transcript.video_path = "/tmp/fake.mp4"

        chunker = Chunker()

        def fake_cut(video_path, output_path, start, end):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake video " * 1000)

        with patch.object(chunker, '_cut_clip', side_effect=fake_cut):
            clips = chunker.process(transcript)

        # 300с / 120с ≈ 2-3 чанка
        assert len(clips) >= 2
        assert all(isinstance(c, RawClip) for c in clips)

    def test_clips_have_overlap(self, tmp_path):
        """Начало каждого следующего чанка = конец предыдущего - overlap."""
        from config.settings import settings
        settings.output_dir = tmp_path

        segments = [(i * 8.0, i * 8.0 + 7.0, f"фраза {i}") for i in range(40)]
        transcript = make_transcript(segments, duration=300.0)
        transcript.video_id = "test_overlap"
        transcript.video_path = "/tmp/fake.mp4"

        chunker = Chunker()

        def fake_cut(video_path, output_path, start, end):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"x" * 1000)

        with patch.object(chunker, '_cut_clip', side_effect=fake_cut):
            clips = chunker.process(transcript)

        # Проверяем overlap: начало[i+1] должно быть меньше конца[i]
        for i in range(len(clips) - 1):
            assert clips[i + 1].start < clips[i].end, (
                f"Чанк {i+1} начинается после конца чанка {i} — нет overlap"
            )

    def test_first_clip_starts_at_zero(self, tmp_path):
        """Первый чанк всегда начинается с 0."""
        from config.settings import settings
        settings.output_dir = tmp_path

        segments = [(i * 8.0, i * 8.0 + 7.0, f"фраза {i}") for i in range(40)]
        transcript = make_transcript(segments, duration=300.0)
        transcript.video_id = "test_start"
        transcript.video_path = "/tmp/fake.mp4"

        chunker = Chunker()

        def fake_cut(video_path, output_path, start, end):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"x" * 1000)

        with patch.object(chunker, '_cut_clip', side_effect=fake_cut):
            clips = chunker.process(transcript)

        assert clips[0].start == 0.0

    def test_last_clip_ends_at_video_end(self, tmp_path):
        """Последний чанк заканчивается точно на длительности видео."""
        from config.settings import settings
        settings.output_dir = tmp_path

        segments = [(i * 8.0, i * 8.0 + 7.0, f"фраза {i}") for i in range(40)]
        transcript = make_transcript(segments, duration=300.0)
        transcript.video_id = "test_end"
        transcript.video_path = "/tmp/fake.mp4"

        chunker = Chunker()

        def fake_cut(video_path, output_path, start, end):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"x" * 1000)

        with patch.object(chunker, '_cut_clip', side_effect=fake_cut):
            clips = chunker.process(transcript)

        assert clips[-1].end == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# Integration тест
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_real_chunking():
    """
    Реальная нарезка avatar0101.mp4 по транскрипту.
    Запуск: pytest tests/test_chunker.py -v -m integration

    Требует: готовый checkpoint транскрипта в checkpoints/avatar01_transcript.json
    """
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не найден в PATH")

    from config.settings import settings

    checkpoint = settings.checkpoint_dir / "avatar01_transcript.json"
    if not checkpoint.exists():
        pytest.skip(f"Сначала запусти integration тест транскрибатора. Не найден: {checkpoint}")

    transcript = Transcript.model_validate_json(checkpoint.read_text())

    chunker = Chunker()
    clips = chunker.process(transcript)

    # Базовые проверки
    assert len(clips) > 0
    assert clips[0].start == 0.0
    assert clips[-1].end == pytest.approx(transcript.duration, abs=1.0)

    # Проверяем overlap
    for i in range(len(clips) - 1):
        assert clips[i + 1].start < clips[i].end

    # Проверяем что файлы реально созданы
    for clip in clips:
        path = Path(clip.clip_path)
        assert path.exists(), f"Файл не создан: {path}"
        assert path.stat().st_size > 0, f"Файл пустой: {path}"

    print(f"\n✅ Нарезка успешна: {len(clips)} чанков")
    for clip in clips:
        print(
            f"  Чанк {clip.scene_index + 1}: "
            f"{clip.start / 60:.1f}м — {clip.end / 60:.1f}м "
            f"({clip.duration:.0f}с) → {Path(clip.clip_path).name}"
        )
