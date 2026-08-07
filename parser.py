import re
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

WEEKDAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")


def parse_hhmm(text: str):
    """Parse '1:50 pm', '1.50pm', '13:50' -> (hour24, minute)."""
    text = text.strip().lower()
    m = re.search(r"(\d{1,2})[:.](\d{2})\s*([ap])\.?\s*m?\.?", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        ampm = m.group(3)
        if hour == 12:
            hour = 0
        if ampm == "p":
            hour += 12
        if minute > 59:
            raise ValueError(f"Bad minutes in time '{text}'")
        return hour, minute

    m2 = re.search(r"^(\d{1,2}):(\d{2})$", text)
    if m2:
        hour, minute = int(m2.group(1)), int(m2.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute

    raise ValueError(f"Couldn't parse time from '{text}' (use e.g. '5:30 pm')")


def parse_date_token(token: str, today: date):
    token = token.strip().lower()
    if token == "today":
        return today
    if token == "tomorrow":
        return today + timedelta(days=1)
    m = _DATE_RE.match(token)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return date(y, mo, d)
    return None


def parse_line(line: str, now_ist: datetime) -> dict:
    """
    Parses one reminder line, e.g.:
      today 1:50 pm - make coffee
      tomorrow 9:00 am - submit report
      17.08.2026 - birth wish him          (no time -> defaults to 9:00 AM)
      daily 7:00 am - drink water
      weekly mon 9:00 am - team sync

    Returns: {message, next_run (aware datetime, IST), recurrence, weekday}
    """
    if " - " not in line:
        raise ValueError(f"Missing ' - ' separator: '{line}'")

    left, message = line.split(" - ", 1)
    left = left.strip()
    message = message.strip()
    if not message:
        raise ValueError(f"Missing message text: '{line}'")

    tokens = left.split()
    if not tokens:
        raise ValueError(f"Missing date/time: '{line}'")

    today = now_ist.date()
    recurrence = None
    weekday = None

    head = tokens[0].lower()
    if head == "daily":
        recurrence = "daily"
        time_tokens = tokens[1:]
        target_date = today
    elif head == "weekly":
        if len(tokens) < 2:
            raise ValueError(f"Missing weekday after 'weekly': '{line}'")
        wd_name = tokens[1].lower()[:3]
        if wd_name not in WEEKDAY_MAP:
            raise ValueError(f"Unknown weekday '{tokens[1]}' in: '{line}'")
        recurrence = "weekly"
        weekday = WEEKDAY_MAP[wd_name]
        time_tokens = tokens[2:]
        days_ahead = (weekday - today.weekday()) % 7
        target_date = today + timedelta(days=days_ahead)
    else:
        target_date = parse_date_token(tokens[0], today)
        if target_date is None:
            raise ValueError(f"Couldn't parse date '{tokens[0]}' in: '{line}'")
        time_tokens = tokens[1:]

    if time_tokens:
        hour, minute = parse_hhmm(" ".join(time_tokens))
    else:
        hour, minute = 9, 0  # default time when only a date is given

    naive = datetime(target_date.year, target_date.month, target_date.day, hour, minute)
    next_run = naive.replace(tzinfo=IST)

    if recurrence is None and next_run <= now_ist:
        raise ValueError(f"That time has already passed: '{line}'")
    if recurrence == "daily" and next_run <= now_ist:
        next_run += timedelta(days=1)
    if recurrence == "weekly" and next_run <= now_ist:
        next_run += timedelta(days=7)

    return {
        "message": message,
        "next_run": next_run,
        "recurrence": recurrence,
        "weekday": weekday,
    }
