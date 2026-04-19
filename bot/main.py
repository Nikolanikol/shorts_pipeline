"""
Telegram бот — только уведомления о статусе пайплайна и публикации.

Команды:
  /start   — приветствие
  /queue   — сколько видео в очереди
  /status  — последние 10 видео

Отправка видео на телефон отключена (используем авто-публикацию в TikTok).
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from bot.queue_db import get_recent, get_stats, init_db

# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).parent.parent / ".env")

TOKEN    = os.getenv("TELEGRAM_TOKEN")
OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "0"))

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------

def _owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != OWNER_ID:
            return
        return await func(update, context)
    return wrapper


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@_owner_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_stats()
    await update.message.reply_text(
        f"👋 Привет! Слежу за пайплайном.\n\n"
        f"📊 Очередь:\n"
        f"  ⏳ Ожидают: {stats['pending']}\n"
        f"  ✅ Опубликовано: {stats['sent']}\n"
        f"  ⏭ Пропущено: {stats['skipped']}\n"
        f"  ❌ Ошибки: {stats['failed']}\n\n"
        f"Команды:\n"
        f"/queue — статус очереди\n"
        f"/status — последние 10 видео",
    )


@_owner_only
async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_stats()
    total = sum(stats.values())
    await update.message.reply_text(
        f"📊 Очередь ({total} всего):\n"
        f"  ⏳ Ожидают: {stats['pending']}\n"
        f"  ✅ Опубликовано: {stats['sent']}\n"
        f"  ⏭ Пропущено: {stats['skipped']}\n"
        f"  ❌ Ошибки: {stats['failed']}"
    )


@_owner_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = get_recent(10)
    if not items:
        await update.message.reply_text("Очередь пуста.")
        return

    icons = {"pending": "⏳", "sent": "✅", "skipped": "⏭", "failed": "❌"}
    lines = []
    for item in items:
        icon = icons.get(item.status, "❓")
        name = Path(item.video_path).name
        lines.append(f"{icon} {name}")

    await update.message.reply_text(
        "📋 Последние 10 видео:\n\n" + "\n".join(lines)
    )


# ---------------------------------------------------------------------------

def main():
    if not TOKEN or OWNER_ID == 0:
        print("❌ Заполни TELEGRAM_TOKEN и TELEGRAM_OWNER_ID в файле .env")
        return

    init_db()
    log.info(f"Бот запущен (только уведомления). Owner ID: {OWNER_ID}")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("queue",  cmd_queue))
    app.add_handler(CommandHandler("status", cmd_status))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
