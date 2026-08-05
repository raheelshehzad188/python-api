"""Shared restaurant order creation logic for admin panel and AI tools."""

from __future__ import annotations

from decimal import Decimal

from restaurant_schema import (
    SETTINGS_TABLE,
    CUSTOMERS_TABLE,
    ORDERS_TABLE,
    ORDER_ITEMS_TABLE,
    PROMOTIONS_TABLE,
)

ORDER_TYPES = ("dine_in", "takeaway", "delivery")
PAYMENT_STATUSES = ("paid", "pending", "refunded")


def _money(value):
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_order_type(value):
    raw = (value or "delivery").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in ("pickup", "take_away"):
        return "takeaway"
    if raw in ORDER_TYPES:
        return raw
    return "delivery"


def _normalize_status(value):
    raw = (value or "pending").strip().lower().replace(" ", "_")
    aliases = {
        "accepted": "confirmed",
        "delivered": "completed",
    }
    return aliases.get(raw, raw)


def _next_order_number(db, user_id):
    db.cursor.execute(
        f"SELECT COUNT(*) AS c FROM {ORDERS_TABLE} WHERE user_id=%s",
        [user_id],
    )
    count = int((db.cursor.fetchone() or {}).get("c") or 0)
    return f"ORD-{user_id}-{count + 1:05d}"


def _resolve_customer(db, user_id, *, customer_id=None, customer_name="", phone="", email="", address=""):
    customer_name = (customer_name or "").strip()
    phone = (phone or "").strip()
    email = (email or "").strip()
    address = (address or "").strip()

    if customer_id:
        row = db.row(CUSTOMERS_TABLE, {"id": customer_id, "user_id": user_id})
        if row:
            updates = {}
            if address and not (row.get("address") or "").strip():
                updates["address"] = address
            if email and not (row.get("email") or "").strip():
                updates["email"] = email
            if updates:
                db.update(CUSTOMERS_TABLE, updates, {"id": customer_id})
            return row["id"], row.get("name") or customer_name, row.get("phone") or phone, row.get("email") or email

    if phone:
        for r in db.select(CUSTOMERS_TABLE, {"user_id": user_id}) or []:
            if (r.get("phone") or "").strip() == phone:
                updates = {}
                if address and not (r.get("address") or "").strip():
                    updates["address"] = address
                if email and not (r.get("email") or "").strip():
                    updates["email"] = email
                if updates:
                    db.update(CUSTOMERS_TABLE, updates, {"id": r["id"]})
                return r["id"], r.get("name") or customer_name, phone, r.get("email") or email

    if customer_name:
        new_id = db.insert(
            CUSTOMERS_TABLE,
            {
                "user_id": user_id,
                "name": customer_name,
                "phone": phone or None,
                "email": email or None,
                "address": address or None,
            },
        )
        return new_id, customer_name, phone, email

    return None, customer_name, phone, email


def _apply_coupon(db, user_id, coupon_code, subtotal):
    code = (coupon_code or "").strip()
    if not code:
        return 0.0, ""
    promos = db.select(PROMOTIONS_TABLE, {"user_id": user_id}) or []
    for p in promos:
        if not p.get("is_active", 1):
            continue
        title = (p.get("title") or "").strip().lower()
        discount = (p.get("discount") or "").strip()
        if code.lower() not in (title, discount.lower()):
            continue
        pct = 0.0
        if "%" in discount:
            try:
                pct = float(discount.replace("%", "").strip())
            except ValueError:
                pct = 0.0
        if pct > 0:
            return round(subtotal * pct / 100, 2), p.get("title") or code
    return 0.0, code


def prepare_order_items(db, user_id, raw_items, find_menu_item, find_variation, find_addon):
    """Validate and price line items. Returns (prepared_list, subtotal) or raises ValueError."""
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("items is required (a non-empty list)")

    prepared = []
    subtotal = 0.0
    for entry in raw_items:
        if not isinstance(entry, dict):
            raise ValueError("each item must be an object")

        menu_row = find_menu_item(
            db,
            user_id,
            menu_item_id=entry.get("menu_item_id") or entry.get("id"),
            name=entry.get("name") or entry.get("item_name"),
        )
        if not menu_row:
            raise ValueError(f"Menu item not found: {entry.get('name') or entry.get('menu_item_id')}")
        if not menu_row.get("is_available", 1):
            raise ValueError(f"{menu_row.get('name')} is currently unavailable")

        qty = entry.get("quantity") or entry.get("qty") or 1
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            qty = 1
        if qty < 1:
            raise ValueError("quantity must be at least 1")

        unit_price = _money(menu_row.get("price"))
        variation_name = ""
        variation = find_variation(
            db,
            user_id,
            menu_row["id"],
            variation_id=entry.get("variation_id"),
            variation_name=entry.get("variation") or entry.get("variation_name"),
        )
        if (entry.get("variation_id") or entry.get("variation") or entry.get("variation_name")) and not variation:
            raise ValueError(f"Variation not found for {menu_row.get('name')}")
        if variation:
            unit_price += _money(variation.get("price_adjustment"))
            variation_name = variation.get("name") or ""

        addon_names = []
        addon_inputs = entry.get("addon_ids") or entry.get("addons") or []
        if isinstance(addon_inputs, (str, int)):
            addon_inputs = [addon_inputs]
        for a in addon_inputs:
            if isinstance(a, dict):
                addon = find_addon(db, user_id, addon_id=a.get("id"), addon_name=a.get("name"))
            elif isinstance(a, int):
                addon = find_addon(db, user_id, addon_id=a)
            else:
                addon = find_addon(db, user_id, addon_name=str(a))
            if not addon:
                raise ValueError(f"Add-on not found: {a}")
            unit_price += _money(addon.get("price"))
            addon_names.append(addon.get("name") or "")

        line_total = round(unit_price * qty, 2)
        subtotal += line_total
        prepared.append(
            {
                "menu_item_id": menu_row["id"],
                "item_name": menu_row.get("name") or "",
                "variation_name": variation_name,
                "addons": ", ".join(addon_names),
                "unit_price": round(unit_price, 2),
                "quantity": qty,
                "line_total": line_total,
                "item_notes": (entry.get("item_notes") or entry.get("notes") or "").strip() or None,
            }
        )

    return prepared, round(subtotal, 2)


def create_order(
    db,
    user_id,
    data,
    *,
    find_menu_item,
    find_variation,
    find_addon,
    source="manual",
    enforce_minimum=True,
    require_payment_method=True,
):
    """Create an order with line items. Returns order row dict."""
    settings = db.row(SETTINGS_TABLE, {"user_id": user_id}) or {}
    currency = (settings.get("currency_code") or "PKR").strip() or "PKR"

    order_type = _normalize_order_type(data.get("order_type"))
    customer_name = (data.get("customer_name") or data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()
    address = (data.get("address") or data.get("delivery_address") or "").strip()
    payment_method = (data.get("payment_method") or "").strip()
    payment_status = (data.get("payment_status") or "pending").strip().lower()
    if payment_status not in PAYMENT_STATUSES:
        payment_status = "pending"
    status = _normalize_status(data.get("status") or "pending")
    notes = (data.get("notes") or data.get("customer_notes") or "").strip()
    internal_notes = (data.get("internal_notes") or "").strip()
    coupon_code = (data.get("coupon_code") or "").strip()

    if not customer_name:
        raise ValueError("customer_name is required")
    if order_type == "delivery" and not address:
        raise ValueError("address is required for delivery orders")
    if require_payment_method and not payment_method:
        raise ValueError("payment_method is required")

    prepared, subtotal = prepare_order_items(
        db, user_id, data.get("items") or [], find_menu_item, find_variation, find_addon
    )

    minimum_order = _money(settings.get("minimum_order"))
    if enforce_minimum and minimum_order and subtotal < minimum_order and source != "manual":
        raise ValueError(
            f"Minimum order is {minimum_order} {currency}. Current subtotal is {subtotal} {currency}."
        )

    discount = _money(data.get("discount"))
    if coupon_code and not discount:
        coupon_discount, coupon_code = _apply_coupon(db, user_id, coupon_code, subtotal)
        discount = coupon_discount

    tax_rate = _money(settings.get("tax_rate"))
    service_rate = _money(settings.get("service_charge_rate"))
    if data.get("tax") is not None:
        tax = _money(data.get("tax"))
    else:
        taxable = max(subtotal - discount, 0)
        tax = round(taxable * tax_rate / 100, 2) if tax_rate else 0.0

    if data.get("service_charges") is not None:
        service_charges = _money(data.get("service_charges"))
    else:
        service_charges = round(subtotal * service_rate / 100, 2) if service_rate else 0.0

    if data.get("delivery_charges") is not None:
        delivery_charges = _money(data.get("delivery_charges"))
    else:
        delivery_charges = _money(settings.get("delivery_charges")) if order_type == "delivery" else 0.0

    total = round(subtotal - discount + tax + service_charges + delivery_charges, 2)

    customer_id, customer_name, phone, email = _resolve_customer(
        db,
        user_id,
        customer_id=data.get("customer_id"),
        customer_name=customer_name,
        phone=phone,
        email=email,
        address=address,
    )

    order_number = (data.get("order_number") or "").strip() or _next_order_number(db, user_id)

    order_id = db.insert(
        ORDERS_TABLE,
        {
            "user_id": user_id,
            "order_number": order_number,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "phone": phone or None,
            "email": email or None,
            "order_type": order_type,
            "address": address or None,
            "table_id": data.get("table_id") or None,
            "table_number": (data.get("table_number") or "").strip() or None,
            "guests": int(data.get("guests") or 0),
            "delivery_time": (data.get("delivery_time") or "").strip() or None,
            "pickup_time": (data.get("pickup_time") or "").strip() or None,
            "payment_method": payment_method or None,
            "payment_status": payment_status,
            "status": status,
            "subtotal": subtotal,
            "tax": tax,
            "discount": discount,
            "service_charges": service_charges,
            "delivery_charges": delivery_charges,
            "total": total,
            "coupon_code": coupon_code or None,
            "assigned_driver": (data.get("assigned_driver") or "").strip() or None,
            "assigned_waiter": (data.get("assigned_waiter") or "").strip() or None,
            "assigned_kitchen_staff": (data.get("assigned_kitchen_staff") or "").strip() or None,
            "notes": notes or None,
            "customer_notes": notes or None,
            "internal_notes": internal_notes or None,
            "source": source,
        },
    )
    for it in prepared:
        db.insert(ORDER_ITEMS_TABLE, {"order_id": order_id, **it})

    return db.row(ORDERS_TABLE, {"id": order_id}), currency
