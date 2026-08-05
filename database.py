import os
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, BigInteger, Text, Enum as SQLEnum
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.exc import OperationalError

import enum

# --- Config ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/reminders.db")

# Fix for Render Postgres URL (sqlalchemy 2.0+ requires postgresql+psycopg)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Enums ---
class ReminderType(str, enum.Enum):
    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"

# --- Models ---
class User(Base):
    __tablename__ = "users"
    chat_id = Column(BigInteger, primary_key=True)
    timezone = Column(String(64), default="UTC")
    snooze_options = Column(String(64), default="5,10,15")  # Minutes, comma separated
    created_at = Column(DateTime, default=datetime.utcnow)

class Reminder(Base):
    __tablename__ = "reminders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, index=True, nullable=False)
    type = Column(SQLEnum(ReminderType), nullable=False)
    # Time stored as UTC time component (for daily/weekly) or full UTC datetime (for once)
    time_utc = Column(DateTime, nullable=False, index=True) 
    days_of_week = Column(String(32), nullable=True)  # "mon,wed,fri" for weekly
    text = Column(Text, nullable=False)
    active = Column(Boolean, default=True, index=True)
    next_run_utc = Column(DateTime, nullable=True, index=True) # Denormalized for fast /list
    aps_job_id = Column(String(64), unique=True, nullable=True) # Link to APScheduler job
    created_at = Column(DateTime, default=datetime.utcnow)

# --- Helpers ---
def init_db():
    # Create data dir for sqlite
    if "sqlite" in DATABASE_URL:
        db_path = DATABASE_URL.replace("sqlite:///", "")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    Base.metadata.create_all(bind=engine)

@contextmanager
def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# --- CRUD ---
def get_or_create_user(db: Session, chat_id: int) -> User:
    user = db.query(User).filter(User.chat_id == chat_id).first()
    if not user:
        user = User(chat_id=chat_id)
        db.add(user)
        db.flush()
    return user

def create_reminder(db: Session, chat_id: int, rtype: ReminderType, time_utc: datetime, 
                    text: str, days_of_week: Optional[str] = None) -> Reminder:
    rem = Reminder(
        chat_id=chat_id,
        type=rtype,
        time_utc=time_utc,
        days_of_week=days_of_week,
        text=text,
        active=True,
        next_run_utc=time_utc
    )
    db.add(rem)
    db.flush()
    return rem

def get_active_reminders(db: Session, chat_id: int) -> List[Reminder]:
    return db.query(Reminder).filter(Reminder.chat_id == chat_id, Reminder.active == True).order_by(Reminder.next_run_utc).all()

def get_reminder(db: Session, rem_id: int, chat_id: int) -> Optional[Reminder]:
    return db.query(Reminder).filter(Reminder.id == rem_id, Reminder.chat_id == chat_id).first()

def deactivate_reminder(db: Session, rem_id: int, chat_id: int) -> bool:
    rem = get_reminder(db, rem_id, chat_id)
    if rem:
        rem.active = False
        rem.next_run_utc = None
        return True
    return False

def update_reminder_next_run(db: Session, rem_id: int, next_run: datetime, aps_job_id: str = None):
    rem = db.query(Reminder).filter(Reminder.id == rem_id).first()
    if rem:
        rem.next_run_utc = next_run
        if aps_job_id:
            rem.aps_job_id = aps_job_id

def update_user_tz(db: Session, chat_id: int, tz: str):
    user = get_or_create_user(db, chat_id)
    user.timezone = tz

def get_user_tz(db: Session, chat_id: int) -> str:
    user = db.query(User).filter(User.chat_id == chat_id).first()
    return user.timezone if user else "UTC"

def get_user_snoozes(db: Session, chat_id: int) -> List[int]:
    user = db.query(User).filter(User.chat_id == chat_id).first()
    if user and user.snooze_options:
        try: return [int(x) for x in user.snooze_options.split(",")]
        except: pass
    return [5, 10, 15]

def set_user_snoozes(db: Session, chat_id: int, options: List[int]):
    user = get_or_create_user(db, chat_id)
    user.snooze_options = ",".join(map(str, options))
