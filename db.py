import sqlite3
from datetime import datetime, timedelta
from contextlib import contextmanager

DB_PATH = "reminders.db"

DEFAULT_CHECKLIST = [
    ("07:00", "Wake up and stretch."),
    ("07:15", "Drink a glass of water."),
    ("07:30", "Eat a healthy breakfast."),
    ("08:00", "Take morning vitamins."),
    ("08:30", "Review daily schedule."),
    ("09:00", "Start your main work or study."),
    ("10:30", "Take a short 5-minute screen break."),
    ("12:30", "Eat a balanced lunch."),
    ("13:00", "Walk outside for 10 minutes."),
    ("14:30", "Drink second large glass of water."),
    ("16:00", "Stand up and stretch your legs."),
    ("17:30", "Wrap up work and close your laptop."),
    ("18:30", "Prepare and eat dinner."),
    ("19:30", "Spend time on hobbies or relaxation."),
    ("20:30", "Lay out clothes for tomorrow."),
]


@contextmanager
def get_conn():
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
@@ -15,69 +35,183 @@ def get_conn():


def init_db():
    with get_conn() as conn:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata'
            )
            """
        )
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
                time TEXT NOT NULL,
                text TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                last_sent_date TEXT,
                snooze_until TEXT
            )
            """
        )
        # Safe to run repeatedly; ignore if column already exists.
        try:
            conn.execute("ALTER TABLE reminders ADD COLUMN snooze_until TEXT")
        except sqlite3.OperationalError:
            pass


def register_user(chat_id: int):
    with _conn() as conn:
        cur = conn.execute("SELECT 1 FROM users WHERE chat_id = ?", (chat_id,))
        if cur.fetchone() is None:
            conn.execute(
                "INSERT INTO users (chat_id, timezone) VALUES (?, ?)",
                (chat_id, "Asia/Kolkata"),
            )
            load_default_reminders(chat_id, _new_conn=conn)


def load_default_reminders(chat_id: int, _new_conn=None):
    def _do(conn):
        conn.execute("DELETE FROM reminders WHERE chat_id = ?", (chat_id,))
        for time_str, text in DEFAULT_CHECKLIST:
            conn.execute(
                "INSERT INTO reminders (chat_id, time, text, done, last_sent_date, snooze_until) "
                "VALUES (?, ?, ?, 0, NULL, NULL)",
                (chat_id, time_str, text),
            )

    if _new_conn is not None:
        _do(_new_conn)
    else:
        with _conn() as conn:
            _do(conn)


def get_reminders(chat_id: int):
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE chat_id = ? ORDER BY time ASC", (chat_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_reminder(reminder_id: int):
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
        ).fetchone()
        return dict(row) if row else None


def add_reminder(chat_id: int, time_str: str, text: str):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO reminders (chat_id, time, text, done, last_sent_date, snooze_until) "
            "VALUES (?, ?, ?, 0, NULL, NULL)",
            (chat_id, time_str, text),
        )


def add_reminder(chat_id, message, next_run_utc_iso, recurrence=None, weekday=None):
    with get_conn() as conn:
def edit_reminder(chat_id: int, reminder_id: int, time_str: str = None, text: str = None) -> bool:
    if time_str is None and text is None:
        return False
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (chat_id, message, next_run, recurrence, weekday) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, message, next_run_utc_iso, recurrence, weekday),
            "SELECT 1 FROM reminders WHERE id = ? AND chat_id = ?", (reminder_id, chat_id)
        )
        return cur.lastrowid
        if cur.fetchone() is None:
            return False
        if time_str is not None:
            conn.execute(
                "UPDATE reminders SET time = ? WHERE id = ? AND chat_id = ?",
                (time_str, reminder_id, chat_id),
            )
        if text is not None:
            conn.execute(
                "UPDATE reminders SET text = ? WHERE id = ? AND chat_id = ?",
                (text, reminder_id, chat_id),
            )
        return True


def get_due(now_utc_iso):
    with get_conn() as conn:
def delete_reminder(chat_id: int, reminder_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT id, chat_id, message, next_run, recurrence, weekday "
            "FROM reminders WHERE active=1 AND next_run<=?",
            (now_utc_iso,),
            "DELETE FROM reminders WHERE id = ? AND chat_id = ?", (reminder_id, chat_id)
        )
        return cur.fetchall()
        return cur.rowcount > 0


def deactivate(reminder_id):
    with get_conn() as conn:
        conn.execute("UPDATE reminders SET active=0 WHERE id=?", (reminder_id,))
def set_done(chat_id: int, reminder_id: int, done: int):
    with _conn() as conn:
        conn.execute(
            "UPDATE reminders SET done = ? WHERE id = ? AND chat_id = ?",
            (done, reminder_id, chat_id),
        )


def update_next_run(reminder_id, next_run_utc_iso):
    with get_conn() as conn:
def mark_sent(reminder_id: int, today_str: str):
    with _conn() as conn:
        conn.execute(
            "UPDATE reminders SET next_run=? WHERE id=?",
            (next_run_utc_iso, reminder_id),
            "UPDATE reminders SET last_sent_date = ?, done = 0 WHERE id = ?",
            (today_str, reminder_id),
        )


def list_active(chat_id):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, message, next_run, recurrence, weekday "
            "FROM reminders WHERE active=1 AND chat_id=? ORDER BY next_run",
            (chat_id,),
def get_timezone(chat_id: int) -> str:
    with _conn() as conn:
        row = conn.execute(
            "SELECT timezone FROM users WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row["timezone"] if row else "Asia/Kolkata"


def set_timezone(chat_id: int, tz_name: str):
    with _conn() as conn:
        conn.execute(
            "UPDATE users SET timezone = ? WHERE chat_id = ?", (tz_name, chat_id)
        )
        return cur.fetchall()


def get_reminder(reminder_id, chat_id):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id FROM reminders WHERE id=? AND chat_id=? AND active=1",
            (reminder_id, chat_id),
def get_all_users():
    with _conn() as conn:
        rows = conn.execute("SELECT chat_id FROM users").fetchall()
        return [r["chat_id"] for r in rows]


# ---------------------------------------------------------------------------
# Snooze
# ---------------------------------------------------------------------------

def snooze_reminder(reminder_id: int, minutes: int, now_utc: datetime = None):
    """Set snooze_until to now (UTC) + minutes. Stored as ISO string, UTC, naive."""
    now_utc = now_utc or datetime.utcnow()
    snooze_until = now_utc + timedelta(minutes=minutes)
    with _conn() as conn:
        conn.execute(
            "UPDATE reminders SET snooze_until = ? WHERE id = ?",
            (snooze_until.isoformat(), reminder_id),
        )
        return cur.fetchone()


def clear_snooze(reminder_id: int):
    with _conn() as conn:
        conn.execute(
            "UPDATE reminders SET snooze_until = NULL WHERE id = ?", (reminder_id,)
        )


def get_due_snoozes(now_utc: datetime = None):
    """Reminders whose snooze has expired, across all users."""
    now_utc = now_utc or datetime.utcnow()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE snooze_until IS NOT NULL AND snooze_until <= ?",
            (now_utc.isoformat(),),
        ).fetchall()
        return [dict(r) for r in rows]
