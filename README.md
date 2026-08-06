# Telegram Reminder Bot

A powerful, accurate Telegram reminder bot with flexible scheduling, snooze functionality, and image support.

## Features

- ✅ **Accurate Timing** - Uses APScheduler with proper triggers, not polling
- 🔄 **Flexible Scheduling** - One-time, daily, weekly, and relative time reminders
- 😴 **Snooze Functionality** - Customizable snooze buttons on every reminder
- 📸 **Image Reminders** - Set reminders with images as context
- 📋 **Bulk Reminders** - Set multiple reminders at once
- 🕐 **Timezone Support** - User-specific timezone handling
- 💾 **Persistent Storage** - SQLite/PostgreSQL database
- 🚀 **Ready for Deployment** - Works on Render's free tier

## Commands

### Basic Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/start` | Start the bot | `/start` |
| `/help` | Show help | `/help` |
| `/list` | List all reminders | `/list` |

### Setting Reminders

| Command | Description | Example |
|---------|-------------|---------|
| `/remind HH:MM text` | Remind today at time | `/remind 16:00 Make coffee` |
| `/remind YYYY-MM-DD HH:MM text` | Specific date/time | `/remind 2026-08-10 16:00 Buy gift` |
| `/remind today HH:MM text` | Today at time | `/remind today 18:00 Walk dog` |
| `/remind tomorrow HH:MM text` | Tomorrow at time | `/remind tomorrow 09:00 Meeting` |
| `/remind daily HH:MM text` | Daily recurring | `/remind daily 07:00 Wake up` |
| `/remind weekly day,day HH:MM text` | Weekly recurring | `/remind weekly mon,wed,fri 18:00 Gym` |
| `/remind in Xm text` | In X minutes | `/remind in 30m Take medicine` |
| `/remind in Xh text` | In X hours | `/remind in 2h Call mom` |

### Managing Reminders

| Command | Description | Example |
|---------|-------------|---------|
| `/edit ID changes` | Edit a reminder | `/edit 5 17:00 Buy groceries` |
| `/delete ID` | Delete a reminder | `/delete 5` |
| `/done ID` | Mark as done | `/done 5` |

### Settings

| Command | Description | Example |
|---------|-------------|---------|
| `/timezone Area/City` | Set timezone | `/timezone America/New_York` |
| `/snoozes 5,10,15` | Custom snooze options | `/snoozes 3,7,12` |

### Advanced Features

| Command | Description | Example |
|---------|-------------|---------|
| `/bulkremind` | Multiple reminders at once | See below |
| `/imgremind time text` | Reminder with image | Send photo with caption |

### Bulk Remind Example

