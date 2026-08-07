import os
import logging
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import db
from parser import parse_line, IST

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
PORT = int(os.environ.get("PORT", 8080))


def fmt_ist(next_run_utc_iso: str) -> str:
    dt = datetime.fromisoformat(next_run_utc_iso).astimezone(IST)
    return dt.strftime("%d %b %Y, %I:%M %p IST")


HELP_TEXT = (
    "⏰ *Reminder bot*\n\n"
    "*/remind <when> - <message>*\n"
    "  /remind today 5:30 pm - call mom\n"
    "  /remind tomorrow 9:00 am - submit report\n"
    "  /remind 17.08.2026 - birth wish him   (no time = 9:00 AM)\n"
    "  /remind daily 7:00 am - drink water\n"
    "  /remind weekly mon 9:00 am - team sync\n\n"
    "*/bulkremind* (one reminder per line, after the command)\n"
    "  /bulkremind\n"
    "  today 1:50 pm - make coffee\n"
    "  today 1:52 pm - make tea\n"
    "  today 1:59 pm - do hw\n"
    "  17.08.2026 - birth wish him\n"
    "  daily 6:30 am - gym\n\n"
    "*/myreminders* - list active reminders\n"
    "*/cancel <id>* - cancel one (id from /myreminders)"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def _create_reminders(update: Update, lines: list[str]):
    chat_id = update.effective_chat.id
    now_ist = datetime.now(IST)
    ok, errors = [], []

    for line in lines:
        try:
            parsed = parse_line(line, now_ist)
            next_run_utc = parsed["next_run"].astimezone(timezone.utc).isoformat()
            rid = db.add_reminder(
                chat_id, parsed["message"], next_run_utc,
                parsed["recurrence"], parsed["weekday"],
            )
            tag = f" ({parsed['recurrence']})" if parsed["recurrence"] else ""
            ok.append(f"#{rid}{tag} {fmt_ist(next_run_utc)} — {parsed['message']}")
        except ValueError as e:
            errors.append(str(e))

    reply = ""
    if ok:
        reply += "✅ Scheduled:\n" + "\n".join(ok) + "\n"
    if errors:
        reply += "\n❌ Failed:\n" + "\n".join(errors)
    await update.message.reply_text(reply.strip() or "Nothing scheduled.")


async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.partition(" ")[2].strip()
    if not text:
        await update.message.reply_text("Usage: /remind today 5:30 pm - call mom")
        return
    await _create_reminders(update, [text])


async def bulkremind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = update.message.text.split("\n")[1:]
    lines = [l.strip() for l in lines if l.strip()]
    if not lines:
        await update.message.reply_text(
            "Send reminders on the lines after the command, e.g.:\n"
            "/bulkremind\n"
            "today 1:50 pm - make coffee\n"
            "today 1:52 pm - make tea\n"
            "17.08.2026 - birth wish him"
        )
        return
    await _create_reminders(update, lines)


async def myreminders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.list_active(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("No active reminders.")
        return
    lines = []
    for rid, message, next_run, recurrence, weekday in rows:
        tag = f" ({recurrence})" if recurrence else ""
        lines.append(f"#{rid}{tag} {fmt_ist(next_run)} — {message}")
    await update.message.reply_text("\n".join(lines))


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /cancel <id>  (see /myreminders)")
        return
    rid = int(args[0])
    if db.get_reminder(rid, chat_id):
        db.deactivate(rid)
        await update.message.reply_text(f"Cancelled #{rid}.")
    else:
        await update.message.reply_text(f"No active reminder #{rid} for you.")


async def check_due(context: ContextTypes.DEFAULT_TYPE):
    now_utc_iso = datetime.now(timezone.utc).isoformat()
    due = db.get_due(now_utc_iso)
    for rid, chat_id, message, next_run, recurrence, weekday in due:
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"⏰ Reminder: {message}")
        except Exception:
            logger.exception(f"Failed to send reminder #{rid}")

        if recurrence == "daily":
            new_run = datetime.fromisoformat(next_run) + timedelta(days=1)
            db.update_next_run(rid, new_run.isoformat())
        elif recurrence == "weekly":
            new_run = datetime.fromisoformat(next_run) + timedelta(days=7)
            db.update_next_run(rid, new_run.isoformat())
        else:
            db.deactivate(rid)


async def post_init(application: Application):
    # Render's free Web Service tier requires something bound to $PORT,
    # so run a tiny keep-alive server inside the same asyncio loop
    # (avoids the thread-vs-asyncio-loop issues from separate threads).
    from aiohttp import web

    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Keep-alive web server listening on port {PORT}")


def main():
    import asyncio
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    db.init_db()
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("remind", remind_cmd))
    application.add_handler(CommandHandler("bulkremind", bulkremind_cmd))
    application.add_handler(CommandHandler("myreminders", myreminders_cmd))
    application.add_handler(CommandHandler("cancel", cancel_cmd))

    application.job_queue.run_repeating(check_due, interval=30, first=10)

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
