"""
SQLite storage for reminders.
Table: reminders(id, chat_id, hour, minute, text, created_at)
Time is stored in 24h (hour, minute) for easy scheduling.
"""
import sqlite3
from contextlib import contextmanager

DB_PATH = "reminders.db"


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                hour INTEGER NOT NULL,
                minute INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def add_reminder(chat_id: int, hour: int, minute: int, text: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (chat_id, hour, minute, text) VALUES (?, ?, ?, ?)",
            (chat_id, hour, minute, text),
        )
        conn.commit()
        return cur.lastrowid


def add_reminders_bulk(chat_id: int, items: list[tuple[int, int, str]]) -> list[int]:
    ids = []
    with get_conn() as conn:
        for hour, minute, text in items:
            cur = conn.execute(
                "INSERT INTO reminders (chat_id, hour, minute, text) VALUES (?, ?, ?, ?)",
                (chat_id, hour, minute, text),
            )
            ids.append(cur.lastrowid)
        conn.commit()
    return ids


def get_reminders(chat_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE chat_id = ? ORDER BY hour, minute",
            (chat_id,),
        ).fetchall()
        return rows


def get_all_reminders():
    """Used on bot startup to reschedule every job for every chat."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM reminders ORDER BY hour, minute").fetchall()
        return rows


def delete_reminder(chat_id: int, reminder_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM reminders WHERE chat_id = ? AND id = ?", (chat_id, reminder_id)
        )
        conn.commit()
        return cur.rowcount > 0


def clear_reminders(chat_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM reminders WHERE chat_id = ?", (chat_id,))
        conn.commit()
        return cur.rowcount


def update_reminder_time(chat_id: int, reminder_id: int, hour: int, minute: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE reminders SET hour = ?, minute = ? WHERE chat_id = ? AND id = ?",
            (hour, minute, chat_id, reminder_id),
        )
        conn.commit()
        return cur.rowcount > 0
