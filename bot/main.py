"""
Telegram бот для доставки TikTok-готовых видео на телефон.

Команды:
  /start   — приветствие
  /queue   — сколько видео в очереди
  /next    — отправить следующее видео прямо сейчас
  /status  — последние 10 видео

Кнопки под каждым видео:
  ✅ Опубликовано  — помечает как posted
  ⏭ Пропустить    — помечает как skipped, шлёт следующее
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import RetryAfter, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from bot.queue_db import (
    add_video,
    get_next_pending,
    get_recent,
    get_stats,
    init_db,
    update_status,
)

# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).parent.parent / ".env")

TOKEN    = os.getenv("TELEGRAM_TOKEN")
OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "0"))

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

MAX_FILE_MB = 49  # Telegram Bot API лимит 50 МБ


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _owner_only(func):
    """Декоратор — только владелец может управлять ботом."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != OWNER_ID:
            return  # молча игнорируем чужих
        return await func(update, context)
    return wrapper


def _make_buttons(item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Опубликовано", callback_data=f"posted_{item_id}"),
        InlineKeyboardButton("⏭ Пропустить",   callback_data=f"skip_{item_id}"),
    ]])


async def _send_video(context: ContextTypes.DEFAULT_TYPE, item) -> bool:
    """
    Отправляет видео в Telegram как документ (без сжатия).
    Возвращает True если успешно.
    """
    video_path = Path(item.video_path)

    if not video_path.exists():
        log.error(f"Файл не найден: {video_path}")
        update_status(item.id, "failed")
        await context.bot.send_message(
            OWNER_ID,
            f"❌ Файл не найден:\n`{video_path.name}`",
            parse_mode="Markdown",
        )
        return False

    size_mb = video_path.stat().st_size / 1024 / 1024
    if size_mb > MAX_FILE_MB:
        log.warning(f"Файл {size_mb:.1f} МБ > {MAX_FILE_MB} МБ лимит")
        await context.bot.send_message(
            OWNER_ID,
            f"⚠️ Файл слишком большой ({size_mb:.1f} МБ):\n`{video_path.name}`\n\nМаксимум {MAX_FILE_MB} МБ. Проверь настройки CQ в пайплайне.",
            parse_mode="Markdown",
        )
        update_status(item.id, "failed")
        return False

    caption = item.caption or video_path.stem
    caption_full = f"📹 *{video_path.name}*\n\n{caption}"

    for attempt in range(3):
        try:
            with open(video_path, "rb") as f:
                msg = await context.bot.send_document(
                    chat_id=OWNER_ID,
                    document=f,
                    filename=video_path.name,
                    caption=caption_full,
                    parse_mode="Markdown",
                    reply_markup=_make_buttons(item.id),
                    read_timeout=300,
                    write_timeout=300,
                )
            update_status(item.id, "pending", message_id=msg.message_id)
            log.info(f"Отправлено: {video_path.name} ({size_mb:.1f} МБ)")
            return True

        except RetryAfter as e:
            log.warning(f"Rate limit, жду {e.retry_after}с...")
            import asyncio
            await asyncio.sleep(e.retry_after + 1)

        except TimedOut:
            log.warning(f"Timeout (попытка {attempt + 1}/3)")
            import asyncio
            await asyncio.sleep(5 * (2 ** attempt))

        except Exception as e:
            log.error(f"Ошибка отправки: {e}")
            if attempt == 2:
                update_status(item.id, "failed")
                await context.bot.send_message(
                    OWNER_ID,
                    f"❌ Не удалось отправить `{video_path.name}`:\n{e}",
                    parse_mode="Markdown",
                )
                return False
            import asyncio
            await asyncio.sleep(5)

    return False


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@_owner_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_stats()
    await update.message.reply_text(
        f"👋 Привет! Я доставляю видео для TikTok.\n\n"
        f"📊 Очередь:\n"
        f"  ⏳ Ожидают: {stats['pending']}\n"
        f"  ✅ Опубликовано: {stats['sent']}\n"
        f"  ⏭ Пропущено: {stats['skipped']}\n\n"
        f"Команды:\n"
        f"/next — следующее видео прямо сейчас\n"
        f"/queue — статус очереди\n"
        f"/status — последние 10 видео",
    )


@_owner_only
async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_stats()
    await update.message.reply_text(
        f"📊 Очередь:\n"
        f"  ⏳ Ожидают: {stats['pending']}\n"
        f"  ✅ Опубликовано: {stats['sent']}\n"
        f"  ⏭ Пропущено: {stats['skipped']}\n"
        f"  ❌ Ошибки: {stats['failed']}"
    )


@_owner_only
async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправить следующее видео из очереди немедленно."""
    item = get_next_pending()
    if not item:
        await update.message.reply_text("✅ Очередь пуста — нечего отправлять!")
        return
    await update.message.reply_text("⏳ Отправляю...")
    await _send_video(context, item)


@_owner_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Последние 10 видео."""
    items = get_recent(10)
    if not items:
        await update.message.reply_text("Очередь пуста.")
        return

    lines = []
    icons = {"pending": "⏳", "sent": "✅", "skipped": "⏭", "failed": "❌"}
    for item in items:
        icon = icons.get(item.status, "❓")
        name = Path(item.video_path).name
        lines.append(f"{icon} `{name}`")

    await update.message.reply_text(
        "📋 Последние видео:\n\n" + "\n".join(lines),
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Кнопки
# ---------------------------------------------------------------------------

@_owner_only
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, item_id = query.data.rsplit("_", 1)
    item_id = int(item_id)

    if action == "posted":
        update_status(item_id, "sent")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("✅ Отмечено как опубликовано!")

        # Авто-присылаем следующее
        next_item = get_next_pending()
        if next_item:
            stats = get_stats()
            await query.message.reply_text(
                f"⏳ В очереди ещё {stats['pending']} видео. Отправляю следующее..."
            )
            await _send_video(context, next_item)
        else:
            await query.message.reply_text("🎉 Очередь пуста — всё опубликовано!")

    elif action == "skip":
        update_status(item_id, "skipped")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("⏭ Пропущено.")

        next_item = get_next_pending()
        if next_item:
            await _send_video(context, next_item)


# ---------------------------------------------------------------------------
# Job: авто-отправка по расписанию
# ---------------------------------------------------------------------------

async def job_send_scheduled(context: ContextTypes.DEFAULT_TYPE):
    """Запускается по расписанию (10:00 и 18:00). Шлёт следующее видео."""
    item = get_next_pending()
    if item:
        log.info(f"Авто-отправка по расписанию: {Path(item.video_path).name}")
        await _send_video(context, item)
    else:
        log.info("Авто-отправка: очередь пуста")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main():
    if not TOKEN or OWNER_ID == 0:
        print("❌ Заполни TELEGRAM_TOKEN и TELEGRAM_OWNER_ID в файле .env")
        return

    init_db()
    log.info(f"Бот запущен. Owner ID: {OWNER_ID}")

    app = Application.builder().token(TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("queue",  cmd_queue))
    app.add_handler(CommandHandler("next",   cmd_next))
    app.add_handler(CommandHandler("status", cmd_status))

    # Кнопки
    app.add_handler(CallbackQueryHandler(on_button))

    # Расписание: 10:00 и 18:00 каждый день
    app.job_queue.run_daily(job_send_scheduled, time=__import__("datetime").time(10, 0))
    app.job_queue.run_daily(job_send_scheduled, time=__import__("datetime").time(18, 0))

    log.info("Расписание: 10:00 и 18:00 ежедневно")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
