"""Service provider bookings — week calendar + list APIs.

This is Flask (not Laravel). Realtime updates on the frontend use polling;
there is no Reverb/Echo in this stack.
"""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, jsonify, request

from db import Database
from gemini_cache import refresh_cache_after_instruction_change
from services_settings import HANDLER_NAME, _is_services_user

bookings_bp = Blueprint("bookings", __name__)

TABLE = "services_bookings"
STATUSES = ("pending", "confirmed", "completed", "cancelled")


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


def _parse_date(value):
    raw = (value or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _parse_time(value):
    raw = (value or "").strip()
    if re.match(r"^\d{2}:\d{2}$", raw):
        return f"{raw}:00"
    if re.match(r"^\d{2}:\d{2}:\d{2}$", raw):
        return raw
    return None


def _hhmm_to_min(value):
    raw = (value or "").strip()
    m = re.match(r"^(\d{2}):(\d{2})", raw)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _week_bounds(anchor=None):
    """Return Monday..Sunday dates for the week containing anchor (default today)."""
    if anchor is None:
        anchor = date.today()
    monday = anchor - timedelta(days=anchor.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _empty_day_counts():
    return {
        "total": 0,
        "confirmed": 0,
        "pending": 0,
        "completed": 0,
        "cancelled": 0,
        "revenue": 0.0,
    }


def _public_booking(row, service_name=None):
    price = row.get("price")
    if isinstance(price, Decimal):
        price = float(price)
    elif price is None:
        price = 0.0
    else:
        price = float(price)

    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "service_id": row.get("service_id"),
        "staff_id": row.get("staff_id"),
        "staff_name": row.get("staff_name") or "",
        "customer_id": row.get("customer_id"),
        "service_name": service_name if service_name is not None else (row.get("service_name") or ""),
        "customer_name": row.get("customer_name") or "",
        "phone": row.get("phone") or "",
        "booking_date": _fmt_date(row.get("booking_date")),
        "start_time": _fmt_time(row.get("start_time")),
        "end_time": _fmt_time(row.get("end_time")),
        "status": row.get("status") or "pending",
        "notes": row.get("notes") or "",
        "price": price,
        "created_at": _fmt_date(row.get("created_at")),
    }


def _validate_and_resolve_staff(db, user_id, service_id, day, start_time,
                                duration, staff_id, exclude_booking_id=None):
    """Return (staff_id, error). Auto-assigns least-busy staff when none given.

    Per-staff availability: allows concurrent bookings for different staff.
    """
    from staff_scheduling import (
        available_staff_for_slot,
        auto_assign_staff,
        capable_staff,
    )

    if not service_id:
        # No service → cannot reason about skills; keep provided staff as-is.
        return staff_id, None

    if staff_id:
        try:
            sid = int(staff_id)
        except (TypeError, ValueError):
            return None, "Invalid staff_id"
        free = available_staff_for_slot(
            db, user_id, service_id, day, start_time, duration,
            exclude_booking_id=exclude_booking_id,
        )
        if any(int(s["id"]) == sid for s in free):
            return sid, None
        # Distinguish "not capable" from "busy"
        capable_ids = {int(s["id"]) for s in capable_staff(db, user_id, service_id)}
        if sid not in capable_ids:
            return None, "Selected staff cannot perform this service"
        return None, "Selected staff is not available at this time"

    # Auto-assign least busy capable + free staff
    chosen = auto_assign_staff(
        db, user_id, service_id, day, start_time, duration,
        exclude_booking_id=exclude_booking_id,
    )
    if not chosen:
        return None, "No staff available for this service at the selected time"
    return int(chosen["id"]), None


def ensure_schema():
    db = Database()
    try:
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                service_id INT DEFAULT NULL,
                customer_name VARCHAR(255) NOT NULL,
                phone VARCHAR(50) DEFAULT NULL,
                booking_date DATE NOT NULL,
                start_time TIME NOT NULL,
                end_time TIME NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                notes TEXT DEFAULT NULL,
                price DECIMAL(10,2) NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_user_date (user_id, booking_date),
                INDEX idx_user_status (user_id, status)
            )
            """
        )
    finally:
        db.close()


def _fetch_bookings_in_range(db, user_id, start_date, end_date):
    """Single optimized query for a date range with service name."""
    db.cursor.execute(
        f"""
        SELECT b.*, c.name AS service_name, s.name AS staff_name
        FROM {TABLE} b
        LEFT JOIN services_catalog c ON c.id = b.service_id
        LEFT JOIN services_staff s ON s.id = b.staff_id
        WHERE b.user_id = %s
          AND b.booking_date >= %s
          AND b.booking_date <= %s
        ORDER BY b.booking_date ASC, b.start_time ASC
        """,
        [user_id, start_date.isoformat(), end_date.isoformat()],
    )
    return db.cursor.fetchall()


def _group_days(start_date, end_date, rows):
    """Group bookings into day cards with status counts (one pass)."""
    days = {}
    cursor = start_date
    while cursor <= end_date:
        key = cursor.isoformat()
        days[key] = {
            "date": key,
            "weekday": cursor.weekday(),  # Mon=0
            "day_name": cursor.strftime("%A"),
            "day_label": cursor.strftime("%d %B"),
            "is_today": cursor == date.today(),
            "counts": _empty_day_counts(),
            "bookings": [],
        }
        cursor += timedelta(days=1)

    for row in rows:
        key = _fmt_date(row.get("booking_date"))
        if key not in days:
            continue
        booking = _public_booking(row)
        days[key]["bookings"].append(booking)
        counts = days[key]["counts"]
        counts["total"] += 1
        status = booking["status"]
        if status in counts:
            counts[status] += 1
        if status in ("confirmed", "completed"):
            counts["revenue"] += booking["price"]

    return [days[d] for d in sorted(days.keys())]


# --------------------------------------------------------------------------- #
# Week calendar + summary                                                     #
# --------------------------------------------------------------------------- #


@bookings_bp.route("/users/<int:user_id>/bookings/week", methods=["GET"])
def week_calendar(user_id):
    """One query for the whole week, grouped by day for the dashboard cards."""
    week_param = request.args.get("week")  # any date inside the desired week
    anchor = _parse_date(week_param) if week_param else date.today()
    if week_param and not anchor:
        return jsonify({"status": False, "message": "Invalid week date"}), 400

    monday, sunday = _week_bounds(anchor)

    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        rows = _fetch_bookings_in_range(db, user_id, monday, sunday)
        days = _group_days(monday, sunday, rows)

        today = date.today()
        week_start_this, week_end_this = _week_bounds(today)

        # Summary cards — also from the same week rows + small extra queries
        today_rows = [r for r in rows if _fmt_date(r.get("booking_date")) == today.isoformat()]
        # If requested week is not current week, still compute "today" / "this week"
        # from a dedicated range query so summary stays accurate.
        if monday != week_start_this:
            current_week_rows = _fetch_bookings_in_range(db, user_id, week_start_this, week_end_this)
            today_rows = [
                r for r in current_week_rows if _fmt_date(r.get("booking_date")) == today.isoformat()
            ]
            summary_source = current_week_rows
        else:
            summary_source = rows
            today_rows = [r for r in rows if _fmt_date(r.get("booking_date")) == today.isoformat()]

        def count_status(source, status=None):
            if status is None:
                return len(source)
            return sum(1 for r in source if (r.get("status") or "") == status)

        def revenue(source):
            total = 0.0
            for r in source:
                if (r.get("status") or "") not in ("confirmed", "completed"):
                    continue
                p = r.get("price") or 0
                total += float(p)
            return round(total, 2)

        summary = {
            "today_bookings": count_status(today_rows),
            "week_bookings": count_status(summary_source),
            "pending": count_status(summary_source, "pending"),
            "completed": count_status(summary_source, "completed"),
            "cancelled": count_status(summary_source, "cancelled"),
            "today_revenue": revenue(today_rows),
        }
    finally:
        db.close()

    return jsonify(
        {
            "status": True,
            "week_start": monday.isoformat(),
            "week_end": sunday.isoformat(),
            "days": days,
            "summary": summary,
        }
    )


# --------------------------------------------------------------------------- #
# List (day filter / search / status / pagination)                            #
# --------------------------------------------------------------------------- #


@bookings_bp.route("/users/<int:user_id>/bookings", methods=["GET"])
def list_bookings(user_id):
    booking_date = request.args.get("date")
    status = (request.args.get("status") or "").strip().lower()
    search = (request.args.get("search") or "").strip()
    staff_id = (request.args.get("staff_id") or "").strip()
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 10)), 1), 50)

    if booking_date and not _parse_date(booking_date):
        return jsonify({"status": False, "message": "Invalid date"}), 400
    if status and status not in STATUSES:
        return jsonify({"status": False, "message": "Invalid status"}), 400

    where = ["b.user_id=%s"]
    values = [user_id]

    if booking_date:
        where.append("b.booking_date=%s")
        values.append(booking_date)
    if status:
        where.append("b.status=%s")
        values.append(status)
    if staff_id.isdigit():
        where.append("b.staff_id=%s")
        values.append(int(staff_id))
    if search:
        where.append("(b.customer_name LIKE %s OR b.phone LIKE %s OR c.name LIKE %s)")
        like = f"%{search}%"
        values.extend([like, like, like])

    where_sql = " AND ".join(where)
    offset = (page - 1) * per_page

    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        db.cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM {TABLE} b
            LEFT JOIN services_catalog c ON c.id = b.service_id
            WHERE {where_sql}
            """,
            values,
        )
        total = db.cursor.fetchone()["total"]

        db.cursor.execute(
            f"""
            SELECT b.*, c.name AS service_name, s.name AS staff_name
            FROM {TABLE} b
            LEFT JOIN services_catalog c ON c.id = b.service_id
            LEFT JOIN services_staff s ON s.id = b.staff_id
            WHERE {where_sql}
            ORDER BY b.booking_date DESC, b.start_time ASC
            LIMIT %s OFFSET %s
            """,
            values + [per_page, offset],
        )
        rows = db.cursor.fetchall()
    finally:
        db.close()

    return jsonify(
        {
            "status": True,
            "bookings": [_public_booking(r) for r in rows],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": max((total + per_page - 1) // per_page, 1),
            },
        }
    )


# --------------------------------------------------------------------------- #
# Create / Update / Delete                                                    #
# --------------------------------------------------------------------------- #


@bookings_bp.route("/users/<int:user_id>/bookings", methods=["POST"])
def create_booking(user_id):
    data = request.json or {}
    customer_name = (data.get("customer_name") or "").strip()
    phone = (data.get("phone") or "").strip()
    booking_date = _parse_date(data.get("booking_date") or data.get("date"))
    start_time = _parse_time(data.get("start_time"))
    end_time = _parse_time(data.get("end_time"))
    status = (data.get("status") or "pending").strip().lower()
    notes = (data.get("notes") or "").strip()
    service_id = data.get("service_id") or None
    staff_id = data.get("staff_id") or None
    customer_id = data.get("customer_id") or None
    price = float(data.get("price") or 0)

    if not customer_name:
        return jsonify({"status": False, "message": "Customer name is required"}), 400
    if not booking_date or not start_time or not end_time:
        return jsonify({"status": False, "message": "Date, start_time and end_time are required"}), 400
    if status not in STATUSES:
        return jsonify({"status": False, "message": "Invalid status"}), 400

    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        duration = None
        if service_id:
            service = db.row("services_catalog", {"id": service_id, "user_id": user_id})
            if not service:
                return jsonify({"status": False, "message": "Service not found"}), 404
            if not price and service.get("price") is not None:
                price = float(service.get("price") or 0)
            duration = int(service.get("duration_minutes") or 0) or None

        if duration is None:
            sm = _hhmm_to_min(start_time)
            em = _hhmm_to_min(end_time)
            if sm is not None and em is not None and em > sm:
                duration = em - sm

        # Per-staff availability: validate/auto-assign unless cancelled
        if status != "cancelled":
            resolved_staff, staff_err = _validate_and_resolve_staff(
                db, user_id, service_id, booking_date, start_time, duration, staff_id,
            )
            if staff_err:
                return jsonify({"status": False, "message": staff_err}), 409
            staff_id = resolved_staff

        payload = {
            "user_id": user_id,
            "service_id": service_id,
            "customer_name": customer_name,
            "phone": phone or None,
            "booking_date": booking_date.isoformat(),
            "start_time": start_time,
            "end_time": end_time,
            "status": status,
            "notes": notes or None,
            "price": price,
            "staff_id": staff_id,
            "customer_id": customer_id,
            "duration_minutes": duration,
        }
        try:
            new_id = db.insert(TABLE, payload)
        except Exception:
            payload.pop("staff_id", None)
            payload.pop("customer_id", None)
            payload.pop("duration_minutes", None)
            new_id = db.insert(TABLE, payload)
        refresh_cache_after_instruction_change(db, user_id)

        db.cursor.execute(
            f"""
            SELECT b.*, c.name AS service_name, s.name AS staff_name
            FROM {TABLE} b
            LEFT JOIN services_catalog c ON c.id = b.service_id
            LEFT JOIN services_staff s ON s.id = b.staff_id
            WHERE b.id=%s
            """,
            [new_id],
        )
        row = db.cursor.fetchone()
    finally:
        db.close()

    return jsonify({"status": True, "message": "Booking created", "booking": _public_booking(row)})


@bookings_bp.route("/users/<int:user_id>/bookings/<int:booking_id>", methods=["PUT"])
def update_booking(user_id, booking_id):
    data = request.json or {}

    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        existing = db.row(TABLE, {"id": booking_id, "user_id": user_id})
        if not existing:
            return jsonify({"status": False, "message": "Booking not found"}), 404

        update_data = {}

        if "customer_name" in data:
            name = (data.get("customer_name") or "").strip()
            if not name:
                return jsonify({"status": False, "message": "Customer name cannot be empty"}), 400
            update_data["customer_name"] = name

        if "phone" in data:
            update_data["phone"] = (data.get("phone") or "").strip() or None

        if "booking_date" in data or "date" in data:
            d = _parse_date(data.get("booking_date") or data.get("date"))
            if not d:
                return jsonify({"status": False, "message": "Invalid date"}), 400
            update_data["booking_date"] = d.isoformat()

        if "start_time" in data:
            t = _parse_time(data.get("start_time"))
            if not t:
                return jsonify({"status": False, "message": "Invalid start_time"}), 400
            update_data["start_time"] = t

        if "end_time" in data:
            t = _parse_time(data.get("end_time"))
            if not t:
                return jsonify({"status": False, "message": "Invalid end_time"}), 400
            update_data["end_time"] = t

        if "status" in data:
            status = (data.get("status") or "").strip().lower()
            if status not in STATUSES:
                return jsonify({"status": False, "message": "Invalid status"}), 400
            update_data["status"] = status

        if "notes" in data:
            update_data["notes"] = (data.get("notes") or "").strip() or None

        if "service_id" in data:
            service_id = data.get("service_id") or None
            if service_id:
                service = db.row("services_catalog", {"id": service_id, "user_id": user_id})
                if not service:
                    return jsonify({"status": False, "message": "Service not found"}), 404
            update_data["service_id"] = service_id

        if "staff_id" in data:
            update_data["staff_id"] = data.get("staff_id") or None

        if "customer_id" in data:
            update_data["customer_id"] = data.get("customer_id") or None

        if "price" in data:
            update_data["price"] = float(data.get("price") or 0)

        if not update_data:
            return jsonify({"status": False, "message": "Nothing to update"}), 400

        # Re-validate staff availability when time/date/service/staff changes
        merged = {**existing, **update_data}
        final_status = (merged.get("status") or "pending")
        touches_schedule = any(
            k in update_data for k in ("staff_id", "start_time", "end_time", "booking_date", "service_id", "status")
        )
        if touches_schedule and final_status != "cancelled":
            b_date = _parse_date(_fmt_date(merged.get("booking_date")))
            s_time = _fmt_time(merged.get("start_time"))
            svc_id = merged.get("service_id") or None
            duration = None
            if svc_id:
                svc = db.row("services_catalog", {"id": svc_id, "user_id": user_id})
                duration = int((svc or {}).get("duration_minutes") or 0) or None
            if duration is None:
                sm = _hhmm_to_min(_fmt_time(merged.get("start_time")))
                em = _hhmm_to_min(_fmt_time(merged.get("end_time")))
                if sm is not None and em is not None and em > sm:
                    duration = em - sm
            if b_date and s_time:
                resolved_staff, staff_err = _validate_and_resolve_staff(
                    db, user_id, svc_id, b_date, s_time, duration,
                    merged.get("staff_id"), exclude_booking_id=booking_id,
                )
                if staff_err:
                    return jsonify({"status": False, "message": staff_err}), 409
                update_data["staff_id"] = resolved_staff
                if duration:
                    update_data["duration_minutes"] = duration

        try:
            db.update(TABLE, update_data, {"id": booking_id, "user_id": user_id})
        except Exception:
            update_data.pop("staff_id", None)
            update_data.pop("customer_id", None)
            update_data.pop("duration_minutes", None)
            db.update(TABLE, update_data, {"id": booking_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)

        db.cursor.execute(
            f"""
            SELECT b.*, c.name AS service_name, s.name AS staff_name
            FROM {TABLE} b
            LEFT JOIN services_staff s ON s.id = b.staff_id
            LEFT JOIN services_catalog c ON c.id = b.service_id
            WHERE b.id=%s
            """,
            [booking_id],
        )
        row = db.cursor.fetchone()
    finally:
        db.close()

    return jsonify({"status": True, "message": "Booking updated", "booking": _public_booking(row)})


@bookings_bp.route("/users/<int:user_id>/bookings/<int:booking_id>", methods=["DELETE"])
def delete_booking(user_id, booking_id):
    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        existing = db.row(TABLE, {"id": booking_id, "user_id": user_id})
        if not existing:
            return jsonify({"status": False, "message": "Booking not found"}), 404

        db.delete(TABLE, {"id": booking_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()

    return jsonify({"status": True, "message": "Booking deleted"})


@bookings_bp.route("/users/<int:user_id>/bookings/available-staff", methods=["GET"])
def available_staff(user_id):
    """Staff free for a service at a date/time (for booking UI + AI)."""
    service_id = request.args.get("service_id")
    date_str = request.args.get("date")
    start_time = request.args.get("start_time") or request.args.get("time")

    if not service_id or not service_id.isdigit():
        return jsonify({"status": False, "message": "service_id is required"}), 400

    day = _parse_date(date_str)
    if not day:
        return jsonify({"status": False, "message": "Valid date (YYYY-MM-DD) is required"}), 400

    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        from staff_scheduling import (
            available_slots_multi,
            available_staff_for_slot,
            capable_staff,
        )
        from services_crud import _serialize_staff, _staff_service_ids_from_pivot

        if start_time:
            rows = available_staff_for_slot(db, user_id, int(service_id), day, start_time)
            staff = [
                _serialize_staff(r, service_ids=_staff_service_ids_from_pivot(db, user_id, r["id"]))
                for r in rows
            ]
            return jsonify({"status": True, "staff": staff, "count": len(staff)})

        # No time given → return day slot map with capacity + all capable staff
        slots, duration = available_slots_multi(db, user_id, int(service_id), day)
        caps = capable_staff(db, user_id, int(service_id))
        staff = [
            _serialize_staff(r, service_ids=_staff_service_ids_from_pivot(db, user_id, r["id"]))
            for r in caps
        ]
        return jsonify({
            "status": True,
            "slots": slots,
            "duration_minutes": duration,
            "capable_staff": staff,
            "count": len(staff),
        })
    finally:
        db.close()


@bookings_bp.route("/users/<int:user_id>/bookings/staff-timeline", methods=["GET"])
def staff_timeline(user_id):
    """Bookings grouped per staff for a given day (Staff Timeline view)."""
    date_str = request.args.get("date")
    day = _parse_date(date_str) if date_str else date.today()
    if date_str and not day:
        return jsonify({"status": False, "message": "Invalid date"}), 400

    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        staff_rows = db.select("services_staff", {"user_id": user_id}) or []
        db.cursor.execute(
            f"""
            SELECT b.*, c.name AS service_name, s.name AS staff_name
            FROM {TABLE} b
            LEFT JOIN services_catalog c ON c.id = b.service_id
            LEFT JOIN services_staff s ON s.id = b.staff_id
            WHERE b.user_id=%s AND b.booking_date=%s AND b.status != 'cancelled'
            ORDER BY b.start_time ASC
            """,
            [user_id, day.isoformat()],
        )
        rows = db.cursor.fetchall() or []

        by_staff = {}
        for b in rows:
            by_staff.setdefault(b.get("staff_id") or 0, []).append(_public_booking(b))

        timeline = []
        for s in staff_rows:
            timeline.append({
                "staff_id": s["id"],
                "staff_name": s.get("name") or "",
                "role": s.get("role") or "",
                "status": s.get("status") or "active",
                "bookings": by_staff.get(s["id"], []),
            })
        if by_staff.get(0):
            timeline.append({
                "staff_id": None,
                "staff_name": "Unassigned",
                "role": "",
                "status": "",
                "bookings": by_staff.get(0, []),
            })

        return jsonify({"status": True, "date": day.isoformat(), "timeline": timeline})
    finally:
        db.close()


@bookings_bp.route("/users/<int:user_id>/bookings/staff-stats", methods=["GET"])
def staff_stats(user_id):
    """Staff-centric dashboard + reports metrics for a date range.

    Query: ?start=YYYY-MM-DD&end=YYYY-MM-DD (default: current month).
    """
    today = date.today()
    start = _parse_date(request.args.get("start")) or today.replace(day=1)
    end = _parse_date(request.args.get("end")) or today

    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        staff_rows = db.select("services_staff", {"user_id": user_id}) or []
        staff_by_id = {s["id"]: s for s in staff_rows}

        rows = _fetch_bookings_in_range(db, user_id, start, end)

        # Leaves overlapping today
        leaves = db.select("services_staff_leaves", {"user_id": user_id}) or []
        on_leave_ids = set()
        tstr = today.isoformat()
        for l in leaves:
            sd = l.get("start_date")
            ed = l.get("end_date")
            sd = sd.isoformat() if hasattr(sd, "isoformat") else str(sd)
            ed = ed.isoformat() if hasattr(ed, "isoformat") else str(ed)
            if sd <= tstr <= ed:
                on_leave_ids.add(l.get("staff_id"))

        # Per-staff aggregation
        per_staff = {}
        for s in staff_rows:
            per_staff[s["id"]] = {
                "staff_id": s["id"],
                "staff_name": s.get("name") or "",
                "role": s.get("role") or "",
                "status": s.get("status") or "active",
                "bookings": 0,
                "completed": 0,
                "revenue": 0.0,
                "booked_minutes": 0,
                "today_bookings": 0,
            }

        service_perf = {}
        today_appointments = 0
        for r in rows:
            status = (r.get("status") or "")
            if status == "cancelled":
                continue
            sid = r.get("staff_id")
            is_today = _fmt_date(r.get("booking_date")) == tstr
            if is_today:
                today_appointments += 1
            if sid in per_staff:
                agg = per_staff[sid]
                agg["bookings"] += 1
                if is_today:
                    agg["today_bookings"] += 1
                if status == "completed":
                    agg["completed"] += 1
                if status in ("confirmed", "completed"):
                    agg["revenue"] += float(r.get("price") or 0)
                sm = _hhmm_to_min(_fmt_time(r.get("start_time")))
                em = _hhmm_to_min(_fmt_time(r.get("end_time")))
                if sm is not None and em is not None and em > sm:
                    agg["booked_minutes"] += (em - sm)

            svc = r.get("service_name") or f"Service {r.get('service_id')}"
            sp = service_perf.setdefault(svc, {"service": svc, "bookings": 0, "revenue": 0.0})
            sp["bookings"] += 1
            if status in ("confirmed", "completed"):
                sp["revenue"] += float(r.get("price") or 0)

        # Staff utilization: booked minutes vs available capacity over range
        days_span = max(1, (end - start).days + 1)
        for sid, agg in per_staff.items():
            s = staff_by_id.get(sid, {})
            ws = _hhmm_to_min(_fmt_time(s.get("work_start"))) if s.get("work_start") else 9 * 60
            we = _hhmm_to_min(_fmt_time(s.get("work_end"))) if s.get("work_end") else 18 * 60
            daily_capacity = max(1, (we or 1080) - (ws or 540))
            wd = (s.get("working_days") or "")
            work_days_count = len([x for x in wd.split(",") if x.strip().isdigit()]) or 5
            # approx: proportion of span days that are working days
            capacity = daily_capacity * max(1, round(days_span * work_days_count / 7))
            agg["utilization"] = round(min(100.0, (agg["booked_minutes"] / capacity) * 100), 1) if capacity else 0.0

        per_staff_list = list(per_staff.values())
        booked = [p for p in per_staff_list if p["bookings"] > 0]

        top_performing = sorted(per_staff_list, key=lambda p: (-p["revenue"], -p["completed"]))[:5]
        most_booked = sorted(per_staff_list, key=lambda p: -p["bookings"])[:5]
        least_busy = sorted(booked, key=lambda p: p["bookings"])[:5] if booked else []

        def status_of(s):
            st = (s.get("status") or "active").lower()
            return st

        available_count = sum(1 for s in staff_rows if status_of(s) in ("active", "available") and s["id"] not in on_leave_ids)
        busy_count = sum(1 for s in staff_rows if status_of(s) == "busy")
        leave_count = len([sid for sid in on_leave_ids if sid in staff_by_id]) + sum(
            1 for s in staff_rows if status_of(s) in ("leave", "on_leave") and s["id"] not in on_leave_ids
        )

        return jsonify({
            "status": True,
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "summary": {
                "today_appointments": today_appointments,
                "total_staff": len(staff_rows),
                "available_staff": available_count,
                "busy_staff": busy_count,
                "on_leave_staff": leave_count,
            },
            "appointments_per_staff": sorted(per_staff_list, key=lambda p: -p["bookings"]),
            "revenue_per_staff": sorted(per_staff_list, key=lambda p: -p["revenue"]),
            "top_performing_staff": top_performing,
            "most_booked_staff": most_booked,
            "least_busy_staff": least_busy,
            "service_performance": sorted(service_perf.values(), key=lambda x: -x["bookings"]),
            "staff_utilization": sorted(per_staff_list, key=lambda p: -p.get("utilization", 0)),
        })
    finally:
        db.close()


def upcoming_bookings_for_cache(db, user_id, limit=20):
    """Helper for Services.cache_payload — upcoming non-cancelled bookings."""
    today = date.today().isoformat()
    db.cursor.execute(
        f"""
        SELECT b.*, c.name AS service_name
        FROM {TABLE} b
        LEFT JOIN services_catalog c ON c.id = b.service_id
        WHERE b.user_id=%s
          AND b.booking_date >= %s
          AND b.status != 'cancelled'
        ORDER BY b.booking_date ASC, b.start_time ASC
        LIMIT %s
        """,
        [user_id, today, limit],
    )
    return [_public_booking(r) for r in db.cursor.fetchall()]
