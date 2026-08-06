import os
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from pymongo import MongoClient
from pymongo.collection import Collection
from bson import ObjectId
from dotenv import load_dotenv
import pytz

load_dotenv()

# MongoDB connection
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGODB_DB_NAME", "reminder_bot")

class Database:
    """MongoDB database handler"""
    
    def __init__(self):
        self.client = MongoClient(MONGODB_URL)
        self.db = self.client[DB_NAME]
        self.reminders = self.db['reminders']
        self.users = self.db['users']
        self._create_indexes()
    
    def _create_indexes(self):
        """Create database indexes for performance"""
        self.reminders.create_index('chat_id')
        self.reminders.create_index('active')
        self.reminders.create_index([('chat_id', 1), ('active', 1)])
        self.users.create_index('chat_id', unique=True)
    
    def close(self):
        """Close database connection"""
        self.client.close()


# Singleton database instance
_db_instance = None

def get_db() -> Database:
    """Get or create database instance"""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance

def init_db():
    """Initialize database (create indexes)"""
    db = get_db()
    db._create_indexes()
    print("Database initialized with MongoDB")


class Reminder:
    """Reminder model"""
    
    def __init__(self, data: dict):
        self.id = str(data.get('_id', data.get('id', '')))
        self.chat_id = data.get('chat_id')
        self.message_id = data.get('message_id')
        self.type = data.get('type', 'once')  # once, daily, weekly
        self.time_utc = data.get('time_utc')
        self.days_of_week = data.get('days_of_week', [])
        self.text = data.get('text', '')
        self.active = data.get('active', True)
        self.next_run_utc = data.get('next_run_utc')
        self.image_file_id = data.get('image_file_id')
        self.created_at = data.get('created_at', datetime.utcnow())
        self.updated_at = data.get('updated_at', datetime.utcnow())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'chat_id': self.chat_id,
            'message_id': self.message_id,
            'type': self.type,
            'time_utc': self.time_utc.isoformat() if isinstance(self.time_utc, datetime) else self.time_utc,
            'days_of_week': self.days_of_week,
            'text': self.text,
            'active': self.active,
            'next_run_utc': self.next_run_utc.isoformat() if isinstance(self.next_run_utc, datetime) else self.next_run_utc,
            'image_file_id': self.image_file_id,
            'created_at': self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
        }


class User:
    """User model"""
    
    def __init__(self, data: dict):
        self.chat_id = data.get('chat_id')
        self.timezone = data.get('timezone', 'UTC')
        self.snooze_options = data.get('snooze_options', '5,10,15')
        self.created_at = data.get('created_at', datetime.utcnow())
    
    def get_snooze_options(self) -> List[int]:
        try:
            return [int(x.strip()) for x in self.snooze_options.split(',')]
        except:
            return [5, 10, 15]


def get_user(chat_id: int) -> User:
    """Get or create user"""
    db = get_db()
    user_data = db.users.find_one({'chat_id': chat_id})
    
    if not user_data:
        user_data = {
            'chat_id': chat_id,
            'timezone': 'UTC',
            'snooze_options': '5,10,15',
            'created_at': datetime.utcnow()
        }
        db.users.insert_one(user_data)
    
    return User(user_data)


def add_reminder(chat_id: int, type: str, time_utc: datetime,
                 text: str, days_of_week: Optional[List[str]] = None,
                 image_file_id: Optional[str] = None) -> Reminder:
    """Add a new reminder"""
    db = get_db()
    
    reminder_data = {
        'chat_id': chat_id,
        'message_id': None,
        'type': type,
        'time_utc': time_utc,
        'days_of_week': days_of_week or [],
        'text': text,
        'active': True,
        'next_run_utc': time_utc,
        'image_file_id': image_file_id,
        'created_at': datetime.utcnow(),
        'updated_at': datetime.utcnow()
    }
    
    result = db.reminders.insert_one(reminder_data)
    reminder_data['_id'] = result.inserted_id
    return Reminder(reminder_data)


def update_reminder(reminder_id: str, **kwargs) -> Optional[Reminder]:
    """Update a reminder"""
    db = get_db()
    
    # Convert string ID to ObjectId if needed
    from bson.objectid import ObjectId
    try:
        obj_id = ObjectId(reminder_id)
    except:
        obj_id = reminder_id
    
    update_data = {k: v for k, v in kwargs.items() if v is not None}
    update_data['updated_at'] = datetime.utcnow()
    
    # Handle days_of_week serialization
    if 'days_of_week' in update_data and isinstance(update_data['days_of_week'], list):
        update_data['days_of_week'] = update_data['days_of_week']
    
    result = db.reminders.find_one_and_update(
        {'_id': obj_id},
        {'$set': update_data},
        return_document=True
    )
    
    if result:
        return Reminder(result)
    return None


def delete_reminder(reminder_id: str) -> bool:
    """Delete a reminder"""
    db = get_db()
    
    from bson.objectid import ObjectId
    try:
        obj_id = ObjectId(reminder_id)
    except:
        obj_id = reminder_id
    
    result = db.reminders.delete_one({'_id': obj_id})
    return result.deleted_count > 0


def get_active_reminders(chat_id: Optional[int] = None) -> List[Reminder]:
    """Get all active reminders"""
    db = get_db()
    
    query = {'active': True}
    if chat_id:
        query['chat_id'] = chat_id
    
    cursor = db.reminders.find(query).sort('next_run_utc', 1)
    return [Reminder(doc) for doc in cursor]


def get_reminder(reminder_id: str) -> Optional[Reminder]:
    """Get a specific reminder by ID"""
    db = get_db()
    
    from bson.objectid import ObjectId
    try:
        obj_id = ObjectId(reminder_id)
    except:
        obj_id = reminder_id
    
    doc = db.reminders.find_one({'_id': obj_id})
    return Reminder(doc) if doc else None


def update_next_run(reminder_id: str, next_run: datetime):
    """Update next run time for a reminder"""
    db = get_db()
    
    from bson.objectid import ObjectId
    try:
        obj_id = ObjectId(reminder_id)
    except:
        obj_id = reminder_id
    
    db.reminders.update_one(
        {'_id': obj_id},
        {'$set': {'next_run_utc': next_run, 'updated_at': datetime.utcnow()}}
    )


def get_reminders_by_ids(reminder_ids: List[str]) -> List[Reminder]:
    """Get multiple reminders by IDs"""
    db = get_db()
    
    from bson.objectid import ObjectId
    obj_ids = []
    for rid in reminder_ids:
        try:
            obj_ids.append(ObjectId(rid))
        except:
            pass
    
    cursor = db.reminders.find({'_id': {'$in': obj_ids}})
    return [Reminder(doc) for doc in cursor]
