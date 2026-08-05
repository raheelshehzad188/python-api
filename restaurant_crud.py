"""Restaurant chatbot — CRUD for the admin dashboard.

Categories, Menu Items, Variations, Add-ons, Combo Deals, Promotions,
Customers, Orders, Payment Methods and FAQs. All isolated to
`restaurant_*` tables and gated to Restaurant-handler users.
"""

import re
from decimal import Decimal

from flask import Blueprint, jsonify, request

from db import Database
from gemini_cache import refresh_cache_after_instruction_change
from restaurant_settings import _is_restaurant_user, _ensure_user_defaults
from restaurant_schema import (
    CATEGORIES_TABLE,
    MENU_TABLE,
    VARIATIONS_TABLE,
    ADDONS_TABLE,
    COMBOS_TABLE,
    PROMOTIONS_TABLE,
    CUSTOMERS_TABLE,
    ORDERS_TABLE,
    ORDER_ITEMS_TABLE,
    PAYMENTS_TABLE,
    FAQS_TABLE,
    SETTINGS_TABLE,
    TABLES_TABLE,
    RESERVATIONS_TABLE,
)
from restaurant_order_service import (
    ORDER_TYPES,
    PAYMENT_STATUSES,
    create_order,
    _normalize_order_type,
    _normalize_status,
)

restaurant_crud_bp = Blueprint("restaurant_crud", __name__)

ORDER_STATUSES = [
    "pending",
    "confirmed",
    "preparing",
    "cooking",
    "ready",
    "out_for_delivery",
    "completed",
    "cancelled",
]

RESERVATION_STATUSES = [
    "pending",
    "confirmed",
    "checked_in",
    "completed",
    "cancelled",
    "no_show",
]

TABLE_AVAILABILITY = ["available", "occupied", "reserved"]

OCCASIONS = ["birthday", "anniversary", "business_meeting", "other"]


def _forbidden():
    return jsonify({"status": False, "message": "Not allowed"}), 403


def _gate(db, user_id):
    is_restaurant, _, _ = _is_restaurant_user(db, user_id)
    if not is_restaurant:
        return False, _forbidden()
    return True, None


# --------------------------------------------------------------------------- #
# Value helpers                                                               #
# --------------------------------------------------------------------------- #


def _num(value, default=0.0):
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool01(value, default=1):
    if value is None:
        return default
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    return 1 if str(value).strip().lower() in ("1", "true", "yes", "on") else 0


def _fmt_date(value):
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _validate_time(value):
    if value is None or value == "":
        return None
    raw = str(value).strip()
    if re.match(r"^\d{2}:\d{2}$", raw):
        return raw
    if re.match(r"^\d{2}:\d{2}:\d{2}$", raw):
        return raw[:5]
    return False


def _validate_date(value):
    if value is None or value == "":
        return None
    raw = str(value).strip()
    return raw if re.match(r"^\d{4}-\d{2}-\d{2}$", raw) else False


def _status_key(value):
    return _normalize_status(value)


def _find_menu_item(db, user_id, *, menu_item_id=None, name=None):
    if menu_item_id:
        row = db.row(MENU_TABLE, {"id": menu_item_id, "user_id": user_id})
        if row:
            return row
    if name:
        target = str(name).strip().lower()
        rows = db.select(MENU_TABLE, {"user_id": user_id}) or []
        for r in rows:
            if (r.get("name") or "").strip().lower() == target:
                return r
        for r in rows:
            if target in (r.get("name") or "").strip().lower():
                return r
    return None


def _find_variation(db, user_id, menu_item_id, *, variation_id=None, variation_name=None):
    rows = db.select(VARIATIONS_TABLE, {"user_id": user_id, "menu_item_id": menu_item_id}) or []
    if variation_id:
        for r in rows:
            if r["id"] == variation_id:
                return r
    if variation_name:
        target = str(variation_name).strip().lower()
        for r in rows:
            if (r.get("name") or "").strip().lower() == target:
                return r
    return None


def _find_addon(db, user_id, *, addon_id=None, addon_name=None):
    rows = db.select(ADDONS_TABLE, {"user_id": user_id}) or []
    if addon_id:
        for r in rows:
            if r["id"] == addon_id:
                return r
    if addon_name:
        target = str(addon_name).strip().lower()
        for r in rows:
            if (r.get("name") or "").strip().lower() == target:
                return r
    return None


def _owned(db, table, user_id, row_id):
    return db.row(table, {"id": row_id, "user_id": user_id})


# --------------------------------------------------------------------------- #
# Serializers                                                                 #
# --------------------------------------------------------------------------- #


def serialize_category(r):
    return {
        "id": r["id"],
        "name": r.get("name") or "",
        "description": r.get("description") or "",
        "sort_order": _int(r.get("sort_order")),
        "is_active": bool(r.get("is_active", 1)),
    }


def serialize_menu_item(r, category_name=""):
    return {
        "id": r["id"],
        "category_id": r.get("category_id"),
        "category_name": category_name or r.get("category_name") or "",
        "name": r.get("name") or "",
        "description": r.get("description") or "",
        "price": _num(r.get("price")),
        "prep_time_minutes": _int(r.get("prep_time_minutes")),
        "is_available": bool(r.get("is_available", 1)),
        "is_featured": bool(r.get("is_featured", 0)),
        "image_url": r.get("image_url") or "",
    }


def serialize_variation(r, item_name=""):
    return {
        "id": r["id"],
        "menu_item_id": r.get("menu_item_id"),
        "menu_item_name": item_name or r.get("menu_item_name") or "",
        "name": r.get("name") or "",
        "price_adjustment": _num(r.get("price_adjustment")),
        "sort_order": _int(r.get("sort_order")),
        "is_active": bool(r.get("is_active", 1)),
    }


def serialize_addon(r):
    return {
        "id": r["id"],
        "name": r.get("name") or "",
        "price": _num(r.get("price")),
        "sort_order": _int(r.get("sort_order")),
        "is_active": bool(r.get("is_active", 1)),
    }


def serialize_combo(r):
    return {
        "id": r["id"],
        "name": r.get("name") or "",
        "description": r.get("description") or "",
        "includes": r.get("includes") or "",
        "price": _num(r.get("price")),
        "is_active": bool(r.get("is_active", 1)),
    }


def serialize_promotion(r):
    return {
        "id": r["id"],
        "title": r.get("title") or "",
        "description": r.get("description") or "",
        "discount": r.get("discount") or "",
        "start_date": _fmt_date(r.get("start_date")),
        "end_date": _fmt_date(r.get("end_date")),
        "is_active": bool(r.get("is_active", 1)),
    }


def serialize_customer(r):
    return {
        "id": r["id"],
        "name": r.get("name") or "",
        "phone": r.get("phone") or "",
        "email": r.get("email") or "",
        "address": r.get("address") or "",
        "notes": r.get("notes") or "",
    }


def serialize_payment(r):
    return {
        "id": r["id"],
        "name": r.get("name") or "",
        "details": r.get("details") or "",
        "sort_order": _int(r.get("sort_order")),
        "is_active": bool(r.get("is_active", 1)),
    }


def serialize_faq(r):
    return {
        "id": r["id"],
        "question": r.get("question") or "",
        "answer": r.get("answer") or "",
    }


def serialize_order(db, r, currency="PKR"):
    items = db.select(ORDER_ITEMS_TABLE, {"order_id": r["id"]}) or []
    status = _status_key(r.get("status") or "pending")
    return {
        "id": r["id"],
        "order_number": r.get("order_number") or f"#{r['id']}",
        "customer_id": r.get("customer_id"),
        "customer_name": r.get("customer_name") or "",
        "phone": r.get("phone") or "",
        "email": r.get("email") or "",
        "order_type": r.get("order_type") or "delivery",
        "address": r.get("address") or "",
        "table_id": r.get("table_id"),
        "table_number": r.get("table_number") or "",
        "guests": _int(r.get("guests")),
        "delivery_time": r.get("delivery_time") or "",
        "pickup_time": r.get("pickup_time") or "",
        "payment_method": r.get("payment_method") or "",
        "payment_status": r.get("payment_status") or "pending",
        "status": status,
        "subtotal": _num(r.get("subtotal")),
        "tax": _num(r.get("tax")),
        "discount": _num(r.get("discount")),
        "service_charges": _num(r.get("service_charges")),
        "delivery_charges": _num(r.get("delivery_charges")),
        "total": _num(r.get("total")),
        "currency": currency,
        "coupon_code": r.get("coupon_code") or "",
        "assigned_driver": r.get("assigned_driver") or "",
        "assigned_waiter": r.get("assigned_waiter") or "",
        "assigned_kitchen_staff": r.get("assigned_kitchen_staff") or "",
        "notes": r.get("notes") or "",
        "customer_notes": r.get("customer_notes") or r.get("notes") or "",
        "internal_notes": r.get("internal_notes") or "",
        "source": r.get("source") or "manual",
        "created_at": _fmt_date(r.get("created_at")) if r.get("created_at") else "",
        "items": [
            {
                "id": it["id"],
                "menu_item_id": it.get("menu_item_id"),
                "item_name": it.get("item_name") or "",
                "variation_name": it.get("variation_name") or "",
                "addons": it.get("addons") or "",
                "item_notes": it.get("item_notes") or "",
                "unit_price": _num(it.get("unit_price")),
                "quantity": _int(it.get("quantity"), 1),
                "line_total": _num(it.get("line_total")),
            }
            for it in items
        ],
    }


def serialize_table(r):
    return {
        "id": r["id"],
        "table_number": r.get("table_number") or "",
        "capacity": _int(r.get("capacity"), 2),
        "location": r.get("location") or "",
        "floor": r.get("floor") or "",
        "availability": r.get("availability") or "available",
        "sort_order": _int(r.get("sort_order")),
    }


def serialize_reservation(r):
    time_val = r.get("reservation_time")
    time_str = ""
    if time_val is not None:
        time_str = time_val.strftime("%H:%M") if hasattr(time_val, "strftime") else str(time_val)[:5]
    return {
        "id": r["id"],
        "reservation_number": r.get("reservation_number") or f"RES-{r['id']}",
        "customer_id": r.get("customer_id"),
        "customer_name": r.get("customer_name") or "",
        "phone": r.get("phone") or "",
        "email": r.get("email") or "",
        "guests": _int(r.get("guests"), 2),
        "reservation_date": _fmt_date(r.get("reservation_date")),
        "reservation_time": time_str,
        "table_id": r.get("table_id"),
        "table_number": r.get("table_number") or "",
        "occasion": r.get("occasion") or "",
        "special_notes": r.get("special_notes") or "",
        "status": r.get("status") or "pending",
        "created_at": _fmt_date(r.get("created_at")) if r.get("created_at") else "",
    }


# --------------------------------------------------------------------------- #
# Generic simple CRUD factory                                                 #
# --------------------------------------------------------------------------- #


def _register_simple_crud(slug, table, serializer, plural_key, prepare, singular_label):
    """Register list/create/update/delete routes for a simple resource."""

    def _list(user_id):
        db = Database()
        try:
            ok, err = _gate(db, user_id)
            if not ok:
                return err
            rows = db.select(table, {"user_id": user_id})
        finally:
            db.close()
        return jsonify({"status": True, plural_key: [serializer(r) for r in rows]})

    def _create(user_id):
        data = request.json or {}
        payload, error = prepare(data, for_create=True)
        if error:
            return jsonify({"status": False, "message": error}), 400
        db = Database()
        try:
            ok, err = _gate(db, user_id)
            if not ok:
                return err
            _ensure_user_defaults(db, user_id)
            payload["user_id"] = user_id
            new_id = db.insert(table, payload)
            refresh_cache_after_instruction_change(db, user_id)
            row = db.row(table, {"id": new_id})
        finally:
            db.close()
        return jsonify({"status": True, "message": f"{singular_label} created", "item": serializer(row)})

    def _update(user_id, item_id):
        data = request.json or {}
        payload, error = prepare(data, for_create=False)
        if error:
            return jsonify({"status": False, "message": error}), 400
        if not payload:
            return jsonify({"status": False, "message": "Nothing to update"}), 400
        db = Database()
        try:
            ok, err = _gate(db, user_id)
            if not ok:
                return err
            if not _owned(db, table, user_id, item_id):
                return jsonify({"status": False, "message": f"{singular_label} not found"}), 404
            db.update(table, payload, {"id": item_id, "user_id": user_id})
            refresh_cache_after_instruction_change(db, user_id)
            row = db.row(table, {"id": item_id})
        finally:
            db.close()
        return jsonify({"status": True, "message": f"{singular_label} updated", "item": serializer(row)})

    def _delete(user_id, item_id):
        db = Database()
        try:
            ok, err = _gate(db, user_id)
            if not ok:
                return err
            if not _owned(db, table, user_id, item_id):
                return jsonify({"status": False, "message": f"{singular_label} not found"}), 404
            db.delete(table, {"id": item_id, "user_id": user_id})
            refresh_cache_after_instruction_change(db, user_id)
        finally:
            db.close()
        return jsonify({"status": True, "message": f"{singular_label} deleted"})

    base = f"/users/<int:user_id>/restaurant-{slug}"
    restaurant_crud_bp.add_url_rule(base, f"rest_list_{slug}", _list, methods=["GET"])
    restaurant_crud_bp.add_url_rule(base, f"rest_create_{slug}", _create, methods=["POST"])
    restaurant_crud_bp.add_url_rule(
        f"{base}/<int:item_id>", f"rest_update_{slug}", _update, methods=["PUT"]
    )
    restaurant_crud_bp.add_url_rule(
        f"{base}/<int:item_id>", f"rest_delete_{slug}", _delete, methods=["DELETE"]
    )


# --------------------------------------------------------------------------- #
# Prepare functions                                                           #
# --------------------------------------------------------------------------- #


def _prepare_category(data, for_create=False):
    payload = {}
    if "name" in data:
        payload["name"] = (data.get("name") or "").strip()
    if "description" in data:
        payload["description"] = (data.get("description") or "").strip()
    if "sort_order" in data:
        payload["sort_order"] = _int(data.get("sort_order"))
    if "is_active" in data:
        payload["is_active"] = _bool01(data.get("is_active"))
    if for_create and not payload.get("name"):
        return None, "name is required"
    return payload, None


def _prepare_addon(data, for_create=False):
    payload = {}
    if "name" in data:
        payload["name"] = (data.get("name") or "").strip()
    if "price" in data:
        payload["price"] = _num(data.get("price"))
    if "sort_order" in data:
        payload["sort_order"] = _int(data.get("sort_order"))
    if "is_active" in data:
        payload["is_active"] = _bool01(data.get("is_active"))
    if for_create and not payload.get("name"):
        return None, "name is required"
    return payload, None


def _prepare_combo(data, for_create=False):
    payload = {}
    if "name" in data:
        payload["name"] = (data.get("name") or "").strip()
    if "description" in data:
        payload["description"] = (data.get("description") or "").strip()
    if "includes" in data:
        payload["includes"] = (data.get("includes") or "").strip()
    if "price" in data:
        payload["price"] = _num(data.get("price"))
    if "is_active" in data:
        payload["is_active"] = _bool01(data.get("is_active"))
    if for_create and not payload.get("name"):
        return None, "name is required"
    return payload, None


def _prepare_promotion(data, for_create=False):
    payload = {}
    if "title" in data:
        payload["title"] = (data.get("title") or "").strip()
    if "description" in data:
        payload["description"] = (data.get("description") or "").strip()
    if "discount" in data:
        payload["discount"] = (data.get("discount") or "").strip()
    for key in ("start_date", "end_date"):
        if key in data:
            checked = _validate_date(data.get(key))
            if checked is False:
                return None, f"{key} must be YYYY-MM-DD"
            payload[key] = checked
    if "is_active" in data:
        payload["is_active"] = _bool01(data.get("is_active"))
    if for_create and not payload.get("title"):
        return None, "title is required"
    return payload, None


def _prepare_customer(data, for_create=False):
    payload = {}
    for key in ("name", "phone", "email", "address", "notes"):
        if key in data:
            payload[key] = (data.get(key) or "").strip()
    if for_create and not payload.get("name"):
        return None, "name is required"
    return payload, None


def _prepare_payment(data, for_create=False):
    payload = {}
    if "name" in data:
        payload["name"] = (data.get("name") or "").strip()
    if "details" in data:
        payload["details"] = (data.get("details") or "").strip()
    if "sort_order" in data:
        payload["sort_order"] = _int(data.get("sort_order"))
    if "is_active" in data:
        payload["is_active"] = _bool01(data.get("is_active"))
    if for_create and not payload.get("name"):
        return None, "name is required"
    return payload, None


def _prepare_faq(data, for_create=False):
    payload = {}
    if "question" in data:
        payload["question"] = (data.get("question") or "").strip()
    if "answer" in data:
        payload["answer"] = (data.get("answer") or "").strip()
    if for_create and (not payload.get("question") or not payload.get("answer")):
        return None, "question and answer are required"
    return payload, None


_register_simple_crud("categories", CATEGORIES_TABLE, serialize_category, "categories", _prepare_category, "Category")
_register_simple_crud("addons", ADDONS_TABLE, serialize_addon, "addons", _prepare_addon, "Add-on")
_register_simple_crud("combos", COMBOS_TABLE, serialize_combo, "combos", _prepare_combo, "Combo deal")
_register_simple_crud("promotions", PROMOTIONS_TABLE, serialize_promotion, "promotions", _prepare_promotion, "Promotion")
_register_simple_crud("customers", CUSTOMERS_TABLE, serialize_customer, "customers", _prepare_customer, "Customer")
_register_simple_crud("payments", PAYMENTS_TABLE, serialize_payment, "payments", _prepare_payment, "Payment method")
_register_simple_crud("faqs", FAQS_TABLE, serialize_faq, "faqs", _prepare_faq, "FAQ")


# --------------------------------------------------------------------------- #
# Menu items (needs category name join + validation)                         #
# --------------------------------------------------------------------------- #


def _category_map(db, user_id):
    return {c["id"]: c.get("name") or "" for c in db.select(CATEGORIES_TABLE, {"user_id": user_id})}


def _prepare_menu_item(data, for_create=False):
    payload = {}
    if "name" in data:
        payload["name"] = (data.get("name") or "").strip()
    if "description" in data:
        payload["description"] = (data.get("description") or "").strip()
    if "category_id" in data:
        payload["category_id"] = _int(data.get("category_id")) or None
    if "price" in data:
        payload["price"] = _num(data.get("price"))
    if "prep_time_minutes" in data:
        payload["prep_time_minutes"] = _int(data.get("prep_time_minutes"))
    if "is_available" in data:
        payload["is_available"] = _bool01(data.get("is_available"))
    if "is_featured" in data:
        payload["is_featured"] = _bool01(data.get("is_featured"), default=0)
    if "image_url" in data:
        payload["image_url"] = (data.get("image_url") or "").strip()
    if for_create and not payload.get("name"):
        return None, "name is required"
    return payload, None


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-menu", methods=["GET"])
def list_menu(user_id):
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        cats = _category_map(db, user_id)
        rows = db.select(MENU_TABLE, {"user_id": user_id})
        items = [serialize_menu_item(r, cats.get(r.get("category_id"), "")) for r in rows]
    finally:
        db.close()
    return jsonify({"status": True, "menu": items})


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-menu", methods=["POST"])
def create_menu(user_id):
    data = request.json or {}
    payload, error = _prepare_menu_item(data, for_create=True)
    if error:
        return jsonify({"status": False, "message": error}), 400
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        _ensure_user_defaults(db, user_id)
        payload["user_id"] = user_id
        new_id = db.insert(MENU_TABLE, payload)
        refresh_cache_after_instruction_change(db, user_id)
        cats = _category_map(db, user_id)
        row = db.row(MENU_TABLE, {"id": new_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Menu item created", "item": serialize_menu_item(row, cats.get(row.get("category_id"), ""))})


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-menu/<int:item_id>", methods=["PUT"])
def update_menu(user_id, item_id):
    data = request.json or {}
    payload, error = _prepare_menu_item(data, for_create=False)
    if error:
        return jsonify({"status": False, "message": error}), 400
    if not payload:
        return jsonify({"status": False, "message": "Nothing to update"}), 400
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        if not _owned(db, MENU_TABLE, user_id, item_id):
            return jsonify({"status": False, "message": "Menu item not found"}), 404
        db.update(MENU_TABLE, payload, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
        cats = _category_map(db, user_id)
        row = db.row(MENU_TABLE, {"id": item_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Menu item updated", "item": serialize_menu_item(row, cats.get(row.get("category_id"), ""))})


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-menu/<int:item_id>", methods=["DELETE"])
def delete_menu(user_id, item_id):
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        if not _owned(db, MENU_TABLE, user_id, item_id):
            return jsonify({"status": False, "message": "Menu item not found"}), 404
        db.delete(MENU_TABLE, {"id": item_id, "user_id": user_id})
        db.execute(f"DELETE FROM {VARIATIONS_TABLE} WHERE menu_item_id=%s AND user_id=%s", [item_id, user_id])
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Menu item deleted"})


# --------------------------------------------------------------------------- #
# Variations (linked to a menu item)                                          #
# --------------------------------------------------------------------------- #


def _menu_map(db, user_id):
    return {m["id"]: m.get("name") or "" for m in db.select(MENU_TABLE, {"user_id": user_id})}


def _prepare_variation(data, for_create=False):
    payload = {}
    if "name" in data:
        payload["name"] = (data.get("name") or "").strip()
    if "menu_item_id" in data:
        payload["menu_item_id"] = _int(data.get("menu_item_id")) or None
    if "price_adjustment" in data:
        payload["price_adjustment"] = _num(data.get("price_adjustment"))
    if "sort_order" in data:
        payload["sort_order"] = _int(data.get("sort_order"))
    if "is_active" in data:
        payload["is_active"] = _bool01(data.get("is_active"))
    if for_create and not payload.get("name"):
        return None, "name is required"
    if for_create and not payload.get("menu_item_id"):
        return None, "menu_item_id is required"
    return payload, None


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-variations", methods=["GET"])
def list_variations(user_id):
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        menu = _menu_map(db, user_id)
        rows = db.select(VARIATIONS_TABLE, {"user_id": user_id})
        items = [serialize_variation(r, menu.get(r.get("menu_item_id"), "")) for r in rows]
    finally:
        db.close()
    return jsonify({"status": True, "variations": items})


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-variations", methods=["POST"])
def create_variation(user_id):
    data = request.json or {}
    payload, error = _prepare_variation(data, for_create=True)
    if error:
        return jsonify({"status": False, "message": error}), 400
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        _ensure_user_defaults(db, user_id)
        payload["user_id"] = user_id
        new_id = db.insert(VARIATIONS_TABLE, payload)
        refresh_cache_after_instruction_change(db, user_id)
        menu = _menu_map(db, user_id)
        row = db.row(VARIATIONS_TABLE, {"id": new_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Variation created", "item": serialize_variation(row, menu.get(row.get("menu_item_id"), ""))})


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-variations/<int:item_id>", methods=["PUT"])
def update_variation(user_id, item_id):
    data = request.json or {}
    payload, error = _prepare_variation(data, for_create=False)
    if error:
        return jsonify({"status": False, "message": error}), 400
    if not payload:
        return jsonify({"status": False, "message": "Nothing to update"}), 400
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        if not _owned(db, VARIATIONS_TABLE, user_id, item_id):
            return jsonify({"status": False, "message": "Variation not found"}), 404
        db.update(VARIATIONS_TABLE, payload, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
        menu = _menu_map(db, user_id)
        row = db.row(VARIATIONS_TABLE, {"id": item_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Variation updated", "item": serialize_variation(row, menu.get(row.get("menu_item_id"), ""))})


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-variations/<int:item_id>", methods=["DELETE"])
def delete_variation(user_id, item_id):
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        if not _owned(db, VARIATIONS_TABLE, user_id, item_id):
            return jsonify({"status": False, "message": "Variation not found"}), 404
        db.delete(VARIATIONS_TABLE, {"id": item_id, "user_id": user_id})
        refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Variation deleted"})


def _currency(db, user_id):
    row = db.row(SETTINGS_TABLE, {"user_id": user_id}) or {}
    return (row.get("currency_code") or "PKR").strip() or "PKR"


# --------------------------------------------------------------------------- #
# Orders                                                                      #
# --------------------------------------------------------------------------- #


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-orders", methods=["GET"])
def list_orders(user_id):
    status_filter = (request.args.get("status") or "").strip().lower()
    order_type = (request.args.get("order_type") or "").strip().lower()
    payment_status = (request.args.get("payment_status") or "").strip().lower()
    date_filter = (request.args.get("date") or "").strip()
    search = (request.args.get("search") or "").strip().lower()
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        sql = f"SELECT * FROM {ORDERS_TABLE} WHERE user_id=%s"
        params = [user_id]
        if status_filter:
            norm = _status_key(status_filter)
            sql += " AND (status=%s OR status=%s)"
            params.extend([norm, status_filter])
        if order_type:
            sql += " AND order_type=%s"
            params.append(_normalize_order_type(order_type))
        if payment_status:
            sql += " AND payment_status=%s"
            params.append(payment_status)
        if date_filter:
            sql += " AND DATE(created_at)=%s"
            params.append(date_filter)
        if search:
            sql += (
                " AND (LOWER(customer_name) LIKE %s OR phone LIKE %s "
                "OR LOWER(order_number) LIKE %s OR CAST(id AS CHAR) LIKE %s)"
            )
            like = f"%{search}%"
            params.extend([like, like, like, like])
        sql += " ORDER BY created_at DESC, id DESC"
        db.cursor.execute(sql, params)
        rows = db.cursor.fetchall()
        currency = _currency(db, user_id)
        orders = [serialize_order(db, r, currency) for r in rows]
    finally:
        db.close()
    return jsonify({
        "status": True,
        "orders": orders,
        "statuses": ORDER_STATUSES,
        "order_types": list(ORDER_TYPES),
        "payment_statuses": list(PAYMENT_STATUSES),
    })


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-orders", methods=["POST"])
def create_order_route(user_id):
    data = request.json or {}
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        _ensure_user_defaults(db, user_id)
        try:
            row, currency = create_order(
                db,
                user_id,
                data,
                find_menu_item=_find_menu_item,
                find_variation=_find_variation,
                find_addon=_find_addon,
                source=data.get("source") or "manual",
                enforce_minimum=False,
                require_payment_method=bool(data.get("payment_method")),
            )
        except ValueError as exc:
            return jsonify({"status": False, "message": str(exc)}), 400
        order = serialize_order(db, row, currency)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Order created", "order": order})


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-orders/<int:order_id>/duplicate", methods=["POST"])
def duplicate_order(user_id, order_id):
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        row = _owned(db, ORDERS_TABLE, user_id, order_id)
        if not row:
            return jsonify({"status": False, "message": "Order not found"}), 404
        items = db.select(ORDER_ITEMS_TABLE, {"order_id": order_id}) or []
        payload = {
            "customer_id": row.get("customer_id"),
            "customer_name": row.get("customer_name"),
            "phone": row.get("phone"),
            "email": row.get("email"),
            "order_type": row.get("order_type"),
            "address": row.get("address"),
            "table_id": row.get("table_id"),
            "table_number": row.get("table_number"),
            "guests": row.get("guests"),
            "delivery_time": row.get("delivery_time"),
            "pickup_time": row.get("pickup_time"),
            "payment_method": row.get("payment_method"),
            "payment_status": "pending",
            "status": "pending",
            "discount": _num(row.get("discount")),
            "tax": _num(row.get("tax")),
            "service_charges": _num(row.get("service_charges")),
            "delivery_charges": _num(row.get("delivery_charges")),
            "coupon_code": row.get("coupon_code"),
            "assigned_driver": row.get("assigned_driver"),
            "assigned_waiter": row.get("assigned_waiter"),
            "assigned_kitchen_staff": row.get("assigned_kitchen_staff"),
            "customer_notes": row.get("customer_notes") or row.get("notes"),
            "internal_notes": row.get("internal_notes"),
            "items": [
                {
                    "menu_item_id": it.get("menu_item_id"),
                    "item_name": it.get("item_name"),
                    "variation_name": it.get("variation_name"),
                    "addons": it.get("addons"),
                    "quantity": it.get("quantity"),
                    "item_notes": it.get("item_notes"),
                }
                for it in items
            ],
        }
        try:
            new_row, currency = create_order(
                db,
                user_id,
                payload,
                find_menu_item=_find_menu_item,
                find_variation=_find_variation,
                find_addon=_find_addon,
                source="manual",
                enforce_minimum=False,
                require_payment_method=bool(payload.get("payment_method")),
            )
        except ValueError as exc:
            return jsonify({"status": False, "message": str(exc)}), 400
        order = serialize_order(db, new_row, currency)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Order duplicated", "order": order})


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-orders/<int:order_id>", methods=["GET"])
def get_order(user_id, order_id):
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        row = _owned(db, ORDERS_TABLE, user_id, order_id)
        if not row:
            return jsonify({"status": False, "message": "Order not found"}), 404
        order = serialize_order(db, row, _currency(db, user_id))
    finally:
        db.close()
    return jsonify({"status": True, "order": order})


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-orders/<int:order_id>", methods=["PUT"])
def update_order(user_id, order_id):
    data = request.json or {}
    payload = {}
    if "status" in data:
        status = _status_key(data.get("status"))
        if status not in ORDER_STATUSES:
            return jsonify({"status": False, "message": f"status must be one of {ORDER_STATUSES}"}), 400
        payload["status"] = status
    if "payment_status" in data:
        ps = (data.get("payment_status") or "").strip().lower()
        if ps not in PAYMENT_STATUSES:
            return jsonify({"status": False, "message": f"payment_status must be one of {PAYMENT_STATUSES}"}), 400
        payload["payment_status"] = ps
    if "order_type" in data:
        payload["order_type"] = _normalize_order_type(data.get("order_type"))
    for key in (
        "customer_name", "phone", "email", "address", "payment_method", "notes",
        "customer_notes", "internal_notes", "table_number", "delivery_time", "pickup_time",
        "coupon_code", "assigned_driver", "assigned_waiter", "assigned_kitchen_staff",
    ):
        if key in data:
            payload[key] = (data.get(key) or "").strip()
    if "guests" in data:
        payload["guests"] = max(0, _int(data.get("guests")))
    if "table_id" in data:
        payload["table_id"] = _int(data.get("table_id")) or None
    if not payload:
        return jsonify({"status": False, "message": "Nothing to update"}), 400

    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        if not _owned(db, ORDERS_TABLE, user_id, order_id):
            return jsonify({"status": False, "message": "Order not found"}), 404
        db.update(ORDERS_TABLE, payload, {"id": order_id, "user_id": user_id})
        row = db.row(ORDERS_TABLE, {"id": order_id})
        order = serialize_order(db, row, _currency(db, user_id))
    finally:
        db.close()
    return jsonify({"status": True, "message": "Order updated", "order": order})


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-orders/<int:order_id>", methods=["DELETE"])
def delete_order(user_id, order_id):
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        if not _owned(db, ORDERS_TABLE, user_id, order_id):
            return jsonify({"status": False, "message": "Order not found"}), 404
        db.execute(f"DELETE FROM {ORDER_ITEMS_TABLE} WHERE order_id=%s", [order_id])
        db.delete(ORDERS_TABLE, {"id": order_id, "user_id": user_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Order deleted"})


# --------------------------------------------------------------------------- #
# Customer detail (history, reservations, lifetime spend)                    #
# --------------------------------------------------------------------------- #


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-customers/<int:customer_id>/detail", methods=["GET"])
def customer_detail(user_id, customer_id):
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        customer = _owned(db, CUSTOMERS_TABLE, user_id, customer_id)
        if not customer:
            return jsonify({"status": False, "message": "Customer not found"}), 404
        currency = _currency(db, user_id)
        db.cursor.execute(
            f"SELECT * FROM {ORDERS_TABLE} WHERE user_id=%s AND customer_id=%s ORDER BY created_at DESC",
            [user_id, customer_id],
        )
        orders = [serialize_order(db, r, currency) for r in db.cursor.fetchall()]
        db.cursor.execute(
            f"SELECT * FROM {RESERVATIONS_TABLE} WHERE user_id=%s AND customer_id=%s ORDER BY reservation_date DESC, reservation_time DESC",
            [user_id, customer_id],
        )
        reservations = [serialize_reservation(r) for r in db.cursor.fetchall()]
        lifetime_spend = round(sum(_num(o.get("total")) for o in orders), 2)
    finally:
        db.close()
    return jsonify({
        "status": True,
        "customer": serialize_customer(customer),
        "orders": orders,
        "reservations": reservations,
        "lifetime_spend": lifetime_spend,
        "currency": currency,
    })


# --------------------------------------------------------------------------- #
# Tables                                                                      #
# --------------------------------------------------------------------------- #


def _prepare_table(data, for_create=False):
    payload = {}
    if "table_number" in data:
        payload["table_number"] = (data.get("table_number") or "").strip()
    if "capacity" in data:
        payload["capacity"] = max(1, _int(data.get("capacity"), 2))
    if "location" in data:
        payload["location"] = (data.get("location") or "").strip()
    if "floor" in data:
        payload["floor"] = (data.get("floor") or "").strip()
    if "availability" in data:
        avail = (data.get("availability") or "available").strip().lower()
        if avail not in TABLE_AVAILABILITY:
            return None, f"availability must be one of {TABLE_AVAILABILITY}"
        payload["availability"] = avail
    if "sort_order" in data:
        payload["sort_order"] = _int(data.get("sort_order"))
    if for_create and not payload.get("table_number"):
        return None, "table_number is required"
    return payload, None


_register_simple_crud("tables", TABLES_TABLE, serialize_table, "tables", _prepare_table, "Table")


# --------------------------------------------------------------------------- #
# Reservations                                                                #
# --------------------------------------------------------------------------- #


def _next_reservation_number(db, user_id):
    db.cursor.execute(f"SELECT COUNT(*) AS c FROM {RESERVATIONS_TABLE} WHERE user_id=%s", [user_id])
    count = int((db.cursor.fetchone() or {}).get("c") or 0)
    return f"RES-{user_id}-{count + 1:05d}"


def _reservation_conflict(db, user_id, table_id, reservation_date, reservation_time, exclude_id=None):
    if not table_id:
        return None
    sql = (
        f"SELECT * FROM {RESERVATIONS_TABLE} WHERE user_id=%s AND table_id=%s "
        "AND reservation_date=%s AND reservation_time=%s AND status NOT IN ('cancelled','no_show','completed')"
    )
    params = [user_id, table_id, reservation_date, reservation_time]
    if exclude_id:
        sql += " AND id<>%s"
        params.append(exclude_id)
    db.cursor.execute(sql, params)
    return db.cursor.fetchone()


def _prepare_reservation(data, for_create=False):
    payload = {}
    if "customer_name" in data:
        payload["customer_name"] = (data.get("customer_name") or "").strip()
    for key in ("phone", "email", "table_number", "special_notes"):
        if key in data:
            payload[key] = (data.get(key) or "").strip()
    if "guests" in data:
        guests = _int(data.get("guests"), 2)
        if guests < 1:
            return None, "guests must be at least 1"
        payload["guests"] = guests
    if "reservation_date" in data:
        checked = _validate_date(data.get("reservation_date"))
        if checked is False:
            return None, "reservation_date must be YYYY-MM-DD"
        payload["reservation_date"] = checked
    if "reservation_time" in data:
        checked = _validate_time(data.get("reservation_time"))
        if checked is False:
            return None, "reservation_time must be HH:MM"
        payload["reservation_time"] = checked
    if "table_id" in data:
        payload["table_id"] = _int(data.get("table_id")) or None
    if "customer_id" in data:
        payload["customer_id"] = _int(data.get("customer_id")) or None
    if "occasion" in data:
        occ = (data.get("occasion") or "").strip().lower().replace(" ", "_")
        if occ and occ not in OCCASIONS:
            return None, f"occasion must be one of {OCCASIONS}"
        payload["occasion"] = occ or None
    if "status" in data:
        status = (data.get("status") or "pending").strip().lower().replace(" ", "_")
        if status not in RESERVATION_STATUSES:
            return None, f"status must be one of {RESERVATION_STATUSES}"
        payload["status"] = status
    if for_create and not payload.get("customer_name"):
        return None, "customer_name is required"
    if for_create and not payload.get("reservation_date"):
        return None, "reservation_date is required"
    if for_create and not payload.get("reservation_time"):
        return None, "reservation_time is required"
    return payload, None


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-reservations", methods=["GET"])
def list_reservations(user_id):
    status_filter = (request.args.get("status") or "").strip().lower()
    date_filter = (request.args.get("date") or "").strip()
    search = (request.args.get("search") or "").strip().lower()
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        sql = f"SELECT * FROM {RESERVATIONS_TABLE} WHERE user_id=%s"
        params = [user_id]
        if status_filter:
            sql += " AND status=%s"
            params.append(status_filter)
        if date_filter:
            sql += " AND reservation_date=%s"
            params.append(date_filter)
        if search:
            sql += (
                " AND (LOWER(customer_name) LIKE %s OR phone LIKE %s "
                "OR LOWER(reservation_number) LIKE %s OR CAST(id AS CHAR) LIKE %s)"
            )
            like = f"%{search}%"
            params.extend([like, like, like, like])
        sql += " ORDER BY reservation_date DESC, reservation_time DESC"
        db.cursor.execute(sql, params)
        rows = db.cursor.fetchall()
        reservations = [serialize_reservation(r) for r in rows]
    finally:
        db.close()
    return jsonify({
        "status": True,
        "reservations": reservations,
        "statuses": RESERVATION_STATUSES,
        "occasions": OCCASIONS,
    })


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-reservations", methods=["POST"])
def create_reservation(user_id):
    data = request.json or {}
    payload, error = _prepare_reservation(data, for_create=True)
    if error:
        return jsonify({"status": False, "message": error}), 400
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        _ensure_user_defaults(db, user_id)
        if payload.get("table_id"):
            table = _owned(db, TABLES_TABLE, user_id, payload["table_id"])
            if not table:
                return jsonify({"status": False, "message": "Table not found"}), 404
            payload["table_number"] = table.get("table_number") or payload.get("table_number")
            if payload["guests"] > _int(table.get("capacity"), 2):
                return jsonify({"status": False, "message": "Guests exceed table capacity"}), 400
        conflict = _reservation_conflict(
            db, user_id, payload.get("table_id"), payload["reservation_date"], payload["reservation_time"]
        )
        if conflict:
            return jsonify({"status": False, "message": "Table already reserved for this date and time"}), 400
        payload["user_id"] = user_id
        payload["reservation_number"] = _next_reservation_number(db, user_id)
        if payload.get("customer_id"):
            cust = _owned(db, CUSTOMERS_TABLE, user_id, payload["customer_id"])
            if cust:
                payload["customer_name"] = cust.get("name") or payload["customer_name"]
                payload["phone"] = payload.get("phone") or cust.get("phone")
                payload["email"] = payload.get("email") or cust.get("email")
        new_id = db.insert(RESERVATIONS_TABLE, payload)
        if payload.get("table_id") and payload.get("status") in ("confirmed", "checked_in"):
            db.update(TABLES_TABLE, {"availability": "reserved"}, {"id": payload["table_id"], "user_id": user_id})
        row = db.row(RESERVATIONS_TABLE, {"id": new_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Reservation created", "item": serialize_reservation(row)})


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-reservations/<int:item_id>", methods=["PUT"])
def update_reservation(user_id, item_id):
    data = request.json or {}
    payload, error = _prepare_reservation(data, for_create=False)
    if error:
        return jsonify({"status": False, "message": error}), 400
    if not payload:
        return jsonify({"status": False, "message": "Nothing to update"}), 400
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        existing = _owned(db, RESERVATIONS_TABLE, user_id, item_id)
        if not existing:
            return jsonify({"status": False, "message": "Reservation not found"}), 404
        table_id = payload.get("table_id", existing.get("table_id"))
        res_date = payload.get("reservation_date", existing.get("reservation_date"))
        res_time = payload.get("reservation_time", existing.get("reservation_time"))
        if hasattr(res_time, "strftime"):
            res_time = res_time.strftime("%H:%M")
        elif res_time:
            res_time = str(res_time)[:5]
        conflict = _reservation_conflict(db, user_id, table_id, res_date, res_time, exclude_id=item_id)
        if conflict:
            return jsonify({"status": False, "message": "Table already reserved for this date and time"}), 400
        db.update(RESERVATIONS_TABLE, payload, {"id": item_id, "user_id": user_id})
        row = db.row(RESERVATIONS_TABLE, {"id": item_id})
        if table_id and row.get("status") in ("confirmed", "checked_in"):
            db.update(TABLES_TABLE, {"availability": "reserved"}, {"id": table_id, "user_id": user_id})
        elif table_id and row.get("status") in ("cancelled", "no_show", "completed"):
            db.update(TABLES_TABLE, {"availability": "available"}, {"id": table_id, "user_id": user_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Reservation updated", "item": serialize_reservation(row)})


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-reservations/<int:item_id>", methods=["DELETE"])
def delete_reservation(user_id, item_id):
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        row = _owned(db, RESERVATIONS_TABLE, user_id, item_id)
        if not row:
            return jsonify({"status": False, "message": "Reservation not found"}), 404
        if row.get("table_id"):
            db.update(TABLES_TABLE, {"availability": "available"}, {"id": row["table_id"], "user_id": user_id})
        db.delete(RESERVATIONS_TABLE, {"id": item_id, "user_id": user_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Reservation deleted"})


@restaurant_crud_bp.route("/users/<int:user_id>/restaurant-calendar", methods=["GET"])
def calendar_data(user_id):
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()
    db = Database()
    try:
        ok, err = _gate(db, user_id)
        if not ok:
            return err
        sql = f"SELECT * FROM {RESERVATIONS_TABLE} WHERE user_id=%s"
        params = [user_id]
        if start:
            sql += " AND reservation_date>=%s"
            params.append(start)
        if end:
            sql += " AND reservation_date<=%s"
            params.append(end)
        sql += " ORDER BY reservation_date, reservation_time"
        db.cursor.execute(sql, params)
        reservations = [serialize_reservation(r) for r in db.cursor.fetchall()]
        tables = [serialize_table(r) for r in db.select(TABLES_TABLE, {"user_id": user_id}) or []]
        db.cursor.execute(
            f"SELECT * FROM {ORDERS_TABLE} WHERE user_id=%s AND order_type='dine_in'",
            [user_id],
        )
        dine_in_orders = [serialize_order(db, r, _currency(db, user_id)) for r in db.cursor.fetchall()]
    finally:
        db.close()
    return jsonify({
        "status": True,
        "reservations": reservations,
        "tables": tables,
        "dine_in_orders": dine_in_orders,
    })

