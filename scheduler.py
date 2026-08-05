"""
APScheduler setup + job callback.

Design notes (deviation from a literal "store everything in UTC" reading of the spec):
  - "once" reminders: stored and scheduled as an exact UTC timestamp (DateTrigger). Correct,
    unambiguous, no DST concerns.
  - "daily"/"weekly" reminders: the DB column `time_utc` actually holds the LOCAL HH:MM,
    and the job uses CronTrigger(..., timezone=<user tz>). APScheduler's CronTrigger accepts
    a timezone directly and recomputes the correct UTC fire time itself, including across
    DST transitions. If we instead pre-converted "4pm IST" to a fixed UTC HH:MM and used
    that forever, a "daily at 4pm" reminder would silently fire at 3pm or 5pm local time
    for users in DST-observing timezones. CronTrigger+timezone is the correct way to express
    "same wall-clock time every day" and is what's actually wanted here. Flagging this
    explicitly rather than quietly renaming the column.

Every reminder gets its own job, keyed by a job id derived from the DB row id -- not a
single shared minute-loop. Jobs are persisted via SQLAlchemyJobStore (same DATABASE_URL as
database.py) so they survive process restarts. reload_all_jobs() re-derives jobs from the
`reminders` table on boot as a belt-and-braces measure (also self-heals if the jobstore
table is ever wiped independently of the reminders table).
"""
import logging
from datetime import datetime, timedelta

import pytz
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from database import DATABASE_URL, Reminder, User, get_session

logger = logging.getLogger(__name__)

_bot = None  # set once via set_bot() during post_init, before the scheduler starts

scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=DATABASE_URL, tablename="apscheduler_jobs")},
    timezone=pytz.utc,
)


def set_bot(bot):
    global _bot
    _bot = bot


def job_id_for(reminder_id):
    return f"reminder_{reminder_id}"


def snooze_job_id_for(reminder_id):
    return f"reminder_{reminder_id}_snooze"


async def _fire(reminder_id, is_snooze=False):
    session = get_session()
    try:
        r = session.get(Reminder, reminder_id)
        if not r or not r.active:
            return
        user = session.get(User, r.chat_id)
        snoozes = (user.snooze_options if user else "5,10,15").split(",")

        row1 = [InlineKeyboardButton(f"Snooze {m}m", callback_data=f"snooze:{reminder_id}:{m}") for m in snoozes]
        row2 = [InlineKeyboardButton("✅ Done", callback_data=f"done:{reminder_id}")]
        keyboard = InlineKeyboardMarkup([row1, row2])

        await _bot.send_message(chat_id=r.chat_id, text=f"⏰ {r.text}", reply_markup=keyboard)

        if r.type == "once" and not is_snooze:
            r.active = False
            session.commit()
        elif r.type in ("daily", "weekly"):
            # Keep next_run_utc accurate for /list without querying APScheduler directly.
            job = scheduler.get_job(job_id_for(reminder_id))
            if job and job.next_run_time:
                r.next_run_utc = job.next_run_time.astimezone(pytz.utc).replace(tzinfo=None)
                session.commit()
    finally:
        session.close()


def add_once_job(reminder_id, run_at_utc):
    if run_at_utc.tzinfo is None:
        run_at_utc = pytz.utc.localize(run_at_utc)
    scheduler.add_job(
        _fire,
        trigger=DateTrigger(run_date=run_at_utc),
        args=[reminder_id],
        id=job_id_for(reminder_id),
        replace_existing=True,
        misfire_grace_time=3600,
    )


def add_daily_job(reminder_id, hour, minute, tz_name):
    scheduler.add_job(
        _fire,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=pytz.timezone(tz_name)),
        args=[reminder_id],
        id=job_id_for(reminder_id),
        replace_existing=True,
        misfire_grace_time=3600,
    )


def add_weekly_job(reminder_id, days, hour, minute, tz_name):
    scheduler.add_job(
        _fire,
        trigger=CronTrigger(day_of_week=",".join(days), hour=hour, minute=minute, timezone=pytz.timezone(tz_name)),
        args=[reminder_id],
        id=job_id_for(reminder_id),
        replace_existing=True,
        misfire_grace_time=3600,
    )


def add_snooze_job(reminder_id, delay_minutes):
    """
    Snoozing NEVER touches job_id_for(reminder_id) (the original recurring/one-time job) --
    it only adds/replaces a separate snooze_job_id_for(reminder_id) DateTrigger job. This is
    what guarantees a snoozed daily reminder still fires normally tomorrow, and that snoozing
    near midnight can't skip or duplicate the next scheduled occurrence: the two jobs are
    independent APScheduler jobs with independent ids.
    """
    run_at = datetime.now(pytz.utc) + timedelta(minutes=delay_minutes)
    scheduler.add_job(
        _fire,
        trigger=DateTrigger(run_date=run_at),
        args=[reminder_id, True],
        id=snooze_job_id_for(reminder_id),
        replace_existing=True,
        misfire_grace_time=3600,
    )
    return run_at


def remove_job(reminder_id):
    """Remove BOTH the main job and any pending snooze job for this reminder."""
    for jid in (job_id_for(reminder_id), snooze_job_id_for(reminder_id)):
        try:
            scheduler.remove_job(jid)
        except Exception:
            pass


def remove_snooze_job(reminder_id):
    """Remove only a pending snooze job, leaving the main recurring/one-time job intact."""
    try:
        scheduler.remove_job(snooze_job_id_for(reminder_id))
    except Exception:
        pass


def reload_all_jobs():
    """Call once at startup (after init_db()): re-register jobs for every active reminder."""
    session = get_session()
    try:
        reminders = session.query(Reminder).filter_by(active=True).all()
        for r in reminders:
            user = session.get(User, r.chat_id)
            tz_name = user.timezone if user else "UTC"
            if r.type == "once":
                run_at = r.next_run_utc
                if run_at.tzinfo is None:
                    run_at = pytz.utc.localize(run_at)
                if run_at <= datetime.now(pytz.utc):
                    # Missed while the process was down. Fire it once immediately rather
                    # than silently dropping it, then let normal completion logic handle it.
                    run_at = datetime.now(pytz.utc) + timedelta(seconds=5)
                add_once_job(r.id, run_at)
            elif r.type == "daily":
                h, mi = map(int, r.time_utc.split(":"))
                add_daily_job(r.id, h, mi, tz_name)
            elif r.type == "weekly":
                h, mi = map(int, r.time_utc.split(":"))
                add_weekly_job(r.id, r.days_of_week.split(","), h, mi, tz_name)
        logger.info("Reloaded %d active reminder job(s).", len(reminders))
    finally:
        session.close()
