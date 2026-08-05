"""Service chatbot tools — slim production set.

Gemini may ONLY call these tools. Availability is ALWAYS computed by
availability_engine.get_available_slots — never by the model.

Isolated from Ecommerce / Job Posting.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal

from services_settings import SETTINGS_TABLE, CATALOG_TABLE

BOOKINGS_TABLE = "services_bookings"
STAFF_TABLE = "services_staff"
CUSTOMERS_TABLE = "services_customers"

TOOL_NAMES = (
    "get_business_info",
    "get_service_details",
    "quote_services",
    "get_staff",
    "resolve_date",
    "get_available_slots",
    "search_customer",
    "create_customer",
    "book_appointment",
    "cancel_booking",
    "reschedule_booking",
)


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


def _parse_date(value, db=None, user_id=None):
    """Resolve ISO or relative phrases via availability_engine (backend only)."""
    from availability_engine import resolve_booking_date

    resolved = resolve_booking_date(value, db, user_id)
    return resolved.get("date_obj"), resolved


def _parse_time(value):
    from availability_engine import parse_time

    return parse_time(value)


def _money(value):
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _currency(db, user_id):
    row = db.row(SETTINGS_TABLE, {"user_id": user_id}) or {}
    return (row.get("currency_code") or "USD").strip() or "USD"


def _public_service(row, currency_code=""):
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "category_id": row.get("category_id"),
        "duration_minutes": int(row.get("duration_minutes") or 0),
        "price": _money(row.get("price")),
        "currency_code": currency_code,
        "description": (row.get("description") or row.get("ai_context") or "").strip(),
        "status": row.get("status") or "active",
        "related_service_ids": (row.get("related_service_ids") or "").strip(),
    }


def _public_booking(row):
    """Structured booking payload — Gemini must use ONLY these fields."""
    if not row:
        return None
    date_str = _fmt_date(row.get("booking_date"))
    start = _fmt_time(row.get("start_time"))
    end = _fmt_time(row.get("end_time"))
    service = row.get("service_name") or ""
    return {
        "id": row["id"],
        "date": date_str,
        "from": start,
        "to": end,
        "service": service,
        "service_id": row.get("service_id"),
        "service_name": service,
        "customer_name": row.get("customer_name") or "",
        "phone": row.get("phone") or "",
        "booking_date": date_str,
        "start_time": start,
        "end_time": end,
        "status": row.get("status") or "pending",
        "notes": row.get("notes") or "",
        "price": _money(row.get("price")),
        "staff_id": row.get("staff_id"),
    }


def _fetch_booking(db, booking_id, user_id):
    db.cursor.execute(
        f"""
        SELECT b.*, c.name AS service_name
        FROM {BOOKINGS_TABLE} b
        LEFT JOIN {CATALOG_TABLE} c ON c.id = b.service_id
        WHERE b.id=%s AND b.user_id=%s
        """,
        [booking_id, user_id],
    )
    return db.cursor.fetchone()


def _find_service(db, user_id, service_id=None, name=""):
    if service_id:
        try:
            return db.row(CATALOG_TABLE, {"id": int(service_id), "user_id": user_id})
        except (TypeError, ValueError):
            return None
    name = (name or "").strip().lower()
    if not name:
        return None
    rows = db.select(CATALOG_TABLE, {"user_id": user_id}) or []
    for r in rows:
        if (r.get("status") or "active") == "inactive":
            continue
        svc = (r.get("name") or "").lower()
        if name == svc or name in svc or svc in name:
            return r
    name_tokens = set(re.findall(r"[a-z0-9]+", name))
    best, score = None, 0
    for r in rows:
        if (r.get("status") or "active") == "inactive":
            continue
        tokens = set(re.findall(r"[a-z0-9]+", (r.get("name") or "").lower()))
        hit = len(name_tokens & tokens)
        if hit > score:
            best, score = r, hit
    return best if score else None


def tool_get_business_info(db, user_id, args=None):
    s = db.row(SETTINGS_TABLE, {"user_id": user_id}) or {}
    payments = db.select("services_payment_methods", {"user_id": user_id}) or []
    policies = db.select("services_policies", {"user_id": user_id}) or []
    return {
        "business_name": (s.get("business_name") or "").strip(),
        "business_category": (s.get("business_category") or "").strip(),
        "about": (s.get("about") or "").strip(),
        "address": (s.get("address") or "").strip(),
        "city": (s.get("city") or "").strip(),
        "phone": (s.get("phone") or "").strip(),
        "email": (s.get("email") or "").strip(),
        "website": (s.get("website") or "").strip(),
        "maps_link": (s.get("maps_link") or "").strip(),
        "parking_info": (s.get("parking_info") or "").strip(),
        "booking_rules": (s.get("booking_rules") or "").strip(),
        "currency_code": (s.get("currency_code") or "USD").strip(),
        "payment_methods": [
            {"name": p.get("name") or "", "details": p.get("details") or ""}
            for p in payments
            if p.get("is_active", 1)
        ],
        "policies": [
            {
                "title": p.get("title") or "",
                "type": p.get("policy_type") or "",
                "content": p.get("content") or "",
            }
            for p in policies
            if p.get("is_active", 1)
        ],
    }


def tool_get_service_details(db, user_id, args=None):
    args = args or {}
    currency = _currency(db, user_id)
    row = _find_service(
        db,
        user_id,
        service_id=args.get("service_id"),
        name=args.get("name") or args.get("service_name") or "",
    )
    if not row:
        return {"found": False, "message": "Service not found"}
    service = _public_service(row, currency)
    related = []
    for part in re.split(r"[,;\s]+", service["related_service_ids"] or ""):
        if not part.isdigit():
            continue
        rel = db.row(CATALOG_TABLE, {"id": int(part), "user_id": user_id})
        if rel and (rel.get("status") or "active") != "inactive":
            related.append(_public_service(rel, currency))
    return {"found": True, "service": service, "related_services": related}


def tool_quote_services(db, user_id, args=None):
    """Quote one or more catalog services — never invents packages/combos.

    Args:
      names: ["Hair Cut", "Shaving"]  OR  service_ids: [1, 2]
      OR name / service_name / service_id for a single service
    """
    args = args or {}
    currency = _currency(db, user_id)

    names = args.get("names") or args.get("services") or args.get("service_names") or []
    ids = args.get("service_ids") or args.get("ids") or []
    if isinstance(names, str):
        names = [n.strip() for n in re.split(r"[,&+/]| and | aur ", names, flags=re.I) if n.strip()]
    if not isinstance(names, list):
        names = []
    if not isinstance(ids, list):
        ids = [ids] if ids else []

    if not names and not ids:
        single_name = args.get("name") or args.get("service_name") or ""
        single_id = args.get("service_id")
        if single_name:
            names = [single_name]
        elif single_id:
            ids = [single_id]

    found = []
    missing = []
    seen_ids = set()

    for sid in ids:
        row = _find_service(db, user_id, service_id=sid)
        if not row:
            missing.append(str(sid))
            continue
        if row["id"] in seen_ids:
            continue
        seen_ids.add(row["id"])
        found.append(_public_service(row, currency))

    for name in names:
        row = _find_service(db, user_id, name=str(name))
        if not row:
            missing.append(str(name))
            continue
        if row["id"] in seen_ids:
            continue
        seen_ids.add(row["id"])
        found.append(_public_service(row, currency))

    total_price = round(sum(s["price"] for s in found), 2)
    total_duration = sum(int(s["duration_minutes"] or 0) for s in found)

    return {
        "success": len(found) > 0,
        "count": len(found),
        "services": found,
        "total_price": total_price,
        "total_duration_minutes": total_duration,
        "currency_code": currency,
        "missing": missing,
        "message": (
            "Quoted individual services only — not a package. "
            "List each service with price and duration, then totals."
        ),
        # Explicit flag so the model does not treat this as a package
        "is_package": False,
    }


def tool_get_staff(db, user_id, args=None):
    rows = db.select(STAFF_TABLE, {"user_id": user_id}) or []
    pivot = db.select("staff_services", {"user_id": user_id}) or []
    by_staff = {}
    for p in pivot:
        by_staff.setdefault(int(p["staff_id"]), []).append(int(p["service_id"]))
    staff = []
    for r in rows:
        status = (r.get("status") or ("active" if r.get("is_active", 1) else "inactive")).lower()
        if status in ("inactive", "0", "false"):
            continue
        if not r.get("is_active", 1) and status == "inactive":
            continue
        sids = by_staff.get(int(r["id"]))
        if sids is None:
            raw = (r.get("assigned_service_ids") or "").strip()
            sids = [int(p) for p in raw.split(",") if p.strip().isdigit()]
        staff.append({
            "id": r["id"],
            "name": r.get("name") or "",
            "phone": r.get("phone") or "",
            "email": r.get("email") or "",
            "role": r.get("role") or "",
            "department": r.get("department") or "",
            "working_hours": r.get("working_hours") or "",
            "assigned_service_ids": r.get("assigned_service_ids") or "",
            "service_ids": sids,
            "skills": r.get("skills") or "",
            "status": status or "active",
        })
    return {"count": len(staff), "staff": staff}


def tool_resolve_date(db, user_id, args=None):
    """Backend-only relative date resolution. Gemini must NEVER guess dates."""
    from availability_engine import resolve_booking_date

    args = args or {}
    phrase = args.get("date") or args.get("phrase") or args.get("when") or ""
    resolved = resolve_booking_date(phrase, db, user_id)
    return {
        "success": bool(resolved.get("success")),
        "date": resolved.get("date"),
        "resolved_from": resolved.get("resolved_from"),
        "timezone": resolved.get("timezone"),
        "today": resolved.get("today"),
        "error": resolved.get("error"),
        "message": (
            f"Resolved to {resolved.get('date')}"
            if resolved.get("success")
            else (resolved.get("error") or "Could not resolve date")
        ),
    }


def tool_get_available_slots(db, user_id, args=None):
    """Backend Availability Engine — AI must never invent slots or dates."""
    from availability_engine import get_available_slots

    args = args or {}
    day = args.get("date") or args.get("booking_date") or args.get("when")
    service_id = args.get("service_id")
    if not day:
        return {
            "success": False,
            "available_slots": [],
            "error": "date is required (YYYY-MM-DD or today/tomorrow/kal/Monday)",
        }
    if not service_id:
        return {"success": False, "available_slots": [], "error": "service_id is required"}

    result = get_available_slots(db, user_id, day, service_id)

    # Per-staff availability overlay: a slot is only offered if at least one
    # capable staff member is free. Adds available_staff count + staff_ids.
    try:
        from staff_scheduling import available_slots_multi, capable_staff
        from availability_engine import parse_date

        day_obj = parse_date(result.get("date")) if result.get("date") else None
        if result.get("success") and day_obj and capable_staff(db, user_id, int(service_id)):
            multi, duration = available_slots_multi(db, user_id, int(service_id), day_obj)
            result["available_slots"] = [
                {"from": s["from"], "to": s["to"], "available_staff": s["available_staff"]}
                for s in multi
            ]
            result["staff_slot_map"] = multi
            result["count"] = len(multi)
            result["per_staff"] = True
    except Exception:
        pass

    return result


def tool_search_customer(db, user_id, args=None):
    args = args or {}
    phone = (args.get("phone") or "").strip()
    name = (args.get("name") or "").strip()
    if not phone and not name:
        return {"success": False, "error": "phone or name is required", "customers": []}
    matches = []
    for r in db.select(CUSTOMERS_TABLE, {"user_id": user_id}) or []:
        if phone and phone not in (r.get("phone") or ""):
            continue
        if name and name.lower() not in (r.get("name") or "").lower():
            continue
        matches.append({
            "id": r["id"],
            "name": r.get("name") or "",
            "phone": r.get("phone") or "",
            "email": r.get("email") or "",
            "notes": r.get("notes") or "",
        })
    return {"count": len(matches), "customers": matches}


def tool_create_customer(db, user_id, args=None):
    args = args or {}
    name = (args.get("name") or "").strip()
    phone = (args.get("phone") or "").strip()
    email = (args.get("email") or "").strip()
    notes = (args.get("notes") or "").strip()
    if not name:
        return {"success": False, "error": "name is required"}
    if phone:
        ok, err = _is_valid_phone(phone)
        if not ok:
            return {"success": False, "error": err}
        existing = tool_search_customer(db, user_id, {"phone": phone})
        if existing.get("customers"):
            return {
                "success": True,
                "created": False,
                "customer": existing["customers"][0],
                "message": "Customer already exists",
            }
    new_id = db.insert(
        CUSTOMERS_TABLE,
        {"user_id": user_id, "name": name, "phone": phone, "email": email, "notes": notes},
    )
    row = db.row(CUSTOMERS_TABLE, {"id": new_id})
    return {
        "success": True,
        "created": True,
        "customer": {
            "id": row["id"],
            "name": row.get("name") or "",
            "phone": row.get("phone") or "",
            "email": row.get("email") or "",
            "notes": row.get("notes") or "",
        },
    }


def tool_book_appointment(db, user_id, args=None):
    from availability_engine import is_slot_free, minutes_to_time, time_to_minutes

    args = args or {}
    customer_name = (args.get("customer_name") or args.get("name") or "").strip()
    phone = (args.get("phone") or "").strip()
    day, date_meta = _parse_date(args.get("date") or args.get("booking_date") or args.get("when"), db, user_id)
    start = _parse_time(args.get("start_time") or args.get("from"))
    notes = (args.get("notes") or "").strip()
    staff_id = args.get("staff_id")

    if not customer_name:
        return {"success": False, "error": "customer_name is required"}
    if not day:
        return {
            "success": False,
            "error": date_meta.get("error") or "date is required",
            "resolved_from": date_meta.get("resolved_from"),
            "today": date_meta.get("today"),
        }
    if not start:
        return {"success": False, "error": "start_time (HH:MM) is required"}
    if phone:
        ok, err = _is_valid_phone(phone)
        if not ok:
            return {"success": False, "error": err}

    svc = _find_service(
        db,
        user_id,
        service_id=args.get("service_id"),
        name=args.get("service_name") or "",
    )
    if not svc:
        return {"success": False, "error": "service not found"}

    duration = int(svc.get("duration_minutes") or 30)
    start_m = time_to_minutes(start)
    if start_m is None:
        return {"success": False, "error": "Invalid start_time"}
    end = _parse_time(minutes_to_time(start_m + duration))

    # Per-staff availability: validate chosen staff or auto-assign least busy.
    from staff_scheduling import (
        available_slots_multi,
        available_staff_for_slot,
        auto_assign_staff,
        capable_staff,
    )

    has_staff_config = bool(capable_staff(db, user_id, svc["id"]))
    if has_staff_config:
        if staff_id:
            free_staff = available_staff_for_slot(db, user_id, svc["id"], day, start, duration)
            if not any(int(s["id"]) == int(staff_id) for s in free_staff):
                multi, _dur = available_slots_multi(db, user_id, svc["id"], day)
                return {
                    "success": False,
                    "error": "Selected staff is not available at this time",
                    "date": day.isoformat(),
                    "available_slots": [{"from": s["from"], "to": s["to"]} for s in multi],
                }
        else:
            chosen = auto_assign_staff(db, user_id, svc["id"], day, start, duration)
            if not chosen:
                multi, _dur = available_slots_multi(db, user_id, svc["id"], day)
                return {
                    "success": False,
                    "error": "No staff available for this service at the selected time",
                    "date": day.isoformat(),
                    "available_slots": [{"from": s["from"], "to": s["to"]} for s in multi],
                }
            staff_id = chosen["id"]
    else:
        # No staff assignments configured → fall back to global slot engine.
        free, availability = is_slot_free(db, user_id, day, start, duration)
        if not free:
            return {
                "success": False,
                "error": "Selected slot is not available",
                "date": day.isoformat(),
                "available_slots": availability.get("available_slots") or [],
            }

    if phone:
        tool_create_customer(db, user_id, {"name": customer_name, "phone": phone})

    insert_data = {
        "user_id": user_id,
        "service_id": svc["id"],
        "customer_name": customer_name,
        "phone": phone,
        "booking_date": day.isoformat(),
        "start_time": start,
        "end_time": end,
        "status": "confirmed",
        "notes": notes,
        "price": _money(svc.get("price")),
        "duration_minutes": duration,
    }
    if staff_id:
        insert_data["staff_id"] = staff_id
    try:
        new_id = db.insert(BOOKINGS_TABLE, insert_data)
    except Exception as exc:
        if "staff_id" in insert_data or "duration_minutes" in insert_data:
            insert_data.pop("staff_id", None)
            insert_data.pop("duration_minutes", None)
            try:
                new_id = db.insert(BOOKINGS_TABLE, insert_data)
            except Exception as exc2:
                return {"success": False, "error": str(exc2)}
        else:
            return {"success": False, "error": str(exc)}

    booking = _public_booking(_fetch_booking(db, new_id, user_id))
    return {
        "success": True,
        "booking": booking,
        "date": booking.get("date") if booking else day.isoformat(),
        "message": "Appointment booked successfully. Confirm using booking fields only.",
    }


def _cancellation_min_hours(db, user_id):
    """Hours before appointment when cancel is still allowed (default 2)."""
    settings = db.row(SETTINGS_TABLE, {"user_id": user_id}) or {}
    text = " ".join(
        [
            str(settings.get("cancellation_policy") or ""),
            *[
                str(p.get("content") or "")
                for p in (db.select("services_policies", {"user_id": user_id}) or [])
                if (p.get("policy_type") or "").lower() == "cancellation" and p.get("is_active", 1)
            ],
        ]
    )
    m = re.search(r"(\d+)\s*hours?", text, flags=re.I)
    if m:
        return max(0, int(m.group(1)))
    return 2


def _normalize_phone(phone):
    digits = re.sub(r"\D+", "", str(phone or ""))
    return digits


def _is_valid_phone(phone):
    """Basic PK / international mobile check — reject obviously invalid numbers."""
    digits = _normalize_phone(phone)
    if not digits:
        return False, "phone is required"
    if len(digits) < 10 or len(digits) > 15:
        return False, "Invalid phone number"
    if digits in {"0000000000", "1111111111", "1234567890", "00000000000"}:
        return False, "Invalid phone number"
    return True, ""


def _evaluate_cancellation(db, user_id, row):
    """Backend-only cancel policy check. Gemini must never calculate this."""
    from availability_engine import now_local

    booking = _public_booking(row)
    if not row:
        return {
            "allowed": False,
            "reason": "Booking not found",
            "booking": None,
        }

    status = (row.get("status") or "").lower()
    if status == "cancelled":
        return {
            "allowed": False,
            "reason": "This booking is already cancelled.",
            "booking": booking,
        }

    min_hours = _cancellation_min_hours(db, user_id)
    bdate = row.get("booking_date")
    if hasattr(bdate, "isoformat"):
        bdate_str = bdate.isoformat()
    else:
        bdate_str = str(bdate or "")[:10]
    start_raw = _fmt_time(row.get("start_time")) or "00:00"

    try:
        from availability_engine import business_timezone

        tz, _tz_name = business_timezone(db, user_id)
        appt_dt = datetime.strptime(f"{bdate_str} {start_raw}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        now = now_local(db, user_id)
        deadline = appt_dt - timedelta(hours=min_hours)
        if now > deadline:
            return {
                "allowed": False,
                "reason": (
                    f"Cancellation is not allowed within {min_hours} hours of the appointment. "
                    "Please contact the business directly."
                ),
                "booking": booking,
                "policy_hours": min_hours,
                "appointment_datetime": appt_dt.isoformat(),
                "current_datetime": now.isoformat(),
            }
    except ValueError:
        return {
            "allowed": False,
            "reason": "Could not validate appointment time for cancellation.",
            "booking": booking,
        }

    return {
        "allowed": True,
        "reason": "Cancellation is allowed.",
        "booking": booking,
        "policy_hours": min_hours,
    }


def tool_cancel_booking(db, user_id, args=None):
    """Find booking → backend decides allowed → cancel only if allowed.

    Gemini must ONLY explain the returned allowed/reason/booking — never compute policy.
    """
    args = args or {}
    booking_id = args.get("booking_id")
    phone = (args.get("phone") or "").strip()
    day, _meta = _parse_date(args.get("date") or args.get("booking_date") or args.get("when"), db, user_id)
    row = None
    if booking_id:
        row = _fetch_booking(db, booking_id, user_id)
    elif phone and day:
        db.cursor.execute(
            f"""
            SELECT b.*, c.name AS service_name
            FROM {BOOKINGS_TABLE} b
            LEFT JOIN {CATALOG_TABLE} c ON c.id = b.service_id
            WHERE b.user_id=%s AND b.phone=%s AND b.booking_date=%s AND b.status != 'cancelled'
            ORDER BY b.start_time ASC LIMIT 1
            """,
            [user_id, phone, day.isoformat()],
        )
        row = db.cursor.fetchone()
    elif phone:
        # Latest upcoming booking for this phone
        from availability_engine import today_local

        db.cursor.execute(
            f"""
            SELECT b.*, c.name AS service_name
            FROM {BOOKINGS_TABLE} b
            LEFT JOIN {CATALOG_TABLE} c ON c.id = b.service_id
            WHERE b.user_id=%s AND b.phone=%s AND b.status != 'cancelled'
              AND b.booking_date >= %s
            ORDER BY b.booking_date ASC, b.start_time ASC LIMIT 1
            """,
            [user_id, phone, today_local(db, user_id).isoformat()],
        )
        row = db.cursor.fetchone()

    if not row:
        return {
            "success": False,
            "allowed": False,
            "reason": "Booking not found",
            "booking": None,
        }

    decision = _evaluate_cancellation(db, user_id, row)
    if not decision.get("allowed"):
        return {
            "success": False,
            "allowed": False,
            "reason": decision.get("reason"),
            "booking": decision.get("booking"),
            "policy_hours": decision.get("policy_hours"),
            "appointment_datetime": decision.get("appointment_datetime"),
            "current_datetime": decision.get("current_datetime"),
        }

    db.update(BOOKINGS_TABLE, {"status": "cancelled"}, {"id": row["id"], "user_id": user_id})
    cancelled = _public_booking(_fetch_booking(db, row["id"], user_id))
    return {
        "success": True,
        "allowed": True,
        "reason": "Booking cancelled successfully.",
        "booking": cancelled,
        "policy_hours": decision.get("policy_hours"),
    }


def tool_reschedule_booking(db, user_id, args=None):
    from availability_engine import is_slot_free, minutes_to_time, time_to_minutes

    args = args or {}
    booking_id = args.get("booking_id")
    new_day, date_meta = _parse_date(
        args.get("date") or args.get("booking_date") or args.get("new_date") or args.get("when"),
        db,
        user_id,
    )
    new_start = _parse_time(args.get("start_time") or args.get("new_start_time") or args.get("from"))
    if not booking_id:
        return {"success": False, "error": "booking_id is required"}
    if not new_day or not new_start:
        return {
            "success": False,
            "error": date_meta.get("error") or "new date and start_time are required",
            "resolved_from": date_meta.get("resolved_from"),
            "today": date_meta.get("today"),
        }

    row = _fetch_booking(db, booking_id, user_id)
    if not row:
        return {"success": False, "error": "Booking not found", "allowed": False}

    # Same lead-time policy as cancel for reschedule.
    decision = _evaluate_cancellation(db, user_id, row)
    if not decision.get("allowed"):
        return {
            "success": False,
            "allowed": False,
            "reason": (
                decision.get("reason") or "Reschedule not allowed"
            ).replace("Cancellation", "Reschedule"),
            "booking": decision.get("booking"),
            "policy_hours": decision.get("policy_hours"),
        }

    duration = 30
    if row.get("service_id"):
        svc = db.row(CATALOG_TABLE, {"id": row["service_id"], "user_id": user_id})
        if svc:
            duration = int(svc.get("duration_minutes") or 30)

    service_id = row.get("service_id")
    new_staff_id = row.get("staff_id")
    from staff_scheduling import (
        available_slots_multi,
        available_staff_for_slot,
        auto_assign_staff,
        capable_staff,
    )

    has_staff_config = bool(service_id and capable_staff(db, user_id, service_id))
    if has_staff_config:
        # Keep same staff if still free, else auto-reassign.
        keep = False
        if new_staff_id:
            free_staff = available_staff_for_slot(
                db, user_id, service_id, new_day, new_start, duration,
                exclude_booking_id=booking_id,
            )
            keep = any(int(s["id"]) == int(new_staff_id) for s in free_staff)
        if not keep:
            chosen = auto_assign_staff(
                db, user_id, service_id, new_day, new_start, duration,
                exclude_booking_id=booking_id,
            )
            if not chosen:
                multi, _dur = available_slots_multi(db, user_id, service_id, new_day)
                return {
                    "success": False,
                    "error": "No staff available at the new time",
                    "date": new_day.isoformat(),
                    "available_slots": [{"from": s["from"], "to": s["to"]} for s in multi],
                }
            new_staff_id = chosen["id"]
    else:
        free, availability = is_slot_free(
            db, user_id, new_day, new_start, duration, exclude_booking_id=booking_id
        )
        if not free:
            return {
                "success": False,
                "error": "New slot is not available",
                "date": new_day.isoformat(),
                "available_slots": availability.get("available_slots") or [],
            }

    end = _parse_time(minutes_to_time(time_to_minutes(new_start) + duration))
    update_fields = {
        "booking_date": new_day.isoformat(),
        "start_time": new_start,
        "end_time": end,
        "status": "confirmed",
    }
    if has_staff_config and new_staff_id:
        update_fields["staff_id"] = new_staff_id
    try:
        db.update(BOOKINGS_TABLE, update_fields, {"id": booking_id, "user_id": user_id})
    except Exception:
        update_fields.pop("staff_id", None)
        db.update(BOOKINGS_TABLE, update_fields, {"id": booking_id, "user_id": user_id})
    booking = _public_booking(_fetch_booking(db, booking_id, user_id))
    return {
        "success": True,
        "allowed": True,
        "booking": booking,
        "date": booking.get("date") if booking else new_day.isoformat(),
        "message": "Booking rescheduled. Confirm using booking fields only.",
    }


TOOL_HANDLERS = {
    "get_business_info": tool_get_business_info,
    "get_service_details": tool_get_service_details,
    "quote_services": tool_quote_services,
    "get_staff": tool_get_staff,
    "resolve_date": tool_resolve_date,
    "get_available_slots": tool_get_available_slots,
    "search_customer": tool_search_customer,
    "create_customer": tool_create_customer,
    "book_appointment": tool_book_appointment,
    "cancel_booking": tool_cancel_booking,
    "reschedule_booking": tool_reschedule_booking,
}


def run_tool(db, user_id, name, args=None):
    name = (name or "").strip()
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return {
            "success": False,
            "error": f"Unknown tool '{name}'",
            "available_tools": list(TOOL_NAMES),
        }
    try:
        return handler(db, user_id, args or {})
    except Exception as exc:
        return {"success": False, "error": str(exc), "tool": name}
