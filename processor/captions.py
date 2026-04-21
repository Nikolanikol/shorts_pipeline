"""
Генерация подписей для TikTok на основе конфига shows.json.

Формат подписи:
    Аватар: Легенда об Аанге ⚡️ | Часть 3
    #аватар #аниме #мультфильм #тикток

Конфиг: config/shows.json
Ключи словаря — части имени файла (регистр не важен).
"""

import json
from pathlib import Path

SHOWS_CONFIG = Path(__file__).parent.parent / "config" / "shows.json"


def _load_shows() -> dict:
    """Загружает конфиг сериалов (без технических ключей _comment, _default)."""
    if not SHOWS_CONFIG.exists():
        return {}
    with open(SHOWS_CONFIG, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _get_default() -> dict:
    """Возвращает дефолтную конфигурацию."""
    if SHOWS_CONFIG.exists():
        with open(SHOWS_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        default = data.get("_default", {})
    else:
        default = {}
    return {
        "title": default.get("title"),
        "emoji": default.get("emoji", "🎬"),
        "hashtags": default.get("hashtags", ["#видео", "#тикток"]),
    }


def _find_show(video_id: str) -> dict:
    """
    Ищет шоу по video_id (очищенное имя файла).
    Сравнивает lowercase ключи конфига с lowercase video_id.
    Нормализует пробелы и подчёркивания для сравнения.

    Returns:
        Словарь с полями title, emoji, hashtags
    """
    shows = _load_shows()

    # Нормализация: убираем пробелы/подчёркивания и переводим в lowercase
    def normalize(s: str) -> str:
        return s.lower().replace("_", "").replace(" ", "").replace("-", "")

    video_norm = normalize(video_id)

    for key, show in shows.items():
        if normalize(key) in video_norm:
            return show

    return _get_default()


def make_caption(video_id: str, part_number: int) -> str:
    """
    Генерирует подпись для TikTok.

    Args:
        video_id: ID видео (очищенное имя файла, например "Аватар_1_сезон_1_серия")
        part_number: номер части/чанка (1, 2, 3...)

    Returns:
        Строка вида:
            "Аватар: Легенда об Аанге ⚡️ | Часть 3\\n#аватар #аниме #мультфильм #тикток"
    """
    show = _find_show(video_id)

    title = show.get("title") or video_id.replace("_", " ")
    emoji = show.get("emoji", "🎬")
    hashtags = show.get("hashtags", ["#видео", "#тикток"])

    hashtag_str = " ".join(hashtags)
    return f"{title} {emoji} | Часть {part_number}\n{hashtag_str}"
