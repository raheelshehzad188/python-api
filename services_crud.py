"""Services chatbot panel — REST CRUD for categories, staff, customers, etc."""

import re
from decimal import Decimal

from flask import Blueprint, jsonify, request

from db import Database
from gemini_cache import (
    CACHE_EXPIRES_KEY,
    CACHE_ID_KEY,
    CACHE_MODEL_KEY,
    refresh_cache_after_instruction_change,
    update_user_cache,
)
from services_schema import (
    BOOKINGS_TABLE,
    CATEGORIES_TABLE,
    CUSTOMERS_TABLE,
    FAQS_TABLE,
    MEMBERSHIPS_TABLE,
    PACKAGES_TABLE,
    PAYMENTS_TABLE,
    POLICIES_TABLE,
    PROMOTIONS_TABLE,
    STAFF_TABLE,
)
from services_settings import HANDLER_NAME, _is_services_user

services_crud_bp = Blueprint("services_crud", __name__)


def _forbidden():
    return jsonify({"status": False, "message": "Not allowed"}), 403


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _gate_services_user(db, user_id):
    """Return (True, None) or (False, error_response)."""
    is_services, _, _ = _is_services_user(db, user_id)
    if not is_services:
        return False, _forbidden()
    return True, None


def _fmt_date(value):
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _fmt_decimal(value):
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _validate_date(value):
    if value is None or value == "":
        return None
    raw = str(value).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return False
    return raw


def _pick(data, fields, defaults=None):
    """Extract allowed fields from request JSON."""
    defaults = defaults or {}
    out = {}
    for key in fields:
        if key in data:
            out[key] = data[key]
        elif key in defaults:
            out[key] = defaults[key]
    return out


def _strip_strings(payload):
    for key, value in list(payload.items()):
        if isinstance(value, str):
            payload[key] = value.strip()
    return payload


def _serialize_category(row):
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "description": row.get("description") or "",
        "sort_order": int(row.get("sort_order") or 0),
        "is_active": _bool(row.get("is_active"), True),
    }


def _fmt_time_val(value):
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    s = str(value)
    return s[:5] if len(s) >= 5 else s


def _serialize_staff(row, service_ids=None):
    if service_ids is None:
        raw = (row.get("assigned_service_ids") or "").strip()
        service_ids = [int(p) for p in raw.split(",") if p.strip().isdigit()]
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "phone": row.get("phone") or "",
        "email": row.get("email") or "",
        "role": row.get("role") or "",
        "department": row.get("department") or "",
        "working_hours": row.get("working_hours") or "",
        "assigned_service_ids": row.get("assigned_service_ids") or "",
        "service_ids": service_ids,
        "working_days": row.get("working_days") or "",
        "work_start": _fmt_time_val(row.get("work_start")),
        "work_end": _fmt_time_val(row.get("work_end")),
        "break_start": _fmt_time_val(row.get("break_start")),
        "break_end": _fmt_time_val(row.get("break_end")),
        "max_bookings_per_slot": int(row.get("max_bookings_per_slot") or 1),
        "max_hours_per_day": int(row.get("max_hours_per_day") or 0),
        "status": row.get("status") or "active",
        "is_active": _bool(row.get("is_active"), True),
        "ai_context": row.get("ai_context") or "",
        "skills": row.get("skills") or "",
        "gender": row.get("gender") or "",
        "photo_url": row.get("photo_url") or "",
        "rating": float(row.get("rating") or 0),
        "completed_jobs": int(row.get("completed_jobs") or 0),
        "commission_percent": float(row.get("commission_percent") or 0),
    }


def _staff_service_ids_from_pivot(db, user_id, staff_id):
    rows = db.select("staff_services", {"user_id": user_id, "staff_id": staff_id}) or []
    return [int(r["service_id"]) for r in rows if r.get("service_id")]


def _sync_staff_services(db, user_id, staff_id, service_ids):
    """Replace pivot rows for a staff member. service_ids: list[int]."""
    try:
        db.cursor.execute(
            "DELETE FROM staff_services WHERE user_id=%s AND staff_id=%s",
            [user_id, staff_id],
        )
        db.connection.commit()
    except Exception:
        pass
    seen = set()
    for sid in service_ids or []:
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            continue
        if sid in seen:
            continue
        seen.add(sid)
        try:
            db.insert("staff_services", {"user_id": user_id, "staff_id": staff_id, "service_id": sid})
        except Exception:
            pass


def _serialize_customer(row):
    birthday = row.get("birthday")
    if hasattr(birthday, "isoformat"):
        birthday = birthday.isoformat()
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "phone": row.get("phone") or "",
        "email": row.get("email") or "",
        "area": row.get("area") or "",
        "building": row.get("building") or "",
        "apartment": row.get("apartment") or "",
        "address": row.get("address") or "",
        "gender": row.get("gender") or "",
        "birthday": birthday or "",
        "favorite_services": row.get("favorite_services") or "",
        "loyalty_points": int(row.get("loyalty_points") or 0),
        "total_visits": int(row.get("total_visits") or 0),
        "lifetime_spend": float(row.get("lifetime_spend") or 0),
        "notes": row.get("notes") or "",
    }


def _serialize_package(row):
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "price": _fmt_decimal(row.get("price")),
        "includes": row.get("includes") or "",
        "ai_context": row.get("ai_context") or "",
        "is_active": _bool(row.get("is_active"), True),
    }


def _serialize_promotion(row):
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "description": row.get("description") or "",
        "discount": row.get("discount") or "",
        "start_date": _fmt_date(row.get("start_date")),
        "end_date": _fmt_date(row.get("end_date")),
        "is_active": _bool(row.get("is_active"), True),
    }


def _serialize_membership(row):
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "price": _fmt_decimal(row.get("price")),
        "benefits": row.get("benefits") or "",
        "ai_context": row.get("ai_context") or "",
        "is_active": _bool(row.get("is_active"), True),
    }


def _serialize_faq(row):
    return {
        "id": row["id"],
        "question": row.get("question") or "",
        "answer": row.get("answer") or "",
    }


def _serialize_policy(row):
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "content": row.get("content") or "",
        "policy_type": row.get("policy_type") or "",
        "is_active": _bool(row.get("is_active"), True),
    }


def _serialize_payment(row):
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "details": row.get("details") or "",
        "is_active": _bool(row.get("is_active"), True),
        "sort_order": int(row.get("sort_order") or 0),
    }


def _fmt_time(value):
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    s = str(value)
    if re.match(r"^\d{2}:\d{2}:\d{2}$", s):
        return s[:5]
    return s


def _public_booking(row):
    return {
        "id": row["id"],
        "service_id": row.get("service_id"),
        "service_name": row.get("service_name") or "",
        "customer_name": row.get("customer_name") or "",
        "phone": row.get("phone") or "",
        "booking_date": _fmt_date(row.get("booking_date")),
        "start_time": _fmt_time(row.get("start_time")),
        "end_time": _fmt_time(row.get("end_time")),
        "status": row.get("status") or "pending",
        "notes": row.get("notes") or "",
        "price": _fmt_decimal(row.get("price")),
    }


def _customer_visit_history(db, user_id, customer):
    """Bookings matching customer phone or name for this user."""
    phone = (customer.get("phone") or "").strip()
    name = (customer.get("name") or "").strip()
    if not phone and not name:
        return []

    conditions = ["b.user_id=%s"]
    values = [user_id]
    match_parts = []
    if phone:
        match_parts.append("b.phone=%s")
        values.append(phone)
    if name:
        match_parts.append("b.customer_name=%s")
        values.append(name)
    conditions.append("(" + " OR ".join(match_parts) + ")")

    db.cursor.execute(
        f"""
        SELECT b.*, c.name AS service_name
        FROM {BOOKINGS_TABLE} b
        LEFT JOIN services_catalog c ON c.id = b.service_id
        WHERE {" AND ".join(conditions)}
        ORDER BY b.booking_date DESC, b.start_time DESC
        """,
        values,
    )
    return [_public_booking(r) for r in db.cursor.fetchall()]


def _owned_row(db, table, user_id, row_id):
    return db.row(table, {"id": row_id, "user_id": user_id})


def _prepare_category_payload(data, for_create=False):
    payload = _strip_strings(
        _pick(data, ("name", "description", "sort_order", "is_active"), {"sort_order": 0, "is_active": True})
    )
    if for_create and not payload.get("name"):
        return None, "name is required"
    if "sort_order" in payload:
        payload["sort_order"] = int(payload.get("sort_order") or 0)
    if "is_active" in payload:
        payload["is_active"] = 1 if _bool(payload["is_active"]) else 0
    return payload, None


def _prepare_staff_payload(data, for_create=False):
    payload = _strip_strings(
        _pick(
            data,
            (
                "name",
                "phone",
                "email",
                "role",
                "department",
                "working_hours",
                "assigned_service_ids",
                "working_days",
                "work_start",
                "work_end",
                "break_start",
                "break_end",
                "max_bookings_per_slot",
                "max_hours_per_day",
                "status",
                "is_active",
                "ai_context",
                "skills",
            ),
            {"status": "active", "is_active": True},
        )
    )
    if for_create and not payload.get("name"):
        return None, "name is required"
    if "is_active" in payload:
        payload["is_active"] = 1 if _bool(payload["is_active"]) else 0
    for key in ("max_bookings_per_slot", "max_hours_per_day"):
        if key in payload:
            try:
                payload[key] = int(payload[key] or 0)
            except (TypeError, ValueError):
                payload[key] = 0
    if payload.get("max_bookings_per_slot", 1) and int(payload.get("max_bookings_per_slot") or 0) < 1:
        payload["max_bookings_per_slot"] = 1
    for key in (
        "phone", "email", "role", "department", "working_hours", "assigned_service_ids",
        "working_days", "work_start", "work_end", "break_start", "break_end",
        "ai_context", "skills",
    ):
        if key in payload and payload[key] == "":
            payload[key] = None
    return payload, None


def _extract_service_ids(data):
    """Pull service_ids list from request (list or CSV). Returns list|None."""
    if "service_ids" not in data and "assigned_service_ids" not in data:
        return None
    raw = data.get("service_ids")
    if raw is None:
        raw = data.get("assigned_service_ids")
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",")]
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        return None
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except (TypeError, ValueError):
            continue
    return out


def _prepare_customer_payload(data, for_create=False):
    payload = _strip_strings(
        _pick(
            data,
            (
                "name", "phone", "email", "area", "building", "apartment", "address",
                "gender", "birthday", "favorite_services", "loyalty_points",
                "total_visits", "lifetime_spend", "notes",
            ),
        )
    )
    if for_create and not payload.get("name"):
        return None, "name is required"
    for key in (
        "phone", "email", "area", "building", "apartment", "address",
        "gender", "birthday", "favorite_services", "notes",
    ):
        if key in payload and payload[key] == "":
            payload[key] = None
    return payload, None


def _prepare_package_payload(data, for_create=False):
    payload = _strip_strings(
        _pick(data, ("name", "price", "includes", "ai_context", "is_active"), {"price": 0, "is_active": True})
    )
    if for_create and not payload.get("name"):
        return None, "name is required"
    if "price" in payload:
        payload["price"] = _fmt_decimal(payload["price"])
    if "is_active" in payload:
        payload["is_active"] = 1 if _bool(payload["is_active"]) else 0
    return payload, None


def _prepare_promotion_payload(data, for_create=False):
    payload = _strip_strings(
        _pick(
            data,
            ("title", "description", "discount", "start_date", "end_date", "is_active"),
            {"is_active": True},
        )
    )
    if for_create and not payload.get("title"):
        return None, "title is required"
    for date_key in ("start_date", "end_date"):
        if date_key in payload:
            parsed = _validate_date(payload[date_key])
            if parsed is False:
                return None, f"{date_key} must be YYYY-MM-DD"
            payload[date_key] = parsed
    if "is_active" in payload:
        payload["is_active"] = 1 if _bool(payload["is_active"]) else 0
    for key in ("description", "discount"):
        if key in payload and payload[key] == "":
            payload[key] = None
    return payload, None


def _prepare_membership_payload(data, for_create=False):
    payload = _strip_strings(
        _pick(data, ("name", "price", "benefits", "ai_context", "is_active"), {"price": 0, "is_active": True})
    )
    if for_create and not payload.get("name"):
        return None, "name is required"
    if "price" in payload:
        payload["price"] = _fmt_decimal(payload["price"])
    if "is_active" in payload:
        payload["is_active"] = 1 if _bool(payload["is_active"]) else 0
    return payload, None


def _prepare_faq_payload(data, for_create=False):
    payload = _strip_strings(_pick(data, ("question", "answer")))
    if for_create:
        if not payload.get("question"):
            return None, "question is required"
        if not payload.get("answer"):
            return None, "answer is required"
    return payload, None


def _prepare_policy_payload(data, for_create=False):
    payload = _strip_strings(
        _pick(data, ("title", "content", "policy_type", "is_active"), {"is_active": True})
    )
    if for_create:
        if not payload.get("title"):
            return None, "title is required"
        if not payload.get("content"):
            return None, "content is required"
    if "is_active" in payload:
        payload["is_active"] = 1 if _bool(payload["is_active"]) else 0
    if "policy_type" in payload and payload["policy_type"] == "":
        payload["policy_type"] = None
    return payload, None


def _prepare_payment_payload(data, for_create=False):
    payload = _strip_strings(
        _pick(data, ("name", "details", "is_active", "sort_order"), {"is_active": True, "sort_order": 0})
    )
    if for_create and not payload.get("name"):
        return None, "name is required"
    if "sort_order" in payload:
        payload["sort_order"] = int(payload.get("sort_order") or 0)
    if "is_active" in payload:
        payload["is_active"] = 1 if _bool(payload["is_active"]) else 0
    if "details" in payload and payload["details"] == "":
        payload["details"] = None
    return payload, None


# --------------------------------------------------------------------------- #
# Categories                                                                  #
# --------------------------------------------------------------------------- #


@services_crud_bp.route("/users/<int:user_id>/services-categories", methods=["GET"])
def list_categories(user_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        rows = db.select(CATEGORIES_TABLE, {"user_id": user_id})
    finally:
        db.close()
    return jsonify({"status": True, "categories": [_serialize_category(r) for r in rows]})


@services_crud_bp.route("/users/<int:user_id>/services-categories", methods=["POST"])
def create_category(user_id):
    data = request.json or {}
    payload, error = _prepare_category_payload(data, for_create=True)
    if error:
        return jsonify({"status": False, "message": error}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        payload["user_id"] = user_id
        new_id = db.insert(CATEGORIES_TABLE, payload)
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(CATEGORIES_TABLE, {"id": new_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Category created", "category": _serialize_category(row)})


@services_crud_bp.route("/users/<int:user_id>/services-categories/<int:item_id>", methods=["PUT"])
def update_category(user_id, item_id):
    data = request.json or {}
    payload, error = _prepare_category_payload(data)
    if error:
        return jsonify({"status": False, "message": error}), 400
    if not payload:
        return jsonify({"status": False, "message": "Nothing to update"}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, CATEGORIES_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Category not found"}), 404
        db.update(CATEGORIES_TABLE, payload, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(CATEGORIES_TABLE, {"id": item_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Category updated", "category": _serialize_category(row)})


@services_crud_bp.route("/users/<int:user_id>/services-categories/<int:item_id>", methods=["DELETE"])
def delete_category(user_id, item_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, CATEGORIES_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Category not found"}), 404
        db.delete(CATEGORIES_TABLE, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Category deleted"})


# --------------------------------------------------------------------------- #
# Staff                                                                       #
# --------------------------------------------------------------------------- #


@services_crud_bp.route("/users/<int:user_id>/services-staff", methods=["GET"])
def list_staff(user_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        rows = db.select(STAFF_TABLE, {"user_id": user_id})
        pivot = db.select("staff_services", {"user_id": user_id}) or []
        by_staff = {}
        for p in pivot:
            by_staff.setdefault(int(p["staff_id"]), []).append(int(p["service_id"]))
        result = []
        for r in rows:
            sids = by_staff.get(int(r["id"]))
            result.append(_serialize_staff(r, service_ids=sids))
    finally:
        db.close()
    return jsonify({"status": True, "staff": result})


@services_crud_bp.route("/users/<int:user_id>/services-staff", methods=["POST"])
def create_staff(user_id):
    data = request.json or {}
    payload, error = _prepare_staff_payload(data, for_create=True)
    if error:
        return jsonify({"status": False, "message": error}), 400
    service_ids = _extract_service_ids(data)

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        payload["user_id"] = user_id
        if service_ids is not None:
            payload["assigned_service_ids"] = ",".join(str(s) for s in service_ids) or None
        new_id = db.insert(STAFF_TABLE, payload)
        if service_ids is not None:
            _sync_staff_services(db, user_id, new_id, service_ids)
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(STAFF_TABLE, {"id": new_id})
        sids = _staff_service_ids_from_pivot(db, user_id, new_id)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Staff member created", "staff": _serialize_staff(row, service_ids=sids)})


@services_crud_bp.route("/users/<int:user_id>/services-staff/<int:item_id>", methods=["PUT"])
def update_staff(user_id, item_id):
    data = request.json or {}
    payload, error = _prepare_staff_payload(data)
    if error:
        return jsonify({"status": False, "message": error}), 400
    service_ids = _extract_service_ids(data)
    if not payload and service_ids is None:
        return jsonify({"status": False, "message": "Nothing to update"}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, STAFF_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Staff member not found"}), 404
        if service_ids is not None:
            payload["assigned_service_ids"] = ",".join(str(s) for s in service_ids) or None
        if payload:
            db.update(STAFF_TABLE, payload, {"id": item_id, "user_id": user_id})
        if service_ids is not None:
            _sync_staff_services(db, user_id, item_id, service_ids)
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(STAFF_TABLE, {"id": item_id})
        sids = _staff_service_ids_from_pivot(db, user_id, item_id)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Staff member updated", "staff": _serialize_staff(row, service_ids=sids)})


@services_crud_bp.route("/users/<int:user_id>/services-staff/<int:item_id>", methods=["DELETE"])
def delete_staff(user_id, item_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, STAFF_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Staff member not found"}), 404
        db.delete(STAFF_TABLE, {"id": item_id, "user_id": user_id})
        try:
            db.cursor.execute(
                "DELETE FROM staff_services WHERE user_id=%s AND staff_id=%s", [user_id, item_id]
            )
            db.cursor.execute(
                "DELETE FROM services_staff_leaves WHERE user_id=%s AND staff_id=%s", [user_id, item_id]
            )
            db.connection.commit()
        except Exception:
            pass
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Staff member deleted"})


# --------------------------------------------------------------------------- #
# Staff leaves (per-staff unavailability)                                     #
# --------------------------------------------------------------------------- #

LEAVE_TYPES = ("vacation", "sick", "holiday", "emergency", "other")


def _serialize_leave(row):
    def _d(v):
        return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v else "")
    return {
        "id": row["id"],
        "staff_id": row.get("staff_id"),
        "leave_type": row.get("leave_type") or "vacation",
        "start_date": _d(row.get("start_date")),
        "end_date": _d(row.get("end_date")),
        "reason": row.get("reason") or "",
    }


@services_crud_bp.route("/users/<int:user_id>/services-staff-leaves", methods=["GET"])
def list_staff_leaves(user_id):
    staff_id = request.args.get("staff_id")
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        where = {"user_id": user_id}
        if staff_id and str(staff_id).isdigit():
            where["staff_id"] = int(staff_id)
        rows = db.select("services_staff_leaves", where) or []
    finally:
        db.close()
    return jsonify({"status": True, "leaves": [_serialize_leave(r) for r in rows]})


@services_crud_bp.route("/users/<int:user_id>/services-staff-leaves", methods=["POST"])
def create_staff_leave(user_id):
    data = request.json or {}
    try:
        staff_id = int(data.get("staff_id"))
    except (TypeError, ValueError):
        return jsonify({"status": False, "message": "staff_id is required"}), 400
    start_date = (data.get("start_date") or "").strip()
    end_date = (data.get("end_date") or start_date).strip()
    leave_type = (data.get("leave_type") or "vacation").strip().lower()
    if leave_type not in LEAVE_TYPES:
        leave_type = "other"
    if not start_date:
        return jsonify({"status": False, "message": "start_date is required"}), 400
    if not end_date:
        end_date = start_date

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        if not _owned_row(db, STAFF_TABLE, user_id, staff_id):
            return jsonify({"status": False, "message": "Staff member not found"}), 404
        new_id = db.insert("services_staff_leaves", {
            "user_id": user_id,
            "staff_id": staff_id,
            "leave_type": leave_type,
            "start_date": start_date,
            "end_date": end_date,
            "reason": (data.get("reason") or "").strip() or None,
        })
        row = db.row("services_staff_leaves", {"id": new_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Leave added", "leave": _serialize_leave(row)})


@services_crud_bp.route("/users/<int:user_id>/services-staff-leaves/<int:item_id>", methods=["DELETE"])
def delete_staff_leave(user_id, item_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        row = _owned_row(db, "services_staff_leaves", user_id, item_id)
        if not row:
            return jsonify({"status": False, "message": "Leave not found"}), 404
        db.delete("services_staff_leaves", {"id": item_id, "user_id": user_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Leave removed"})


# --------------------------------------------------------------------------- #
# Customers                                                                   #
# --------------------------------------------------------------------------- #


@services_crud_bp.route("/users/<int:user_id>/services-customers", methods=["GET"])
def list_customers(user_id):
    customer_id = request.args.get("id") or request.args.get("customer_id")
    include_visits = request.args.get("include_visits", "").lower() in ("1", "true", "yes")

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err

        if customer_id:
            row = _owned_row(db, CUSTOMERS_TABLE, user_id, int(customer_id))
            if not row:
                return jsonify({"status": False, "message": "Customer not found"}), 404
            customer = _serialize_customer(row)
            if include_visits:
                customer["visit_history"] = _customer_visit_history(db, user_id, row)
            return jsonify({"status": True, "customer": customer})

        rows = db.select(CUSTOMERS_TABLE, {"user_id": user_id})
        customers = [_serialize_customer(r) for r in rows]
        if include_visits:
            for i, row in enumerate(rows):
                customers[i]["visit_history"] = _customer_visit_history(db, user_id, row)
    finally:
        db.close()

    return jsonify({"status": True, "customers": customers})


@services_crud_bp.route("/users/<int:user_id>/services-customers", methods=["POST"])
def create_customer(user_id):
    data = request.json or {}
    payload, error = _prepare_customer_payload(data, for_create=True)
    if error:
        return jsonify({"status": False, "message": error}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        payload["user_id"] = user_id
        new_id = db.insert(CUSTOMERS_TABLE, payload)
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(CUSTOMERS_TABLE, {"id": new_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Customer created", "customer": _serialize_customer(row)})


@services_crud_bp.route("/users/<int:user_id>/services-customers/<int:item_id>", methods=["PUT"])
def update_customer(user_id, item_id):
    data = request.json or {}
    payload, error = _prepare_customer_payload(data)
    if error:
        return jsonify({"status": False, "message": error}), 400
    if not payload:
        return jsonify({"status": False, "message": "Nothing to update"}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, CUSTOMERS_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Customer not found"}), 404
        db.update(CUSTOMERS_TABLE, payload, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(CUSTOMERS_TABLE, {"id": item_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Customer updated", "customer": _serialize_customer(row)})


@services_crud_bp.route("/users/<int:user_id>/services-customers/<int:item_id>", methods=["DELETE"])
def delete_customer(user_id, item_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, CUSTOMERS_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Customer not found"}), 404
        db.delete(CUSTOMERS_TABLE, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Customer deleted"})


# --------------------------------------------------------------------------- #
# Packages                                                                    #
# --------------------------------------------------------------------------- #


@services_crud_bp.route("/users/<int:user_id>/services-packages", methods=["GET"])
def list_packages(user_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        rows = db.select(PACKAGES_TABLE, {"user_id": user_id})
    finally:
        db.close()
    return jsonify({"status": True, "packages": [_serialize_package(r) for r in rows]})


@services_crud_bp.route("/users/<int:user_id>/services-packages", methods=["POST"])
def create_package(user_id):
    data = request.json or {}
    payload, error = _prepare_package_payload(data, for_create=True)
    if error:
        return jsonify({"status": False, "message": error}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        payload["user_id"] = user_id
        new_id = db.insert(PACKAGES_TABLE, payload)
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(PACKAGES_TABLE, {"id": new_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Package created", "package": _serialize_package(row)})


@services_crud_bp.route("/users/<int:user_id>/services-packages/<int:item_id>", methods=["PUT"])
def update_package(user_id, item_id):
    data = request.json or {}
    payload, error = _prepare_package_payload(data)
    if error:
        return jsonify({"status": False, "message": error}), 400
    if not payload:
        return jsonify({"status": False, "message": "Nothing to update"}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, PACKAGES_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Package not found"}), 404
        db.update(PACKAGES_TABLE, payload, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(PACKAGES_TABLE, {"id": item_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Package updated", "package": _serialize_package(row)})


@services_crud_bp.route("/users/<int:user_id>/services-packages/<int:item_id>", methods=["DELETE"])
def delete_package(user_id, item_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, PACKAGES_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Package not found"}), 404
        db.delete(PACKAGES_TABLE, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Package deleted"})


# --------------------------------------------------------------------------- #
# Promotions                                                                  #
# --------------------------------------------------------------------------- #


@services_crud_bp.route("/users/<int:user_id>/services-promotions", methods=["GET"])
def list_promotions(user_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        rows = db.select(PROMOTIONS_TABLE, {"user_id": user_id})
    finally:
        db.close()
    return jsonify({"status": True, "promotions": [_serialize_promotion(r) for r in rows]})


@services_crud_bp.route("/users/<int:user_id>/services-promotions", methods=["POST"])
def create_promotion(user_id):
    data = request.json or {}
    payload, error = _prepare_promotion_payload(data, for_create=True)
    if error:
        return jsonify({"status": False, "message": error}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        payload["user_id"] = user_id
        new_id = db.insert(PROMOTIONS_TABLE, payload)
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(PROMOTIONS_TABLE, {"id": new_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Promotion created", "promotion": _serialize_promotion(row)})


@services_crud_bp.route("/users/<int:user_id>/services-promotions/<int:item_id>", methods=["PUT"])
def update_promotion(user_id, item_id):
    data = request.json or {}
    payload, error = _prepare_promotion_payload(data)
    if error:
        return jsonify({"status": False, "message": error}), 400
    if not payload:
        return jsonify({"status": False, "message": "Nothing to update"}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, PROMOTIONS_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Promotion not found"}), 404
        db.update(PROMOTIONS_TABLE, payload, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(PROMOTIONS_TABLE, {"id": item_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Promotion updated", "promotion": _serialize_promotion(row)})


@services_crud_bp.route("/users/<int:user_id>/services-promotions/<int:item_id>", methods=["DELETE"])
def delete_promotion(user_id, item_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, PROMOTIONS_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Promotion not found"}), 404
        db.delete(PROMOTIONS_TABLE, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Promotion deleted"})


# --------------------------------------------------------------------------- #
# Memberships                                                                 #
# --------------------------------------------------------------------------- #


@services_crud_bp.route("/users/<int:user_id>/services-memberships", methods=["GET"])
def list_memberships(user_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        rows = db.select(MEMBERSHIPS_TABLE, {"user_id": user_id})
    finally:
        db.close()
    return jsonify({"status": True, "memberships": [_serialize_membership(r) for r in rows]})


@services_crud_bp.route("/users/<int:user_id>/services-memberships", methods=["POST"])
def create_membership(user_id):
    data = request.json or {}
    payload, error = _prepare_membership_payload(data, for_create=True)
    if error:
        return jsonify({"status": False, "message": error}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        payload["user_id"] = user_id
        new_id = db.insert(MEMBERSHIPS_TABLE, payload)
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(MEMBERSHIPS_TABLE, {"id": new_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Membership created", "membership": _serialize_membership(row)})


@services_crud_bp.route("/users/<int:user_id>/services-memberships/<int:item_id>", methods=["PUT"])
def update_membership(user_id, item_id):
    data = request.json or {}
    payload, error = _prepare_membership_payload(data)
    if error:
        return jsonify({"status": False, "message": error}), 400
    if not payload:
        return jsonify({"status": False, "message": "Nothing to update"}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, MEMBERSHIPS_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Membership not found"}), 404
        db.update(MEMBERSHIPS_TABLE, payload, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(MEMBERSHIPS_TABLE, {"id": item_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Membership updated", "membership": _serialize_membership(row)})


@services_crud_bp.route("/users/<int:user_id>/services-memberships/<int:item_id>", methods=["DELETE"])
def delete_membership(user_id, item_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, MEMBERSHIPS_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Membership not found"}), 404
        db.delete(MEMBERSHIPS_TABLE, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Membership deleted"})


# --------------------------------------------------------------------------- #
# FAQs                                                                        #
# --------------------------------------------------------------------------- #


@services_crud_bp.route("/users/<int:user_id>/services-faqs", methods=["GET"])
def list_faqs(user_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        rows = db.select(FAQS_TABLE, {"user_id": user_id})
    finally:
        db.close()
    return jsonify({"status": True, "faqs": [_serialize_faq(r) for r in rows]})


@services_crud_bp.route("/users/<int:user_id>/services-faqs", methods=["POST"])
def create_faq(user_id):
    data = request.json or {}
    payload, error = _prepare_faq_payload(data, for_create=True)
    if error:
        return jsonify({"status": False, "message": error}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        payload["user_id"] = user_id
        new_id = db.insert(FAQS_TABLE, payload)
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(FAQS_TABLE, {"id": new_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "FAQ created", "faq": _serialize_faq(row)})


@services_crud_bp.route("/users/<int:user_id>/services-faqs/<int:item_id>", methods=["PUT"])
def update_faq(user_id, item_id):
    data = request.json or {}
    payload, error = _prepare_faq_payload(data)
    if error:
        return jsonify({"status": False, "message": error}), 400
    if not payload:
        return jsonify({"status": False, "message": "Nothing to update"}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, FAQS_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "FAQ not found"}), 404
        db.update(FAQS_TABLE, payload, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(FAQS_TABLE, {"id": item_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "FAQ updated", "faq": _serialize_faq(row)})


@services_crud_bp.route("/users/<int:user_id>/services-faqs/<int:item_id>", methods=["DELETE"])
def delete_faq(user_id, item_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, FAQS_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "FAQ not found"}), 404
        db.delete(FAQS_TABLE, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()
    return jsonify({"status": True, "message": "FAQ deleted"})


# --------------------------------------------------------------------------- #
# Policies                                                                    #
# --------------------------------------------------------------------------- #


@services_crud_bp.route("/users/<int:user_id>/services-policies", methods=["GET"])
def list_policies(user_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        rows = db.select(POLICIES_TABLE, {"user_id": user_id})
    finally:
        db.close()
    return jsonify({"status": True, "policies": [_serialize_policy(r) for r in rows]})


@services_crud_bp.route("/users/<int:user_id>/services-policies", methods=["POST"])
def create_policy(user_id):
    data = request.json or {}
    payload, error = _prepare_policy_payload(data, for_create=True)
    if error:
        return jsonify({"status": False, "message": error}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        payload["user_id"] = user_id
        new_id = db.insert(POLICIES_TABLE, payload)
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(POLICIES_TABLE, {"id": new_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Policy created", "policy": _serialize_policy(row)})


@services_crud_bp.route("/users/<int:user_id>/services-policies/<int:item_id>", methods=["PUT"])
def update_policy(user_id, item_id):
    data = request.json or {}
    payload, error = _prepare_policy_payload(data)
    if error:
        return jsonify({"status": False, "message": error}), 400
    if not payload:
        return jsonify({"status": False, "message": "Nothing to update"}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, POLICIES_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Policy not found"}), 404
        db.update(POLICIES_TABLE, payload, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(POLICIES_TABLE, {"id": item_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Policy updated", "policy": _serialize_policy(row)})


@services_crud_bp.route("/users/<int:user_id>/services-policies/<int:item_id>", methods=["DELETE"])
def delete_policy(user_id, item_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, POLICIES_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Policy not found"}), 404
        db.delete(POLICIES_TABLE, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Policy deleted"})


# --------------------------------------------------------------------------- #
# Payment methods                                                             #
# --------------------------------------------------------------------------- #


@services_crud_bp.route("/users/<int:user_id>/services-payment-methods", methods=["GET"])
def list_payment_methods(user_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        rows = db.select(PAYMENTS_TABLE, {"user_id": user_id})
        rows = sorted(rows, key=lambda r: (int(r.get("sort_order") or 0), r.get("id") or 0))
    finally:
        db.close()
    return jsonify({"status": True, "payment_methods": [_serialize_payment(r) for r in rows]})


@services_crud_bp.route("/users/<int:user_id>/services-payment-methods", methods=["POST"])
def create_payment_method(user_id):
    data = request.json or {}
    payload, error = _prepare_payment_payload(data, for_create=True)
    if error:
        return jsonify({"status": False, "message": error}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        payload["user_id"] = user_id
        new_id = db.insert(PAYMENTS_TABLE, payload)
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(PAYMENTS_TABLE, {"id": new_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Payment method created", "payment_method": _serialize_payment(row)})


@services_crud_bp.route("/users/<int:user_id>/services-payment-methods/<int:item_id>", methods=["PUT"])
def update_payment_method(user_id, item_id):
    data = request.json or {}
    payload, error = _prepare_payment_payload(data)
    if error:
        return jsonify({"status": False, "message": error}), 400
    if not payload:
        return jsonify({"status": False, "message": "Nothing to update"}), 400

    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, PAYMENTS_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Payment method not found"}), 404
        db.update(PAYMENTS_TABLE, payload, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
        row = db.row(PAYMENTS_TABLE, {"id": item_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Payment method updated", "payment_method": _serialize_payment(row)})


@services_crud_bp.route("/users/<int:user_id>/services-payment-methods/<int:item_id>", methods=["DELETE"])
def delete_payment_method(user_id, item_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        existing = _owned_row(db, PAYMENTS_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Payment method not found"}), 404
        db.delete(PAYMENTS_TABLE, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Payment method deleted"})


# --------------------------------------------------------------------------- #
# Gemini cache                                                                #
# --------------------------------------------------------------------------- #


@services_crud_bp.route("/users/<int:user_id>/services-cache-status", methods=["GET"])
def services_cache_status(user_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        rows = db.select("user_meta", {"user_id": user_id})
        meta = {m["meta_key"]: m["meta_value"] for m in rows}
    finally:
        db.close()

    return jsonify(
        {
            "status": True,
            "handler_class": HANDLER_NAME,
            "cache_id": meta.get(CACHE_ID_KEY) or "",
            "expire_time": meta.get(CACHE_EXPIRES_KEY) or "",
            "cache_model": meta.get(CACHE_MODEL_KEY) or "",
        }
    )


@services_crud_bp.route("/users/<int:user_id>/services-cache-refresh", methods=["POST"])
def services_cache_refresh(user_id):
    db = Database()
    try:
        ok, err = _gate_services_user(db, user_id)
        if not ok:
            return err
        result = update_user_cache(db, user_id)
    finally:
        db.close()

    if not result.get("success"):
        return jsonify({"status": False, "message": result.get("message") or "Cache refresh failed"}), 400

    return jsonify(
        {
            "status": True,
            "message": "Cache refreshed",
            "cache_id": result.get("cache_id") or "",
            "expire_time": result.get("expire_time") or "",
            "cache_model": result.get("cache_model") or "",
        }
    )
