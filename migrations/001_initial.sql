-- Initial database schema
-- This is for reference; SQLAlchemy creates tables automatically

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_id INTEGER,
    type VARCHAR(10) NOT NULL,  -- once, daily, weekly
    time_utc DATETIME NOT NULL,
    days_of_week VARCHAR(100),  -- JSON array for weekly
    text TEXT NOT NULL,
    active BOOLEAN DEFAULT 1,
    next_run_utc DATETIME,
    image_file_id VARCHAR(255),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    timezone VARCHAR(50) DEFAULT 'UTC',
    snooze_options VARCHAR(50) DEFAULT '5,10,15',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_reminders_chat_id ON reminders(chat_id);
CREATE INDEX IF NOT EXISTS idx_reminders_active ON reminders(active);
CREATE INDEX IF NOT EXISTS idx_reminders_next_run ON reminders(next_run_utc);
