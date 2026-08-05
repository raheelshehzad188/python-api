"""Clone a chatbot user account with business data (not WhatsApp sessions/chats)."""

from __future__ import annotations

import hashlib
import re

from db import Database
from services_settings import HANDLER_NAME as SERVICES_HANDLER
from restaurant_settings import HANDLER_NAME as RESTAURANT_HANDLER

SKIP_META_KEYS = frozenset({
    "wa_session",
    "wa_webhook_token",
    "wa_reply_api_key",
    "gemini_cache_id",
})


def _md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()


def _strip_row(row, drop=None):
    drop = drop or frozenset()
    return {
        k: v
        for k, v in row.items()
        if k not in drop and k not in ("id", "created_at", "updated_at")
    }


def _handler_class(db, user_id):
    meta_rows = db.select("user_meta", {"user_id": user_id})
    meta = {m["meta_key"]: m["meta_value"] for m in meta_rows}
    type_id = meta.get("chatbot_type_id")
    ctype = db.row("chatbot_types", {"id": type_id}) if type_id else None
    return (ctype or {}).get("handler_class") or "", meta


def _copy_simple(db, table, source_id, target_id):
    count = 0
    for row in db.select(table, {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        db.insert(table, payload)
        count += 1
    return count


def _clone_services_data(db, source_id, target_id):
    copied = {}

    src_settings = db.row("services_settings", {"user_id": source_id})
    if src_settings:
        payload = _strip_row(src_settings)
        payload["user_id"] = target_id
        db.insert("services_settings", payload)
        copied["services_settings"] = 1

    for table in (
        "services_working_hours",
        "services_holidays",
        "services_payment_methods",
        "services_policies",
        "services_faqs",
        "services_packages",
        "services_promotions",
        "services_memberships",
        "services_products",
        "services_notifications",
    ):
        copied[table] = _copy_simple(db, table, source_id, target_id)

    cat_map = {}
    for row in db.select("services_categories", {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        new_id = db.insert("services_categories", payload)
        cat_map[row["id"]] = new_id
    copied["services_categories"] = len(cat_map)

    svc_map = {}
    for row in db.select("services_catalog", {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        if payload.get("category_id"):
            payload["category_id"] = cat_map.get(payload["category_id"])
        new_id = db.insert("services_catalog", payload)
        svc_map[row["id"]] = new_id
    copied["services_catalog"] = len(svc_map)

    # Fix related_service_ids after all services exist
    for row in db.select("services_catalog", {"user_id": target_id}) or []:
        raw = (row.get("related_service_ids") or "").strip()
        if not raw:
            continue
        parts = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                old_id = int(part)
            except ValueError:
                parts.append(part)
                continue
            if old_id in svc_map:
                parts.append(str(svc_map[old_id]))
        if parts:
            db.update(
                "services_catalog",
                {"related_service_ids": ",".join(parts)},
                {"id": row["id"]},
            )

    staff_map = {}
    for row in db.select("services_staff", {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        assigned = (payload.get("assigned_service_ids") or "").strip()
        if assigned:
            mapped = []
            for part in assigned.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    old_id = int(part)
                except ValueError:
                    continue
                if old_id in svc_map:
                    mapped.append(str(svc_map[old_id]))
            payload["assigned_service_ids"] = ",".join(mapped)
        new_id = db.insert("services_staff", payload)
        staff_map[row["id"]] = new_id
    copied["services_staff"] = len(staff_map)

    cust_map = {}
    for row in db.select("services_customers", {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        new_id = db.insert("services_customers", payload)
        cust_map[row["id"]] = new_id
    copied["services_customers"] = len(cust_map)

    room_map = {}
    for row in db.select("services_rooms", {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        new_id = db.insert("services_rooms", payload)
        room_map[row["id"]] = new_id
    copied["services_rooms"] = len(room_map)

    booking_map = {}
    for row in db.select("services_bookings", {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        if payload.get("service_id"):
            payload["service_id"] = svc_map.get(payload["service_id"])
        if payload.get("staff_id"):
            payload["staff_id"] = staff_map.get(payload["staff_id"])
        if payload.get("customer_id"):
            payload["customer_id"] = cust_map.get(payload["customer_id"])
        if payload.get("room_id"):
            payload["room_id"] = room_map.get(payload["room_id"])
        new_id = db.insert("services_bookings", payload)
        booking_map[row["id"]] = new_id
    copied["services_bookings"] = len(booking_map)

    review_count = 0
    for row in db.select("services_reviews", {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        if payload.get("booking_id"):
            payload["booking_id"] = booking_map.get(payload["booking_id"])
        if payload.get("customer_id"):
            payload["customer_id"] = cust_map.get(payload["customer_id"])
        if payload.get("service_id"):
            payload["service_id"] = svc_map.get(payload["service_id"])
        db.insert("services_reviews", payload)
        review_count += 1
    copied["services_reviews"] = review_count

    return copied


def _clone_restaurant_data(db, source_id, target_id):
    copied = {}

    src_settings = db.row("restaurant_settings", {"user_id": source_id})
    if src_settings:
        payload = _strip_row(src_settings)
        payload["user_id"] = target_id
        db.insert("restaurant_settings", payload)
        copied["restaurant_settings"] = 1

    for table in (
        "restaurant_working_hours",
        "restaurant_holidays",
        "restaurant_payment_methods",
        "restaurant_faqs",
    ):
        copied[table] = _copy_simple(db, table, source_id, target_id)

    table_map = {}
    for row in db.select("restaurant_tables", {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        new_id = db.insert("restaurant_tables", payload)
        table_map[row["id"]] = new_id
    copied["restaurant_tables"] = len(table_map)

    cat_map = {}
    for row in db.select("restaurant_categories", {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        new_id = db.insert("restaurant_categories", payload)
        cat_map[row["id"]] = new_id
    copied["restaurant_categories"] = len(cat_map)

    menu_map = {}
    for row in db.select("restaurant_menu_items", {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        if payload.get("category_id"):
            payload["category_id"] = cat_map.get(payload["category_id"])
        new_id = db.insert("restaurant_menu_items", payload)
        menu_map[row["id"]] = new_id
    copied["restaurant_menu_items"] = len(menu_map)

    for row in db.select("restaurant_variations", {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        if payload.get("menu_item_id"):
            payload["menu_item_id"] = menu_map.get(payload["menu_item_id"])
        db.insert("restaurant_variations", payload)
    copied["restaurant_variations"] = len(db.select("restaurant_variations", {"user_id": target_id}) or [])

    for table in ("restaurant_addons", "restaurant_combos", "restaurant_promotions"):
        copied[table] = _copy_simple(db, table, source_id, target_id)

    cust_map = {}
    for row in db.select("restaurant_customers", {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        new_id = db.insert("restaurant_customers", payload)
        cust_map[row["id"]] = new_id
    copied["restaurant_customers"] = len(cust_map)

    order_map = {}
    for row in db.select("restaurant_orders", {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        if payload.get("customer_id"):
            payload["customer_id"] = cust_map.get(payload["customer_id"])
        new_id = db.insert("restaurant_orders", payload)
        order_map[row["id"]] = new_id
    copied["restaurant_orders"] = len(order_map)

    item_count = 0
    for old_order_id in order_map:
        for row in db.select("restaurant_order_items", {"order_id": old_order_id}) or []:
            payload = _strip_row(row)
            payload["order_id"] = order_map.get(old_order_id)
            if payload.get("menu_item_id"):
                payload["menu_item_id"] = menu_map.get(payload["menu_item_id"])
            db.insert("restaurant_order_items", payload)
            item_count += 1
    copied["restaurant_order_items"] = item_count

    res_count = 0
    for row in db.select("restaurant_reservations", {"user_id": source_id}) or []:
        payload = _strip_row(row)
        payload["user_id"] = target_id
        if payload.get("customer_id"):
            payload["customer_id"] = cust_map.get(payload["customer_id"])
        if payload.get("table_id"):
            payload["table_id"] = table_map.get(payload["table_id"])
        db.insert("restaurant_reservations", payload)
        res_count += 1
    copied["restaurant_reservations"] = res_count

    return copied


def clone_user(source_id: int, name: str, email: str, password: str):
    name = (name or "").strip()
    email = (email or "").strip().lower()
    password = password or ""

    if not name or not email or not password:
        return None, "Name, email and password are required"
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return None, "Invalid email address"

    db = Database()
    try:
        source = db.row("admins", {"id": source_id})
        if not source:
            return None, "Source user not found"
        if db.row("admins", {"email": email}):
            return None, "A user with this email already exists"

        new_id = db.insert(
            "admins",
            {
                "name": name,
                "email": email,
                "password": _md5(password),
                "role_id": source.get("role_id"),
                "profile_pic": source.get("profile_pic"),
            },
        )

        meta_copied = 0
        for row in db.select("user_meta", {"user_id": source_id}) or []:
            key = row.get("meta_key") or ""
            if key in SKIP_META_KEYS:
                continue
            db.insert(
                "user_meta",
                {"user_id": new_id, "meta_key": key, "meta_value": row.get("meta_value")},
            )
            meta_copied += 1

        inst_count = 0
        for row in db.select("bot_instructions", {"user_id": source_id}) or []:
            payload = _strip_row(row)
            payload["user_id"] = new_id
            db.insert("bot_instructions", payload)
            inst_count += 1

        handler, _ = _handler_class(db, source_id)
        business_copied = {}
        if handler == SERVICES_HANDLER:
            business_copied = _clone_services_data(db, source_id, new_id)
        elif handler == RESTAURANT_HANDLER:
            business_copied = _clone_restaurant_data(db, source_id, new_id)

        roles_map = {r["id"]: r["name"] for r in db.select("roles")}
        user = dict(db.row("admins", {"id": new_id}) or {})
        user.pop("password", None)
        user["role"] = roles_map.get(user.get("role_id"))

        return {
            "user": user,
            "source_user_id": source_id,
            "handler_class": handler,
            "meta_rows": meta_copied,
            "instructions": inst_count,
            "business_tables": business_copied,
        }, None
    finally:
        db.close()
