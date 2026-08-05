"""Per-staff availability & scheduling engine for Service businesses.

Unlike the legacy business-global engine (availability_engine.py), this module
computes availability PER STAFF MEMBER. Multiple customers can book the same
time as long as different capable staff are free.

Core rules:
  - A staff member can perform a service only if assigned (staff_services pivot
    or legacy assigned_service_ids CSV).
  - A staff member is unavailable when: inactive/on-leave status, on a leave
    date, outside their working days/hours, in their break, already booked
    (overlap) up to max_bookings_per_slot, or over max_hours_per_day.
  - Business working hours + holidays act as the outer bound.
"""

from __future__ import annotations

from datetime import date

from availability_engine import (
    DEFAULT_SLOT_STEP_MINUTES,
    _day_hours,
    _is_holiday,
    minutes_to_time,
    now_local,
    parse_time,
    resolve_booking_date,
    resolve_service_duration,
    time_to_minutes,
    today_local,
)

STAFF_TABLE = "services_staff"
BOOKINGS_TABLE = "services_bookings"
PIVOT_TABLE = "staff_services"
LEAVES_TABLE = "services_staff_leaves"

# Statuses that make a staff member unbookable outright.
BLOCKED_STAFF_STATUS = {"inactive", "leave", "on_leave", "terminated"}
# Booking statuses that still occupy a staff member's time.
ACTIVE_BOOKING_STATUSES = (
    "pending", "confirmed", "assigned", "on_the_way",
    "checked_in", "in_progress", "completed",
)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _overlaps(a1, a2, b1, b2):
    return a1 < b2 and b1 < a2


def _staff_service_ids(db, user_id, staff_row):
    """Service ids a staff can perform (pivot first, fall back to CSV)."""
    sid = staff_row["id"]
    rows = db.select(PIVOT_TABLE, {"user_id": user_id, "staff_id": sid}) or []
    ids = {int(r["service_id"]) for r in rows if r.get("service_id")}
    if ids:
        return ids
    # Legacy fallback: assigned_service_ids CSV
    raw = (staff_row.get("assigned_service_ids") or "").strip()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def _working_days(staff_row):
    """Set of weekday ints (Mon=0..Sun=6) the staff works. Empty = all days."""
    raw = (staff_row.get("working_days") or "").strip()
    if not raw:
        return set(range(7))
    days = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            d = int(part)
            if 0 <= d <= 6:
                days.add(d)
    return days or set(range(7))


def _staff_hours(staff_row, biz_open_m, biz_close_m):
    """Effective (open, close) minutes for staff, clamped to business hours."""
    ws = time_to_minutes(staff_row.get("work_start"))
    we = time_to_minutes(staff_row.get("work_end"))
    open_m = max(biz_open_m, ws) if ws is not None else biz_open_m
    close_m = min(biz_close_m, we) if we is not None else biz_close_m
    return open_m, close_m


def _is_on_leave(db, user_id, staff_id, day):
    rows = db.select(LEAVES_TABLE, {"user_id": user_id, "staff_id": staff_id}) or []
    target = day.isoformat()
    for r in rows:
        start = r.get("start_date")
        end = r.get("end_date")
        start = start.isoformat() if hasattr(start, "isoformat") else str(start)
        end = end.isoformat() if hasattr(end, "isoformat") else str(end)
        if start <= target <= end:
            return True, (r.get("leave_type") or "leave")
    return False, ""


def _staff_bookings_on_date(db, user_id, day, staff_id, exclude_booking_id=None):
    db.cursor.execute(
        f"""
        SELECT id, start_time, end_time, status
        FROM {BOOKINGS_TABLE}
        WHERE user_id=%s AND booking_date=%s AND staff_id=%s
          AND status != 'cancelled'
        ORDER BY start_time ASC
        """,
        [user_id, day.isoformat(), staff_id],
    )
    rows = db.cursor.fetchall() or []
    if exclude_booking_id:
        rows = [r for r in rows if int(r.get("id") or 0) != int(exclude_booking_id)]
    return rows


def _staff_booked_minutes(bookings):
    total = 0
    for b in bookings:
        s = time_to_minutes(b.get("start_time"))
        e = time_to_minutes(b.get("end_time"))
        if s is not None and e is not None and e > s:
            total += (e - s)
    return total


def capable_staff(db, user_id, service_id, active_only=True):
    """All staff who can perform service_id (regardless of time)."""
    try:
        sid = int(service_id)
    except (TypeError, ValueError):
        return []
    result = []
    for row in db.select(STAFF_TABLE, {"user_id": user_id}) or []:
        status = (row.get("status") or "active").lower()
        if active_only and status in BLOCKED_STAFF_STATUS:
            continue
        if active_only and not row.get("is_active", 1) and status != "active":
            continue
        if sid in _staff_service_ids(db, user_id, row):
            result.append(row)
    return result


# --------------------------------------------------------------------------- #
# Core: is a specific staff free at a start time?                              #
# --------------------------------------------------------------------------- #


def staff_free_at(db, user_id, staff_row, day, start_m, duration, *,
                  biz_open_m, biz_close_m, break_start, break_end,
                  exclude_booking_id=None):
    """Return (free: bool, reason: str)."""
    staff_id = staff_row["id"]
    end_m = start_m + duration

    status = (staff_row.get("status") or "active").lower()
    if status in BLOCKED_STAFF_STATUS:
        return False, f"staff {status}"

    if day.weekday() not in _working_days(staff_row):
        return False, "day off"

    on_leave, ltype = _is_on_leave(db, user_id, staff_id, day)
    if on_leave:
        return False, ltype

    open_m, close_m = _staff_hours(staff_row, biz_open_m, biz_close_m)
    if start_m < open_m or end_m > close_m:
        return False, "outside working hours"

    # Business break
    if break_start is not None and break_end is not None and _overlaps(start_m, end_m, break_start, break_end):
        return False, "break time"
    # Staff break
    sbs = time_to_minutes(staff_row.get("break_start"))
    sbe = time_to_minutes(staff_row.get("break_end"))
    if sbs is not None and sbe is not None and _overlaps(start_m, end_m, sbs, sbe):
        return False, "staff break"

    bookings = _staff_bookings_on_date(db, user_id, day, staff_id, exclude_booking_id)

    # Max hours per day
    max_hours = int(staff_row.get("max_hours_per_day") or 0)
    if max_hours > 0 and (_staff_booked_minutes(bookings) + duration) > max_hours * 60:
        return False, "max daily hours reached"

    # Overlap capacity (max_bookings_per_slot concurrent)
    capacity = max(1, int(staff_row.get("max_bookings_per_slot") or 1))
    overlapping = 0
    for b in bookings:
        s = time_to_minutes(b.get("start_time"))
        e = time_to_minutes(b.get("end_time"))
        if s is not None and e is not None and _overlaps(start_m, end_m, s, e):
            overlapping += 1
    if overlapping >= capacity:
        return False, "already booked"

    return True, ""


# --------------------------------------------------------------------------- #
# Availability across all capable staff                                        #
# --------------------------------------------------------------------------- #


def available_staff_for_slot(db, user_id, service_id, day, start_time,
                             duration=None, exclude_booking_id=None):
    """List of staff dicts free for service at day/start_time."""
    if not isinstance(day, date):
        resolved = resolve_booking_date(day, db, user_id)
        day = resolved.get("date_obj")
    start_m = time_to_minutes(parse_time(start_time) or start_time)
    if not day or start_m is None:
        return []

    if duration is None:
        _svc, duration = resolve_service_duration(db, user_id, service_id)
    duration = int(duration or DEFAULT_SLOT_STEP_MINUTES)

    holiday, _ = _is_holiday(db, user_id, day)
    if holiday:
        return []
    hours = _day_hours(db, user_id, day)
    if not hours or hours.get("is_closed"):
        return []
    biz_open_m = time_to_minutes(hours.get("open_time"))
    biz_close_m = time_to_minutes(hours.get("close_time"))
    if biz_open_m is None or biz_close_m is None:
        return []
    break_start = time_to_minutes(hours.get("break_start"))
    break_end = time_to_minutes(hours.get("break_end"))

    free = []
    for staff in capable_staff(db, user_id, service_id):
        ok, _reason = staff_free_at(
            db, user_id, staff, day, start_m, duration,
            biz_open_m=biz_open_m, biz_close_m=biz_close_m,
            break_start=break_start, break_end=break_end,
            exclude_booking_id=exclude_booking_id,
        )
        if ok:
            free.append(staff)
    return free


def auto_assign_staff(db, user_id, service_id, day, start_time,
                      duration=None, exclude_booking_id=None):
    """Pick the least-busy available staff for this slot. Returns row or None."""
    if not isinstance(day, date):
        resolved = resolve_booking_date(day, db, user_id)
        day = resolved.get("date_obj")
    candidates = available_staff_for_slot(
        db, user_id, service_id, day, start_time, duration, exclude_booking_id
    )
    if not candidates:
        return None

    def load_today(staff):
        return len(_staff_bookings_on_date(db, user_id, day, staff["id"], exclude_booking_id))

    candidates.sort(key=lambda s: (load_today(s), int(s["id"])))
    return candidates[0]


def compute_staff_slots(db, user_id, staff_row, day, duration,
                        slot_step=DEFAULT_SLOT_STEP_MINUTES, skip_past_today=True):
    """Free {from,to} slots for ONE staff member on a day."""
    holiday, _ = _is_holiday(db, user_id, day)
    if holiday:
        return []
    hours = _day_hours(db, user_id, day)
    if not hours or hours.get("is_closed"):
        return []
    biz_open_m = time_to_minutes(hours.get("open_time"))
    biz_close_m = time_to_minutes(hours.get("close_time"))
    if biz_open_m is None or biz_close_m is None:
        return []
    break_start = time_to_minutes(hours.get("break_start"))
    break_end = time_to_minutes(hours.get("break_end"))

    duration = max(1, int(duration or DEFAULT_SLOT_STEP_MINUTES))
    step = max(1, int(slot_step or DEFAULT_SLOT_STEP_MINUTES))
    open_m, close_m = _staff_hours(staff_row, biz_open_m, biz_close_m)

    now_minutes = None
    if skip_past_today and day == today_local(db, user_id):
        now = now_local(db, user_id)
        now_minutes = now.hour * 60 + now.minute

    slots = []
    cursor = open_m
    last_start = close_m - duration
    while cursor <= last_start:
        if now_minutes is not None and cursor < now_minutes:
            cursor += step
            continue
        ok, _reason = staff_free_at(
            db, user_id, staff_row, day, cursor, duration,
            biz_open_m=biz_open_m, biz_close_m=biz_close_m,
            break_start=break_start, break_end=break_end,
        )
        if ok:
            slots.append({"from": minutes_to_time(cursor), "to": minutes_to_time(cursor + duration)})
        cursor += step
    return slots


def available_slots_multi(db, user_id, service_id, day, slot_step=DEFAULT_SLOT_STEP_MINUTES):
    """Union of slots where AT LEAST ONE capable staff is free, with counts.

    Returns list of {"from","to","available_staff": N, "staff_ids": [...]}.
    """
    _svc, duration = resolve_service_duration(db, user_id, service_id)
    duration = int(duration or DEFAULT_SLOT_STEP_MINUTES)

    hours = _day_hours(db, user_id, day)
    if not hours or hours.get("is_closed"):
        return [], duration
    biz_open_m = time_to_minutes(hours.get("open_time"))
    biz_close_m = time_to_minutes(hours.get("close_time"))
    holiday, _ = _is_holiday(db, user_id, day)
    if holiday or biz_open_m is None or biz_close_m is None:
        return [], duration
    break_start = time_to_minutes(hours.get("break_start"))
    break_end = time_to_minutes(hours.get("break_end"))

    staff_list = capable_staff(db, user_id, service_id)
    if not staff_list:
        return [], duration

    step = max(1, int(slot_step or DEFAULT_SLOT_STEP_MINUTES))
    now_minutes = None
    if day == today_local(db, user_id):
        now = now_local(db, user_id)
        now_minutes = now.hour * 60 + now.minute

    slots = []
    cursor = biz_open_m
    last_start = biz_close_m - duration
    while cursor <= last_start:
        if now_minutes is not None and cursor < now_minutes:
            cursor += step
            continue
        free_ids = []
        for staff in staff_list:
            ok, _reason = staff_free_at(
                db, user_id, staff, day, cursor, duration,
                biz_open_m=biz_open_m, biz_close_m=biz_close_m,
                break_start=break_start, break_end=break_end,
            )
            if ok:
                free_ids.append(staff["id"])
        if free_ids:
            slots.append({
                "from": minutes_to_time(cursor),
                "to": minutes_to_time(cursor + duration),
                "available_staff": len(free_ids),
                "staff_ids": free_ids,
            })
        cursor += step
    return slots, duration
