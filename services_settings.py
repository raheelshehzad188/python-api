import re
from flask import Blueprint, jsonify, request

from db import Database
from gemini_cache import refresh_cache_after_instruction_change

services_settings_bp = Blueprint("services_settings", __name__)

# --------------------------------------------------------------------------- #
# Tables                                                                     #
# --------------------------------------------------------------------------- #

SETTINGS_TABLE = "services_settings"
CATALOG_TABLE = "services_catalog"
WORKING_HOURS_TABLE = "services_working_hours"
HOLIDAYS_TABLE = "services_holidays"

# --------------------------------------------------------------------------- #
# Helpers                                                                    #
# --------------------------------------------------------------------------- #

HANDLER_NAME = "Services"


def _is_services_user(db, user_id):
    """True when user's main chatbot type handler class is Services."""
    meta_rows = db.select("user_meta", {"user_id": user_id})
    meta = {m["meta_key"]: m["meta_value"] for m in meta_rows}
    type_id = meta.get("chatbot_type_id")
    ctype = db.row("chatbot_types", {"id": type_id}) if type_id else None
    handler = (ctype or {}).get("handler_class")
    return handler == HANDLER_NAME, handler, meta


def _parse_time_input(value):
    """Accept 'HH:MM' from <input type="time"> and return 'HH:MM:SS' or None."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # Basic normalize: HH:MM or HH:MM:SS
    if re.match(r"^\d{2}:\d{2}$", raw):
        return f"{raw}:00"
    if re.match(r"^\d{2}:\d{2}:\d{2}$", raw):
        return raw
    return None


def _ensure_user_defaults(db, user_id):
    """Ensure currency row + 7 working-hours rows exist for this user."""
    # Currency
    settings = db.row(SETTINGS_TABLE, {"user_id": user_id})
    if not settings:
        db.insert(
            SETTINGS_TABLE,
            {
                "user_id": user_id,
                "currency_code": "USD",
            },
        )

    # Working hours: seed Mon..Sun (day_of_week 0..6)
    existing_days = {r.get("day_of_week") for r in db.select(WORKING_HOURS_TABLE, {"user_id": user_id})}
    for day in range(7):
        if day in existing_days:
            continue
        db.insert(
            WORKING_HOURS_TABLE,
            {
                "user_id": user_id,
                "day_of_week": day,
                "open_time": "09:00:00",
                "close_time": "17:00:00",
                "break_start": None,
                "break_end": None,
            },
        )


def _public_working_hours(rows):
    # Ensure sorted by day_of_week and normalize TIME fields as 'HH:MM'
    def fmt_time(t):
        if not t:
            return ""
        s = str(t)
        # MySQL might return 'HH:MM:SS'
        if re.match(r"^\d{2}:\d{2}:\d{2}$", s):
            return s[:5]
        if re.match(r"^\d{2}:\d{2}$", s):
            return s
        return s

    rows = rows or []
    rows = sorted(rows, key=lambda r: int(r.get("day_of_week") or 0))
    return [
        {
            "id": r["id"],
            "day_of_week": r.get("day_of_week"),
            "open_time": fmt_time(r.get("open_time")),
            "break_start": fmt_time(r.get("break_start")),
            "break_end": fmt_time(r.get("break_end")),
            "close_time": fmt_time(r.get("close_time")),
            "is_closed": bool(r.get("is_closed")),
        }
        for r in rows
    ]


def _public_services(rows, currency_code):
    rows = rows or []
    # MySQL DECIMAL is returned as Decimal sometimes; stringify for UI
    return [
        {
            "id": r["id"],
            "name": r.get("name") or "",
            "duration_minutes": r.get("duration_minutes") or 0,
            "price": float(r.get("price") or 0),
            "currency_code": currency_code or "",
            "ai_context": r.get("ai_context") or "",
            "description": r.get("description") or r.get("ai_context") or "",
            "related_service_ids": r.get("related_service_ids") or "",
            "category_id": r.get("category_id"),
            "image_url": r.get("image_url") or "",
            "status": r.get("status") or "active",
        }
        for r in rows
    ]


def _public_holidays(rows):
    rows = rows or []
    return [
        {
            "id": r["id"],
            "date": (r.get("holiday_date") or "").isoformat()
            if hasattr(r.get("holiday_date"), "isoformat")
            else (r.get("holiday_date") or ""),
            "title": r.get("title") or r.get("reason") or "",
            "reason": r.get("reason") or "",
            "description": r.get("description") or "",
        }
        for r in rows
    ]


def _ensure_column(db, table, column, definition):
    db.cursor.execute(
        "SELECT COUNT(*) AS c FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        [table, column],
    )
    if db.cursor.fetchone()["c"] == 0:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def ensure_schema():
    """Ensure full Services panel schema (isolated from Ecommerce / Job)."""
    from services_schema import ensure_services_schema

    ensure_services_schema()


# --------------------------------------------------------------------------- #
# GET - Full settings                                                         #
# --------------------------------------------------------------------------- #


@services_settings_bp.route("/users/<int:user_id>/services-settings", methods=["GET"])
def get_services_settings(user_id):
    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            # Keep compatible with dashboard menu gating:
            # return 200 + is_services=false instead of 403.
            return jsonify(
                {
                    "status": True,
                    "is_services": False,
                    "handler_class": "",
                    "currency_code": "USD",
                    "working_hours": [],
                    "services": [],
                    "holidays": [],
                }
            )

        _ensure_user_defaults(db, user_id)
        settings = db.row(SETTINGS_TABLE, {"user_id": user_id}) or {}
        currency_code = settings.get("currency_code") or "USD"

        hours = db.select(WORKING_HOURS_TABLE, {"user_id": user_id})
        services = db.select(CATALOG_TABLE, {"user_id": user_id})
        holidays = db.select(HOLIDAYS_TABLE, {"user_id": user_id})
        staff = db.select("services_staff", {"user_id": user_id}) or []
        packages = db.select("services_packages", {"user_id": user_id}) or []
        promotions = db.select("services_promotions", {"user_id": user_id}) or []
        faqs = db.select("services_faqs", {"user_id": user_id}) or []
        memberships = db.select("services_memberships", {"user_id": user_id}) or []
    finally:
        db.close()

    return jsonify(
        {
            "status": True,
            "is_services": True,
            "handler_class": HANDLER_NAME,
            "currency_code": currency_code,
            "business": {
                "business_name": settings.get("business_name") or "",
                "business_category": settings.get("business_category") or "",
                "about": settings.get("about") or "",
                "address": settings.get("address") or "",
                "city": settings.get("city") or "",
                "phone": settings.get("phone") or "",
                "email": settings.get("email") or "",
                "website": settings.get("website") or "",
                "maps_link": settings.get("maps_link") or "",
                "logo_url": settings.get("logo_url") or "",
                "parking_info": settings.get("parking_info") or "",
                "payment_methods": settings.get("payment_methods") or "",
                "cancellation_policy": settings.get("cancellation_policy") or "",
                "booking_rules": settings.get("booking_rules") or "",
                "primary_color": settings.get("primary_color") or "#0ea5e9",
                "secondary_color": settings.get("secondary_color") or "#2563eb",
                "accent_color": settings.get("accent_color") or "#10b981",
                "app_background": settings.get("app_background") or "#f8fbff",
            },
            "working_hours": _public_working_hours(hours),
            "services": _public_services(services, currency_code),
            "holidays": _public_holidays(holidays),
            "staff": [
                {
                    "id": r["id"],
                    "name": r.get("name") or "",
                    "role": r.get("role") or "",
                    "skills": r.get("skills") or "",
                    "gender": r.get("gender") or "",
                    "ai_context": r.get("ai_context") or "",
                    "is_active": bool(r.get("is_active", 1)),
                }
                for r in staff
            ],
            "packages": [
                {
                    "id": r["id"],
                    "name": r.get("name") or "",
                    "price": float(r.get("price") or 0),
                    "includes": r.get("includes") or "",
                    "ai_context": r.get("ai_context") or "",
                    "is_active": bool(r.get("is_active", 1)),
                }
                for r in packages
            ],
            "promotions": [
                {
                    "id": r["id"],
                    "title": r.get("title") or "",
                    "description": r.get("description") or "",
                    "discount": r.get("discount") or "",
                    "start_date": (r.get("start_date").isoformat() if hasattr(r.get("start_date"), "isoformat") else (r.get("start_date") or "")),
                    "end_date": (r.get("end_date").isoformat() if hasattr(r.get("end_date"), "isoformat") else (r.get("end_date") or "")),
                    "is_active": bool(r.get("is_active", 1)),
                }
                for r in promotions
            ],
            "faqs": [
                {
                    "id": r["id"],
                    "question": r.get("question") or "",
                    "answer": r.get("answer") or "",
                }
                for r in faqs
            ],
            "memberships": [
                {
                    "id": r["id"],
                    "name": r.get("name") or "",
                    "price": float(r.get("price") or 0),
                    "benefits": r.get("benefits") or "",
                    "ai_context": r.get("ai_context") or "",
                    "is_active": bool(r.get("is_active", 1)),
                }
                for r in memberships
            ],
        }
    )


# --------------------------------------------------------------------------- #
# Currency                                                                   #
# --------------------------------------------------------------------------- #


@services_settings_bp.route("/users/<int:user_id>/services-settings/currency", methods=["POST"])
def save_currency(user_id):
    data = request.json or {}
    currency_code = (data.get("currency_code") or data.get("currency") or "").strip().upper()
    if not currency_code:
        return jsonify({"status": False, "message": "currency_code is required"}), 400

    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        _ensure_user_defaults(db, user_id)
        db.update(SETTINGS_TABLE, {"currency_code": currency_code}, {"user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()

    return jsonify({"status": True, "message": "Currency saved", "currency_code": currency_code})


@services_settings_bp.route("/users/<int:user_id>/services-settings/business", methods=["POST"])
def save_business_profile(user_id):
    """Save industry-agnostic business profile used by the Service receptionist cache."""
    data = request.json or {}
    fields = (
        "business_name",
        "business_category",
        "about",
        "address",
        "city",
        "phone",
        "email",
        "website",
        "maps_link",
        "logo_url",
        "parking_info",
        "payment_methods",
        "cancellation_policy",
        "booking_rules",
        "primary_color",
        "secondary_color",
        "accent_color",
        "app_background",
    )
    update = {}
    for key in fields:
        if key in data:
            update[key] = (data.get(key) or "").strip() if isinstance(data.get(key), str) else data.get(key)

    if not update:
        return jsonify({"status": False, "message": "No business fields provided"}), 400

    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        _ensure_user_defaults(db, user_id)
        db.update(SETTINGS_TABLE, update, {"user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
        settings = db.row(SETTINGS_TABLE, {"user_id": user_id}) or {}
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "Business profile saved",
        "business": {k: settings.get(k) or "" for k in fields},
    })


# --------------------------------------------------------------------------- #
# Services catalog CRUD                                                      #
# --------------------------------------------------------------------------- #


@services_settings_bp.route("/users/<int:user_id>/services-settings/services", methods=["GET"])
def list_services(user_id):
    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        _ensure_user_defaults(db, user_id)
        settings = db.row(SETTINGS_TABLE, {"user_id": user_id}) or {}
        currency_code = settings.get("currency_code") or "USD"
        rows = db.select(CATALOG_TABLE, {"user_id": user_id})
    finally:
        db.close()

    return jsonify({"status": True, "services": _public_services(rows, currency_code)})


@services_settings_bp.route("/users/<int:user_id>/services-settings/services", methods=["POST"])
def create_service(user_id):
    data = request.json or {}
    name = (data.get("name") or "").strip()
    duration_minutes = int(data.get("duration_minutes") or data.get("time") or 0)
    price = float(data.get("price") or 0)
    ai_context = data.get("ai_context") or data.get("description") or ""
    description = data.get("description") or ai_context
    related_service_ids = (data.get("related_service_ids") or "").strip()
    category_id = data.get("category_id") or None
    image_url = (data.get("image_url") or "").strip() or None
    status = (data.get("status") or "active").strip() or "active"

    if not name:
        return jsonify({"status": False, "message": "Service name is required"}), 400
    if duration_minutes < 0:
        return jsonify({"status": False, "message": "duration_minutes must be >= 0"}), 400
    if price < 0:
        return jsonify({"status": False, "message": "price must be >= 0"}), 400

    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        _ensure_user_defaults(db, user_id)
        payload = {
            "user_id": user_id,
            "name": name,
            "duration_minutes": duration_minutes,
            "price": price,
            "ai_context": ai_context,
        }
        # Optional columns (added by ensure_schema migrations)
        payload["description"] = description
        payload["related_service_ids"] = related_service_ids
        payload["category_id"] = category_id
        payload["image_url"] = image_url
        payload["status"] = status
        try:
            new_id = db.insert(CATALOG_TABLE, payload)
        except Exception:
            payload.pop("description", None)
            payload.pop("related_service_ids", None)
            payload.pop("category_id", None)
            payload.pop("image_url", None)
            payload.pop("status", None)
            new_id = db.insert(CATALOG_TABLE, payload)
        refresh_cache_after_instruction_change(db, user_id)
        settings = db.row(SETTINGS_TABLE, {"user_id": user_id}) or {}
        row = db.row(CATALOG_TABLE, {"id": new_id})
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "Service created",
        "service": _public_services([row], settings.get("currency_code") or "USD")[0],
    })


@services_settings_bp.route("/users/<int:user_id>/services-settings/services/<int:service_id>", methods=["PUT"])
def update_service(user_id, service_id):
    data = request.json or {}
    name = (data.get("name") or "").strip()
    duration_minutes = int(data.get("duration_minutes") or data.get("time") or 0)
    price = float(data.get("price") or 0)
    ai_context = data.get("ai_context") or data.get("description") or ""
    description = data.get("description") or ai_context
    related_service_ids = (data.get("related_service_ids") or "").strip()
    category_id = data.get("category_id") if "category_id" in data else None
    image_url = (data.get("image_url") or "").strip() or None if "image_url" in data else None
    status = (data.get("status") or "active").strip() or "active"

    if not name:
        return jsonify({"status": False, "message": "Service name is required"}), 400

    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        existing = db.row(CATALOG_TABLE, {"id": service_id, "user_id": user_id})
        if not existing:
            return jsonify({"status": False, "message": "Service not found"}), 404

        update_data = {
            "name": name,
            "duration_minutes": duration_minutes,
            "price": price,
            "ai_context": ai_context,
            "description": description,
            "related_service_ids": related_service_ids,
            "status": status,
        }
        if "category_id" in data:
            update_data["category_id"] = category_id
        if "image_url" in data:
            update_data["image_url"] = image_url
        try:
            db.update(CATALOG_TABLE, update_data, {"id": service_id, "user_id": user_id})
        except Exception:
            update_data.pop("description", None)
            update_data.pop("related_service_ids", None)
            update_data.pop("category_id", None)
            update_data.pop("image_url", None)
            update_data.pop("status", None)
            db.update(CATALOG_TABLE, update_data, {"id": service_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
        settings = db.row(SETTINGS_TABLE, {"user_id": user_id}) or {}
        row = db.row(CATALOG_TABLE, {"id": service_id})
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "Service updated",
        "service": _public_services([row], settings.get("currency_code") or "USD")[0],
    })


@services_settings_bp.route("/users/<int:user_id>/services-settings/services/<int:service_id>", methods=["DELETE"])
def delete_service(user_id, service_id):
    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        existing = db.row(CATALOG_TABLE, {"id": service_id, "user_id": user_id})
        if not existing:
            return jsonify({"status": False, "message": "Service not found"}), 404

        db.delete(CATALOG_TABLE, {"id": service_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()

    return jsonify({"status": True, "message": "Service deleted"})


# --------------------------------------------------------------------------- #
# Working hours CRUD (per day)                                              #
# --------------------------------------------------------------------------- #


@services_settings_bp.route("/users/<int:user_id>/services-settings/working-hours", methods=["GET"])
def list_working_hours(user_id):
    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        _ensure_user_defaults(db, user_id)
        rows = db.select(WORKING_HOURS_TABLE, {"user_id": user_id})
    finally:
        db.close()

    return jsonify({"status": True, "working_hours": _public_working_hours(rows)})


@services_settings_bp.route(
    "/users/<int:user_id>/services-settings/working-hours/<int:day_of_week>", methods=["PUT"]
)
def update_working_hour(user_id, day_of_week):
    data = request.json or {}
    day_of_week = int(day_of_week)
    if day_of_week < 0 or day_of_week > 6:
        return jsonify({"status": False, "message": "day_of_week must be between 0 and 6"}), 400

    is_closed = bool(data.get("is_closed"))
    open_time = _parse_time_input(data.get("open_time"))
    break_start = _parse_time_input(data.get("break_start"))
    break_end = _parse_time_input(data.get("break_end"))
    close_time = _parse_time_input(data.get("close_time"))

    if not is_closed and (not open_time or not close_time):
        return jsonify({"status": False, "message": "open_time and close_time are required"}), 400

    if is_closed:
        open_time = None
        close_time = None
        break_start = None
        break_end = None
    elif (break_start and not break_end) or (break_end and not break_start):
        break_start = None
        break_end = None

    payload = {
        "open_time": open_time,
        "close_time": close_time,
        "break_start": break_start,
        "break_end": break_end,
        "is_closed": 1 if is_closed else 0,
    }

    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        _ensure_user_defaults(db, user_id)

        existing = db.row(WORKING_HOURS_TABLE, {"user_id": user_id, "day_of_week": day_of_week})
        if not existing:
            db.insert(
                WORKING_HOURS_TABLE,
                {"user_id": user_id, "day_of_week": day_of_week, **payload},
            )
        else:
            try:
                db.update(WORKING_HOURS_TABLE, payload, {"id": existing["id"]})
            except Exception:
                payload.pop("is_closed", None)
                db.update(WORKING_HOURS_TABLE, payload, {"id": existing["id"]})

        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()

    return jsonify({"status": True, "message": "Working hour updated"})


# --------------------------------------------------------------------------- #
# Holidays CRUD                                                              #
# --------------------------------------------------------------------------- #


def _validate_date(date_str):
    if not date_str:
        return False
    raw = str(date_str).strip()
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", raw))


@services_settings_bp.route("/users/<int:user_id>/services-settings/holidays", methods=["GET"])
def list_holidays(user_id):
    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403
        rows = db.select(HOLIDAYS_TABLE, {"user_id": user_id})
    finally:
        db.close()
    return jsonify({"status": True, "holidays": _public_holidays(rows)})


@services_settings_bp.route("/users/<int:user_id>/services-settings/holidays", methods=["POST"])
def create_holiday(user_id):
    data = request.json or {}
    holiday_date = (data.get("date") or data.get("holiday_date") or "").strip()
    title = (data.get("title") or data.get("reason") or "").strip()
    description = (data.get("description") or "").strip()
    reason = title
    if not _validate_date(holiday_date):
        return jsonify({"status": False, "message": "date must be YYYY-MM-DD"}), 400

    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        _ensure_user_defaults(db, user_id)
        payload = {
            "user_id": user_id,
            "holiday_date": holiday_date,
            "reason": reason or None,
            "title": title or None,
            "description": description or None,
        }
        try:
            new_id = db.insert(HOLIDAYS_TABLE, payload)
        except Exception:
            payload.pop("title", None)
            payload.pop("description", None)
            new_id = db.insert(HOLIDAYS_TABLE, payload)
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(HOLIDAYS_TABLE, {"id": new_id})
    finally:
        db.close()

    return jsonify({"status": True, "message": "Holiday added", "holiday": _public_holidays([row])[0]})


@services_settings_bp.route(
    "/users/<int:user_id>/services-settings/holidays/<int:holiday_id>", methods=["PUT"]
)
def update_holiday(user_id, holiday_id):
    data = request.json or {}
    holiday_date = (data.get("date") or data.get("holiday_date") or "").strip()
    title = (data.get("title") or data.get("reason") or "").strip()
    description = (data.get("description") or "").strip()
    reason = title
    if not _validate_date(holiday_date):
        return jsonify({"status": False, "message": "date must be YYYY-MM-DD"}), 400

    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        existing = db.row(HOLIDAYS_TABLE, {"id": holiday_id, "user_id": user_id})
        if not existing:
            return jsonify({"status": False, "message": "Holiday not found"}), 404

        update_data = {
            "holiday_date": holiday_date,
            "reason": reason or None,
            "title": title or None,
            "description": description or None,
        }
        try:
            db.update(HOLIDAYS_TABLE, update_data, {"id": holiday_id, "user_id": user_id})
        except Exception:
            update_data.pop("title", None)
            update_data.pop("description", None)
            db.update(HOLIDAYS_TABLE, update_data, {"id": holiday_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(HOLIDAYS_TABLE, {"id": holiday_id})
    finally:
        db.close()

    return jsonify({"status": True, "message": "Holiday updated", "holiday": _public_holidays([row])[0]})


@services_settings_bp.route(
    "/users/<int:user_id>/services-settings/holidays/<int:holiday_id>", methods=["DELETE"]
)
def delete_holiday(user_id, holiday_id):
    db = Database()
    try:
        is_services, _, _ = _is_services_user(db, user_id)
        if not is_services:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        existing = db.row(HOLIDAYS_TABLE, {"id": holiday_id, "user_id": user_id})
        if not existing:
            return jsonify({"status": False, "message": "Holiday not found"}), 404

        db.delete(HOLIDAYS_TABLE, {"id": holiday_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()

    return jsonify({"status": True, "message": "Holiday deleted"})

