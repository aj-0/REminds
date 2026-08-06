import logging
import asyncio
import os
import json
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
from telegram.constants import ParseMode

from database import (
    init_db, get_db, get_user, add_reminder, update_reminder,
    delete_reminder, get_active_reminders, get_reminder, update_next_run,
    User, Reminder
)
from scheduler import ReminderScheduler
from utils import (
    parse_remind_command, format_reminder_message, parse_bulk_remind,
    format_list_reminders, extract_text_from_image
)

logger = logging.getLogger(__name__)

# Conversation states
SELECTING_ACTION, AWAITING_REMIND_TEXT, AWAITING_EDIT_ID, AWAITING_EDIT_CHANGES = range(4)


class ReminderBot:
    """Main bot handler class"""
    
    def __init__(self, token: str, mongodb_url: str, db_name: str = "reminder_bot"):
        self.token = token
        self.mongodb_url = mongodb_url
        self.db_name = db_name
        
        # Initialize scheduler with callback
        self.scheduler = ReminderScheduler(self._job_callback)
        
        # Initialize application
        self.application = Application.builder().token(token).build()
        
        # Register handlers
        self._register_handlers()
    
    def _register_handlers(self):
        """Register all command and callback handlers"""
        app = self.application
        
        # Command handlers
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("list", self.cmd_list))
        app.add_handler(CommandHandler("remind", self.cmd_remind))
        app.add_handler(CommandHandler("bulkremind", self.cmd_bulk_remind))
        app.add_handler(CommandHandler("edit", self.cmd_edit))
        app.add_handler(CommandHandler("delete", self.cmd_delete))
        app.add_handler(CommandHandler("done", self.cmd_done))
        app.add_handler(CommandHandler("timezone", self.cmd_timezone))
        app.add_handler(CommandHandler("snoozes", self.cmd_snoozes))
        
        # Callback query handler for inline buttons
        app.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Message handler for image reminders
        app.add_handler(MessageHandler(filters.PHOTO & filters.CAPTION, self.handle_image_remind))
        
        # Error handler
        app.add_error_handler(self.error_handler)
    
    async def _job_callback(self, reminder_id: str, chat_id: int, 
                            text: str, reminder_type: str,
                            image_file_id: Optional[str] = None):
        """Callback function for scheduled jobs"""
        try:
            # Get the application's bot
            bot = self.application.bot
            
            # Format message
            message = f"⏰ **Reminder!**\n\n{text}"
            
            # Create keyboard
            keyboard = [
                [
                    InlineKeyboardButton("😴 Snooze 5m", callback_data=f"snooze_{reminder_id}_5"),
                    InlineKeyboardButton("😴 Snooze 10m", callback_data=f"snooze_{reminder_id}_10"),
                    InlineKeyboardButton("😴 Snooze 15m", callback_data=f"snooze_{reminder_id}_15"),
                ],
                [InlineKeyboardButton("✅ Done", callback_data=f"done_{reminder_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Send message (with image if available)
            if image_file_id:
                sent_message = await bot.send_photo(
                    chat_id=chat_id,
                    photo=image_file_id,
                    caption=message,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup
                )
            else:
                sent_message = await bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup
                )
            
            # Update reminder's message_id for snooze editing
            if reminder_type != 'snooze':
                update_reminder(reminder_id, message_id=sent_message.message_id)
            
            # Update next_run for recurring reminders
            if reminder_type in ['daily', 'weekly']:
                try:
                    reminder = get_reminder(reminder_id)
                    if reminder and reminder.next_run_utc:
                        # Calculate next run time
                        from apscheduler.triggers.cron import CronTrigger
                        import pytz
                        
                        time_utc = reminder.time_utc
                        if isinstance(time_utc, str):
                            time_utc = datetime.fromisoformat(time_utc)
                        
                        if reminder.type == 'daily':
                            trigger = CronTrigger(hour=time_utc.hour, 
                                                minute=time_utc.minute,
                                                timezone='UTC')
                        else:
                            days = reminder.days_of_week if isinstance(reminder.days_of_week, list) else []
                            day_names = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
                            days_str = ','.join([day_names[d] for d in days])
                            trigger = CronTrigger(day_of_week=days_str,
                                                hour=time_utc.hour,
                                                minute=time_utc.minute,
                                                timezone='UTC')
                        
                        next_run = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
                        if next_run:
                            update_next_run(reminder_id, next_run)
                except Exception as e:
                    logger.error(f"Failed to update next_run for reminder {reminder_id}: {e}")
            
            logger.info(f"Sent reminder #{reminder_id} to chat {chat_id}")
            
        except Exception as e:
            logger.error(f"Failed to send reminder #{reminder_id}: {e}")
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        chat_id = update.effective_chat.id
        
        # Initialize user
        get_user(chat_id)
        
        await update.message.reply_text(
            "👋 **Welcome to the Reminder Bot!**\n\n"
            "I can help you remember important tasks and events.\n\n"
            "**Commands:**\n"
            "• `/remind [time] [text]` - Set a reminder\n"
            "• `/bulkremind` - Set multiple reminders at once\n"
            "• `/list` - View all reminders\n"
            "• `/edit [id] [changes]` - Edit a reminder\n"
            "• `/delete [id]` - Delete a reminder\n"
            "• `/done [id]` - Mark reminder as done\n"
            "• `/timezone [area/city]` - Set your timezone\n"
            "• `/snoozes [5,10,15]` - Customize snooze options\n"
            "• `/help` - Show this help message\n\n"
            "**Examples:**\n"
            "• `/remind 16:00 Make coffee`\n"
            "• `/remind tomorrow 09:00 Meeting`\n"
            "• `/remind daily 07:00 Wake up`\n"
            "• `/remind weekly mon,wed,fri 18:00 Gym`\n"
            "• `/remind in 30m Take medicine`\n\n"
            "📸 You can also send a photo with `/imgremind [time] [text]` in caption!",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        await self.cmd_start(update, context)
    
    async def cmd_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /list command"""
        chat_id = update.effective_chat.id
        
        user = get_user(chat_id)
        reminders = get_active_reminders(chat_id)
        message = format_list_reminders(reminders, user.timezone)
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_remind(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /remind command"""
        chat_id = update.effective_chat.id
        text = update.message.text.replace('/remind', '', 1).strip()
        
        if not text:
            await update.message.reply_text(
                "❌ Please provide reminder details.\n"
                "Example: `/remind 16:00 Make coffee`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        user = get_user(chat_id)
        parsed = parse_remind_command(text, user.timezone)
        
        if parsed is None:
            await update.message.reply_text(
                "❌ Could not parse your reminder. Please use format:\n"
                "• `/remind HH:MM text`\n"
                "• `/remind YYYY-MM-DD HH:MM text`\n"
                "• `/remind today/tomorrow HH:MM text`\n"
                "• `/remind daily HH:MM text`\n"
                "• `/remind weekly day,day HH:MM text`\n"
                "• `/remind in Xm/Xh text`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Check if time has passed today
        if 'suggest_tomorrow' in parsed:
            original_time = parsed['original_time']
            keyboard = [
                [
                    InlineKeyboardButton("✅ Today anyway", callback_data=f"confirm_today_{original_time.timestamp()}"),
                    InlineKeyboardButton("📅 Tomorrow", callback_data=f"confirm_tomorrow_{original_time.timestamp()}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Store context for later
            context.user_data['pending_remind'] = {
                'text': parsed.get('text', ''),
                'type': parsed.get('type', 'once'),
                'days_of_week': parsed.get('days_of_week'),
                'original_time': original_time
            }
            
            await update.message.reply_text(
                f"⏰ That time ({original_time.strftime('%I:%M %p')}) has already passed today.\n"
                "Would you like to set it for today anyway or tomorrow?",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
            return
        
        # Create the reminder
        reminder = add_reminder(
            chat_id, parsed['type'],
            parsed['time_utc'], parsed['text'],
            parsed.get('days_of_week')
        )
        
        # Schedule the job
        if parsed['type'] == 'once':
            self.scheduler.add_one_time_job(
                reminder.id, chat_id,
                parsed['time_utc'], parsed['text']
            )
        elif parsed['type'] == 'daily':
            self.scheduler.add_daily_job(
                reminder.id, chat_id,
                parsed['time_utc'].hour, parsed['time_utc'].minute,
                parsed['text']
            )
        elif parsed['type'] == 'weekly':
            self.scheduler.add_weekly_job(
                reminder.id, chat_id,
                parsed['days_of_week'],
                parsed['time_utc'].hour, parsed['time_utc'].minute,
                parsed['text']
            )
        
        # Format confirmation
        from pytz import timezone as pytz_timezone
        tz = pytz_timezone(user.timezone)
        local_time = pytz.UTC.localize(parsed['time_utc']).astimezone(tz)
        
        time_str = local_time.strftime('%I:%M %p').lstrip('0')
        date_str = local_time.strftime('%A, %B %d, %Y')
        
        if parsed['type'] == 'once':
            confirm = f"⏰ **Got it!** I'll remind you\n\n"
            confirm += f"📝 **{parsed['text']}**\n"
            confirm += f"📅 {date_str} at {time_str}"
        elif parsed['type'] == 'daily':
            confirm = f"🔁 **Daily reminder set!**\n\n"
            confirm += f"📝 **{parsed['text']}**\n"
            confirm += f"⏰ Every day at {time_str}"
        else:  # weekly
            days = [['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][d] 
                   for d in parsed['days_of_week']]
            confirm = f"📅 **Weekly reminder set!**\n\n"
            confirm += f"📝 **{parsed['text']}**\n"
            confirm += f"📅 {', '.join(days)} at {time_str}"
        
        confirm += f"\n\n🆔 Reminder ID: `{reminder.id}`"
        
        await update.message.reply_text(confirm, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_bulk_remind(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /bulkremind command"""
        chat_id = update.effective_chat.id
        text = update.message.text.replace('/bulkremind', '', 1).strip()
        
        if not text:
            await update.message.reply_text(
                "❌ Please provide reminders in this format:\n"
                "`/bulkremind`\n"
                "`* 6:00 AM: Wake up`\n"
                "`* 6:15 AM: Stretch`\n"
                "`* 6:45 AM: Exercise`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        user = get_user(chat_id)
        reminders_data = parse_bulk_remind(text, user.timezone)
        
        if not reminders_data:
            await update.message.reply_text(
                "❌ Could not parse reminders. Please use format:\n"
                "`* HH:MM: Task description`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        created = []
        for data in reminders_data:
            reminder = add_reminder(
                chat_id, 'once',
                data['time_utc'], data['text']
            )
            self.scheduler.add_one_time_job(
                reminder.id, chat_id,
                data['time_utc'], data['text']
            )
            created.append(reminder)
        
        await update.message.reply_text(
            f"✅ **Created {len(created)} reminders!**\n\n"
            + '\n'.join([f"• `{r.id}` - {r.text}" for r in created]),
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def handle_image_remind(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle image with caption for reminder"""
        chat_id = update.effective_chat.id
        caption = update.message.caption
        
        if not caption or not caption.startswith('/imgremind'):
            return
        
        # Extract command text
        text = caption.replace('/imgremind', '', 1).strip()
        
        if not text:
            await update.message.reply_text(
                "❌ Please provide time and text in caption.\n"
                "Example: `/imgremind 18.8.2026 7.00PM Buy groceries`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Get image file ID
        if update.message.photo:
            image_file_id = update.message.photo[-1].file_id
        else:
            image_file_id = None
        
        user = get_user(chat_id)
        parsed = parse_remind_command(text, user.timezone)
        
        if parsed is None:
            await update.message.reply_text(
                "❌ Could not parse time. Please use format:\n"
                "`/imgremind DD.MM.YYYY HH:MM AM/PM Description`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Create reminder with image
        reminder = add_reminder(
            chat_id, 'once',
            parsed['time_utc'], parsed['text'],
            image_file_id=image_file_id
        )
        
        # Schedule with image
        self.scheduler.add_one_time_job(
            reminder.id, chat_id,
            parsed['time_utc'], parsed['text'],
            image_file_id
        )
        
        # Confirm
        from pytz import timezone as pytz_timezone
        tz = pytz_timezone(user.timezone)
        local_time = pytz.UTC.localize(parsed['time_utc']).astimezone(tz)
        
        await update.message.reply_text(
            f"📸 **Image reminder set!**\n\n"
            f"📝 **{parsed['text']}**\n"
            f"📅 {local_time.strftime('%A, %B %d, %Y')} at "
            f"{local_time.strftime('%I:%M %p').lstrip('0')}\n"
            f"🆔 ID: `{reminder.id}`",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def cmd_edit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /edit command"""
        chat_id = update.effective_chat.id
        text = update.message.text.replace('/edit', '', 1).strip()
        
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await update.message.reply_text(
                "❌ Usage: `/edit [id] [new time and/or text]`\n"
                "Example: `/edit 5 17:00 Buy groceries`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        reminder_id = parts[0]
        changes = parts[1]
        
        user = get_user(chat_id)
        reminder = get_reminder(reminder_id)
        
        if not reminder or reminder.chat_id != chat_id:
            await update.message.reply_text("❌ Reminder not found.")
            return
        
        # Parse new time/text
        parsed = parse_remind_command(changes, user.timezone)
        
        if parsed is None:
            await update.message.reply_text("❌ Could not parse changes.")
            return
        
        # Update database
        update_data = {
            'time_utc': parsed['time_utc'],
            'text': parsed['text'],
            'type': parsed.get('type', reminder.type),
            'days_of_week': parsed.get('days_of_week', reminder.days_of_week)
        }
        
        update_reminder(reminder_id, **update_data)
        
        # Reschedule job
        job_id = f"reminder_{reminder_id}"
        self.scheduler.remove_job(job_id)
        
        if update_data['type'] == 'once':
            self.scheduler.add_one_time_job(
                reminder_id, chat_id,
                parsed['time_utc'], parsed['text']
            )
        elif update_data['type'] == 'daily':
            self.scheduler.add_daily_job(
                reminder_id, chat_id,
                parsed['time_utc'].hour, parsed['time_utc'].minute,
                parsed['text']
            )
        elif update_data['type'] == 'weekly':
            self.scheduler.add_weekly_job(
                reminder_id, chat_id,
                parsed['days_of_week'],
                parsed['time_utc'].hour, parsed['time_utc'].minute,
                parsed['text']
            )
        
        await update.message.reply_text(
            f"✅ **Reminder #{reminder_id} updated!**\n"
            f"📝 New: {parsed['text']}",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def cmd_delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /delete command"""
        chat_id = update.effective_chat.id
        text = update.message.text.replace('/delete', '', 1).strip()
        
        if not text:
            await update.message.reply_text(
                "❌ Usage: `/delete [id]`\n"
                "Example: `/delete 5`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        reminder_id = text
        reminder = get_reminder(reminder_id)
        
        if not reminder or reminder.chat_id != chat_id:
            await update.message.reply_text("❌ Reminder not found.")
            return
        
        # Remove from scheduler
        self.scheduler.remove_job(f"reminder_{reminder_id}")
        
        # Delete from database
        delete_reminder(reminder_id)
        
        await update.message.reply_text(
            f"🗑️ **Reminder #{reminder_id} deleted.**\n"
            f"📝 '{reminder.text}'",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def cmd_done(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /done command"""
        chat_id = update.effective_chat.id
        text = update.message.text.replace('/done', '', 1).strip()
        
        if not text:
            await update.message.reply_text(
                "❌ Usage: `/done [id]`\n"
                "Example: `/done 5`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        reminder_id = text
        reminder = get_reminder(reminder_id)
        
        if not reminder or reminder.chat_id != chat_id:
            await update.message.reply_text("❌ Reminder not found.")
            return
        
        # Only mark one-time reminders as done
        if reminder.type != 'once':
            await update.message.reply_text(
                "❌ Can only mark one-time reminders as done. "
                "Use `/delete` for recurring reminders.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Remove from scheduler
        self.scheduler.remove_job(f"reminder_{reminder_id}")
        
        # Mark as inactive
        update_reminder(reminder_id, active=False)
        
        await update.message.reply_text(
            f"✅ **Reminder #{reminder_id} marked as done!**\n"
            f"📝 '{reminder.text}'",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def cmd_timezone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /timezone command"""
        chat_id = update.effective_chat.id
        text = update.message.text.replace('/timezone', '', 1).strip()
        
        if not text:
            await update.message.reply_text(
                "❌ Please provide your timezone.\n"
                "Example: `/timezone America/New_York`\n"
                "Find yours at: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Validate timezone
        import pytz
        try:
            pytz.timezone(text)
        except pytz.exceptions.UnknownTimeZoneError:
            await update.message.reply_text(
                f"❌ Unknown timezone: `{text}`\n"
                "Please use format like: `America/New_York`, `Europe/London`, `Asia/Tokyo`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        from database import get_db
        db = get_db()
        db.users.update_one(
            {'chat_id': chat_id},
            {'$set': {'timezone': text}}
        )
        
        await update.message.reply_text(
            f"✅ **Timezone updated!**\n"
            f"🕐 Your timezone is now: `{text}`",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def cmd_snoozes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /snoozes command"""
        chat_id = update.effective_chat.id
        text = update.message.text.replace('/snoozes', '', 1).strip()
        
        if not text:
            await update.message.reply_text(
                "❌ Please provide snooze durations in minutes, separated by commas.\n"
                "Example: `/snoozes 5,10,15`\n"
                "Current default: 5, 10, 15 minutes",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Validate format
        parts = text.split(',')
        try:
            minutes = [int(p.strip()) for p in parts]
            if any(m < 1 or m > 120 for m in minutes):
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid format. Please provide numbers between 1 and 120, separated by commas.\n"
                "Example: `/snoozes 5,10,15`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        from database import get_db
        db = get_db()
        db.users.update_one(
            {'chat_id': chat_id},
            {'$set': {'snooze_options': ','.join(str(m) for m in minutes[:5])}}
        )
        
        await update.message.reply_text(
            f"✅ **Snooze options updated!**\n"
            f"🔔 Snooze buttons: {', '.join(str(m) + 'm' for m in minutes[:5])}",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button callbacks"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        # Handle snooze
        if data.startswith('snooze_'):
            parts = data.split('_')
            reminder_id = parts[1]
            delay = int(parts[2])
            
            reminder = get_reminder(reminder_id)
            if not reminder:
                await query.edit_message_text("❌ Reminder no longer exists.")
                return
            
            # Add snooze job
            self.scheduler.add_snooze_job(
                reminder_id, query.message.chat.id,
                delay, reminder.text, reminder.image_file_id
            )
            
            # Update message
            await query.edit_message_text(
                f"🔁 **Snoozed for {delay} minutes**\n\n"
                f"📝 '{reminder.text}'",
                parse_mode=ParseMode.MARKDOWN
            )
        
        # Handle done
        elif data.startswith('done_'):
            reminder_id = data.split('_')[1]
            
            reminder = get_reminder(reminder_id)
            if not reminder:
                await query.edit_message_text("❌ Reminder no longer exists.")
                return
            
            # Mark as done
            update_reminder(reminder_id, active=False)
            
            # Remove job
            self.scheduler.remove_job(f"reminder_{reminder_id}")
            
            await query.edit_message_text(
                f"✅ **Done!**\n\n"
                f"📝 '{reminder.text}'",
                parse_mode=ParseMode.MARKDOWN
            )
        
        # Handle time confirmation (today/tomorrow)
        elif data.startswith('confirm_'):
            parts = data.split('_')
            option = parts[1]  # today or tomorrow
            timestamp = float(parts[2])
            original_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            
            pending = context.user_data.get('pending_remind', {})
            if not pending:
                await query.edit_message_text("❌ Session expired. Please try again.")
                return
            
            chat_id = query.message.chat.id
            
            if option == 'tomorrow':
                original_time += timedelta(days=1)
            
            # Create reminder with confirmed time
            user = get_user(chat_id)
            
            reminder = add_reminder(
                chat_id, pending.get('type', 'once'),
                original_time, pending.get('text', ''),
                pending.get('days_of_week')
            )
            
            # Schedule
            self.scheduler.add_one_time_job(
                reminder.id, chat_id,
                original_time, pending['text']
            )
            
            # Format confirmation
            from pytz import timezone as pytz_timezone
            tz = pytz_timezone(user.timezone)
            local_time = pytz.UTC.localize(original_time).astimezone(tz)
            
            await query.edit_message_text(
                f"⏰ **Got it!**\n\n"
                f"📝 **{pending['text']}**\n"
                f"📅 {local_time.strftime('%A, %B %d, %Y')} at "
                f"{local_time.strftime('%I:%M %p').lstrip('0')}\n"
                f"🆔 ID: `{reminder.id}`",
                parse_mode=ParseMode.MARKDOWN
            )
            
            context.user_data.pop('pending_remind', None)
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Update {update} caused error {context.error}")
        
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again later.",
                parse_mode=ParseMode.MARKDOWN
            )
    
    async def start(self):
        """Start the bot"""
        # Initialize database
        init_db()
        
        # Start scheduler
        self.scheduler.start()
        
        # Reload reminders from database
        self.scheduler.reload_reminders()
        
        # Start polling
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        
        logger.info("Bot started")
    
    async def stop(self):
        """Stop the bot"""
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()
        self.scheduler.shutdown()
        logger.info("Bot stopped")
