import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List, Dict
import pytz
from dateutil import parser as dateparser
from dateutil.relativedelta import relativedelta

# Day mapping
DAY_MAP = {
    'mon': 0, 'monday': 0,
    'tue': 1, 'tuesday': 1,
    'wed': 2, 'wednesday': 2,
    'thu': 3, 'thursday': 3,
    'fri': 4, 'friday': 4,
    'sat': 5, 'saturday': 5,
    'sun': 6, 'sunday': 6
}

DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def parse_time_input(time_str: str) -> Optional[datetime.time]:
    """Parse various time formats"""
    # Try HH:MM format
    patterns = [
        r'(\d{1,2}):(\d{2})\s*(am|pm)?',
        r'(\d{1,2})\s*(am|pm)',
        r'(\d{1,2}):(\d{2})',
    ]
    
    for pattern in patterns:
        match = re.match(pattern, time_str.strip(), re.IGNORECASE)
        if match:
            groups = match.groups()
            if len(groups) == 3:  # HH:MM am/pm
                hour, minute, ampm = int(groups[0]), int(groups[1]), groups[2]
                if ampm and ampm.lower() == 'pm' and hour < 12:
                    hour += 12
                elif ampm and ampm.lower() == 'am' and hour == 12:
                    hour = 0
                return datetime.time(hour, minute)
            elif len(groups) == 1:  # Just hour + am/pm
                hour, ampm = int(groups[0]), groups[1]
                if ampm.lower() == 'pm' and hour < 12:
                    hour += 12
                elif ampm.lower() == 'am' and hour == 12:
                    hour = 0
                return datetime.time(hour, 0)
            else:  # HH:MM
                return datetime.time(int(groups[0]), int(groups[1]))
    
    return None


def parse_remind_command(text: str, user_tz: str = 'UTC') -> Optional[Dict]:
    """
    Parse /remind command and return structured data
    Returns dict with: type, time_utc, text, days_of_week
    """
    if not text:
        return None
    
    text = text.strip()
    parts = text.split()
    
    if len(parts) < 3:
        return None
    
    result = {'type': 'once', 'days_of_week': None}
    
    # Check for recurring types
    if parts[0].lower() in ['daily', 'weekly']:
        result['type'] = parts[0].lower()
        parts = parts[1:]
    
    # Parse days for weekly
    if result['type'] == 'weekly' and parts[0].lower() in DAY_MAP:
        days_str = parts[0].lower()
        days = []
        for d in days_str.split(','):
            d = d.strip()
            if d in DAY_MAP:
                days.append(DAY_MAP[d])
        if days:
            result['days_of_week'] = days
            parts = parts[1:]
    
    # Parse time/date
    time_str = parts[0] if len(parts) > 0 else ''
    date_str = None
    
    # Check for date formats
    if parts[0].lower() in ['today', 'tomorrow']:
        date_str = parts[0].lower()
        parts = parts[1:]
        if parts:
            time_str = parts[0]
            parts = parts[1:]
    
    # Check for specific date
    date_match = re.match(r'(\d{4}-\d{2}-\d{2})|(\d{2}\.\d{2}\.\d{4})', parts[0] if parts else '')
    if date_match:
        date_str = parts[0]
        parts = parts[1:]
        if parts:
            time_str = parts[0]
            parts = parts[1:]
    
    # Check for relative time
    relative_match = re.match(r'in\s+(\d+)\s*(m|min|minute|h|hour|d|day)s?', time_str, re.IGNORECASE)
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2).lower()[0]
        now = datetime.now(pytz.UTC)
        if unit == 'm':
            result['time_utc'] = now + timedelta(minutes=amount)
        elif unit == 'h':
            result['time_utc'] = now + timedelta(hours=amount)
        elif unit == 'd':
            result['time_utc'] = now + timedelta(days=amount)
        result['text'] = ' '.join(parts) if parts else ''
        return result
    
    # Parse time
    time_obj = parse_time_input(time_str)
    if not time_obj:
        return None
    
    # Determine date
    now = datetime.now(pytz.UTC)
    user_now = now.astimezone(pytz.timezone(user_tz))
    
    if date_str == 'today':
        target_date = user_now.date()
    elif date_str == 'tomorrow':
        target_date = user_now.date() + timedelta(days=1)
    elif date_str:
        # Parse specific date
        try:
            if '-' in date_str:
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            elif '.' in date_str:
                target_date = datetime.strptime(date_str, '%d.%m.%Y').date()
            else:
                return None
        except ValueError:
            return None
    else:
        target_date = user_now.date()
    
    # Create datetime in user's timezone
    target_dt = datetime.combine(target_date, time_obj)
    target_dt = pytz.timezone(user_tz).localize(target_dt)
    
    # If time has passed for today, suggest tomorrow
    if date_str is None and target_dt < user_now:
        return {'suggest_tomorrow': True, 'original_time': target_dt}
    
    # Convert to UTC
    result['time_utc'] = target_dt.astimezone(pytz.UTC).replace(tzinfo=None)
    result['text'] = ' '.join(parts) if parts else ''
    
    return result


def format_reminder_message(reminder, user_tz: str = 'UTC') -> str:
    """Format reminder message for display"""
    time_utc = reminder.time_utc
    if isinstance(time_utc, str):
        time_utc = datetime.fromisoformat(time_utc)
    
    # Convert to user's timezone
    tz = pytz.timezone(user_tz)
    local_time = pytz.UTC.localize(time_utc).astimezone(tz)
    
    time_str = local_time.strftime('%I:%M %p').lstrip('0')
    date_str = local_time.strftime('%A, %B %d, %Y')
    
    if reminder.type == 'once':
        header = f"⏰ **Reminder:** {reminder.text}"
        return f"{header}\n📅 {date_str} at {time_str}"
    
    elif reminder.type == 'daily':
        header = f"🔁 **Daily Reminder:** {reminder.text}"
        return f"{header}\n⏰ Every day at {time_str}"
    
    elif reminder.type == 'weekly':
        days = json.loads(reminder.days_of_week) if isinstance(reminder.days_of_week, str) else (reminder.days_of_week or [])
        day_names = [DAY_NAMES[d] for d in days]
        header = f"🔁 **Weekly Reminder:** {reminder.text}"
        return f"{header}\n📅 {', '.join(day_names)} at {time_str}"
    
    return str(reminder.text)


def parse_bulk_remind(text: str, user_tz: str = 'UTC') -> List[Dict]:
    """Parse bulk remind commands"""
    reminders = []
    lines = text.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        # Match pattern: * time: text or - time: text
        match = re.match(r'[\*\-\•]\s*(\d{1,2}:\d{2}\s*(?:am|pm)?)\s*[:\-]?\s*(.+)', line, re.IGNORECASE)
        if match:
            time_str, reminder_text = match.groups()
            time_obj = parse_time_input(time_str)
            if time_obj and reminder_text:
                now = datetime.now(pytz.UTC)
                user_now = now.astimezone(pytz.timezone(user_tz))
                target_dt = datetime.combine(user_now.date(), time_obj)
                target_dt = pytz.timezone(user_tz).localize(target_dt)
                
                # If time passed, schedule for next day
                if target_dt < user_now:
                    target_dt += timedelta(days=1)
                
                reminders.append({
                    'type': 'once',
                    'time_utc': target_dt.astimezone(pytz.UTC).replace(tzinfo=None),
                    'text': reminder_text.strip()
                })
    
    return reminders


def format_list_reminders(reminders, user_tz: str = 'UTC') -> str:
    """Format list of reminders for display"""
    if not reminders:
        return "📭 No active reminders."
    
    tz = pytz.timezone(user_tz)
    lines = ["📋 **Your Active Reminders:**\n"]
    
    for i, reminder in enumerate(reminders, 1):
        time_utc = reminder.time_utc
        if isinstance(time_utc, str):
            time_utc = datetime.fromisoformat(time_utc)
        
        local_time = pytz.UTC.localize(time_utc).astimezone(tz)
        time_str = local_time.strftime('%I:%M %p').lstrip('0')
        date_str = local_time.strftime('%b %d')
        
        type_icon = {'once': '⏰', 'daily': '🔁', 'weekly': '📅'}.get(reminder.type, '📌')
        
        lines.append(
            f"{type_icon} **ID {reminder.id}:** {reminder.text}\n"
            f"   └ Next: {date_str} at {time_str} ({reminder.type})"
        )
    
    return '\n'.join(lines)


def extract_text_from_image(image_path: str) -> Optional[str]:
    """Extract text from image using OCR"""
    try:
        from PIL import Image
        import pytesseract
        
        image = Image.open(image_path)
        text = pytesseract.image_to_string(image)
        return text.strip() if text else None
    except Exception as e:
        print(f"OCR Error: {e}")
        return None
