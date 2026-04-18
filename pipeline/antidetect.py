"""
Шаг 3: Анти-бан обработка клипов.

Применяет набор ffmpeg фильтров чтобы видео не детектировалось
как повторная загрузка оригинала:

  - Zoom crop (4%) асимметричный — ломает perceptual hash сильнее центрального кропа
  - Tempo ±5.5% через atempo — ломает аудиофингерпринт (pitch сохраняется!)
  - Видеошум noise=alls=8 — сдвигает пиксельный hash без видимых артефактов
  - Насыщенность ±8% — дополнительный сдвиг цветового профиля
  - Стрип метаданных — убирает C2PA следы происхождения файла

Каждый параметр варьируется на основе хеша имени файла —
два клипа из одного эпизода получают разные трансформации.
"""

import hashlib
import subprocess
from pathlib import Path

from loguru import logger

from config.settings import settings
from models.schemas import RawClip, ProcessedClip


class AntiDetect:
    """
    Обрабатывает клипы через ffmpeg для обхода детекции повторного контента.
    Все трансформации минимальны — визуально и на слух незаметны для зрителя.
    """

    def _get_variation(self, clip_path: str, index: int) -> float:
        """
        Возвращает вариацию от -1.0 до 1.0 на основе хеша файла.
        Разные клипы → разные трансформации. Детерминировано.
        """
        hash_input = f"{clip_path}_{index}"
        h = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
        return (h % 1000) / 500.0 - 1.0

    def _build_filter_complex(self, clip_path: str, width: int, height: int) -> tuple[str, float]:
        """
        Строит строку ffmpeg видеофильтров и возвращает коэффициент скорости.

        Returns:
            (vf_filter_string, speed_factor)
        """
        v  = self._get_variation(clip_path, 0)  # для zoom + скорости
        v2 = self._get_variation(clip_path, 1)  # для насыщенности
        v3 = self._get_variation(clip_path, 2)  # для уровня шума

        # --- Zoom crop (4%) — асимметричный ---
        # Обрезаем 4% от размера, центр кропа смещён по v → разный хеш у каждого клипа
        zoom = settings.antidetect_zoom_crop
        zoom_factor = 1.0 + zoom + abs(v) * zoom  # 1.04 — 1.08
        crop_w = int(width / zoom_factor)
        crop_h = int(height / zoom_factor)
        # Асимметричный сдвиг: не от центра, а с учётом знака v
        offset_x = int((width  - crop_w) * (0.5 + v * 0.2))
        offset_y = int((height - crop_h) * (0.5 + v * 0.2))
        offset_x = max(0, min(offset_x, width  - crop_w))
        offset_y = max(0, min(offset_y, height - crop_h))

        # --- Скорость (±5.5%) ---
        # atempo принимает range [0.5, 2.0] — наш диапазон 0.945-1.055 в нём
        speed_var = settings.antidetect_speed_variation
        speed = 1.0 + v * speed_var
        speed = round(max(0.945, min(1.055, speed)), 4)

        # --- Насыщенность (±8%) ---
        saturation = round(1.0 + v2 * 0.08, 3)  # 0.92 — 1.08

        # --- Видеошум ---
        # Варьируем уровень шума в диапазоне [base, base*2]
        # allf=t = temporal noise (естественнее выглядит чем spatial)
        noise_base = settings.antidetect_noise_level
        noise_level = noise_base + int(abs(v3) * noise_base)  # 8 — 16

        # Собираем цепочку видеофильтров
        vf = (
            f"crop={crop_w}:{crop_h}:{offset_x}:{offset_y},"
            f"scale={width}:{height},"
            f"setpts={1.0/speed:.4f}*PTS,"
            f"eq=saturation={saturation},"
            f"noise=alls={noise_level}:allf=t"
        )

        return vf, speed

    def _get_video_dimensions(self, clip_path: str) -> tuple[int, int]:
        """Получает ширину и высоту видео через ffprobe."""
        cmd = [
            "ffprobe", "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            clip_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        try:
            w, h = result.stdout.strip().split(",")
            return int(w), int(h)
        except (ValueError, AttributeError):
            logger.warning(f"Не удалось получить размеры видео, используем 1280x720: {clip_path}")
            return 1280, 720

    def process_clip(self, clip: RawClip) -> ProcessedClip:
        """
        Обрабатывает один клип.

        Args:
            clip: RawClip из Chunker

        Returns:
            ProcessedClip с путём к обработанному файлу
        """
        input_path = Path(clip.clip_path)
        if not input_path.exists():
            raise FileNotFoundError(f"Клип не найден: {input_path}")

        output_path = input_path.parent / f"{input_path.stem}_ad{input_path.suffix}"

        width, height = self._get_video_dimensions(str(input_path))
        vf, speed = self._build_filter_complex(str(input_path), width, height)

        # Аудио: atempo меняет tempo БЕЗ pitch shift (в отличие от asetrate)
        # Диапазон atempo: [0.5, 2.0] — наш speed 0.945-1.055 в нём
        af = f"atempo={speed}"

        logger.debug(
            f"  {input_path.name}: speed={speed}, noise={vf.split('alls=')[1].split(':')[0]}"
        )

        from config.encoder import get_video_encoder
        enc = get_video_encoder()
        # -hwaccel cuda: декодирование на GPU
        # -threads 4: не даём занять все ядра CPU фильтрами
        hw_args = ["-hwaccel", "cuda"] if enc.name == "h264_nvenc" else []
        cmd = [
            "ffmpeg",
            *hw_args,
            "-i", str(input_path),
            "-vf", vf,
            "-af", af,
            "-map_metadata", "-1",
            *enc.args(),
            "-c:a", "aac",
            "-threads", "4",
            "-y",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"ffmpeg ошибка:\n{result.stderr[-500:]}")
            raise RuntimeError(f"AntiDetect не удался: {input_path.name}")

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(f"Выходной файл пустой: {output_path}")

        # Замена оригинального звука на фоновую музыку (если папка music/ не пуста)
        from pipeline.audio_replace import replace_audio
        from config.settings import settings
        if settings.music_dir.exists() and any(settings.music_dir.iterdir()):
            music_output = output_path.parent / f"{output_path.stem}_music{output_path.suffix}"
            final_path = replace_audio(str(output_path), str(music_output))
            if final_path == str(music_output) and music_output.exists():
                output_path.unlink()
                output_path = music_output
            filters = ["zoom_crop", "speed_variation", "saturation", "audio_tempo",
                       "video_noise", "metadata_strip", "audio_replace"]
        else:
            filters = ["zoom_crop", "speed_variation", "saturation", "audio_tempo",
                       "video_noise", "metadata_strip"]

        return ProcessedClip(
            video_id=clip.video_id,
            scene_index=clip.scene_index,
            raw_clip_path=str(input_path),
            processed_clip_path=str(output_path),
            filters_applied=filters,
            start=clip.start,
            end=clip.end,
            speed=speed,  # сохраняем для синхронизации субтитров
        )

    def process(self, clips: list[RawClip]) -> list[ProcessedClip]:
        """
        Обрабатывает все клипы из Chunker.

        Args:
            clips: список RawClip

        Returns:
            список ProcessedClip
        """
        logger.info(f"AntiDetect: обрабатываем {len(clips)} клипов...")
        processed = []

        for i, clip in enumerate(clips):
            logger.info(f"  [{i + 1}/{len(clips)}] {Path(clip.clip_path).name}")
            processed_clip = self.process_clip(clip)
            processed.append(processed_clip)

        logger.info(f"AntiDetect завершён: {len(processed)} клипов обработано")
        return processed
