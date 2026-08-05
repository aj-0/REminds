"""
SQLAlchemy models + session factory.

DATABASE_URL controls the backend:
  - unset -> sqlite:///reminders.db  (fine locally, EPHEMERAL on Render free tier)
  - set to a Postgres URL (e.g. from a Render Postgres instance) -> persists across redeploys

APScheduler's SQLAlchemyJobStore (see scheduler.py) points at the SAME DATABASE_URL,
so reminders and their scheduled jobs live in one place and survive together.
"""
import os

from sqlalchemy import Boolean, Column, DateTime, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///reminders.db")

# Render/Heroku-style Postgres URLs sometimes come as postgres:// which
# SQLAlchemy 2.x rejects -- normalize to postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    chat_id = Column(Integer, primary_key=True)
    timezone = Column(String, nullable=False, default="UTC")
    snooze_options = Column(String, nullable=False, default="5,10,15")  # comma-separated minutes


class Reminder(Base):
    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(Integer, nullable=False, index=True)
    type = Column(String, nullable=False)  # "once" | "daily" | "weekly"

    # For "once": unused (next_run_utc holds the exact fire time).
    # For "daily"/"weekly": LOCAL HH:MM (see scheduler.py docstring for why).
    time_utc = Column(String, nullable=True, default="")

    days_of_week = Column(String, nullable=True)  # "mon,wed,fri" for weekly, else None
    text = Column(String, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    next_run_utc = Column(DateTime, nullable=False)  # naive UTC datetime, kept in sync for /list


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    return SessionLocal()
