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

def get_reminder_by_id(reminder_id):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, chat_id, message, next_run, recurrence, weekday "
            "FROM reminders WHERE id=?",
            (reminder_id,),
        )
        return cur.fetchone()


# ---------------------------------------------------------------------------
# Delivered notifications awaiting an explicit "ACK" from the user.
# A notification is resent every few minutes until it is acknowledged.
# ---------------------------------------------------------------------------

def init_ack_tables():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reminder_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                next_resend TEXT NOT NULL,  -- UTC isoformat
                send_count INTEGER DEFAULT 1,
                acked INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_messages (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                notification_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            )
            """
        )


def add_notification(reminder_id, chat_id, text, next_resend_utc_iso):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notifications (reminder_id, chat_id, text, next_resend) "
            "VALUES (?, ?, ?, ?)",
            (reminder_id, chat_id, text, next_resend_utc_iso),
        )
        return cur.lastrowid


def record_notification_message(notification_id, chat_id, message_id):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO notification_messages "
            "(chat_id, message_id, notification_id) VALUES (?, ?, ?)",
            (chat_id, message_id, notification_id),
        )


def get_resends_due(now_utc_iso):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, reminder_id, chat_id, text, send_count "
            "FROM notifications WHERE acked=0 AND next_resend<=?",
            (now_utc_iso,),
        )
        return cur.fetchall()


def bump_resend(notification_id, next_resend_utc_iso):
    with get_conn() as conn:
        conn.execute(
            "UPDATE notifications SET next_resend=?, send_count=send_count+1 WHERE id=?",
            (next_resend_utc_iso, notification_id),
        )


def _ack_where(conn, where, params):
    """Mark matching unacked notifications as acked; return their reminder ids."""
    rows = conn.execute(
        f"SELECT id, reminder_id FROM notifications WHERE acked=0 AND {where}", params
    ).fetchall()
    if rows:
        conn.executemany(
            "UPDATE notifications SET acked=1 WHERE id=?", [(r[0],) for r in rows]
        )
    return sorted({r[1] for r in rows})


def ack_by_message(chat_id, message_id):
    with get_conn() as conn:
        return _ack_where(
            conn,
            "chat_id=? AND id IN (SELECT notification_id FROM notification_messages "
            "WHERE chat_id=? AND message_id=?)",
            (chat_id, chat_id, message_id),
        )


def ack_reminder(chat_id, reminder_id):
    with get_conn() as conn:
        return _ack_where(conn, "chat_id=? AND reminder_id=?", (chat_id, reminder_id))


def ack_all(chat_id):
    with get_conn() as conn:
        return _ack_where(conn, "chat_id=?", (chat_id,))


def list_unacked(chat_id):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT reminder_id, text, send_count FROM notifications "
            "WHERE acked=0 AND chat_id=? ORDER BY id",
            (chat_id,),
        )
        return cur.fetchall()
