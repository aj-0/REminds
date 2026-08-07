# Telegram Reminder Bot _ (Personal_Bot)

IST-based reminder bot with single, bulk, date-based, and recurring (daily/weekly) reminders.
Persists to SQLite (`reminders.db`) so scheduled reminders survive a restart on Render.

## Setup

## Deploy on Render

1. push this repo in your github
2. render.com : New → Web Service → connect repo (uses `render.yaml`).
3. Set environment var `BOT_TOKEN` in the Render dashboard.
4. Deploy. Free tier needs the bot to bind to `$PORT` — already handled via
   a tiny aiohttp keep-alive server started in `post_init` (same event loop,
   no extra threads, so it won't hit the Python 3.14 strict-threading issue).

Note: Render's free disk isn't guaranteed persistent across deploys — `reminders.db`
survives restarts/sleep but not a fresh deploy. Fine for personal use; swap in a
Render Postgres/persistent disk if that matters to you.

## Commands

```
/remind today 5:30 pm - call mom
/remind tomorrow 9:00 am - submit report
/remind 17.08.2026 - birth wish him        # no time -> defaults to 9:00 AM
/remind daily 7:00 am - drink water
/remind weekly mon 9:00 am - team sync

/bulkremind
today 1:50 pm - make coffee
today 1:52 pm - make tea
today 1:59 pm - do hw
17.08.2026 - birth wish him
daily 6:30 am - gym

/myreminders          # list active, with ids
/cancel <id>          # cancel one
```

## Format notes

- Date: `today`, `tomorrow`, or `DD.MM.YYYY`
- Time: 12-hour (`5:30 pm`, `9:00am`) or 24-hour (`17:30`) — always interpreted in IST
- Recurring: `daily <time> - msg` or `weekly <mon|tue|wed|thu|fri|sat|sun> <time> - msg`
- Separator between date/time and message must be ` - ` (space-dash-space)
