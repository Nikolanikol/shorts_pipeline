"""
Автоопределение железа и выбор оптимальных настроек Whisper.

Поддерживаемые конфигурации:
  Windows + NVIDIA GPU  →  cuda + float16, размер модели по VRAM
  Mac Apple Silicon     →  cpu  + int8,    medium (M1/M2/M3 быстрые на int8)
  Mac Intel             →  cpu  + int8,    small
  CPU fallback          →  cpu  + int8,    small
"""

import platform
import subprocess
from dataclasses import dataclass

from loguru import logger


@dataclass
class HardwareProfile:
    device: str          # cuda / cpu
    compute_type: str    # float16 / int8
    model_size: str      # tiny / base / small / medium / large-v3
    label: str           # человекочитаемое описание


def _get_nvidia_vram_mb() -> int:
    """Возвращает VRAM первой NVIDIA карты в МБ. 0 если не найдена."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return int(result.stdout.strip().split("\n")[0])
    except Exception:
        pass
    return 0


def _is_apple_silicon() -> bool:
    """True если Mac на Apple Silicon (M1/M2/M3/M4)."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=3,
        )
        return "Apple" in result.stdout
    except Exception:
        return False


def detect_hardware() -> HardwareProfile:
    """
    Определяет железо и возвращает оптимальный профиль для Whisper.
    """
    system = platform.system()  # 'Windows', 'Darwin', 'Linux'

    if system in ("Windows", "Linux"):
        vram = _get_nvidia_vram_mb()
        if vram >= 10_000:
            return HardwareProfile(
                device="cuda", compute_type="float16", model_size="large-v3",
                label=f"Windows/Linux GPU — {vram // 1024}GB VRAM → large-v3",
            )
        elif vram >= 5_000:
            return HardwareProfile(
                device="cpu", compute_type="int8", model_size="medium",
                label=f"Windows/Linux GPU detected ({vram // 1024}GB) but CUDA Toolkit missing → cpu+int8",
            )
        elif vram >= 2_000:
            return HardwareProfile(
                device="cuda", compute_type="float16", model_size="small",
                label=f"Windows/Linux GPU — {vram // 1024}GB VRAM → small",
            )
        else:
            return HardwareProfile(
                device="cpu", compute_type="int8", model_size="small",
                label="Windows/Linux CPU → small",
            )

    elif system == "Darwin":  # macOS
        if _is_apple_silicon():
            return HardwareProfile(
                device="cpu", compute_type="int8", model_size="medium",
                label="Mac Apple Silicon → medium (cpu+int8, быстро на M-chip)",
            )
        else:
            return HardwareProfile(
                device="cpu", compute_type="int8", model_size="small",
                label="Mac Intel → small (cpu+int8)",
            )

    # Fallback
    return HardwareProfile(
        device="cpu", compute_type="int8", model_size="small",
        label=f"Unknown OS ({system}) → small (cpu+int8)",
    )


def log_hardware_profile(profile: HardwareProfile) -> None:
    logger.info(f"Железо: {profile.label}")
    logger.info(
        f"  Whisper: {profile.model_size} / {profile.device} / {profile.compute_type}"
    )
