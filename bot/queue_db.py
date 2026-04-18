"""
SQLite очередь видео для Telegram бота.
Хранит все видео, их статусы и подписи.
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "checkpoints" / "bot_queue.db"


@dataclass
class QueueItem:
    id: int
    video_path: str
    caption: str
    status: str        # pending / sent / skipped / failed
    created_at: str
    sent_at: str | None
    message_id: int | None   # Telegram message_id для редактирования кнопок


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Создаёт таблицу если не существует."""
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                video_path  TEXT NOT NULL,
                caption     TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending', 'sent', 'skipped', 'failed')),
                created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                sent_at     TEXT,
                message_id  INTEGER
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_status ON queue(status)")


def add_video(video_path: str, caption: str = "") -> int:
    """Добавляет видео в очередь. Возвращает id."""
    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO queue (video_path, caption) VALUES (?, ?)",
            (video_path, caption),
        )
        return cursor.lastrowid


def get_next_pending() -> QueueItem | None:
    """Возвращает следующее видео со статусом pending."""
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM queue WHERE status='pending' ORDER BY id LIMIT 1"
        ).fetchone()
        if row:
            return QueueItem(**dict(row))
        return None


def update_status(item_id: int, status: str, message_id: int = None) -> None:
    """Обновляет статус видео."""
    with get_db() as db:
        if message_id is not None:
            db.execute(
                "UPDATE queue SET status=?, message_id=?, sent_at=datetime('now','localtime') WHERE id=?",
                (status, message_id, item_id),
            )
        else:
            db.execute(
                "UPDATE queue SET status=?, sent_at=datetime('now','localtime') WHERE id=?",
                (status, item_id),
            )


def get_stats() -> dict:
    """Статистика очереди."""
    with get_db() as db:
        row = db.execute("""
            SELECT
                COUNT(CASE WHEN status='pending'  THEN 1 END) as pending,
                COUNT(CASE WHEN status='sent'     THEN 1 END) as sent,
                COUNT(CASE WHEN status='skipped'  THEN 1 END) as skipped,
                COUNT(CASE WHEN status='failed'   THEN 1 END) as failed
            FROM queue
        """).fetchone()
        return dict(row)


def get_recent(limit: int = 10) -> list[QueueItem]:
    """Последние N видео (любой статус)."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM queue ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [QueueItem(**dict(r)) for r in rows]
