"""Restaurant chatbot — business profile, working hours & holidays routes.

Isolated from Services / Ecommerce / Job Posting. Gated so only users whose
main chatbot type handler is `Restaurant` can read/write these tables.
"""

import re
from flask import Blueprint, jsonify, request

from db import Database
from gemini_cache import refresh_cache_after_instruction_change
from restaurant_schema import (
    SETTINGS_TABLE,
    WORKING_HOURS_TABLE,
    HOLIDAYS_TABLE,
    ensure_restaurant_schema,
)

restaurant_settings_bp = Blueprint("restaurant_settings", __name__)

HANDLER_NAME = "Restaurant"

BUSINESS_FIELDS = (
    "business_name",
    "business_category",
    "about",
    "phone",
    "whatsapp",
    "email",
    "address",
    "city",
    "logo_url",
    "delivery_charges",
    "minimum_order",
    "estimated_delivery_time",
    "payment_methods",
    "delivery_rules",
    "primary_color",
    "secondary_color",
    "accent_color",
    "app_background",
)


# --------------------------------------------------------------------------- #
# Gating & helpers                                                            #
# --------------------------------------------------------------------------- #


def _is_restaurant_user(db, user_id):
    """True when user's main chatbot type handler class is Restaurant."""
    meta_rows = db.select("user_meta", {"user_id": user_id})
    meta = {m["meta_key"]: m["meta_value"] for m in meta_rows}
    type_id = meta.get("chatbot_type_id")
    ctype = db.row("chatbot_types", {"id": type_id}) if type_id else None
    handler = (ctype or {}).get("handler_class")
    return handler == HANDLER_NAME, handler, meta


def _parse_time_input(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if re.match(r"^\d{2}:\d{2}$", raw):
        return f"{raw}:00"
    if re.match(r"^\d{2}:\d{2}:\d{2}$", raw):
        return raw
    return None


def _validate_date(date_str):
    if not date_str:
        return False
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", str(date_str).strip()))


def _fmt_time(t):
    if not t:
        return ""
    s = str(t)
    if re.match(r"^\d{2}:\d{2}:\d{2}$", s):
        return s[:5]
    return s


def _ensure_user_defaults(db, user_id):
    """Ensure settings row + 7 working-hours rows exist."""
    settings = db.row(SETTINGS_TABLE, {"user_id": user_id})
    if not settings:
        db.insert(SETTINGS_TABLE, {"user_id": user_id, "currency_code": "PKR"})

    existing_days = {r.get("day_of_week") for r in db.select(WORKING_HOURS_TABLE, {"user_id": user_id})}
    for day in range(7):
        if day in existing_days:
            continue
        db.insert(
            WORKING_HOURS_TABLE,
            {
                "user_id": user_id,
                "day_of_week": day,
                "open_time": "10:00:00",
                "close_time": "23:00:00",
                "break_start": None,
                "break_end": None,
                "is_closed": 0,
            },
        )


def _public_working_hours(rows):
    rows = sorted(rows or [], key=lambda r: int(r.get("day_of_week") or 0))
    return [
        {
            "id": r["id"],
            "day_of_week": r.get("day_of_week"),
            "open_time": _fmt_time(r.get("open_time")),
            "break_start": _fmt_time(r.get("break_start")),
            "break_end": _fmt_time(r.get("break_end")),
            "close_time": _fmt_time(r.get("close_time")),
            "is_closed": bool(r.get("is_closed")),
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
            "title": r.get("title") or "",
            "description": r.get("description") or "",
        }
        for r in rows
    ]


def _public_business(settings):
    settings = settings or {}
    return {
        "business_name": settings.get("business_name") or "",
        "business_category": settings.get("business_category") or "",
        "about": settings.get("about") or "",
        "phone": settings.get("phone") or "",
        "whatsapp": settings.get("whatsapp") or "",
        "email": settings.get("email") or "",
        "address": settings.get("address") or "",
        "city": settings.get("city") or "",
        "logo_url": settings.get("logo_url") or "",
        "delivery_charges": float(settings.get("delivery_charges") or 0),
        "minimum_order": float(settings.get("minimum_order") or 0),
        "estimated_delivery_time": settings.get("estimated_delivery_time") or "",
        "payment_methods": settings.get("payment_methods") or "",
        "delivery_rules": settings.get("delivery_rules") or "",
        "currency_code": settings.get("currency_code") or "PKR",
        "primary_color": settings.get("primary_color") or "#0ea5e9",
        "secondary_color": settings.get("secondary_color") or "#2563eb",
        "accent_color": settings.get("accent_color") or "#10b981",
        "app_background": settings.get("app_background") or "#f8fbff",
    }


def ensure_schema():
    ensure_restaurant_schema()


# --------------------------------------------------------------------------- #
# Full settings                                                               #
# --------------------------------------------------------------------------- #


@restaurant_settings_bp.route("/users/<int:user_id>/restaurant-settings", methods=["GET"])
def get_restaurant_settings(user_id):
    db = Database()
    try:
        is_restaurant, _, _ = _is_restaurant_user(db, user_id)
        if not is_restaurant:
            return jsonify(
                {
                    "status": True,
                    "is_restaurant": False,
                    "handler_class": "",
                    "currency_code": "PKR",
                    "business": {},
                    "working_hours": [],
                    "holidays": [],
                }
            )

        _ensure_user_defaults(db, user_id)
        settings = db.row(SETTINGS_TABLE, {"user_id": user_id}) or {}
        hours = db.select(WORKING_HOURS_TABLE, {"user_id": user_id})
        holidays = db.select(HOLIDAYS_TABLE, {"user_id": user_id})
    finally:
        db.close()

    return jsonify(
        {
            "status": True,
            "is_restaurant": True,
            "handler_class": HANDLER_NAME,
            "currency_code": settings.get("currency_code") or "PKR",
            "business": _public_business(settings),
            "working_hours": _public_working_hours(hours),
            "holidays": _public_holidays(holidays),
        }
    )


# --------------------------------------------------------------------------- #
# Currency                                                                    #
# --------------------------------------------------------------------------- #


@restaurant_settings_bp.route("/users/<int:user_id>/restaurant-settings/currency", methods=["POST"])
def save_currency(user_id):
    data = request.json or {}
    currency_code = (data.get("currency_code") or data.get("currency") or "").strip().upper()
    if not currency_code:
        return jsonify({"status": False, "message": "currency_code is required"}), 400

    db = Database()
    try:
        is_restaurant, _, _ = _is_restaurant_user(db, user_id)
        if not is_restaurant:
            return jsonify({"status": False, "message": "Not allowed"}), 403
        _ensure_user_defaults(db, user_id)
        db.update(SETTINGS_TABLE, {"currency_code": currency_code}, {"user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()

    return jsonify({"status": True, "message": "Currency saved", "currency_code": currency_code})


# --------------------------------------------------------------------------- #
# Business profile                                                            #
# --------------------------------------------------------------------------- #


@restaurant_settings_bp.route("/users/<int:user_id>/restaurant-settings/business", methods=["POST"])
def save_business_profile(user_id):
    data = request.json or {}
    update = {}
    for key in BUSINESS_FIELDS:
        if key not in data:
            continue
        value = data.get(key)
        if key in ("delivery_charges", "minimum_order"):
            try:
                value = float(value or 0)
            except (TypeError, ValueError):
                value = 0
        elif isinstance(value, str):
            value = value.strip()
        update[key] = value

    if "currency_code" in data:
        update["currency_code"] = (data.get("currency_code") or "PKR").strip().upper() or "PKR"

    if not update:
        return jsonify({"status": False, "message": "No business fields provided"}), 400

    db = Database()
    try:
        is_restaurant, _, _ = _is_restaurant_user(db, user_id)
        if not is_restaurant:
            return jsonify({"status": False, "message": "Not allowed"}), 403
        _ensure_user_defaults(db, user_id)
        db.update(SETTINGS_TABLE, update, {"user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
        settings = db.row(SETTINGS_TABLE, {"user_id": user_id}) or {}
    finally:
        db.close()

    return jsonify({"status": True, "message": "Business profile saved", "business": _public_business(settings)})


# --------------------------------------------------------------------------- #
# Working hours                                                               #
# --------------------------------------------------------------------------- #


@restaurant_settings_bp.route("/users/<int:user_id>/restaurant-settings/working-hours", methods=["GET"])
def list_working_hours(user_id):
    db = Database()
    try:
        is_restaurant, _, _ = _is_restaurant_user(db, user_id)
        if not is_restaurant:
            return jsonify({"status": False, "message": "Not allowed"}), 403
        _ensure_user_defaults(db, user_id)
        rows = db.select(WORKING_HOURS_TABLE, {"user_id": user_id})
    finally:
        db.close()
    return jsonify({"status": True, "working_hours": _public_working_hours(rows)})


@restaurant_settings_bp.route(
    "/users/<int:user_id>/restaurant-settings/working-hours/<int:day_of_week>", methods=["PUT"]
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
        open_time = close_time = break_start = break_end = None
    elif (break_start and not break_end) or (break_end and not break_start):
        break_start = break_end = None

    payload = {
        "open_time": open_time,
        "close_time": close_time,
        "break_start": break_start,
        "break_end": break_end,
        "is_closed": 1 if is_closed else 0,
    }

    db = Database()
    try:
        is_restaurant, _, _ = _is_restaurant_user(db, user_id)
        if not is_restaurant:
            return jsonify({"status": False, "message": "Not allowed"}), 403
        _ensure_user_defaults(db, user_id)
        existing = db.row(WORKING_HOURS_TABLE, {"user_id": user_id, "day_of_week": day_of_week})
        if not existing:
            db.insert(WORKING_HOURS_TABLE, {"user_id": user_id, "day_of_week": day_of_week, **payload})
        else:
            db.update(WORKING_HOURS_TABLE, payload, {"id": existing["id"]})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()

    return jsonify({"status": True, "message": "Working hour updated"})


# --------------------------------------------------------------------------- #
# Holidays                                                                    #
# --------------------------------------------------------------------------- #


@restaurant_settings_bp.route("/users/<int:user_id>/restaurant-settings/holidays", methods=["GET"])
def list_holidays(user_id):
    db = Database()
    try:
        is_restaurant, _, _ = _is_restaurant_user(db, user_id)
        if not is_restaurant:
            return jsonify({"status": False, "message": "Not allowed"}), 403
        rows = db.select(HOLIDAYS_TABLE, {"user_id": user_id})
    finally:
        db.close()
    return jsonify({"status": True, "holidays": _public_holidays(rows)})


@restaurant_settings_bp.route("/users/<int:user_id>/restaurant-settings/holidays", methods=["POST"])
def create_holiday(user_id):
    data = request.json or {}
    holiday_date = (data.get("date") or data.get("holiday_date") or "").strip()
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    if not _validate_date(holiday_date):
        return jsonify({"status": False, "message": "date must be YYYY-MM-DD"}), 400

    db = Database()
    try:
        is_restaurant, _, _ = _is_restaurant_user(db, user_id)
        if not is_restaurant:
            return jsonify({"status": False, "message": "Not allowed"}), 403
        _ensure_user_defaults(db, user_id)
        new_id = db.insert(
            HOLIDAYS_TABLE,
            {
                "user_id": user_id,
                "holiday_date": holiday_date,
                "title": title or None,
                "description": description or None,
            },
        )
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(HOLIDAYS_TABLE, {"id": new_id})
    finally:
        db.close()

    return jsonify({"status": True, "message": "Holiday added", "holiday": _public_holidays([row])[0]})


@restaurant_settings_bp.route(
    "/users/<int:user_id>/restaurant-settings/holidays/<int:holiday_id>", methods=["PUT"]
)
def update_holiday(user_id, holiday_id):
    data = request.json or {}
    holiday_date = (data.get("date") or data.get("holiday_date") or "").strip()
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    if not _validate_date(holiday_date):
        return jsonify({"status": False, "message": "date must be YYYY-MM-DD"}), 400

    db = Database()
    try:
        is_restaurant, _, _ = _is_restaurant_user(db, user_id)
        if not is_restaurant:
            return jsonify({"status": False, "message": "Not allowed"}), 403
        existing = db.row(HOLIDAYS_TABLE, {"id": holiday_id, "user_id": user_id})
        if not existing:
            return jsonify({"status": False, "message": "Holiday not found"}), 404
        db.update(
            HOLIDAYS_TABLE,
            {"holiday_date": holiday_date, "title": title or None, "description": description or None},
            {"id": holiday_id, "user_id": user_id},
        )
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(HOLIDAYS_TABLE, {"id": holiday_id})
    finally:
        db.close()

    return jsonify({"status": True, "message": "Holiday updated", "holiday": _public_holidays([row])[0]})


@restaurant_settings_bp.route(
    "/users/<int:user_id>/restaurant-settings/holidays/<int:holiday_id>", methods=["DELETE"]
)
def delete_holiday(user_id, holiday_id):
    db = Database()
    try:
        is_restaurant, _, _ = _is_restaurant_user(db, user_id)
        if not is_restaurant:
            return jsonify({"status": False, "message": "Not allowed"}), 403
        existing = db.row(HOLIDAYS_TABLE, {"id": holiday_id, "user_id": user_id})
        if not existing:
            return jsonify({"status": False, "message": "Holiday not found"}), 404
        db.delete(HOLIDAYS_TABLE, {"id": holiday_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()

    return jsonify({"status": True, "message": "Holiday deleted"})
