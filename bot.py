import os
import logging
import asyncio
from datetime import datetime

import pytz
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

import database as db
from utils import parse_time_input, format_time_display

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

SNOOZE_OPTIONS_MIN = [5, 15, 30]


def build_reminder_keyboard(reminder_id: int) -> InlineKeyboardMarkup:
    """Buttons shown on every reminder message: Done + snooze options."""
    snooze_row = [
        InlineKeyboardButton(f"😴 {m}m", callback_data=f"snooze:{reminder_id}:{m}")
        for m in SNOOZE_OPTIONS_MIN
    ]
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Mark as done", callback_data=f"done:{reminder_id}")],
            snooze_row,
        ]
    )


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db.register_user(chat_id)
    text = (
        "👋 *Welcome to your Daily Reminder Bot!*\n\n"
        "I've loaded a default daily checklist for you. Use /list to see it.\n\n"
        "*Commands:*\n"
        "/list — Show today's checklist\n"
        "/add HH:MM AM/PM Task — Add a new reminder\n"
        "/edit ID HH:MM AM/PM Task — Edit a reminder's time and/or text\n"
        "/delete ID — Delete a reminder\n"
        "/done ID — Mark a reminder as done\n"
        "/reset — Reset to the default checklist\n"
        "/timezone Area/City — Set your timezone (e.g. Asia/Kolkata)\n"
        "/help — Show this message again"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db.register_user(chat_id)
    reminders = db.get_reminders(chat_id)
    if not reminders:
        await update.message.reply_text(
            "You have no reminders yet. Use /reset to load the default checklist, "
            "or /add to create your own."
        )
        return

    lines = ["🗓 *Your Daily Checklist*\n"]
    for r in reminders:
        box = "[x]" if r["done"] else "[ ]"
        lines.append(f"{box} #{r['id']} {format_time_display(r['time'])}: {r['text']}")
    lines.append("\n_Tip: /done ID to check something off, /delete ID to remove it._")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def add_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db.register_user(chat_id)
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /add HH:MM AM/PM Task text\n"
            "Example: /add 07:00 AM Wake up and stretch.\n"
            "You can also use 24h time: /add 19:00 Prepare dinner."
        )
        return

    time_parsed = parse_time_input(f"{args[0]} {args[1]}")
    if time_parsed and len(args) > 2:
        task_text = " ".join(args[2:])
    else:
        time_parsed = parse_time_input(args[0])
        task_text = " ".join(args[1:])

    if not time_parsed or not task_text:
        await update.message.reply_text(
            "Couldn't understand that. Use a format like:\n"
            "/add 07:00 AM Wake up and stretch.\n"
            "/add 19:00 Prepare dinner."
        )
        return

    db.add_reminder(chat_id, time_parsed, task_text)
    await update.message.reply_text(f"✅ Added: {format_time_display(time_parsed)} — {task_text}")


async def edit_reminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /edit ID HH:MM AM/PM New task text\n"
            "You can also edit only the time or only the text, e.g.\n"
            "/edit 3 08:00 AM\n"
            "/edit 3 New task text only"
        )
        return

    try:
        reminder_id = int(args[0])
    except ValueError:
        await update.message.reply_text("ID must be a number. Check /list for IDs.")
        return

    time_parsed = None
    task_text = None
    if len(args) >= 3:
        time_parsed = parse_time_input(f"{args[1]} {args[2]}")
        if time_parsed:
            task_text = " ".join(args[3:]) or None
    if not time_parsed:
        time_parsed = parse_time_input(args[1])
        task_text = " ".join(args[2:]) if not time_parsed else task_text
    if time_parsed and len(args) > 2:
        task_text = " ".join(args[2:])
    elif not time_parsed:
        task_text = " ".join(args[1:])

    ok = db.edit_reminder(chat_id, reminder_id, time_str=time_parsed, text=task_text)
    if ok:
        await update.message.reply_text("✏️ Reminder updated. Use /list to confirm.")
    else:
        await update.message.reply_text("Reminder not found. Check /list for valid IDs.")


async def delete_reminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /delete ID (see /list for IDs)")
        return
    try:
        reminder_id = int(args[0])
    except ValueError:
        await update.message.reply_text("ID must be a number. Check /list for IDs.")
        return
    ok = db.delete_reminder(chat_id, reminder_id)
    await update.message.reply_text("🗑 Deleted." if ok else "Reminder not found.")


async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /done ID (see /list for IDs)")
        return
    try:
        reminder_id = int(args[0])
    except ValueError:
        await update.message.reply_text("ID must be a number. Check /list for IDs.")
        return
    db.set_done(chat_id, reminder_id, 1)
    await update.message.reply_text("✅ Marked as done!")


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db.register_user(chat_id)
    db.load_default_reminders(chat_id)
    await update.message.reply_text("🔄 Checklist reset to the default schedule. Use /list to view it.")


async def timezone_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db.register_user(chat_id)
    args = context.args
    if not args:
        current = db.get_timezone(chat_id)
        await update.message.reply_text(
            f"Your current timezone is: {current}\n"
            "Usage: /timezone Area/City\n"
            "Examples: /timezone Asia/Kolkata, /timezone America/New_York, /timezone Europe/London"
        )
        return
    tz_name = args[0]
    if tz_name not in pytz.all_timezones:
        await update.message.reply_text(
            "Unknown timezone. Use a valid tz name, e.g. Asia/Kolkata, Europe/London, America/New_York."
        )
        return
    db.set_timezone(chat_id, tz_name)
    await update.message.reply_text(f"🌍 Timezone set to {tz_name}.")


# ---------------------------------------------------------------------------
# Callback (button) handlers
# ---------------------------------------------------------------------------

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Single entry point for all inline button taps — dispatches by prefix."""
    query = update.callback_query
    data = query.data or ""

    if data.startswith("done:"):
        await _handle_done(query, data)
    elif data.startswith("snooze:"):
        await _handle_snooze(query, data)

    await query.answer()


async def _handle_done(query, data: str):
    reminder_id = int(data.split(":")[1])
    chat_id = query.message.chat_id
    db.set_done(chat_id, reminder_id, 1)
    db.clear_snooze(reminder_id)
    try:
        await query.edit_message_text(text=f"{query.message.text}\n\n✅ Marked as done!")
    except Exception:
        pass


async def _handle_snooze(query, data: str):
    _, reminder_id_str, minutes_str = data.split(":")
    reminder_id = int(reminder_id_str)
    minutes = int(minutes_str)

    db.snooze_reminder(reminder_id, minutes)
    try:
        await query.edit_message_text(
            text=f"⏰ Snoozed for {minutes} min. I'll remind you again shortly."
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Background job: checks every user's reminders every minute
# ---------------------------------------------------------------------------

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    # 1. Regular scheduled reminders, per user/timezone.
    users = db.get_all_users()
    for chat_id in users:
        tz_name = db.get_timezone(chat_id)
        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = pytz.timezone("Asia/Kolkata")

        now = datetime.now(tz)
        current_time_str = now.strftime("%H:%M")
        today_str = now.strftime("%Y-%m-%d")

        reminders = db.get_reminders(chat_id)
        for r in reminders:
            if r["time"] == current_time_str and r["last_sent_date"] != today_str:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"⏰ Reminder: {r['text']}",
                        reply_markup=build_reminder_keyboard(r["id"]),
                    )
                except Exception as e:
                    logger.warning("Failed to send reminder to %s: %s", chat_id, e)
                db.mark_sent(r["id"], today_str)

    # 2. Snoozed reminders whose delay has elapsed (checked in UTC, timezone-independent).
    due = db.get_due_snoozes()
    for r in due:
        try:
            await context.bot.send_message(
                chat_id=r["chat_id"],
                text=f"⏰ Reminder (snoozed): {r['text']}",
                reply_markup=build_reminder_keyboard(r["id"]),
            )
        except Exception as e:
            logger.warning("Failed to send snoozed reminder to %s: %s", r["chat_id"], e)
        db.clear_snooze(r["id"])


# ---------------------------------------------------------------------------
# App bootstrap
# ---------------------------------------------------------------------------

def build_application():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set.")

    db.init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("list", list_reminders))
    application.add_handler(CommandHandler("add", add_reminder))
    application.add_handler(CommandHandler("edit", edit_reminder_cmd))
    application.add_handler(CommandHandler("delete", delete_reminder_cmd))
    application.add_handler(CommandHandler("done", done_cmd))
    application.add_handler(CommandHandler("reset", reset_cmd))
    application.add_handler(CommandHandler("timezone", timezone_cmd))
    application.add_handler(CallbackQueryHandler(callback_router))

    # Check reminders (and due snoozes) every 60 seconds
    application.job_queue.run_repeating(check_reminders, interval=60, first=5)

    return application


def run_bot():
    """Runs the bot with polling. Safe to call from a background thread."""
    asyncio.set_event_loop(asyncio.new_event_loop())
    application = build_application()
    logger.info("Bot started, polling for updates...")
    application.run_polling(close_loop=False, drop_pending_updates=True)


if __name__ == "__main__":
    run_bot()
