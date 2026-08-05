# 🤖 Telegram Reminder Bot (APScheduler + PTB v21)

A high-precision, timezone-aware reminder bot with **snooze support**, **flexible scheduling**, and **persistent jobs** via APScheduler + SQLAlchemyJobStore. Designed for **Render Free Tier** (Web Service + Background Thread).

## ✨ Features

-   **⚡ Timing Accuracy**: Uses `AsyncIOScheduler` with `DateTrigger` (once) & `CronTrigger` (daily/weekly). No polling drift.
-   **💾 Persistence**: Jobs survive restarts/redeploys via `SQLAlchemyJobStore` (PostgreSQL recommended).
-   **🌍 Timezone Support**: Stores UTC, displays in user's local time (IANA zones). Handles DST automatically.
-   **⏰ Flexible Scheduling**:
    -   `/remind 2026-08-10 16:00 Meeting`
    -   `/remind today 16:00 Coffee` (Smart "past time → tomorrow?" prompt)
    -   `/remind in 30m Call mom`
    -   `/remind daily 07:00 Standup`
    -   `/remind weekly mon,wed,fri 18:00 Gym`
    -   **12h Format**: `6:00 AM`, `4 PM`
-   **😴 Snooze Buttons**: Inline `5m/10m/15m` + `Done`/`Delete`. Snoozes are **one-off** jobs; recurring schedule untouched.
-   **📦 Bulk Reminders**: `/bulkremind` → paste a list (see example below).
-   **🛠 Management**: `/list`, `/edit`, `/delete`, `/done`, `/timezone`, `/snoozes`.

## 🚀 Quick Start (Local)

```bash
git clone <repo>
cd telegram-reminder-bot
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your BOT_TOKEN
mkdir -p data
python main.py
