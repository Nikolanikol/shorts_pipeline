"""
processor/scene_selector.py — выбор интересных моментов из интервью через Groq LLM.

Что делает:
  - Принимает транскрипт интервью (Transcript)
  - Отправляет текст в Groq Llama 3.3 70B
  - Получает список топ-N самых интересных моментов с таймкодами
  - Валидирует таймкоды (защита от галлюцинаций LLM)
  - Возвращает SceneSelection готовый для Chunker

Длинные интервью (>20k токенов):
  - Транскрипт разбивается на части с перекрытием
  - Каждая часть анализируется отдельно
  - Лучшие сцены отбираются по score из всех частей

Fallback:
  - Если Groq недоступен или GROQ_API_KEY не задан →
    возвращает None, controller использует обычный Chunker

Использование:
    from processor.scene_selector import SceneSelector
    selector = SceneSelector()
    selection = selector.process(transcript)  # SceneSelection | None
"""

import json
import time
from pathlib import Path

from loguru import logger

from config.settings import settings
from models.schemas import Transcript, Scene, SceneSelection

# Примерное число символов на токен для русского/английского текста
_CHARS_PER_TOKEN = 3.5


class SceneSelector:
    """
    Выбирает самые интересные моменты интервью через Groq API.
    При ошибке возвращает None — caller должен упасть на Chunker.
    """

    SYSTEM_PROMPT = """Ты эксперт по созданию вирусного контента для TikTok и YouTube Shorts.
Тебе дают пронумерованные блоки из транскрипта интервью (каждый ~60 секунд).
Твоя задача — выбрать самые интересные блоки и объяснить почему они зацепят зрителя.

Критерии интересного блока:
- Неожиданное или провокационное утверждение
- Сильная эмоция: смех, удивление, злость, откровенность
- Конкретный практический совет или инсайт
- Личная история или признание
- Момент где спикер говорит что-то против общего мнения
- Цифры или факты которые удивляют

Отвечай ТОЛЬКО валидным JSON без пояснений. Никакого текста до или после JSON."""

    USER_PROMPT_TEMPLATE = """Вот транскрипт интервью, разбитый на блоки по ~60 секунд:

{transcript_text}

Выбери {n} самых интересных блока для TikTok. Укажи номера блоков (block_id).

Ответь в формате JSON:
{{
  "scenes": [
    {{
      "block_id": 3,
      "title": "Короткий заголовок (до 8 слов)",
      "reason": "Почему этот блок зацепит зрителя",
      "score": 0.95
    }}
  ]
}}"""

    def __init__(self):
        self._client = None

    def _get_client(self):
        """Лениво создаёт Groq клиент."""
        if self._client is not None:
            return self._client

        if not settings.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY не задан в .env. "
                "Получи бесплатный ключ на console.groq.com"
            )

        from groq import Groq
        self._client = Groq(api_key=settings.groq_api_key)
        return self._client

    def _estimate_tokens(self, text: str) -> int:
        return int(len(text) / _CHARS_PER_TOKEN)

    def _split_transcript(self, transcript: Transcript) -> list[list]:
        """
        Разбивает транскрипт на части если он слишком длинный для одного запроса.

        Каждая часть — список сегментов. Части перекрываются на ~10% чтобы
        не пропустить сцены на границах.

        Returns:
            Список частей, каждая часть = список TranscriptSegment
        """
        max_chars = int(settings.scene_max_tokens_per_chunk * _CHARS_PER_TOKEN)
        segments = transcript.segments

        # Считаем символы каждого сегмента
        seg_chars = [len(f"[{s.start:.1f}s - {s.end:.1f}s] {s.text}\n") for s in segments]
        total_chars = sum(seg_chars)

        if total_chars <= max_chars:
            return [segments]

        # Разбиваем на части
        parts = []
        current_part = []
        current_chars = 0
        overlap_chars = int(max_chars * 0.1)

        for i, (seg, chars) in enumerate(zip(segments, seg_chars)):
            current_part.append(seg)
            current_chars += chars

            if current_chars >= max_chars:
                parts.append(current_part)

                # Откатываемся назад на overlap_chars для перекрытия
                overlap_segs = []
                overlap_total = 0
                for s in reversed(current_part):
                    seg_len = len(f"[{s.start:.1f}s - {s.end:.1f}s] {s.text}\n")
                    if overlap_total + seg_len > overlap_chars:
                        break
                    overlap_segs.insert(0, s)
                    overlap_total += seg_len

                current_part = overlap_segs
                current_chars = overlap_total

        if current_part:
            parts.append(current_part)

        logger.info(f"Транскрипт разбит на {len(parts)} частей для LLM")
        return parts

    def _build_blocks(self, segments: list, block_duration: float = 60.0) -> list[dict]:
        """
        Группирует сегменты в блоки по ~block_duration секунд.
        Каждый блок: {id, start, end, text}.
        """
        blocks = []
        current_text = []
        block_start = segments[0].start if segments else 0.0
        block_id = 1

        for seg in segments:
            current_text.append(seg.text)
            if seg.end - block_start >= block_duration:
                blocks.append({
                    "id": block_id,
                    "start": block_start,
                    "end": seg.end,
                    "text": " ".join(current_text),
                })
                block_id += 1
                block_start = seg.end
                current_text = []

        if current_text:
            blocks.append({
                "id": block_id,
                "start": block_start,
                "end": segments[-1].end,
                "text": " ".join(current_text),
            })

        return blocks

    def _format_blocks(self, blocks: list[dict]) -> str:
        """Форматирует блоки в текст для LLM."""
        lines = []
        for b in blocks:
            start_m = b["start"] / 60
            end_m = b["end"] / 60
            lines.append(f"[Блок {b['id']} | {start_m:.1f}–{end_m:.1f} мин]\n{b['text']}\n")
        return "\n".join(lines)

    def _call_groq(self, blocks: list[dict], n_scenes: int, attempt: int = 0) -> list[dict]:
        """
        Один вызов Groq API. Передаёт блоки, получает список выбранных block_id.
        При ошибке парсинга — пробует исправить. При rate limit — ждёт и повторяет.
        """
        client = self._get_client()

        transcript_text = self._format_blocks(blocks)
        prompt = self.USER_PROMPT_TEMPLATE.format(
            transcript_text=transcript_text,
            n=n_scenes,
        )

        try:
            response = client.chat.completions.create(
                model=settings.groq_model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=1024,
            )
        except Exception as e:
            error_str = str(e)
            if "rate_limit" in error_str.lower() or "429" in error_str:
                wait = 60 * (attempt + 1)
                logger.warning(f"Groq rate limit, жду {wait}с...")
                time.sleep(wait)
                if attempt < settings.groq_max_retries:
                    return self._call_groq(blocks, n_scenes, attempt + 1)
            raise

        raw = response.choices[0].message.content.strip()

        # Парсим JSON — LLM иногда оборачивает в ```json ... ```
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON ошибка от Groq: {e}\nОтвет: {raw[:300]}")
            if attempt < settings.groq_max_retries:
                logger.info(f"Повтор {attempt + 1}/{settings.groq_max_retries}...")
                time.sleep(2)
                return self._call_groq(blocks, n_scenes, attempt + 1)
            return []

        # Превращаем block_id обратно в таймкоды
        block_map = {b["id"]: b for b in blocks}
        result = []
        for raw_scene in data.get("scenes", []):
            bid = raw_scene.get("block_id")
            if bid and bid in block_map:
                b = block_map[bid]
                result.append({
                    "start": b["start"],
                    "end": b["end"],
                    "title": raw_scene.get("title", ""),
                    "reason": raw_scene.get("reason", ""),
                    "score": raw_scene.get("score", 0.5),
                })
        return result

    def _validate_scene(self, raw: dict, transcript: Transcript) -> Scene | None:
        """
        Валидирует одну сцену от LLM.

        Проверяет:
        - start < end
        - таймкоды внутри длины видео
        - длительность 20–180 секунд
        - start/end реально присутствуют в транскрипте (±5 сек)

        Returns:
            Scene если валидна, None если нет
        """
        try:
            start = float(raw.get("start", 0))
            end = float(raw.get("end", 0))
            score = float(raw.get("score", 0.5))
            title = str(raw.get("title", "")).strip() or None
            reason = str(raw.get("reason", "")).strip() or "Интересный момент"
        except (TypeError, ValueError) as e:
            logger.debug(f"Сцена невалидна (тип): {e} | {raw}")
            return None

        duration = end - start

        if start < 0 or end <= start:
            logger.debug(f"Сцена невалидна: start={start} end={end}")
            return None

        if end > transcript.duration + 5:
            logger.debug(f"Сцена за пределами видео: end={end} > {transcript.duration}")
            return None

        if duration < 20:
            logger.debug(f"Сцена слишком короткая: {duration:.1f}с")
            return None

        if duration > 180:
            logger.debug(f"Сцена слишком длинная: {duration:.1f}с, обрезаем до 180с")
            end = start + 180

        # Привязываем start к ближайшему началу сегмента (в пределах 30 сек)
        seg_starts = [s.start for s in transcript.segments]
        closest_start = min(seg_starts, key=lambda s: abs(s - start))
        if abs(closest_start - start) <= 30:
            start = closest_start
        # Если разрыв больше 30 сек — оставляем как есть (LLM дал промежуточный таймкод)

        score = max(0.0, min(1.0, score))

        return Scene(start=start, end=end, reason=reason, score=score, title=title)

    def _deduplicate(self, scenes: list[Scene], min_gap: float = 30.0) -> list[Scene]:
        """
        Убирает перекрывающиеся сцены.
        Из двух перекрывающихся оставляет ту у которой score выше.
        """
        if not scenes:
            return []

        scenes = sorted(scenes, key=lambda s: s.score, reverse=True)
        result = []

        for scene in scenes:
            overlap = False
            for kept in result:
                # Проверяем перекрытие с запасом min_gap
                if scene.start < kept.end + min_gap and scene.end > kept.start - min_gap:
                    overlap = True
                    break
            if not overlap:
                result.append(scene)

        return sorted(result, key=lambda s: s.start)

    def process(self, transcript: Transcript) -> SceneSelection | None:
        """
        Основной метод. Отправляет транскрипт в Groq и возвращает SceneSelection.

        Args:
            transcript: готовый транскрипт из Transcriber

        Returns:
            SceneSelection с топ-N сценами, или None если Groq недоступен
        """
        if not settings.groq_api_key:
            logger.warning("GROQ_API_KEY не задан — SceneSelector отключён")
            return None

        if settings.scene_selector_backend == "none":
            logger.info("scene_selector_backend=none — используем обычную нарезку")
            return None

        if not transcript.segments:
            logger.warning("Транскрипт пустой — SceneSelector пропущен")
            return None

        logger.info(
            f"SceneSelector: анализирую {transcript.duration / 60:.1f} мин, "
            f"выбираю топ {settings.scenes_to_select} сцен..."
        )

        # Строим блоки по ~60 секунд
        all_blocks = self._build_blocks(transcript.segments, block_duration=60.0)
        logger.debug(f"Блоков (~60с каждый): {len(all_blocks)}")

        # Делим блоки на части если транскрипт слишком длинный для одного запроса
        max_chars = int(settings.scene_max_tokens_per_chunk * _CHARS_PER_TOKEN)
        parts_blocks: list[list] = []
        current_part: list = []
        current_chars = 0
        for block in all_blocks:
            block_chars = len(block["text"]) + 50
            if current_chars + block_chars > max_chars and current_part:
                parts_blocks.append(current_part)
                current_part = []
                current_chars = 0
            current_part.append(block)
            current_chars += block_chars
        if current_part:
            parts_blocks.append(current_part)

        n_per_part = max(settings.scenes_to_select, settings.scenes_to_select * 2 // len(parts_blocks) + 1)
        all_raw_scenes = []

        for i, part_blocks in enumerate(parts_blocks):
            if len(parts_blocks) > 1:
                logger.info(
                    f"  Часть {i + 1}/{len(parts_blocks)}: "
                    f"блоки {part_blocks[0]['id']}–{part_blocks[-1]['id']}"
                )

            try:
                raw_scenes = self._call_groq(part_blocks, n_per_part)
                all_raw_scenes.extend(raw_scenes)
                logger.debug(f"  Часть {i + 1}: получено {len(raw_scenes)} сцен от LLM")
            except Exception as e:
                logger.error(f"Groq ошибка в части {i + 1}: {e}")
                if i == 0 and len(parts_blocks) == 1:
                    logger.warning("SceneSelector недоступен, используем Chunker")
                    return None
                continue

            if i < len(parts_blocks) - 1:
                time.sleep(3)

        if not all_raw_scenes:
            logger.warning("SceneSelector не вернул ни одной сцены")
            return None

        # Валидируем каждую сцену
        valid_scenes = []
        for raw in all_raw_scenes:
            scene = self._validate_scene(raw, transcript)
            if scene:
                valid_scenes.append(scene)

        logger.info(f"Валидных сцен: {len(valid_scenes)} из {len(all_raw_scenes)}")

        if not valid_scenes:
            logger.warning("Ни одна сцена не прошла валидацию — используем Chunker")
            return None

        # Убираем дубли и оставляем топ-N
        unique_scenes = self._deduplicate(valid_scenes)
        top_scenes = sorted(unique_scenes, key=lambda s: s.score, reverse=True)[:settings.scenes_to_select]
        top_scenes = sorted(top_scenes, key=lambda s: s.start)

        for scene in top_scenes:
            logger.info(
                f"  ✓ {scene.start / 60:.1f}м–{scene.end / 60:.1f}м "
                f"({scene.duration:.0f}с, score={scene.score:.2f}): {scene.title or scene.reason[:50]}"
            )

        return SceneSelection(
            video_id=transcript.video_id,
            scenes=top_scenes,
            model_used=settings.groq_model,
        )
