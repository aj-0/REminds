"""
Command + callback handlers, and Application factory.
"""
import logging
from datetime import datetime, timedelta

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import scheduler as sch
from database import Reminder, User, get_session, init_db
from utils import ParseError, now_utc, parse_remind_command, to_local

logger = logging.getLogger(__name__)

DEFAULT_TZ = "UTC"

HELP_TEXT = (
    "*Reminder bot*\n\n"
    "/remind 2026-08-10 16:00 Make coffee — exact date + time\n"
    "/remind today 16:00 Make coffee\n"
    "/remind tomorrow 16:00 Make coffee\n"
    "/remind in 30m Make coffee — relative\n"
    "/remind in 2h Call mom\n"
    "/remind daily 16:00 Make coffee — repeats every day\n"
    "/remind weekly mon,wed,fri 16:00 Take out trash\n\n"
    "/list — show active reminders\n"
    "/edit ID <new spec> — change time/recurrence, e.g. /edit 3 daily 09:00 Stretch\n"
    "/edit ID text <new text> — change text only\n"
    "/delete ID — cancel a reminder\n"
    "/done ID — mark a one-time reminder done\n"
    "/timezone Area/City — e.g. /timezone Asia/Kolkata\n"
    "/snoozes 5,10,15 — customize the snooze button minutes\n"
)


def get_or_create_user(session, chat_id):
    user = session.get(User, chat_id)
    if not user:
        user = User(chat_id=chat_id, timezone=DEFAULT_TZ, snooze_options="5,10,15")
        session.add(user)
        session.commit()
    return user


def _aware(dt):
    return dt if dt.tzinfo is not None else pytz.utc.localize(dt)


def _confirmation_text(r: Reminder, tz_name):
    local_dt = to_local(_aware(r.next_run_utc), tz_name)
    when = local_dt.strftime("%a %d %b, %I:%M %p")
    if r.type == "once":
        return f"⏰ Got it — I'll remind you '{r.text}' at {when} ({tz_name})."
    if r.type == "daily":
        return f"⏰ Got it — I'll remind you '{r.text}' every day at {local_dt.strftime('%I:%M %p')} ({tz_name}). Next: {when}."
    if r.type == "weekly":
        return f"⏰ Got it — I'll remind you '{r.text}' every {r.days_of_week} at {local_dt.strftime('%I:%M %p')} ({tz_name}). Next: {when}."
    return "⏰ Reminder scheduled."


def _schedule_reminder(r: Reminder, tz_name):
    if r.type == "once":
        sch.add_once_job(r.id, _aware(r.next_run_utc))
    elif r.type == "daily":
        h, mi = map(int, r.time_utc.split(":"))
        sch.add_daily_job(r.id, h, mi, tz_name)
    elif r.type == "weekly":
        h, mi = map(int, r.time_utc.split(":"))
        sch.add_weekly_job(r.id, r.days_of_week.split(","), h, mi, tz_name)


def _parse_id(s):
    try:
        return int(s)
    except ValueError:
        return None


# ---------------------------------------------------------------- commands

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session()
    try:
        get_or_create_user(session, update.effective_chat.id)
    finally:
        session.close()
    await update.message.reply_text(
        "Hi! I'm your reminder bot.\nSet your timezone first: /timezone Area/City\n\n" + HELP_TEXT,
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def timezone_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /timezone Area/City (e.g. /timezone Asia/Kolkata)")
        return
    tz_name = context.args[0]
    if tz_name not in pytz.all_timezones_set:
        await update.message.reply_text(f"Unknown timezone '{tz_name}'. Use an IANA name like Asia/Kolkata, Europe/London.")
        return
    session = get_session()
    try:
        user = get_or_create_user(session, update.effective_chat.id)
        user.timezone = tz_name
        session.commit()
    finally:
        session.close()
    await update.message.reply_text(f"Timezone set to {tz_name}.")


async def snoozes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /snoozes 5,10,15")
        return
    try:
        mins = [str(int(x)) for x in context.args[0].split(",")]
        assert 1 <= len(mins) <= 3
    except Exception:
        await update.message.reply_text("Give 1-3 comma-separated integers, e.g. /snoozes 5,10,20")
        return
    session = get_session()
    try:
        user = get_or_create_user(session, update.effective_chat.id)
        user.snooze_options = ",".join(mins)
        session.commit()
    finally:
        session.close()
    await update.message.reply_text(f"Snooze options set to {', '.join(mins)} minutes.")


async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session()
    try:
        user = get_or_create_user(session, chat_id)
        text = " ".join(context.args) if context.args else ""
        try:
            parsed = parse_remind_command(text, user.timezone)
        except ParseError as e:
            await update.message.reply_text(str(e))
            return

        if parsed.get("ambiguous_today"):
            context.chat_data["pending_today"] = parsed
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("Fire now", callback_data="today_now"),
                InlineKeyboardButton("Tomorrow", callback_data="today_tomorrow"),
            ]])
            await update.message.reply_text(
                f"{parsed['hh']:02d}:{parsed['mm']:02d} has already passed today — "
                "fire it right now, or did you mean tomorrow?",
                reply_markup=kb,
            )
            return

        r = Reminder(
            chat_id=chat_id,
            type=parsed["type"],
            time_utc=parsed.get("time_utc", ""),
            days_of_week=parsed.get("days_of_week"),
            text=parsed["text"],
            active=True,
            next_run_utc=parsed["next_run_utc"].replace(tzinfo=None),
        )
        session.add(r)
        session.commit()

        _schedule_reminder(r, user.timezone)
        await update.message.reply_text(_confirmation_text(r, user.timezone))
    finally:
        session.close()


async def today_ambiguous_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pending = context.chat_data.get("pending_today")
    if not pending:
        await query.edit_message_text("This choice has expired — send /remind again.")
        return
    chat_id = update.effective_chat.id
    session = get_session()
    try:
        user = get_or_create_user(session, chat_id)
        if query.data == "today_now":
            next_run = now_utc()
        else:
            tz = pytz.timezone(user.timezone)
            local_now = datetime.now(tz)
            target = local_now.date() + timedelta(days=1)
            local_dt = tz.localize(
                datetime.combine(target, datetime.min.time()).replace(hour=pending["hh"], minute=pending["mm"])
            )
            next_run = local_dt.astimezone(pytz.utc)

        r = Reminder(
            chat_id=chat_id, type="once", time_utc="", days_of_week=None,
            text=pending["text"], active=True, next_run_utc=next_run.replace(tzinfo=None),
        )
        session.add(r)
        session.commit()
        _schedule_reminder(r, user.timezone)
        await query.edit_message_text(_confirmation_text(r, user.timezone))
    finally:
        session.close()
        context.chat_data.pop("pending_today", None)


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session()
    try:
        user = get_or_create_user(session, chat_id)
        reminders = (
            session.query(Reminder)
            .filter_by(chat_id=chat_id, active=True)
            .order_by(Reminder.next_run_utc)
            .all()
        )
        if not reminders:
            await update.message.reply_text("No active reminders.")
            return
        lines = []
        for r in reminders:
            local_dt = to_local(_aware(r.next_run_utc), user.timezone)
            lines.append(f"#{r.id} [{r.type}] {r.text} — next: {local_dt.strftime('%a %d %b, %I:%M %p')}")
        await update.message.reply_text("\n".join(lines))
    finally:
        session.close()


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /delete ID")
        return
    rid = _parse_id(context.args[0])
    if rid is None:
        await update.message.reply_text("ID must be a number.")
        return
    session = get_session()
    try:
        r = session.get(Reminder, rid)
        if not r or r.chat_id != update.effective_chat.id or not r.active:
            await update.message.reply_text("No such active reminder.")
            return
        r.active = False
        session.commit()
        sch.remove_job(rid)
        await update.message.reply_text(f"Deleted reminder #{rid}.")
    finally:
        session.close()


async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /done ID")
        return
    rid = _parse_id(context.args[0])
    if rid is None:
        await update.message.reply_text("ID must be a number.")
        return
    session = get_session()
    try:
        r = session.get(Reminder, rid)
        if not r or r.chat_id != update.effective_chat.id or not r.active:
            await update.message.reply_text("No such active reminder.")
            return
        if r.type != "once":
            await update.message.reply_text("/done only applies to one-time reminders. Use /delete for recurring ones.")
            return
        r.active = False
        session.commit()
        sch.remove_job(rid)
        await update.message.reply_text(f"Marked #{rid} done.")
    finally:
        session.close()


async def edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /edit ID <new spec>  or  /edit ID text <new text>")
        return
    rid = _parse_id(context.args[0])
    if rid is None:
        await update.message.reply_text("ID must be a number.")
        return
    rest = " ".join(context.args[1:])
    session = get_session()
    try:
        r = session.get(Reminder, rid)
        if not r or r.chat_id != update.effective_chat.id or not r.active:
            await update.message.reply_text("No such active reminder.")
            return
        user = get_or_create_user(session, update.effective_chat.id)

        if rest.lower().startswith("text "):
            r.text = rest[5:]
            session.commit()
            await update.message.reply_text(f"Updated text for #{rid}.")
            return

        try:
            parsed = parse_remind_command(rest, user.timezone)
        except ParseError as e:
            await update.message.reply_text(str(e))
            return
        if parsed.get("ambiguous_today"):
            await update.message.reply_text("That time today has already passed — use 'tomorrow' or 'in Nm' instead for edits.")
            return

        sch.remove_job(rid)
        r.type = parsed["type"]
        r.time_utc = parsed.get("time_utc", "")
        r.days_of_week = parsed.get("days_of_week")
        r.text = parsed["text"]
        r.next_run_utc = parsed["next_run_utc"].replace(tzinfo=None)
        session.commit()
        _schedule_reminder(r, user.timezone)
        await update.message.reply_text(f"Updated #{rid}: " + _confirmation_text(r, user.timezone))
    finally:
        session.close()


async def snooze_done_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")  # "snooze:<id>:<minutes>" or "done:<id>"
    action, rid = parts[0], int(parts[1])
    session = get_session()
    try:
        r = session.get(Reminder, rid)
        if not r:
            await query.edit_message_text("This reminder no longer exists.")
            return

        if action == "done":
            if r.type == "once":
                r.active = False
                session.commit()
                sch.remove_job(rid)
            else:
                # Only clear a pending snooze -- the recurring job stays untouched.
                sch.remove_snooze_job(rid)
            await query.edit_message_text(f"✅ {r.text} — done.")

        elif action == "snooze":
            minutes = int(parts[2])
            sch.add_snooze_job(rid, minutes)
            await query.edit_message_text(f"🔁 Snoozed for {minutes} minutes.")
    finally:
        session.close()


# ---------------------------------------------------------------- app factory

async def _post_init(application: Application):
    init_db()
    sch.set_bot(application.bot)
    sch.reload_all_jobs()
    sch.scheduler.start()
    logger.info("Scheduler started, jobs reloaded.")


def build_application(token: str) -> Application:
    application = Application.builder().token(token).post_init(_post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("list", list_cmd))
    application.add_handler(CommandHandler("remind", remind_cmd))
    application.add_handler(CommandHandler("edit", edit_cmd))
    application.add_handler(CommandHandler("delete", delete_cmd))
    application.add_handler(CommandHandler("done", done_cmd))
    application.add_handler(CommandHandler("timezone", timezone_cmd))
    application.add_handler(CommandHandler("snoozes", snoozes_cmd))
    application.add_handler(CallbackQueryHandler(today_ambiguous_cb, pattern="^today_"))
    application.add_handler(CallbackQueryHandler(snooze_done_cb, pattern="^(snooze|done):"))

    return application
