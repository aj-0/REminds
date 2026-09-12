"""
Telegram Reminder Bot
----------------------
Paste lines like:
  today 1:50 pm - make coffee
  tomorrow 9:00 am - submit report
  17.08.2026 - wish him happy birthday      (no time -> defaults to 9:00 AM)
  daily 7:00 am - drink water
  weekly mon 9:00 am - team sync

Each line becomes one stored reminder. A background job checks every
60 seconds for anything due, sends it, then reschedules (daily/weekly)
or deletes it (one-off).

Commands:
  /start          welcome + instructions
  /list           show all reminders (with IDs)
  /remove <id>    delete one reminder
  /clear          delete all reminders for this chat
"""
import logging
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import db
from parser import parse_line, IST

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def fmt_ts(ts: int) -> str:
    dt = datetime.fromtimestamp(ts, tz=IST)
    return dt.strftime("%d %b, %I:%M %p")


def fmt_row(r) -> str:
    when = fmt_ts(r["next_run_ts"])
    if r["recurrence"] == "daily":
        tag = "[daily]"
    elif r["recurrence"] == "weekly":
        tag = f"[weekly {WEEKDAY_NAMES[r['weekday']]}]"
    else:
        tag = "[once]"
    return f"#{r['id']} {tag} {when} - {r['message']}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! Paste reminders one per line, e.g.:\n\n"
        "today 1:50 pm - make coffee\n"
        "tomorrow 9:00 am - submit report\n"
        "17.08.2026 - wish him happy birthday\n"
        "daily 7:00 am - drink water\n"
        "weekly mon 9:00 am - team sync\n\n"
        "Commands:\n"
        "/list - show all reminders\n"
        "/remove <id> - delete one reminder\n"
        "/clear - delete everything"
    )


async def add_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now_ist = datetime.now(IST)

    added, errors = [], []
    for raw_line in update.message.text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = parse_line(line, now_ist)
        except ValueError as e:
            errors.append(str(e))
            continue

        rid = db.add_reminder(
            chat_id,
            parsed["message"],
            int(parsed["next_run"].timestamp()),
            parsed["recurrence"],
            parsed["weekday"],
        )
        added.append(db.get_reminders(chat_id))  # refresh not strictly needed
        added[-1] = rid

    reply_parts = []
    if added:
        rows = db.get_reminders(chat_id)
        by_id = {r["id"]: r for r in rows}
        lines = [fmt_row(by_id[rid]) for rid in added if rid in by_id]
        reply_parts.append(f"Added {len(added)} reminder(s):\n" + "\n".join(lines))
    if errors:
        reply_parts.append("Couldn't parse:\n" + "\n".join(errors))
    if not reply_parts:
        reply_parts.append(
            "Couldn't find any valid lines. Example:\n"
            "today 5:30 pm - water the plants"
        )

    await update.message.reply_text("\n\n".join(reply_parts))


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows = db.get_reminders(chat_id)
    if not rows:
        await update.message.reply_text("No reminders set yet. Paste one to add it.")
        return
    await update.message.reply_text(
        "Your reminders:\n" + "\n".join(fmt_row(r) for r in rows)
    )


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
        await update.message.reply_text(f"Removed reminder #{reminder_id}.")
    else:
        await update.message.reply_text(f"No reminder found with id #{reminder_id}.")


async def clear_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    count = db.clear_reminders(chat_id)
    await update.message.reply_text(f"Cleared {count} reminder(s).")


async def check_reminders(app: Application):
    """Runs every 60s: fire anything due, then reschedule/delete it."""
    now_ts = int(datetime.now(tz=IST).timestamp())
    due = db.get_due_reminders(now_ts)

    for r in due:
        try:
            await app.bot.send_message(chat_id=r["chat_id"], text=f"⏰ Reminder: {r['message']}")
        except Exception:
            logger.exception("Failed to send reminder #%s", r["id"])

        if r["recurrence"] == "daily":
            db.update_next_run(r["id"], r["next_run_ts"] + 86400)
        elif r["recurrence"] == "weekly":
            db.update_next_run(r["id"], r["next_run_ts"] + 7 * 86400)
        else:
            db.delete_by_id(r["id"])


async def on_startup(app: Application):
    scheduler = AsyncIOScheduler(timezone=IST)
    scheduler.add_job(check_reminders, "interval", seconds=60, args=[app], id="check_reminders")
    scheduler.start()
    app.bot_data["scheduler"] = scheduler
    logger.info("Scheduler started.")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN in your .env file (see .env.example).")

    db.init_db()

    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(CommandHandler("remove", remove_reminder))
    app.add_handler(CommandHandler("clear", clear_reminders))

    # Any non-command text message is treated as one or more reminder lines
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_bulk))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
