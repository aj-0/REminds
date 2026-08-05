import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.job import Job
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import SessionLocal, Reminder, ReminderType, update_reminder_next_run, deactivate_reminder
from utils import get_tz, utc_to_local

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler: Optional[AsyncIOScheduler] = None

def init_scheduler(db_url: str) -> AsyncIOScheduler:
    global scheduler
    jobstores = {
        'default': SQLAlchemyJobStore(engine=create_engine(db_url))
    }
    # Increase misfire grace time for free tier latency
    job_defaults = {
        'coalesce': True,  # Run once if multiple triggers stacked
        'max_instances': 1,
        'misfire_grace_time': 300 # 5 mins
    }
    scheduler = AsyncIOScheduler(jobstores=jobstores, job_defaults=job_defaults, timezone=timezone.utc)
    scheduler.start()
    logger.info("APScheduler started with SQLAlchemyJobStore.")
    return scheduler

def shutdown_scheduler():
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler shutdown.")

# --- Job Callbacks ---

async def send_reminder_callback(reminder_id: int, bot_instance: Any):
    """The actual function executed by APScheduler."""
    # We need a fresh DB session because this runs in APScheduler thread/context
    with SessionLocal() as db:
        rem = db.query(Reminder).filter(Reminder.id == reminder_id).first()
        if not rem or not rem.active:
            logger.warning(f"Job fired for inactive/missing reminder {reminder_id}. Removing job.")
            # Job removal handled by caller or here
            if scheduler:
                try: scheduler.remove_job(f"rem_{reminder_id}")
                except: pass
            return

        chat_id = rem.chat_id
        text = rem.text
        
        # Calculate NEXT run for recurring *before* sending, so /list is accurate immediately
        next_run = calculate_next_run_db(rem, db)
        update_reminder_next_run(db, rem.id, next_run)

    # Send Message (Outside DB transaction)
    try:
        # Import here to avoid circular dependency
        from bot import build_reminder_keyboard, get_user_snoozes_db
        
        snooze_opts = get_user_snoozes_db(chat_id)
        keyboard = build_reminder_keyboard(rem.id, snooze_opts)
        
        msg = f"⏰ **Reminder**\n\n{text}"
        sent_msg = await bot_instance.send_message(chat_id=chat_id, text=msg, reply_markup=keyboard, parse_mode="Markdown")
        
        # Store message ID for editing on snooze? 
        # We can pass it via job kwargs or store in DB. 
        # For simplicity, we edit via callback_query message context later.
        
        logger.info(f"Sent reminder {reminder_id} to {chat_id}")
    except Exception as e:
        logger.error(f"Failed to send reminder {reminder_id}: {e}")

def calculate_next_run_db(rem: Reminder, db: Session) -> Optional[datetime]:
    """Calculates next run for recurring tasks. Returns UTC datetime."""
    if rem.type == ReminderType.ONCE:
        return None # One-time jobs are deleted after firing (handled in job listener or here)
    
    now_utc = datetime.now(timezone.utc)
    tz = get_tz("UTC") # DB stores UTC times for cron triggers usually, but we stored 'time_utc' as time component.
    
    # rem.time_utc stores the TIME component (e.g. 2000-01-01 16:00:00) for daily/weekly
    target_time = rem.time_utc.time() # time object
    
    if rem.type == ReminderType.DAILY:
        # Next day at target_time
        next_dt = datetime.combine(now_utc.date() + timedelta(days=1), target_time, tzinfo=timezone.utc)
        # If target time today hasn't passed yet? No, job fired now, so next is tomorrow.
        # But if misfired? coalesce=True handles it. 
        return next_dt
    
    elif rem.type == ReminderType.WEEKLY:
        if not rem.days_of_week: return None
        days = [d.strip() for d in rem.days_of_week.split(',')]
        day_nums = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
        target_nums = sorted([day_nums[d] for d in days if d in day_nums])
        if not target_nums: return None
        
        today_num = now_utc.weekday()
        next_day_num = None
        weeks_ahead = 0
        
        for d_num in target_nums:
            if d_num > today_num:
                next_day_num = d_num
                break
            elif d_num == today_num:
                # Today. But job fired NOW. So next is next week same day.
                next_day_num = d_num
                weeks_ahead = 1
                break
        
        if next_day_num is None:
            # Wrap to next week first day
            next_day_num = target_nums[0]
            weeks_ahead = 1
            
        days_ahead = (next_day_num - today_num + 7) % 7
        if days_ahead == 0: days_ahead = 7 # Next week
        days_ahead += weeks_ahead * 7
        
        next_date = now_utc.date() + timedelta(days=days_ahead)
        return datetime.combine(next_date, target_time, tzinfo=timezone.utc)
    
    return None

# --- Job Management ---

def _get_job_id(reminder_id: int) -> str:
    return f"rem_{reminder_id}"

def _get_snooze_job_id(reminder_id: int, suffix: str) -> str:
    return f"snooze_{reminder_id}_{suffix}"

def schedule_reminder_job(rem: Reminder, bot_instance: Any) -> Optional[Job]:
    """Adds/Updates the main recurring or one-time job in APScheduler."""
    if not scheduler: return None
    job_id = _get_job_id(rem.id)
    
    # Remove existing if any
    try: scheduler.remove_job(job_id)
    except: pass

    trigger = None
    run_date = rem.time_utc # This is UTC datetime for 'once', or UTC time-component for recurring
    
    if rem.type == ReminderType.ONCE:
        # run_date is the exact UTC datetime to fire
        if run_date <= datetime.now(timezone.utc):
            logger.warning(f"Reminder {rem.id} is in the past. Not scheduling.")
            return None
        trigger = DateTrigger(run_date=run_date, timezone=timezone.utc)
    
    elif rem.type == ReminderType.DAILY:
        # run_date (time_utc) holds time component (e.g. 1970-01-01 16:00:00)
        # We need hour/minute in UTC for CronTrigger
        # WARNING: CronTrigger in UTC means "Every day at 16:00 UTC". 
        # User wants "Every day at 16:00 LOCAL". 
        # APScheduler CronTrigger supports `timezone` parameter!
        # We must store user timezone on reminder or fetch it.
        # Optimization: Store user_tz on Reminder model? 
        # For now, fetch from User table.
        from database import get_user_tz
        with SessionLocal() as db:
            user_tz = get_user_tz(db, rem.chat_id)
        
        tz_obj = get_tz(user_tz)
        trigger = CronTrigger(
            hour=run_date.hour, minute=run_date.minute, 
            timezone=tz_obj, # CRITICAL: Run at user's local time
            jitter=10 # Small jitter to distribute load
        )
    
    elif rem.type == ReminderType.WEEKLY:
        from database import get_user_tz
        with SessionLocal() as db:
            user_tz = get_user_tz(db, rem.chat_id)
        tz_obj = get_tz(user_tz)
        
        # Convert days "mon,wed" -> "mon,wed" for cron (apscheduler uses 3-letter lowercase)
        day_map = {"mon":"mon","tue":"tue","wed":"wed","thu":"thu","fri":"fri","sat":"sat","sun":"sun"}
        cron_days = ",".join([day_map[d] for d in rem.days_of_week.split(",") if d in day_map])
        
        trigger = CronTrigger(
            day_of_week=cron_days,
            hour=run_date.hour, minute=run_date.minute,
            timezone=tz_obj,
            jitter=10
        )

    if not trigger: return None

    job = scheduler.add_job(
        send_reminder_callback,
        trigger,
        args=[rem.id, bot_instance],
        id=job_id,
        name=f"Reminder {rem.id}: {rem.text[:30]}",
        replace_existing=True
    )
    
    # Update DB with Job ID for recovery
    with SessionLocal() as db:
        update_reminder_next_run(db, rem.id, job.next_run_time, job.id)
    
    logger.info(f"Scheduled job {job_id} for reminder {rem.id} (Next: {job.next_run_time})")
    return job

def schedule_snooze_job(reminder_id: int, delay_minutes: int, bot_instance: Any, original_msg_id: int = None):
    """Schedules a one-off snooze job. Does NOT affect main recurring job."""
    if not scheduler: return
    job_id = _get_snooze_job_id(reminder_id, f"{delay_minutes}m")
    run_date = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
    
    try: scheduler.remove_job(job_id)
    except: pass

    # We need to send a NEW message or edit existing? 
    # Spec: "edits the original message to show 'Snoozed for 10 minutes'"
    # But the snooze fires LATER. The edit happens NOW (in callback).
    # The snooze job just sends a NEW reminder message (or re-sends).
    # Spec: "reschedules that specific reminder to fire again after the chosen delay"
    
    def snooze_callback():
        # Fire and forget new send
        import asyncio
        # We are in scheduler thread, need to run async send
        # This is tricky. Better to pass bot_instance and use asyncio.run_coroutine_threadsafe
        loop = bot_instance.application.loop # Access running loop
        asyncio.run_coroutine_threadsafe(send_reminder_callback(reminder_id, bot_instance), loop)

    scheduler.add_job(
        snooze_callback,
        DateTrigger(run_date=run_date, timezone=timezone.utc),
        id=job_id,
        name=f"Snooze {reminder_id} {delay_minutes}m"
    )
    logger.info(f"Snooze scheduled for rem {reminder_id} in {delay_minutes}m (Job: {job_id})")

def remove_reminder_jobs(reminder_id: int):
    """Removes main job and any pending snooze jobs."""
    if not scheduler: return
    main_id = _get_job_id(reminder_id)
    try: scheduler.remove_job(main_id)
    except: pass
    # Remove snooze jobs (wildcard not supported easily, iterate)
    jobs = scheduler.get_jobs()
    for job in jobs:
        if job.id.startswith(f"snooze_{reminder_id}_"):
            try: scheduler.remove_job(job.id)
            except: pass

def reschedule_on_edit(rem: Reminder, bot_instance: Any):
    """Called after /edit updates DB. Reschedules main job."""
    schedule_reminder_job(rem, bot_instance)

def reload_all_jobs(bot_instance: Any):
    """On startup: load active reminders from DB and schedule them."""
    if not scheduler: return
    with SessionLocal() as db:
        reminders = db.query(Reminder).filter(Reminder.active == True).all()
    
    count = 0
    for rem in reminders:
        job = schedule_reminder_job(rem, bot_instance)
        if job: count += 1
    logger.info(f"Reloaded {count} active reminders into scheduler.")
