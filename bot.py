import logging
import html
from datetime import datetime, timezone
from typing import List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes, 
    MessageHandler, filters, ConversationHandler
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import (
    get_db, create_reminder, get_active_reminders, get_reminder, 
    deactivate_reminder, update_user_tz, get_user_tz, get_user_snoozes, set_user_snoozes,
    ReminderType, User, Reminder, SessionLocal
)
from scheduler import (
    schedule_reminder_job, remove_reminder_jobs, schedule_snooze_job, 
    scheduler, send_reminder_callback
)
from utils import (
    parse_remind_command, format_dt_for_user, parse_bulk_reminders, 
    get_tz, now_in_tz, ParseResult
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Keyboards ---

def build_reminder_keyboard(reminder_id: int, snooze_options: List[int]) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for mins in snooze_options:
        row.append(InlineKeyboardButton(f"😴 Snooze {mins}m", callback_data=f"snooze_{reminder_id}_{mins}"))
    buttons.append(row)
    buttons.append([
        InlineKeyboardButton("✅ Done", callback_data=f"done_{reminder_id}"),
        InlineKeyboardButton("🗑 Delete", callback_data=f"delete_{reminder_id}")
    ])
    return InlineKeyboardMarkup(buttons)

def build_list_keyboard(reminders: List[Reminder]) -> InlineKeyboardMarkup:
    buttons = []
    for r in reminders[:10]: # Limit buttons
        label = f"{r.id}: {r.text[:20]} ({r.type.value})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"view_{r.id}")])
    return InlineKeyboardMarkup(buttons) if buttons else None

# --- Helpers ---

async def get_user_timezone(chat_id: int) -> str:
    with SessionLocal() as db:
        return get_user_tz(db, chat_id)

def get_user_snoozes_db(chat_id: int) -> List[int]:
    with SessionLocal() as db:
        return get_user_snoozes(db, chat_id)

async def send_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 *Reminder Bot Commands*\n\n"
        "⏰ *Setting Reminders*\n"
        "`/remind 2026-08-10 16:00 Text` — One-time exact\n"
        "`/remind today 16:00 Text` — Today (asks if passed)\n"
        "`/remind tomorrow 16:00 Text` — Tomorrow\n"
        "`/remind in 30m Text` — Relative (m, h, d)\n"
        "`/remind daily 16:00 Text` — Daily recurring\n"
        "`/remind weekly mon,wed,fri 16:00 Text` — Weekly\n"
        "`/remind 16:00 Text` — Once today/tomorrow\n\n"
        "📋 *Management*\n"
        "`/list` — Show active reminders\n"
        "`/edit ID new_time_or_text` — Modify reminder\n"
        "`/delete ID` — Cancel reminder\n"
        "`/done ID` — Mark one-time as complete\n\n"
        "⚙️ *Settings*\n"
        "`/timezone Area/City` — Set your timezone (e.g. `America/New_York`)\n"
        "`/snoozes 5,15,30` — Customize snooze buttons\n\n"
        "📦 *Bulk*\n"
        "`/bulkremind` — Paste a list (see example)\n\n"
        "💡 *Snooze*: Buttons on reminder messages create a one-off alert. "
        "Original recurring schedule is unaffected."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with SessionLocal() as db:
        get_or_create_user(db, update.effective_chat.id)
    await update.message.reply_text("👋 Welcome! I'll remind you on time, every time. Use /help to see commands.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_help(update, context)

async def timezone_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        tz = await get_user_timezone(update.effective_chat.id)
        await update.message.reply_text(f"Your current timezone: `{tz}`\nUsage: `/timezone America/New_York`", parse_mode="Markdown")
        return
    
    tz_name = " ".join(args)
    try:
        get_tz(tz_name) # Validate
        with SessionLocal() as db:
            update_user_tz(db, update.effective_chat.id, tz_name)
        await update.message.reply_text(f"✅ Timezone set to `{tz_name}`", parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("❌ Invalid timezone. Use IANA format (e.g. `Europe/London`, `Asia/Tokyo`).")

async def snoozes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        opts = get_user_snoozes_db(update.effective_chat.id)
        await update.message.reply_text(f"Current snooze options: `{opts}` minutes.\nUsage: `/snoozes 5,10,20`", parse_mode="Markdown")
        return
    try:
        vals = [int(x.strip()) for x in " ".join(args).split(",") if 0 < int(x.strip()) < 1440]
        if not vals: raise ValueError
        with SessionLocal() as db:
            set_user_snoozes(db, update.effective_chat.id, vals)
        await update.message.reply_text(f"✅ Snooze options updated: `{vals}`", parse_mode="Markdown")
    except:
        await update.message.reply_text("❌ Invalid format. Use comma-separated minutes (e.g. `5,10,15`).")

# --- /remind ---

async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/remind <when> <time> [text]`\nExample: `/remind today 16:00 Make coffee`", parse_mode="Markdown")
        return

    tz_name = await get_user_timezone(chat_id)
    result: ParseResult = parse_remind_command(args, tz_name)

    if not result.success:
        await update.message.reply_text(f"❌ {result.error}")
        return

    # Handle "Today" past time logic
    now_local = now_in_tz(tz_name)
    target_local = result.run_time_utc.astimezone(get_tz(tz_name))
    
    if result.type == "once" and "today" in " ".join(args[:1]).lower() and target_local < now_local:
        # Ask user
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ Tomorrow", callback_data=f"confirm_tomorrow_{result.text}"),
            InlineKeyboardButton("🚀 Now (ASAP)", callback_data=f"confirm_now_{result.text}")
        ]])
        await update.message.reply_text(
            f"⏰ That time ({target_local.strftime('%I:%M %p')}) has passed today.\n"
            f"Did you mean *tomorrow* at {target_local.strftime('%I:%M %p')}?",
            reply_markup=keyboard, parse_mode="Markdown"
        )
        # Store pending confirmation in context.user_data
        context.user_data['pending_remind'] = {
            'text': result.text, 'time_utc': result.run_time_utc + timedelta(days=1), 'type': 'once'
        }
        return

    # Create in DB
    with SessionLocal() as db:
        rem = create_reminder(db, chat_id, ReminderType(result.type), result.run_time_utc, result.text, result.days_of_week)
        db.commit()
        rem_id = rem.id

    # Schedule Job
    bot = context.bot
    schedule_reminder_job(rem, bot)

    # Confirm
    next_run_str = format_dt_for_user(result.run_time_utc, tz_name)
    type_str = result.type.capitalize()
    if result.type == "weekly": type_str += f" ({result.days_of_week})"
    
    await update.message.reply_text(
        f"⏰ *Got it!* I'll remind you:\n"
        f"📝 *{html.escape(result.text)}*\n"
        f"🕐 *{next_run_str}* ({type_str})",
        parse_mode="Markdown"
    )

# --- /bulkremind ---

async def bulkremind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *Bulk Reminder Mode*\n\n"
        "Send me a list of times and tasks, one per line.\n"
        "Format: `HH:MM AM/PM: Task description`\n\n"
        "Example:\n"
        "`6:00 AM: Wake up and hydrate`\n"
        "`6:15 AM: Stretch or meditate`\n"
        "`6:45 AM: Exercise`\n"
        "`7:30 AM: Healthy breakfast`\n\n"
        "Times in the past will be scheduled for *tomorrow*.",
        parse_mode="Markdown"
    )
    context.user_data['awaiting_bulk'] = True

async def handle_bulk_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_bulk'): return
    context.user_data['awaiting_bulk'] = False
    
    chat_id = update.effective_chat.id
    tz_name = await get_user_timezone(chat_id)
    items = parse_bulk_reminders(update.message.text, tz_name)
    
    if not items:
        await update.message.reply_text("❌ No valid lines found. Use format `6:00 AM: Task`")
        return

    created = 0
    with SessionLocal() as db:
        for run_utc, text in items:
            rem = create_reminder(db, chat_id, ReminderType.ONCE, run_utc, text)
            schedule_reminder_job(rem, context.bot)
            created += 1
    
    await update.message.reply_text(f"✅ Created *{created}* one-time reminders for tomorrow morning.", parse_mode="Markdown")

# --- /list ---

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tz_name = await get_user_timezone(chat_id)
    
    with SessionLocal() as db:
        rems = get_active_reminders(db, chat_id)
    
    if not rems:
        await update.message.reply_text("📭 No active reminders. Use `/remind` to create one.")
        return

    lines = ["📋 *Your Active Reminders*"]
    for r in rems:
        next_str = format_dt_for_user(r.next_run_utc, tz_name) if r.next_run_utc else "Pending..."
        type_emoji = "🔁" if r.type != ReminderType.ONCE else "⏰"
        days = f" [{r.days_of_week}]" if r.days_of_week else ""
        lines.append(f"{type_emoji} `#{r.id}` {r.text[:40]} — *{next_str}* ({r.type.value}{days})")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=build_list_keyboard(rems))

# --- /edit ---

async def edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: `/edit ID new_time_or_text`\nExample: `/edit 5 17:00` or `/edit 5 New task text`")
        return
    
    chat_id = update.effective_chat.id
    try: rem_id = int(args[0])
    except: await update.message.reply_text("Invalid ID."); return
    
    new_input = " ".join(args[1:])
    tz_name = await get_user_timezone(chat_id)
    
    with SessionLocal() as db:
        rem = get_reminder(db, rem_id, chat_id)
        if not rem: 
            await update.message.reply_text("❌ Reminder not found.")
            return
        
        # Heuristic: if input looks like time, update time. Else update text.
        # Try parse as time first
        parsed_time = parse_time_input(new_input, tz_name)
        is_time = parsed_time is not None
        
        if is_time:
            # Update time
            new_utc = parsed_time.astimezone(timezone.utc)
            rem.time_utc = new_utc
            rem.next_run_utc = new_utc
            # For recurring, time_utc stores time component. 
            # We need to preserve date part for 'once', but for cron we only need hour/min.
            # APScheduler CronTrigger uses hour/min from trigger. 
            # We reschedule job completely.
            msg = f"⏰ Time updated to {format_dt_for_user(new_utc, tz_name)}"
        else:
            # Update text
            rem.text = new_input
            msg = f"📝 Text updated."
        
        db.commit()
    
    # Reschedule
    schedule_reminder_job(rem, context.bot)
    await update.message.reply_text(f"✅ Reminder #{rem_id} updated. {msg}")

# --- /delete /done ---

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args: await update.message.reply_text("Usage: `/delete ID`"); return
    chat_id = update.effective_chat.id
    try: rem_id = int(args[0])
    except: await update.message.reply_text("Invalid ID."); return
    
    with SessionLocal() as db:
        if deactivate_reminder(db, rem_id, chat_id):
            remove_reminder_jobs(rem_id)
            await update.message.reply_text(f"🗑 Reminder #{rem_id} deleted.")
        else:
            await update.message.reply_text("❌ Not found.")

async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Alias for delete for one-time, but semantically "complete"
    await delete_cmd(update, context)

# --- Callback Queries (Buttons) ---

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    msg_id = query.message.message_id
    bot = context.bot

    # Snooze: snooze_{rem_id}_{mins}
    if data.startswith("snooze_"):
        _, rem_id_str, mins_str = data.split("_")
        rem_id, mins = int(rem_id_str), int(mins_str)
        
        # Edit current message
        await query.edit_message_text(
            f"{query.message.text}\n\n🔁 *Snoozed for {mins} minutes.*",
            parse_mode="Markdown"
        )
        
        # Schedule snooze job (sends new reminder later)
        schedule_snooze_job(rem_id, mins, context.bot)
        return

    # Done: done_{rem_id}
    if data.startswith("done_"):
        rem_id = int(data.split("_")[1])
        with SessionLocal() as db:
            if deactivate_reminder(db, rem_id, chat_id):
                remove_reminder_jobs(rem_id)
        await query.edit_message_text(f"{query.message.text}\n\n✅ *Marked as done.*", parse_mode="Markdown")
        return

    # Delete: delete_{rem_id}
    if data.startswith("delete_"):
        rem_id = int(data.split("_")[1])
        with SessionLocal() as db:
            if deactivate_reminder(db, rem_id, chat_id):
                remove_reminder_jobs(rem_id)
        await query.edit_message_text(f"{query.message.text}\n\n🗑 *Deleted.*", parse_mode="Markdown")
        return

    # Confirm Tomorrow (from /remind today past time)
    if data.startswith("confirm_tomorrow_"):
        text = data.split("_", 2)[2]
        pending = context.user_data.get('pending_remind')
        if pending and pending['text'] == text:
            with SessionLocal() as db:
                rem = create_reminder(db, chat_id, ReminderType.ONCE, pending['time_utc'], text)
                schedule_reminder_job(rem, bot)
            tz = await get_user_timezone(chat_id)
            next_str = format_dt_for_user(pending['time_utc'], tz)
            await query.edit_message_text(f"⏰ Set for *tomorrow* at {next_str}.", parse_mode="Markdown")
            context.user_data.pop('pending_remind', None)
        return

    if data.startswith("confirm_now_"):
        text = data.split("_", 2)[2]
        pending = context.user_data.get('pending_remind')
        if pending and pending['text'] == text:
            with SessionLocal() as db:
                rem = create_reminder(db, chat_id, ReminderType.ONCE, datetime.now(timezone.utc), text)
                schedule_reminder_job(rem, bot)
            await query.edit_message_text(f"⏰ Reminder set for *now*.", parse_mode="Markdown")
            context.user_data.pop('pending_remind', None)
        return

# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {context.error}")

# --- Application Builder ---
def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()
    
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("timezone", timezone_cmd))
    app.add_handler(CommandHandler("snoozes", snoozes_cmd))
    app.add_handler(CommandHandler("remind", remind_cmd))
    app.add_handler(CommandHandler("bulkremind", bulkremind_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("edit", edit_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Bulk Text Handler (Must be after commands)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_bulk_text))
    
    # Errors
    app.add_error_handler(error_handler)
    
    return app
