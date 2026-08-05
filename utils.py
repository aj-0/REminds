import re
from datetime import datetime, time, timedelta, timezone
from typing import Optional, Tuple, List, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# --- Timezone Helpers ---
def get_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")

def now_in_tz(tz_name: str) -> datetime:
    return datetime.now(get_tz(tz_name))

def utc_to_local(utc_dt: datetime, tz_name: str) -> datetime:
    if utc_dt.tzinfo is None: utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(get_tz(tz_name))

def local_to_utc(local_dt: datetime, tz_name: str) -> datetime:
    if local_dt.tzinfo is None: local_dt = local_dt.replace(tzinfo=get_tz(tz_name))
    return local_dt.astimezone(timezone.utc)

# --- Parsing ---
# Regex for: "2026-08-10 16:00", "16:00", "4:00 PM", "4 PM"
TIME_REGEX = re.compile(r'^(?:(\d{4}-\d{2}-\d{2})[\sT])?(\d{1,2}):?(\d{2})?\s*(am|pm)?$', re.IGNORECASE)
RELATIVE_REGEX = re.compile(r'^in\s+(\d+)\s*(m|min|minute|minutes|h|hr|hour|hours|d|day|days)$', re.IGNORECASE)
WEEKLY_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

class ParseResult:
    def __init__(self, rtype: str, run_time_utc: datetime, days: str = None, text: str = "", error: str = None):
        self.type = rtype          # 'once', 'daily', 'weekly'
        self.run_time_utc = run_time_utc # datetime in UTC
        self.days_of_week = days   # "mon,wed" for weekly
        self.text = text
        self.error = error

    @property
    def success(self): return self.error is None

def parse_time_input(time_str: str, tz_name: str, base_dt: datetime = None) -> Optional[datetime]:
    """
    Parses 'HH:MM', 'H:MM AM/PM', 'YYYY-MM-DD HH:MM' into aware datetime in user TZ.
    Returns aware datetime in user TZ.
    """
    if base_dt is None: base_dt = now_in_tz(tz_name)
    tz = get_tz(tz_name)
    m = TIME_REGEX.match(time_str.strip())
    if not m: return None

    date_part, hour_str, min_str, ampm = m.groups()
    hour = int(hour_str)
    minute = int(min_str) if min_str else 0

    # 12h conversion
    if ampm:
        ampm = ampm.lower()
        if ampm == 'pm' and hour != 12: hour += 12
        if ampm == 'am' and hour == 12: hour = 0
    elif hour > 23: return None # Invalid 24h

    if date_part:
        try: dt = datetime.strptime(date_part, "%Y-%m-%d").replace(hour=hour, minute=minute, tzinfo=tz)
        except ValueError: return None
    else:
        dt = base_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    return dt

def parse_relative(rel_str: str, tz_name: str) -> Optional[datetime]:
    """Parses 'in 30m', 'in 2h', 'in 1d' -> UTC datetime."""
    m = RELATIVE_REGEX.match(rel_str.strip())
    if not m: return None
    val, unit = int(m.group(1)), m.group(2).lower()
    delta = None
    if unit.startswith('m'): delta = timedelta(minutes=val)
    elif unit.startswith('h'): delta = timedelta(hours=val)
    elif unit.startswith('d'): delta = timedelta(days=val)
    if delta: return datetime.now(timezone.utc) + delta
    return None

def parse_weekly_days(day_str: str) -> Optional[str]:
    """Parses 'mon,wed,fri' -> sorted comma separated string. Validates."""
    parts = [d.strip().lower()[:3] for d in day_str.split(',')]
    nums = []
    for p in parts:
        if p in WEEKLY_DAYS: nums.append(WEEKLY_DAYS[p])
        else: return None
    if not nums: return None
    nums.sort()
    return ",".join(DAY_NAMES[n] for n in nums)

def calculate_next_run(rtype: str, target_time_local: datetime, days: str, tz_name: str) -> datetime:
    """
    Given a local target time (aware), calculates the NEXT occurrence >= now in UTC.
    target_time_local: datetime with tzinfo set to user TZ.
    """
    now = now_in_tz(tz_name)
    tz = get_tz(tz_name)
    
    # Normalize target to today in user TZ
    candidate = target_time_local.replace(year=now.year, month=now.month, day=now.day)
    
    if rtype == "once":
        # If specific date was parsed, target_time_local already has correct date.
        # If only time given (daily/weekly logic handles recurrence), 'once' implies today/tomorrow logic handled in main parser.
        # Here we assume target_time_local is the exact desired datetime.
        if candidate < now: 
            # If user said "once 16:00" and it's past, they likely mean tomorrow? 
            # But /remind once usually requires date. We'll handle 'today/tomorrow' keywords in main parser.
            pass 
        return candidate.astimezone(timezone.utc)

    elif rtype == "daily":
        if candidate < now: candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    elif rtype == "weekly":
        target_weekday = WEEKLY_DAYS[days.split(',')[0]] # First day in list
        # Find next occurrence of any day in list
        day_nums = [WEEKLY_DAYS[d] for d in days.split(',')]
        days_ahead = 7
        for d_num in day_nums:
            diff = (d_num - now.weekday() + 7) % 7
            if diff == 0:
                # Today. Check time.
                if candidate >= now: days_ahead = 0; break
                else: diff = 7 # Next week
            if diff < days_ahead: days_ahead = diff
        
        candidate = now + timedelta(days=days_ahead)
        candidate = candidate.replace(hour=target_time_local.hour, minute=target_time_local.minute, second=0, microsecond=0)
        return candidate.astimezone(timezone.utc)

    return now.astimezone(timezone.utc)

def parse_remind_command(args: List[str], tz_name: str) -> ParseResult:
    """
    Main parser for /remind <args>
    Styles:
    1. /remind 2026-08-10 16:00 Text
    2. /remind today 16:00 Text
    3. /remind tomorrow 16:00 Text
    4. /remind in 30m Text
    5. /remind daily 16:00 Text
    6. /remind weekly mon,wed 16:00 Text
    7. /remind 16:00 Text (Defaults to daily? or once today/tomorrow? Spec says: today if future, else ask tomorrow. Let's default to ONCE today/tomorrow logic)
    """
    if not args: return ParseResult("", None, error="Usage: /remind <when> <time> [text]")

    now = now_in_tz(tz_name)
    today_str = now.strftime("%Y-%m-%d")
    tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    
    first_arg = args[0].lower()
    text = " ".join(args[2:]) if len(args) > 2 else "Reminder"

    # --- 1. Relative: "in 30m" ---
    if first_arg == "in":
        rel_str = " ".join(args[:2]) # "in 30m"
        dt_utc = parse_relative(rel_str, tz_name)
        if dt_utc: return ParseResult("once", dt_utc, text=text)
        return ParseResult("", None, error="Invalid relative time. Use 'in 30m', 'in 2h', 'in 1d'.")

    # --- 2. Keywords: today, tomorrow ---
    if first_arg in ("today", "tomorrow"):
        if len(args) < 2: return ParseResult("", None, error="Missing time. e.g. /remind today 16:00")
        date_str = today_str if first_arg == "today" else tomorrow_str
        time_str = args[1]
        local_dt = parse_time_input(f"{date_str} {time_str}", tz_name)
        if not local_dt: return ParseResult("", None, error="Invalid time format. Use HH:MM or H:MM AM/PM")
        
        # If 'today' and time passed -> Ask user (handled in bot.py), but we return the time anyway.
        return ParseResult("once", local_dt.astimezone(timezone.utc), text=text)

    # --- 3. Recurring: daily, weekly ---
    if first_arg == "daily":
        if len(args) < 2: return ParseResult("", None, error="Missing time. e.g. /remind daily 16:00")
        local_dt = parse_time_input(args[1], tz_name)
        if not local_dt: return ParseResult("", None, error="Invalid time format.")
        next_run = calculate_next_run("daily", local_dt, "", tz_name)
        return ParseResult("daily", next_run, text=text) # time_utc stored as time component in DB

    if first_arg == "weekly":
        if len(args) < 3: return ParseResult("", None, error="Usage: /remind weekly mon,wed 16:00 Text")
        days = parse_weekly_days(args[1])
        if not days: return ParseResult("", None, error="Invalid days. Use mon,tue,wed,thu,fri,sat,sun")
        local_dt = parse_time_input(args[2], tz_name)
        if not local_dt: return ParseResult("", None, error="Invalid time format.")
        next_run = calculate_next_run("weekly", local_dt, days, tz_name)
        return ParseResult("weekly", next_run, days=days, text=text)

    # --- 4. Absolute Date/Time or Time Only ---
    # Try: "2026-08-10 16:00" (2 args) or "16:00" (1 arg)
    # Join first two args to catch "2026-08-10 16:00"
    potential_datetime = " ".join(args[:2])
    local_dt = parse_time_input(potential_datetime, tz_name)
    
    if local_dt:
        # Check if date was explicit in first arg
        if re.match(r'\d{4}-\d{2}-\d{2}', args[0]):
            # Explicit date -> One time
            if len(args) < 3: text = "Reminder"
            else: text = " ".join(args[2:])
            return ParseResult("once", local_dt.astimezone(timezone.utc), text=text)
        else:
            # Only time provided (e.g. "16:00") -> Default to ONCE today/tomorrow logic
            text = " ".join(args[1:]) if len(args) > 1 else "Reminder"
            return ParseResult("once", local_dt.astimezone(timezone.utc), text=text)

    return ParseResult("", None, error="Could not parse time. Formats: `2026-08-10 16:00`, `today 16:00`, `in 30m`, `daily 16:00`, `weekly mon,wed 16:00`")

def format_dt_for_user(dt_utc: datetime, tz_name: str, show_date: bool = True) -> str:
    """Formats UTC datetime to user's local time string."""
    if not dt_utc: return "N/A"
    local = utc_to_local(dt_utc, tz_name)
    fmt = "%b %d, %Y at %I:%M %p" if show_date else "%I:%M %p"
    return local.strftime(fmt).replace("AM", "AM").replace("PM", "PM") # Keep case

|
