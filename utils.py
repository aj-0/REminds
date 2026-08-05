"""
Parsing for every /remind input style, plus UTC<->local conversion helpers.

All "next_run_utc" values returned here are timezone-aware UTC datetimes.
Callers strip tzinfo before storing in the DB (naive UTC) and re-attach
pytz.utc when reading back.
"""
import re
from datetime import datetime, timedelta

import pytz

WEEKDAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class ParseError(Exception):
    pass


def now_utc():
    return datetime.now(pytz.utc)


def to_local(dt_utc, tz_name):
    if dt_utc.tzinfo is None:
        dt_utc = pytz.utc.localize(dt_utc)
    return dt_utc.astimezone(pytz.timezone(tz_name))


def parse_hhmm(s):
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if not m:
        raise ParseError(f"Invalid time '{s}', expected HH:MM (24h).")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ParseError(f"Invalid time '{s}'.")
    return h, mi


def _next_daily_local(h, mi, tz_name):
    tz = pytz.timezone(tz_name)
    local_now = datetime.now(tz)
    candidate = tz.localize(datetime.combine(local_now.date(), datetime.min.time()).replace(hour=h, minute=mi))
    if candidate <= local_now:
        next_date = candidate.date() + timedelta(days=1)
        candidate = tz.localize(datetime.combine(next_date, datetime.min.time()).replace(hour=h, minute=mi))
    return candidate.astimezone(pytz.utc)


def _next_weekly_local(days, h, mi, tz_name):
    tz = pytz.timezone(tz_name)
    local_now = datetime.now(tz)
    day_nums = {WEEKDAY_MAP[d] for d in days}
    for offset in range(0, 8):
        cand_date = local_now.date() + timedelta(days=offset)
        if cand_date.weekday() in day_nums:
            candidate = tz.localize(datetime.combine(cand_date, datetime.min.time()).replace(hour=h, minute=mi))
            if candidate > local_now:
                return candidate.astimezone(pytz.utc)
    raise ParseError("Could not compute next weekly occurrence.")


def parse_remind_command(text, tz_name):
    """
    text: everything after /remind (or after the ID in /edit ID <spec>).
    Returns a dict:
      {"ambiguous_today": True, "hh": int, "mm": int, "text": str}   -- needs user confirmation
    or
      {"type": "once"|"daily"|"weekly", "next_run_utc": aware dt UTC,
       "text": str, ["time_utc": "HH:MM" local], ["days_of_week": "mon,wed"]}
    """
    text = text.strip()
    if not text:
        raise ParseError("Usage: /remind <when> <message>. See /help for examples.")

    parts = text.split(None, 1)
    keyword = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if keyword in ("today", "tomorrow"):
        m = re.match(r"^(\d{1,2}:\d{2})\s+(.+)$", rest)
        if not m:
            raise ParseError(f"Usage: /remind {keyword} HH:MM <message>")
        h, mi = parse_hhmm(m.group(1))
        msg = m.group(2)
        tz = pytz.timezone(tz_name)
        local_now = datetime.now(tz)
        target_date = local_now.date() if keyword == "today" else local_now.date() + timedelta(days=1)
        local_dt = tz.localize(datetime.combine(target_date, datetime.min.time()).replace(hour=h, minute=mi))
        if keyword == "today" and local_dt <= local_now:
            return {"ambiguous_today": True, "hh": h, "mm": mi, "text": msg}
        return {"type": "once", "next_run_utc": local_dt.astimezone(pytz.utc), "text": msg}

    if keyword == "in":
        m = re.match(r"^(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)\s+(.+)$", rest, re.IGNORECASE)
        if not m:
            raise ParseError("Usage: /remind in <N>m|h <message>, e.g. /remind in 30m Make coffee")
        n = int(m.group(1))
        unit = m.group(2).lower()
        msg = m.group(3)
        delta = timedelta(minutes=n) if unit.startswith("m") else timedelta(hours=n)
        return {"type": "once", "next_run_utc": now_utc() + delta, "text": msg}

    if keyword == "daily":
        m = re.match(r"^(\d{1,2}:\d{2})\s+(.+)$", rest)
        if not m:
            raise ParseError("Usage: /remind daily HH:MM <message>")
        h, mi = parse_hhmm(m.group(1))
        msg = m.group(2)
        return {
            "type": "daily",
            "time_utc": f"{h:02d}:{mi:02d}",  # local HH:MM, see scheduler.py note
            "next_run_utc": _next_daily_local(h, mi, tz_name),
            "text": msg,
        }

    if keyword == "weekly":
        m = re.match(r"^([a-zA-Z,]+)\s+(\d{1,2}:\d{2})\s+(.+)$", rest)
        if not m:
            raise ParseError("Usage: /remind weekly mon,wed,fri HH:MM <message>")
        days_raw = m.group(1).lower().split(",")
        h, mi = parse_hhmm(m.group(2))
        msg = m.group(3)
        days = []
        for d in days_raw:
            d = d.strip()[:3]
            if d not in WEEKDAY_MAP:
                raise ParseError(f"Invalid day '{d}'. Use mon,tue,wed,thu,fri,sat,sun")
            days.append(d)
        return {
            "type": "weekly",
            "time_utc": f"{h:02d}:{mi:02d}",  # local HH:MM
            "days_of_week": ",".join(days),
            "next_run_utc": _next_weekly_local(days, h, mi, tz_name),
            "text": msg,
        }

    # Exact date: 2026-08-10 16:00 Make coffee
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s+(.+)$", text)
    if m:
        date_str, time_str, msg = m.group(1), m.group(2), m.group(3)
        h, mi = parse_hhmm(time_str)
        y, mo, d = map(int, date_str.split("-"))
        tz = pytz.timezone(tz_name)
        try:
            local_dt = tz.localize(datetime(y, mo, d, h, mi))
        except Exception as e:
            raise ParseError(f"Invalid date/time: {e}")
        return {"type": "once", "next_run_utc": local_dt.astimezone(pytz.utc), "text": msg}

    raise ParseError(
        "Couldn't parse that. Examples:\n"
        "/remind 2026-08-10 16:00 Make coffee\n"
        "/remind today 16:00 Make coffee\n"
        "/remind tomorrow 16:00 Make coffee\n"
        "/remind in 30m Make coffee\n"
        "/remind daily 16:00 Make coffee\n"
        "/remind weekly mon,wed,fri 16:00 Take out trash"
    )
