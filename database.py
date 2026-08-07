import sqlite3
from contextlib import contextmanager

DB_PATH = "reminders.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                next_run TEXT NOT NULL,   -- UTC isoformat
                recurrence TEXT,          -- NULL | 'daily' | 'weekly'
                weekday INTEGER,          -- 0=Mon .. 6=Sun, only for weekly
                active INTEGER DEFAULT 1
            )
            """
        )


def add_reminder(chat_id, message, next_run_utc_iso, recurrence=None, weekday=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (chat_id, message, next_run, recurrence, weekday) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, message, next_run_utc_iso, recurrence, weekday),
        )
        return cur.lastrowid


def get_due(now_utc_iso):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, chat_id, message, next_run, recurrence, weekday "
            "FROM reminders WHERE active=1 AND next_run<=?",
            (now_utc_iso,),
        )
        return cur.fetchall()


def deactivate(reminder_id):
    with get_conn() as conn:
        conn.execute("UPDATE reminders SET active=0 WHERE id=?", (reminder_id,))


def update_next_run(reminder_id, next_run_utc_iso):
    with get_conn() as conn:
        conn.execute(
            "UPDATE reminders SET next_run=? WHERE id=?",
            (next_run_utc_iso, reminder_id),
        )


def list_active(chat_id):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, message, next_run, recurrence, weekday "
            "FROM reminders WHERE active=1 AND chat_id=? ORDER BY next_run",
            (chat_id,),
        )
        return cur.fetchall()


def get_reminder(reminder_id, chat_id):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id FROM reminders WHERE id=? AND chat_id=? AND active=1",
            (reminder_id, chat_id),
        )
        return cur.fetchone()
