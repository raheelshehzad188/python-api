"""Availability Engine — backend-only slot calculation for Services.

Gemini must NEVER compute openings, relative dates, or cancel windows.
It only calls tools and turns structured JSON into natural language.

Inputs used:
  - Working hours (open / close)
  - Break times
  - Holidays
  - Existing non-cancelled bookings
  - Service duration (from catalog)

Output (always this shape for the AI tool):
  {"available_slots": [{"from": "09:00", "to": "10:00"}, ...], "date": "YYYY-MM-DD"}

Empty array when nothing is free.
Last slot never ends after closing time (e.g. close 17:00, duration 60 → last is 16:00–17:00).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from services_settings import CATALOG_TABLE, WORKING_HOURS_TABLE, HOLIDAYS_TABLE

BOOKINGS_TABLE = "services_bookings"
DEFAULT_SLOT_STEP_MINUTES = 30

# Default business timezone (Pakistan). Override via services_settings.timezone when present.
DEFAULT_TIMEZONE = "Asia/Karachi"

WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "pir": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "jumma": 4,
    "juma": 4,
    "sat": 5,
    "saturday": 5,
    "hafta": 5,
    "sun": 6,
    "sunday": 6,
    "itwar": 6,
}


def _zoneinfo(name):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def business_timezone(db=None, user_id=None):
    """Resolve timezone for this Services business (settings → default)."""
    tz_name = DEFAULT_TIMEZONE
    if db is not None and user_id is not None:
        try:
            settings = db.row("services_settings", {"user_id": user_id}) or {}
            raw = (settings.get("timezone") or "").strip()
            if raw:
                tz_name = raw
        except Exception:
            pass
    return _zoneinfo(tz_name), tz_name


def now_local(db=None, user_id=None):
    tz, _name = business_timezone(db, user_id)
    return datetime.now(tz)


def today_local(db=None, user_id=None):
    return now_local(db, user_id).date()


def _fmt_time(value):
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    s = str(value)
    if re.match(r"^\d{2}:\d{2}:\d{2}$", s):
        return s[:5]
    return s


def _fmt_date(value):
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def parse_date(value):
    """Strict ISO date only (YYYY-MM-DD). For relative phrases use resolve_booking_date."""
    raw = str(value or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def resolve_booking_date(value, db=None, user_id=None, *, now=None):
    """Resolve customer/AI date phrases to a real calendar date using server timezone.

    Accepts:
      - ISO: 2026-07-18
      - today / aaj
      - tomorrow / kal
      - day after tomorrow / parson
      - monday / next monday / friday / this friday
      - in N days

    Returns dict:
      {
        "success": True/False,
        "date": "YYYY-MM-DD" | None,
        "date_obj": date | None,
        "resolved_from": original phrase,
        "timezone": "...",
        "today": "YYYY-MM-DD",
        "error": "..." (when failed)
      }
    """
    tz, tz_name = business_timezone(db, user_id)
    now_dt = now or datetime.now(tz)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=tz)
    else:
        now_dt = now_dt.astimezone(tz)
    today = now_dt.date()

    raw = str(value or "").strip()
    if not raw:
        return {
            "success": False,
            "date": None,
            "date_obj": None,
            "resolved_from": "",
            "timezone": tz_name,
            "today": today.isoformat(),
            "error": "date is required",
        }

    # Already ISO
    iso = parse_date(raw)
    if iso:
        return {
            "success": True,
            "date": iso.isoformat(),
            "date_obj": iso,
            "resolved_from": raw,
            "timezone": tz_name,
            "today": today.isoformat(),
        }

    phrase = raw.lower().strip()
    phrase = re.sub(r"[,\.!?]+$", "", phrase)
    phrase = re.sub(r"\s+", " ", phrase)

    # Strip common wrappers: "on monday", "for tomorrow", "this coming friday"
    phrase = re.sub(r"^(on|for|by|this coming|this|coming)\s+", "", phrase)

    resolved = None

    if phrase in ("today", "aaj", "اج", "آج"):
        resolved = today
    elif phrase in ("tomorrow", "tmrw", "tmr", "kal", "کل", "كالا"):
        resolved = today + timedelta(days=1)
    elif phrase in (
        "day after tomorrow",
        "day after tommorow",
        "parson",
        "parsoun",
        "پرسوں",
        "پروں",
    ):
        resolved = today + timedelta(days=2)
    else:
        m = re.match(r"^(?:in\s+)?(\d+)\s*days?(?:\s+from\s+now)?$", phrase)
        if m:
            resolved = today + timedelta(days=int(m.group(1)))
        else:
            force_next = False
            weekday_phrase = phrase
            if weekday_phrase.startswith("next "):
                force_next = True
                weekday_phrase = weekday_phrase[5:].strip()
            wd = WEEKDAY_ALIASES.get(weekday_phrase)
            if wd is not None:
                days_ahead = (wd - today.weekday()) % 7
                if days_ahead == 0:
                    # "Monday" on Monday → today; "next Monday" on Monday → +7
                    resolved = today if not force_next else today + timedelta(days=7)
                else:
                    resolved = today + timedelta(days=days_ahead)

    if not resolved:
        return {
            "success": False,
            "date": None,
            "date_obj": None,
            "resolved_from": raw,
            "timezone": tz_name,
            "today": today.isoformat(),
            "error": (
                f"Could not resolve date '{raw}'. "
                "Use YYYY-MM-DD or phrases like today, tomorrow, kal, Monday, next Friday."
            ),
        }

    return {
        "success": True,
        "date": resolved.isoformat(),
        "date_obj": resolved,
        "resolved_from": raw,
        "timezone": tz_name,
        "today": today.isoformat(),
    }


def parse_time(value):
    raw = str(value or "").strip()
    if re.match(r"^\d{2}:\d{2}$", raw):
        return f"{raw}:00"
    if re.match(r"^\d{2}:\d{2}:\d{2}$", raw):
        return raw
    return None


def time_to_minutes(value):
    raw = parse_time(value) or _fmt_time(value)
    if not raw or ":" not in raw:
        return None
    parts = raw.split(":")
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None


def minutes_to_time(minutes):
    minutes = max(0, int(minutes)) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _overlaps(start_a, end_a, start_b, end_b):
    return start_a < end_b and start_b < end_a


def _is_holiday(db, user_id, day):
    rows = db.select(HOLIDAYS_TABLE, {"user_id": user_id}) or []
    target = day.isoformat()
    for r in rows:
        if _fmt_date(r.get("holiday_date")) == target:
            title = (r.get("title") or r.get("reason") or "").strip()
            return True, title
    return False, ""


def _day_hours(db, user_id, day):
    """Working hours row for this calendar day (Mon=0 .. Sun=6)."""
    dow = day.weekday()
    rows = db.select(WORKING_HOURS_TABLE, {"user_id": user_id}) or []
    for r in rows:
        if int(r.get("day_of_week") or -1) == dow:
            return r
    return None


def _bookings_on_date(db, user_id, day, exclude_booking_id=None):
    db.cursor.execute(
        f"""
        SELECT b.*, c.name AS service_name
        FROM {BOOKINGS_TABLE} b
        LEFT JOIN {CATALOG_TABLE} c ON c.id = b.service_id
        WHERE b.user_id=%s
          AND b.booking_date=%s
          AND b.status != 'cancelled'
        ORDER BY b.start_time ASC
        """,
        [user_id, day.isoformat()],
    )
    rows = db.cursor.fetchall() or []
    if exclude_booking_id:
        rows = [r for r in rows if int(r.get("id") or 0) != int(exclude_booking_id)]
    return rows


def resolve_service_duration(db, user_id, service_id):
    """Return (service_row_or_None, duration_minutes)."""
    if not service_id:
        return None, None
    try:
        sid = int(service_id)
    except (TypeError, ValueError):
        return None, None
    row = db.row(CATALOG_TABLE, {"id": sid, "user_id": user_id})
    if not row:
        return None, None
    duration = int(row.get("duration_minutes") or 0)
    if duration <= 0:
        duration = DEFAULT_SLOT_STEP_MINUTES
    return row, duration


def compute_available_slots(
    db,
    user_id,
    day,
    duration_minutes,
    slot_step=DEFAULT_SLOT_STEP_MINUTES,
    exclude_booking_id=None,
    skip_past_today=True,
):
    """Core engine — returns list of {"from": "HH:MM", "to": "HH:MM"}.

    Hard rule: slot end must NEVER exceed closing time.
    close=17:00, duration=60 → last valid slot is 16:00→17:00 (never 17:00→18:00).
    """
    if not isinstance(day, date):
        return []

    duration = max(1, int(duration_minutes or DEFAULT_SLOT_STEP_MINUTES))
    step = max(1, int(slot_step or DEFAULT_SLOT_STEP_MINUTES))

    holiday, _reason = _is_holiday(db, user_id, day)
    if holiday:
        return []

    hours = _day_hours(db, user_id, day)
    if not hours or hours.get("is_closed") or not hours.get("open_time") or not hours.get("close_time"):
        return []

    open_m = time_to_minutes(hours.get("open_time"))
    close_m = time_to_minutes(hours.get("close_time"))
    if open_m is None or close_m is None or close_m <= open_m:
        return []

    break_start = time_to_minutes(hours.get("break_start"))
    break_end = time_to_minutes(hours.get("break_end"))

    occupied = []
    for b in _bookings_on_date(db, user_id, day, exclude_booking_id=exclude_booking_id):
        s = time_to_minutes(b.get("start_time"))
        e = time_to_minutes(b.get("end_time"))
        if s is not None and e is not None and e > s:
            occupied.append((s, e))

    now_minutes = None
    if skip_past_today and day == today_local(db, user_id):
        now = now_local(db, user_id)
        now_minutes = now.hour * 60 + now.minute

    # Latest start that still ends at or before close.
    last_start = close_m - duration
    if last_start < open_m:
        return []

    slots = []
    cursor = open_m
    while cursor <= last_start:
        end = cursor + duration
        # Absolute guard — never emit a slot past closing time.
        if end > close_m:
            break
        if now_minutes is not None and cursor < now_minutes:
            cursor += step
            continue
        if break_start is not None and break_end is not None:
            if _overlaps(cursor, end, break_start, break_end):
                cursor += step
                continue
        if any(_overlaps(cursor, end, s, e) for s, e in occupied):
            cursor += step
            continue
        slots.append({
            "from": minutes_to_time(cursor),
            "to": minutes_to_time(end),
        })
        cursor += step

    return slots


def get_available_slots(db, user_id, date_value, service_id, slot_step=DEFAULT_SLOT_STEP_MINUTES):
    """Public API for the AI tool and booking validators.

    Resolves relative dates on the backend. Returns structured JSON including
    the resolved ISO date so Gemini never invents calendar dates.
    """
    resolved = resolve_booking_date(date_value, db, user_id)
    if not resolved.get("success"):
        return {
            "success": False,
            "available_slots": [],
            "date": None,
            "resolved_from": resolved.get("resolved_from"),
            "timezone": resolved.get("timezone"),
            "today": resolved.get("today"),
            "error": resolved.get("error") or "Invalid date",
        }

    day = resolved["date_obj"]
    svc, duration = resolve_service_duration(db, user_id, service_id)
    if duration is None:
        return {
            "success": False,
            "available_slots": [],
            "date": resolved["date"],
            "resolved_from": resolved.get("resolved_from"),
            "timezone": resolved.get("timezone"),
            "today": resolved.get("today"),
            "error": "service not found",
        }

    hours = _day_hours(db, user_id, day) or {}
    holiday, holiday_reason = _is_holiday(db, user_id, day)
    slots = compute_available_slots(
        db,
        user_id,
        day,
        duration_minutes=duration,
        slot_step=slot_step,
    )
    return {
        "success": True,
        "available_slots": slots,
        "date": resolved["date"],
        "resolved_from": resolved.get("resolved_from"),
        "timezone": resolved.get("timezone"),
        "today": resolved.get("today"),
        "service_id": int(service_id),
        "service_name": (svc.get("name") if svc else "") or "",
        "duration_minutes": duration,
        "opens_at": minutes_to_time(time_to_minutes(hours.get("open_time")))
        if hours and not hours.get("is_closed") and time_to_minutes(hours.get("open_time")) is not None
        else None,
        "closes_at": minutes_to_time(time_to_minutes(hours.get("close_time")))
        if hours and not hours.get("is_closed") and time_to_minutes(hours.get("close_time")) is not None
        else None,
        "is_holiday": holiday,
        "holiday_reason": holiday_reason or None,
        "is_closed": bool(hours.get("is_closed")) if hours else True,
        "count": len(slots),
    }


def is_slot_free(
    db,
    user_id,
    day,
    start_time,
    duration_minutes,
    exclude_booking_id=None,
):
    """True when start_time is one of the engine's free slots for that duration."""
    if not isinstance(day, date):
        resolved = resolve_booking_date(day, db, user_id)
        day = resolved.get("date_obj")
    start = _fmt_time(parse_time(start_time) or start_time)
    if not day or not start:
        return False, {"available_slots": [], "date": None}

    slots = compute_available_slots(
        db,
        user_id,
        day,
        duration_minutes=duration_minutes,
        exclude_booking_id=exclude_booking_id,
    )
    payload = {
        "available_slots": slots,
        "date": day.isoformat(),
        "closes_guard": True,
    }
    free = any(s.get("from") == start for s in slots)
    return free, payload
