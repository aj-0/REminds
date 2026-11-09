import os
import re
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import db
from parser import parse_line, IST

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
PORT = int(os.environ.get("PORT", 8080))

# Unacknowledged reminders are resent at this interval until the user replies "ACK".
ACK_RESEND_INTERVAL = timedelta(minutes=4)

# Matches "ACK", "ack", "/ack", "ACK 325", "ACK #325 #326", "/ack@MyBot 12"
ACK_RE = re.compile(r"^\s*/?ack(?:@\w+)?((?:[\s,#]+\d+)*)\s*[.!]?\s*$", re.IGNORECASE)

# A message that is just a number = snooze pending reminders for that many minutes.
SNOOZE_RE = re.compile(r"^\s*(\d+)\s*$")
MAX_SNOOZE_MINUTES = 24 * 60


def fmt_ist(next_run_utc_iso: str) -> str:
    dt = datetime.fromisoformat(next_run_utc_iso).astimezone(IST)
    return dt.strftime("%d %b %Y, %I:%M %p IST")


def time_of_day_emoji(dt_ist: datetime) -> str:
    h = dt_ist.hour
    if 5 <= h < 12:
        return "🌞"  # morning
    if 12 <= h < 17:
        return "☀️"  # afternoon
    if 17 <= h < 21:
        return "🌆"  # evening
    return "🌙"  # night


def fmt_scheduled(rid: int, next_run_utc_iso: str, message: str, recurrence: str | None) -> str:
    """One entry of the '✅ Scheduled:' confirmation, e.g.

    #325 — 07 Nov 2026, 09:00 AM 🌞 (IST)

    — Beetroot Juice, Face Tan & Skincare Products
    """
    dt = datetime.fromisoformat(next_run_utc_iso).astimezone(IST)
    tz = f"(IST, {recurrence})" if recurrence else "(IST)"
    when = dt.strftime("%d %b %Y, %I:%M %p")
    return f"#{rid} — {when} {time_of_day_emoji(dt)} {tz}\n\n— {message}"


def notification_text(rid: int, message: str) -> str:
    return f"⏰ Reminder #{rid}: {message}\n\nACK:🥱?"


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
    "*/cancel <id>* - cancel one (id from /myreminders)\n"
    "*/clrall* - clear all your reminders and stop all repeats\n\n"
    "*ACK* - every reminder repeats every 4 minutes until you reply ACK\n"
    "  ACK  (as a reply to a reminder) - acknowledge that reminder\n"
    "  ACK  (plain message) - acknowledge all pending reminders\n"
    "  ACK 325 - acknowledge reminder #325\n\n"
    "*Snooze* - send any number of minutes, e.g. 10\n"
    "  pauses repeats for that long, then they resume every 4 minutes\n"
    "  (reply to a reminder to snooze just that one)"
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
            ok.append(fmt_scheduled(rid, next_run_utc, parsed["message"], parsed["recurrence"]))
        except ValueError as e:
            errors.append(str(e))

    reply = ""
    if ok:
        reply += "✅ Scheduled:\n\n" + "\n\n".join(ok) + "\n"
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
    pending = db.list_unacked(update.effective_chat.id)
    if not rows and not pending:
        await update.message.reply_text("No active reminders.")
        return
    lines = []
    for rid, message, next_run, recurrence, weekday in rows:
        tag = f" ({recurrence})" if recurrence else ""
        lines.append(f"#{rid}{tag} {fmt_ist(next_run)} — {message}")
    if pending:
        tags = ", ".join(f"#{r}" for r in sorted({p[0] for p in pending}))
        lines.append(f"\n⚠️ Awaiting ACK: {tags}")
    await update.message.reply_text("\n".join(lines).strip())


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /cancel <id>  (see /myreminders)")
        return
    rid = int(args[0])
    # Cancelling also stops any pending "ACK" resends for that reminder.
    stopped = db.ack_reminder(chat_id, rid)
    if db.get_reminder(rid, chat_id):
        db.deactivate(rid)
        await update.message.reply_text(f"Cancelled #{rid}.")
    elif stopped:
        await update.message.reply_text(f"Stopped repeating #{rid}.")
    else:
        await update.message.reply_text(f"No active reminder #{rid} for you.")


async def clrall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    cleared = db.deactivate_all(chat_id)
    stopped = db.ack_all(chat_id)  # also stop any pending ACK repeats
    total = len(set(cleared) | set(stopped))
    if total:
        await update.message.reply_text(f"🧹 Cleared {total}")
    else:
        await update.message.reply_text("No reminders to clear.")


async def ack_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    m = ACK_RE.match(msg.text or "")
    if not m:
        return
    chat_id = update.effective_chat.id
    ids = [int(x) for x in re.findall(r"\d+", m.group(1) or "")]

    acked: list[int] = []
    if ids:
        for rid in ids:
            acked += db.ack_reminder(chat_id, rid)
    else:
        if msg.reply_to_message:
            acked = db.ack_by_message(chat_id, msg.reply_to_message.message_id)
        if not acked:
            acked = db.ack_all(chat_id)

    # Acknowledged silently; only complain when there was nothing to ACK.
    if not acked:
        await msg.reply_text("Nothing pending to acknowledge.")


async def snooze_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    m = SNOOZE_RE.match(msg.text or "")
    if not m:
        return
    minutes = int(m.group(1))
    if not 1 <= minutes <= MAX_SNOOZE_MINUTES:
        await msg.reply_text(f"Snooze must be 1–{MAX_SNOOZE_MINUTES} minutes.")
        return
    chat_id = update.effective_chat.id
    until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()

    # Snoozed silently — no confirmation message.
    snoozed: list[int] = []
    if msg.reply_to_message:
        snoozed = db.snooze_by_message(chat_id, msg.reply_to_message.message_id, until)
    if not snoozed:
        db.snooze_all(chat_id, until)


async def _send_notification(bot, notification_id: int, chat_id: int, text: str):
    try:
        sent = await bot.send_message(chat_id=chat_id, text=text)
        db.record_notification_message(notification_id, chat_id, sent.message_id)
    except Exception:
        logger.exception(f"Failed to send notification {notification_id}")


async def check_due(context: ContextTypes.DEFAULT_TYPE):
    now_utc = datetime.now(timezone.utc)
    now_utc_iso = now_utc.isoformat()
    next_resend_iso = (now_utc + ACK_RESEND_INTERVAL).isoformat()

    # 1) Resend notifications still waiting for an ACK (every 4 minutes).
    for nid, rid, chat_id, text, send_count in db.get_resends_due(now_utc_iso):
        db.bump_resend(nid, next_resend_iso)
        await _send_notification(
            context.bot, nid, chat_id,
            f"🔁 Repeat {send_count} — not acknowledged yet\n\n{text}",
        )

    # 2) Fire newly due reminders; each one then waits for an ACK.
    due = db.get_due(now_utc_iso)
    for rid, chat_id, message, next_run, recurrence, weekday in due:
        text = notification_text(rid, message)
        nid = db.add_notification(rid, chat_id, text, next_resend_iso)
        await _send_notification(context.bot, nid, chat_id, text)

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
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    db.init_db()
    db.init_ack_tables()
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("remind", remind_cmd))
    application.add_handler(CommandHandler("bulkremind", bulkremind_cmd))
    application.add_handler(CommandHandler("myreminders", myreminders_cmd))
    application.add_handler(CommandHandler("cancel", cancel_cmd))
    application.add_handler(CommandHandler("clrall", clrall_cmd))
    # "ACK", "/ack", "ACK 325" — plain text or command form.
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(ACK_RE), ack_handler))
    # "10" — snooze pending reminders for 10 minutes.
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(SNOOZE_RE), snooze_handler))

    application.job_queue.run_repeating(check_due, interval=30, first=10)

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
