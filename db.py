"""
SQLite storage for reminders.
Table: reminders(id, chat_id, message, next_run_ts, recurrence, weekday, created_at)

next_run_ts: unix epoch seconds (UTC) of the next time this reminder should fire.
recurrence:  NULL (one-off), 'daily', or 'weekly'
weekday:     0=Mon..6=Sun, only set when recurrence == 'weekly'
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
                message TEXT NOT NULL,
                next_run_ts INTEGER NOT NULL,
                recurrence TEXT,
                weekday INTEGER,
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


def add_reminder(chat_id: int, message: str, next_run_ts: int, recurrence: str | None, weekday: int | None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (chat_id, message, next_run_ts, recurrence, weekday) VALUES (?, ?, ?, ?, ?)",
            (chat_id, message, next_run_ts, recurrence, weekday),
        )
        conn.commit()
        return cur.lastrowid


def get_reminders(chat_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM reminders WHERE chat_id = ? ORDER BY next_run_ts",
            (chat_id,),
        ).fetchall()


def get_due_reminders(now_ts: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM reminders WHERE next_run_ts <= ?",
            (now_ts,),
        ).fetchall()


def update_next_run(reminder_id: int, new_ts: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE reminders SET next_run_ts = ? WHERE id = ?",
            (new_ts, reminder_id),
        )
        conn.commit()


def delete_reminder(chat_id: int, reminder_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM reminders WHERE chat_id = ? AND id = ?", (chat_id, reminder_id)
        )
        conn.commit()
        return cur.rowcount > 0


def delete_by_id(reminder_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()


def clear_reminders(chat_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM reminders WHERE chat_id = ?", (chat_id,))
        conn.commit()
        return cur.rowcount
