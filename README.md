# Telegram Reminder Bot

APScheduler-backed reminder bot (python-telegram-bot v21). No polling loop that
string-compares `HH:MM` — every reminder is its own `DateTrigger`/`CronTrigger`
job, added/rescheduled/removed the moment you act, and persisted so they
survive restarts.

## Files

| File | Purpose |
|---|---|
| `main.py` | Flask health-check app (for Render's port requirement) + starts bot in a background thread |
| `bot.py` | Command/callback handlers, `Application` factory, `post_init` scheduler bootstrap |
| `database.py` | SQLAlchemy models (`User`, `Reminder`) + session factory |
| `scheduler.py` | `AsyncIOScheduler` setup, job callback, add/remove/reload job helpers |
| `utils.py` | Parsing for every `/remind` input style + UTC/local conversion |

## How timing accuracy works

- **One-time reminders** (`2026-08-10 16:00`, `today`, `tomorrow`, `in 30m`) →
  `DateTrigger` at an exact UTC timestamp. Stored in DB as UTC.
- **Daily/weekly reminders** → `CronTrigger(hour=.., minute=.., day_of_week=.., timezone=<user tz>)`.
  The DB column `time_utc` for these actually stores the **local** `HH:MM`, not UTC —
  see the docstring at the top of `scheduler.py` for why. Short version: `CronTrigger`
  accepts a timezone and recomputes the correct UTC fire time itself, including
  across DST transitions, which is what "remind me at 4pm every day" actually
  means (fixed wall-clock time, not a fixed UTC offset that would drift on DST).
- Jobs are persisted with `SQLAlchemyJobStore` pointed at the same `DATABASE_URL`
  as the reminders table, so they survive process restarts without needing the
  minute-loop reload. `reload_all_jobs()` also re-derives every job from the
  `reminders` table on boot as a second layer of safety.
- Each job's id is `reminder_<id>` (main schedule) and `reminder_<id>_snooze`
  (any pending snooze), so add/edit/delete act on a single job immediately —
  never a shared poll cycle.

## Snooze behavior

Snoozing adds a **separate** `reminder_<id>_snooze` job; it never touches the
original `reminder_<id>` job. That's what guarantees a snoozed daily reminder
still fires normally tomorrow, and that snoozing near midnight can't skip or
duplicate the next scheduled occurrence — the two jobs are fully independent.

## Local setup

```bash
python -m venv venv
source venv/bin/activate         # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: set TELEGRAM_BOT_TOKEN
python main.py
```

Message your bot on Telegram to test.

## Deploying to Render (free Web Service tier)

1. Push this repo to GitHub.
2. Render dashboard → New → Web Service → connect the repo (or `render.yaml`
   auto-detects the blueprint above — either works).
3. Set environment variables (Environment tab):

| Variable | Required? | Value |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **Required** | Token from [@BotFather](https://t.me/BotFather) |
| `DATABASE_URL` | Recommended | Postgres connection string — see below |
| `PORT` | Do not set | Render injects this automatically |
| `PYTHON_VERSION` | Optional | e.g. `3.11.9`, pins the runtime (already in `render.yaml`) |

4. Build command: `pip install -r requirements.txt`
   Start command: `python main.py`
   (both already in `render.yaml`/`Procfile`)

### ⚠️ Ephemeral disk on the free tier

Render's free-tier filesystem is wiped on every redeploy and on spin-down/cold
start. If `DATABASE_URL` is unset, the bot falls back to local SQLite
(`reminders.db`) — that file, and every reminder + scheduled job in it, will
be **lost** on the next deploy or restart.

To actually persist reminders across redeploys, do one of:

- **Hosted Postgres (recommended, works on free tier):** create a free Render
  Postgres instance → copy its "Internal Database URL" → set it as
  `DATABASE_URL` on the web service. Both the `reminders`/`users` tables and
  APScheduler's `apscheduler_jobs` table live there automatically.
- **Render Persistent Disk:** requires a paid plan (not available on free Web
  Services); if you're on a paid instance type, mount a disk and point
  `DATABASE_URL` at a SQLite file inside it, e.g.
  `sqlite:////var/data/reminders.db`.

### Single instance only

Don't put a process manager (e.g. gunicorn with >1 worker) in front of
`main.py` — each worker would start its own Telegram polling loop and Telegram
will reject/conflict on duplicate `getUpdates` calls. Render's free Web
Service runs one instance by default, which is what this is built for.

## Commands

```
/start
/help

/remind 2026-08-10 16:00 Make coffee
/remind today 16:00 Make coffee
/remind tomorrow 16:00 Make coffee
/remind in 30m Make coffee
/remind in 2h Call mom
/remind daily 16:00 Make coffee
/remind weekly mon,wed,fri 16:00 Take out trash

/list
/edit 3 daily 09:00 Stretch
/edit 3 text Stretch for 10 minutes
/delete 3
/done 3

/timezone Asia/Kolkata
/snoozes 5,10,20
```

If `/remind today HH:MM ...` is given a time that's already passed, the bot
asks whether you meant "fire now" or "tomorrow" via inline buttons, instead of
guessing.

Every reminder message includes `Snooze 5m` / `Snooze 10m` / `Snooze 15m`
(customizable via `/snoozes`) and `✅ Done` buttons.
