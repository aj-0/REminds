"""
Telegram Reminder Bot
---------------------
- Paste a whole checklist as one message -> it parses every line and
  schedules a daily recurring reminder for each item.
- Reminders persist in SQLite and survive bot restarts.
- /today shows only reminders remaining for the rest of the day.

Commands:
  /start          welcome + instructions
  /list           show all reminders (with IDs)
  /today          show remaining reminders for today only
  /remove <id>    delete one reminder
  /clear          delete all reminders for this chat
  /edit <id> <HH:MM or HH:MM AM/PM>   change a reminder's time

Just send/paste your checklist text directly (no command) to bulk-add.
"""
import logging
import os
from datetime import datetime, time as dtime

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db
from parser import parse_checklist

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

JOB_PREFIX = "reminder_"


def job_name(chat_id: int, reminder_id: int) -> str:
    return f"{JOB_PREFIX}{chat_id}_{reminder_id}"


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    text = job.data["text"]
    await context.bot.send_message(chat_id=chat_id, text=f"⏰ Reminder: {text}")


def schedule_reminder(app: Application, chat_id: int, reminder_id: int, hour: int, minute: int, text: str):
    name = job_name(chat_id, reminder_id)
    # remove existing job with same name if present (e.g. on edit)
    for j in app.job_queue.get_jobs_by_name(name):
        j.schedule_removal()

    app.job_queue.run_daily(
        send_reminder,
        time=dtime(hour=hour, minute=minute),
        chat_id=chat_id,
        name=name,
        data={"chat_id": chat_id, "text": text},
    )


def fmt_time(hour: int, minute: int) -> str:
    ampm = "AM" if hour < 12 else "PM"
    h12 = hour % 12
    if h12 == 0:
        h12 = 12
    return f"{h12:02d}:{minute:02d} {ampm}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I'm your daily checklist reminder bot.\n\n"
        "Just paste your checklist, one item per line, like:\n"
        "07:00 AM: Wake up and stretch.\n"
        "07:15 AM: Drink a glass of water.\n\n"
        "I'll schedule each line as a daily reminder.\n\n"
        "Commands:\n"
        "/list - show all reminders\n"
        "/today - show what's left for today\n"
        "/remove <id> - delete one reminder\n"
        "/edit <id> <HH:MM AM/PM> - change a reminder's time\n"
        "/clear - delete everything"
    )


async def add_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    items = parse_checklist(text)

    if not items:
        await update.message.reply_text(
            "Couldn't find any valid lines. Use this format:\n"
            "07:00 AM: Wake up and stretch."
        )
        return

    chat_id = update.effective_chat.id
    ids = db.add_reminders_bulk(chat_id, items)

    for reminder_id, (hour, minute, item_text) in zip(ids, items):
        schedule_reminder(context.application, chat_id, reminder_id, hour, minute, item_text)

    lines = [f"#{rid} - {fmt_time(h, m)} - {t}" for rid, (h, m, t) in zip(ids, items)]
    await update.message.reply_text(
        f"Added {len(items)} reminder(s):\n" + "\n".join(lines)
    )


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows = db.get_reminders(chat_id)
    if not rows:
        await update.message.reply_text("No reminders set yet. Paste a checklist to add some.")
        return

    lines = [f"#{r['id']} - {fmt_time(r['hour'], r['minute'])} - {r['text']}" for r in rows]
    await update.message.reply_text("Your reminders:\n" + "\n".join(lines))


async def today_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows = db.get_reminders(chat_id)
    if not rows:
        await update.message.reply_text("No reminders set yet.")
        return

    now = datetime.now().time()
    remaining = [r for r in rows if (r["hour"], r["minute"]) >= (now.hour, now.minute)]
    passed = [r for r in rows if (r["hour"], r["minute"]) < (now.hour, now.minute)]

    if not remaining:
        await update.message.reply_text("All done for today! Nothing left on your checklist.")
        return

    lines = [f"#{r['id']} - {fmt_time(r['hour'], r['minute'])} - {r['text']}" for r in remaining]
    msg = f"Remaining today ({len(remaining)} left, {len(passed)} done):\n" + "\n".join(lines)
    await update.message.reply_text(msg)


async def remove_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Usage: /remove <id>")
        return
    try:
        reminder_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID must be a number. Use /list to see IDs.")
        return

    ok = db.delete_reminder(chat_id, reminder_id)
    if ok:
        for j in context.application.job_queue.get_jobs_by_name(job_name(chat_id, reminder_id)):
            j.schedule_removal()
        await update.message.reply_text(f"Removed reminder #{reminder_id}.")
    else:
        await update.message.reply_text(f"No reminder found with id #{reminder_id}.")


async def clear_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows = db.get_reminders(chat_id)
    count = db.clear_reminders(chat_id)
    for r in rows:
        for j in context.application.job_queue.get_jobs_by_name(job_name(chat_id, r["id"])):
            j.schedule_removal()
    await update.message.reply_text(f"Cleared {count} reminder(s).")


async def edit_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /edit <id> <HH:MM AM/PM>\nExample: /edit 3 07:45 AM")
        return

    try:
        reminder_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID must be a number. Use /list to see IDs.")
        return

    time_str = " ".join(context.args[1:])
    parsed = parse_checklist(f"{time_str}: placeholder")
    if not parsed:
        await update.message.reply_text("Couldn't parse that time. Try format like 07:45 AM or 19:45.")
        return

    hour, minute, _ = parsed[0]
    rows = db.get_reminders(chat_id)
    match = next((r for r in rows if r["id"] == reminder_id), None)
    if not match:
        await update.message.reply_text(f"No reminder found with id #{reminder_id}.")
        return

    db.update_reminder_time(chat_id, reminder_id, hour, minute)
    schedule_reminder(context.application, chat_id, reminder_id, hour, minute, match["text"])
    await update.message.reply_text(
        f"Updated #{reminder_id} to {fmt_time(hour, minute)} - {match['text']}"
    )


async def on_startup(app: Application):
    """Reload every stored reminder into the job queue so schedules survive restarts."""
    rows = db.get_all_reminders()
    for r in rows:
        schedule_reminder(app, r["chat_id"], r["id"], r["hour"], r["minute"], r["text"])
    logger.info("Rescheduled %d reminder(s) on startup.", len(rows))


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN in your .env file (see .env.example).")

    db.init_db()

    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(CommandHandler("today", today_reminders))
    app.add_handler(CommandHandler("remove", remove_reminder))
    app.add_handler(CommandHandler("clear", clear_reminders))
    app.add_handler(CommandHandler("edit", edit_reminder))

    # Any non-command text message is treated as a bulk checklist paste
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_bulk))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
