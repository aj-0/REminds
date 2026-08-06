import os
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, JSON, func
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.pool import StaticPool
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///reminders.db")

# Handle Render's PostgreSQL URL format
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    poolclass=StaticPool if "sqlite" in DATABASE_URL else None,
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Reminder(Base):
    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    chat_id = Column(Integer, nullable=False, index=True)
    message_id = Column(Integer, nullable=True)
    type = Column(String(10), nullable=False)  # once, daily, weekly
    time_utc = Column(DateTime, nullable=False)
    days_of_week = Column(String(100), nullable=True)  # JSON list for weekly
    text = Column(Text, nullable=False)
    active = Column(Boolean, default=True)
    next_run_utc = Column(DateTime, nullable=True)
    image_file_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "type": self.type,
            "time_utc": self.time_utc.isoformat() if self.time_utc else None,
            "days_of_week": json.loads(self.days_of_week) if self.days_of_week else [],
            "text": self.text,
            "active": self.active,
            "next_run_utc": self.next_run_utc.isoformat() if self.next_run_utc else None,
            "image_file_id": self.image_file_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class User(Base):
    __tablename__ = "users"

    chat_id = Column(Integer, primary_key=True, index=True)
    timezone = Column(String(50), default="UTC")
    snooze_options = Column(String(50), default="5,10,15")  # comma-separated minutes
    created_at = Column(DateTime, default=datetime.utcnow)

    def get_snooze_options(self) -> List[int]:
        try:
            return [int(x.strip()) for x in self.snooze_options.split(",")]
        except:
            return [5, 10, 15]


def init_db():
    """Initialize database tables"""
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    """Get database session"""
    db = SessionLocal()
    try:
        return db
    except:
        db.close()
        raise


def get_user(db: Session, chat_id: int) -> Optional[User]:
    """Get or create user"""
    user = db.query(User).filter(User.chat_id == chat_id).first()
    if not user:
        user = User(chat_id=chat_id)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def add_reminder(db: Session, chat_id: int, type: str, time_utc: datetime,
                 text: str, days_of_week: Optional[List[str]] = None,
                 image_file_id: Optional[str] = None) -> Reminder:
    """Add a new reminder"""
    reminder = Reminder(
        chat_id=chat_id,
        type=type,
        time_utc=time_utc,
        days_of_week=json.dumps(days_of_week) if days_of_week else None,
        text=text,
        active=True,
        next_run_utc=time_utc,
        image_file_id=image_file_id
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder


def update_reminder(db: Session, reminder_id: int, **kwargs) -> Optional[Reminder]:
    """Update a reminder"""
    reminder = db.query(Reminder).filter(Reminder.id == reminder_id).first()
    if reminder:
        for key, value in kwargs.items():
            if key == 'days_of_week' and value is not None:
                value = json.dumps(value)
            setattr(reminder, key, value)
        reminder.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(reminder)
    return reminder


def delete_reminder(db: Session, reminder_id: int) -> bool:
    """Delete a reminder"""
    reminder = db.query(Reminder).filter(Reminder.id == reminder_id).first()
    if reminder:
        db.delete(reminder)
        db.commit()
        return True
    return False


def get_active_reminders(db: Session, chat_id: Optional[int] = None) -> List[Reminder]:
    """Get all active reminders"""
    query = db.query(Reminder).filter(Reminder.active == True)
    if chat_id:
        query = query.filter(Reminder.chat_id == chat_id)
    return query.order_by(Reminder.next_run_utc).all()


def get_reminder(db: Session, reminder_id: int) -> Optional[Reminder]:
    """Get a specific reminder"""
    return db.query(Reminder).filter(Reminder.id == reminder_id).first()


def update_next_run(db: Session, reminder_id: int, next_run: datetime):
    """Update next run time for a reminder"""
    reminder = db.query(Reminder).filter(Reminder.id == reminder_id).first()
    if reminder:
        reminder.next_run_utc = next_run
        db.commit()
