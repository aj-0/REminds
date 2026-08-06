import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Callable
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
import json

logger = logging.getLogger(__name__)


class ReminderScheduler:
    """Manages APScheduler for reminder jobs (MongoDB version)"""
    
    def __init__(self, job_callback: Callable):
        self.job_callback = job_callback
        
        # Use in-memory job store (no SQLAlchemy needed)
        jobstores = {
            'default': MemoryJobStore()
        }
        
        executors = {
            'default': ThreadPoolExecutor(20)
        }
        
        job_defaults = {
            'coalesce': False,
            'max_instances': 3,
            'misfire_grace_time': 300  # 5 minutes
        }
        
        self.scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone='UTC'
        )
        
        # Listen for job events
        self.scheduler.add_listener(
            self._job_event_listener,
            EVENT_JOB_EXECUTED | EVENT_JOB_ERROR
        )
    
    def _job_event_listener(self, event):
        """Handle job execution events"""
        if event.exception:
            logger.error(f"Job {event.job_id} failed: {event.exception}")
        else:
            logger.info(f"Job {event.job_id} executed successfully")
    
    def start(self):
        """Start the scheduler"""
        self.scheduler.start()
        logger.info("Scheduler started")
    
    def shutdown(self, wait: bool = True):
        """Shutdown the scheduler"""
        self.scheduler.shutdown(wait=wait)
        logger.info("Scheduler shutdown")
    
    def add_one_time_job(self, reminder_id: str, chat_id: int, 
                         run_date: datetime, text: str,
                         image_file_id: Optional[str] = None) -> str:
        """Add a one-time reminder job"""
        job_id = f"reminder_{reminder_id}"
        
        # Ensure datetime is timezone-aware
        if run_date.tzinfo is None:
            run_date = run_date.replace(tzinfo=timezone.utc)
        
        trigger = DateTrigger(run_date=run_date)
        
        self.scheduler.add_job(
            self.job_callback,
            trigger=trigger,
            id=job_id,
            args=[reminder_id, chat_id, text, 'once', image_file_id],
            replace_existing=True,
            name=f"Reminder #{reminder_id}: {text[:50]}"
        )
        
        logger.info(f"Added one-time job {job_id} for {run_date}")
        return job_id
    
    def add_daily_job(self, reminder_id: str, chat_id: int,
                      hour: int, minute: int, text: str,
                      image_file_id: Optional[str] = None) -> str:
        """Add a daily recurring reminder job"""
        job_id = f"reminder_{reminder_id}"
        
        trigger = CronTrigger(
            hour=hour,
            minute=minute,
            timezone='UTC'
        )
        
        self.scheduler.add_job(
            self.job_callback,
            trigger=trigger,
            id=job_id,
            args=[reminder_id, chat_id, text, 'daily', image_file_id],
            replace_existing=True,
            name=f"Daily Reminder #{reminder_id}: {text[:50]}"
        )
        
        logger.info(f"Added daily job {job_id} at {hour:02d}:{minute:02d} UTC")
        return job_id
    
    def add_weekly_job(self, reminder_id: str, chat_id: int,
                       days_of_week: List[int], hour: int, minute: int,
                       text: str, image_file_id: Optional[str] = None) -> str:
        """Add a weekly recurring reminder job"""
        job_id = f"reminder_{reminder_id}"
        
        # Convert day numbers to day names
        day_names = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
        days_str = ','.join([day_names[d] for d in days_of_week])
        
        trigger = CronTrigger(
            day_of_week=days_str,
            hour=hour,
            minute=minute,
            timezone='UTC'
        )
        
        self.scheduler.add_job(
            self.job_callback,
            trigger=trigger,
            id=job_id,
            args=[reminder_id, chat_id, text, 'weekly', image_file_id],
            replace_existing=True,
            name=f"Weekly Reminder #{reminder_id}: {text[:50]}"
        )
        
        logger.info(f"Added weekly job {job_id} for days {days_str} at {hour:02d}:{minute:02d} UTC")
        return job_id
    
    def add_snooze_job(self, reminder_id: str, chat_id: int,
                       delay_minutes: int, text: str,
                       image_file_id: Optional[str] = None) -> str:
        """Add a snooze job for a reminder"""
        snooze_job_id = f"snooze_{reminder_id}_{int(datetime.now().timestamp())}"
        run_date = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
        
        trigger = DateTrigger(run_date=run_date)
        
        self.scheduler.add_job(
            self.job_callback,
            trigger=trigger,
            id=snooze_job_id,
            args=[reminder_id, chat_id, text, 'snooze', image_file_id],
            replace_existing=True,
            name=f"Snooze Reminder #{reminder_id} ({delay_minutes}m)"
        )
        
        logger.info(f"Added snooze job {snooze_job_id} for {delay_minutes} minutes")
        return snooze_job_id
    
    def remove_job(self, job_id: str):
        """Remove a scheduled job"""
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed job {job_id}")
        except Exception as e:
            logger.warning(f"Failed to remove job {job_id}: {e}")
    
    def get_job(self, job_id: str) -> Optional[Any]:
        """Get a scheduled job"""
        try:
            return self.scheduler.get_job(job_id)
        except:
            return None
    
    def job_exists(self, job_id: str) -> bool:
        """Check if a job exists"""
        return self.get_job(job_id) is not None
    
    def get_next_run_time(self, job_id: str) -> Optional[datetime]:
        """Get next run time for a job"""
        job = self.get_job(job_id)
        if job and job.next_run_time:
            return job.next_run_time
        return None
    
    def reload_reminders(self):
        """Reload all active reminders from MongoDB"""
        from database import get_active_reminders
        
        try:
            reminders = get_active_reminders()
            count = 0
            
            for reminder in reminders:
                try:
                    job_id = f"reminder_{reminder.id}"
                    
                    # Remove existing job if any
                    self.remove_job(job_id)
                    
                    time_utc = reminder.time_utc
                    if isinstance(time_utc, str):
                        time_utc = datetime.fromisoformat(time_utc)
                    
                    if reminder.type == 'once':
                        self.add_one_time_job(
                            reminder.id, reminder.chat_id,
                            time_utc, reminder.text,
                            reminder.image_file_id
                        )
                    
                    elif reminder.type == 'daily':
                        self.add_daily_job(
                            reminder.id, reminder.chat_id,
                            time_utc.hour, time_utc.minute,
                            reminder.text, reminder.image_file_id
                        )
                    
                    elif reminder.type == 'weekly':
                        days = reminder.days_of_week if isinstance(reminder.days_of_week, list) else []
                        self.add_weekly_job(
                            reminder.id, reminder.chat_id,
                            days, time_utc.hour, time_utc.minute,
                            reminder.text, reminder.image_file_id
                        )
                    
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to reload reminder {reminder.id}: {e}")
            
            logger.info(f"Reloaded {count} reminders")
            return count
            
        except Exception as e:
            logger.error(f"Failed to reload reminders: {e}")
            return 0
