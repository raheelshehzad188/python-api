"""Restaurant chatbot tools — the ONLY functions Gemini may call.

All prices, menu data, delivery rules and totals come from the database.
Gemini never calculates totals or invents data. Isolated from every other
chatbot type.
"""

from __future__ import annotations

import re
from decimal import Decimal

from restaurant_schema import (
    SETTINGS_TABLE,
    CATEGORIES_TABLE,
    MENU_TABLE,
    VARIATIONS_TABLE,
    ADDONS_TABLE,
    COMBOS_TABLE,
    PROMOTIONS_TABLE,
    CUSTOMERS_TABLE,
    ORDERS_TABLE,
    ORDER_ITEMS_TABLE,
    WORKING_HOURS_TABLE,
    HOLIDAYS_TABLE,
    PAYMENTS_TABLE,
)

TOOL_NAMES = (
    "get_business_info",
    "get_categories",
    "get_menu",
    "get_menu_item",
    "search_menu",
    "get_working_hours",
    "get_holidays",
    "get_promotions",
    "get_combo_deals",
    "search_customer",
    "create_customer",
    "place_order",
    "cancel_order",
    "track_order",
)

ORDER_STATUSES = (
    "pending",
    "accepted",
    "preparing",
    "ready",
    "out_for_delivery",
    "delivered",
    "cancelled",
)

CANCELLABLE_STATUSES = ("pending", "accepted", "preparing")
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _money(value):
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


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


def _settings(db, user_id):
    return db.row(SETTINGS_TABLE, {"user_id": user_id}) or {}


def _currency(db, user_id):
    return (_settings(db, user_id).get("currency_code") or "PKR").strip() or "PKR"


def _category_map(db, user_id):
    return {c["id"]: c.get("name") or "" for c in db.select(CATEGORIES_TABLE, {"user_id": user_id})}


def _variations_for(db, user_id, menu_item_id):
    rows = db.select(VARIATIONS_TABLE, {"user_id": user_id, "menu_item_id": menu_item_id}) or []
    return [
        {"id": r["id"], "name": r.get("name") or "", "price_adjustment": _money(r.get("price_adjustment"))}
        for r in rows
        if r.get("is_active", 1)
    ]


def _public_menu_item(db, user_id, r, currency, cat_name=""):
    return {
        "id": r["id"],
        "name": r.get("name") or "",
        "description": r.get("description") or "",
        "price": _money(r.get("price")),
        "currency": currency,
        "category_id": r.get("category_id"),
        "category": cat_name,
        "prep_time_minutes": int(r.get("prep_time_minutes") or 0),
        "available": bool(r.get("is_available", 1)),
        "featured": bool(r.get("is_featured", 0)),
        "variations": _variations_for(db, user_id, r["id"]),
    }


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


# --------------------------------------------------------------------------- #
# Read tools                                                                  #
# --------------------------------------------------------------------------- #


def tool_get_business_info(db, user_id, args=None):
    s = _settings(db, user_id)
    return {
        "success": True,
        "business": {
            "name": s.get("business_name") or "",
            "category": s.get("business_category") or "",
            "about": s.get("about") or "",
            "phone": s.get("phone") or "",
            "whatsapp": s.get("whatsapp") or "",
            "email": s.get("email") or "",
            "address": s.get("address") or "",
            "city": s.get("city") or "",
            "currency": s.get("currency_code") or "PKR",
            "delivery_charges": _money(s.get("delivery_charges")),
            "minimum_order": _money(s.get("minimum_order")),
            "estimated_delivery_time": s.get("estimated_delivery_time") or "",
            "payment_methods": s.get("payment_methods") or "",
            "delivery_rules": s.get("delivery_rules") or "",
        },
    }


def tool_get_categories(db, user_id, args=None):
    rows = db.select(CATEGORIES_TABLE, {"user_id": user_id}) or []
    rows = sorted([r for r in rows if r.get("is_active", 1)], key=lambda r: int(r.get("sort_order") or 0))
    return {
        "success": True,
        "categories": [
            {"id": r["id"], "name": r.get("name") or "", "description": r.get("description") or ""}
            for r in rows
        ],
    }


def tool_get_menu(db, user_id, args=None):
    args = args or {}
    currency = _currency(db, user_id)
    cats = _category_map(db, user_id)
    category_id = args.get("category_id")
    category_name = (args.get("category") or args.get("category_name") or "").strip().lower()

    if category_name and not category_id:
        for cid, cname in cats.items():
            if cname.strip().lower() == category_name:
                category_id = cid
                break

    rows = db.select(MENU_TABLE, {"user_id": user_id}) or []
    rows = [r for r in rows if r.get("is_available", 1)]
    if category_id:
        rows = [r for r in rows if r.get("category_id") == category_id]

    grouped = {}
    for r in rows:
        cname = cats.get(r.get("category_id"), "Other")
        grouped.setdefault(cname, []).append(_public_menu_item(db, user_id, r, currency, cname))

    return {
        "success": True,
        "currency": currency,
        "menu": [{"category": cname, "items": items} for cname, items in grouped.items()],
        "count": len(rows),
    }


def tool_get_menu_item(db, user_id, args=None):
    args = args or {}
    currency = _currency(db, user_id)
    cats = _category_map(db, user_id)
    row = _find_menu_item(
        db, user_id, menu_item_id=args.get("menu_item_id") or args.get("id"), name=args.get("name")
    )
    if not row:
        return {"success": False, "error": "Menu item not found"}
    item = _public_menu_item(db, user_id, row, currency, cats.get(row.get("category_id"), ""))
    item["addons"] = [
        {"id": a["id"], "name": a.get("name") or "", "price": _money(a.get("price"))}
        for a in (db.select(ADDONS_TABLE, {"user_id": user_id}) or [])
        if a.get("is_active", 1)
    ]
    return {"success": True, "item": item}


def tool_search_menu(db, user_id, args=None):
    args = args or {}
    query = (args.get("query") or args.get("q") or args.get("name") or "").strip().lower()
    currency = _currency(db, user_id)
    cats = _category_map(db, user_id)
    rows = db.select(MENU_TABLE, {"user_id": user_id}) or []
    rows = [r for r in rows if r.get("is_available", 1)]
    if query:
        rows = [
            r
            for r in rows
            if query in (r.get("name") or "").lower()
            or query in (r.get("description") or "").lower()
            or query in (cats.get(r.get("category_id"), "").lower())
        ]
    return {
        "success": True,
        "currency": currency,
        "query": query,
        "results": [
            _public_menu_item(db, user_id, r, currency, cats.get(r.get("category_id"), "")) for r in rows
        ],
        "count": len(rows),
    }


def tool_get_working_hours(db, user_id, args=None):
    rows = db.select(WORKING_HOURS_TABLE, {"user_id": user_id}) or []
    by_day = {int(r.get("day_of_week") or 0): r for r in rows}
    hours = []
    for day in range(7):
        r = by_day.get(day) or {}
        hours.append(
            {
                "day": DAY_NAMES[day],
                "day_of_week": day,
                "is_closed": bool(r.get("is_closed")),
                "open_time": _fmt_time(r.get("open_time")) if not r.get("is_closed") else "",
                "close_time": _fmt_time(r.get("close_time")) if not r.get("is_closed") else "",
                "break_start": _fmt_time(r.get("break_start")),
                "break_end": _fmt_time(r.get("break_end")),
            }
        )
    return {"success": True, "working_hours": hours}


def tool_get_holidays(db, user_id, args=None):
    rows = db.select(HOLIDAYS_TABLE, {"user_id": user_id}) or []
    return {
        "success": True,
        "holidays": [
            {
                "date": _fmt_date(r.get("holiday_date")),
                "title": r.get("title") or "",
                "description": r.get("description") or "",
            }
            for r in rows
        ],
    }


def tool_get_promotions(db, user_id, args=None):
    rows = db.select(PROMOTIONS_TABLE, {"user_id": user_id}) or []
    rows = [r for r in rows if r.get("is_active", 1)]
    return {
        "success": True,
        "promotions": [
            {
                "title": r.get("title") or "",
                "description": r.get("description") or "",
                "discount": r.get("discount") or "",
                "start_date": _fmt_date(r.get("start_date")),
                "end_date": _fmt_date(r.get("end_date")),
            }
            for r in rows
        ],
    }


def tool_get_combo_deals(db, user_id, args=None):
    currency = _currency(db, user_id)
    rows = db.select(COMBOS_TABLE, {"user_id": user_id}) or []
    rows = [r for r in rows if r.get("is_active", 1)]
    return {
        "success": True,
        "currency": currency,
        "combo_deals": [
            {
                "id": r["id"],
                "name": r.get("name") or "",
                "description": r.get("description") or "",
                "includes": r.get("includes") or "",
                "price": _money(r.get("price")),
            }
            for r in rows
        ],
    }


# --------------------------------------------------------------------------- #
# Customer tools                                                              #
# --------------------------------------------------------------------------- #


def _public_customer(r):
    return {
        "id": r["id"],
        "name": r.get("name") or "",
        "phone": r.get("phone") or "",
        "email": r.get("email") or "",
        "address": r.get("address") or "",
    }


def tool_search_customer(db, user_id, args=None):
    args = args or {}
    phone = (args.get("phone") or "").strip()
    name = (args.get("name") or "").strip()
    rows = db.select(CUSTOMERS_TABLE, {"user_id": user_id}) or []
    match = None
    if phone:
        for r in rows:
            if (r.get("phone") or "").strip() == phone:
                match = r
                break
    if not match and name:
        target = name.lower()
        for r in rows:
            if (r.get("name") or "").strip().lower() == target:
                match = r
                break
    if not match:
        return {"success": True, "found": False, "customer": None}
    return {"success": True, "found": True, "customer": _public_customer(match)}


def tool_create_customer(db, user_id, args=None):
    args = args or {}
    name = (args.get("name") or "").strip()
    if not name:
        return {"success": False, "error": "name is required"}
    phone = (args.get("phone") or "").strip()

    if phone:
        for r in db.select(CUSTOMERS_TABLE, {"user_id": user_id}) or []:
            if (r.get("phone") or "").strip() == phone:
                return {"success": True, "found": True, "customer": _public_customer(r)}

    new_id = db.insert(
        CUSTOMERS_TABLE,
        {
            "user_id": user_id,
            "name": name,
            "phone": phone or None,
            "email": (args.get("email") or "").strip() or None,
            "address": (args.get("address") or "").strip() or None,
            "notes": (args.get("notes") or "").strip() or None,
        },
    )
    row = db.row(CUSTOMERS_TABLE, {"id": new_id})
    return {"success": True, "found": False, "customer": _public_customer(row)}


# --------------------------------------------------------------------------- #
# Order tools                                                                 #
# --------------------------------------------------------------------------- #


def _public_order(db, row, currency):
    items = db.select(ORDER_ITEMS_TABLE, {"order_id": row["id"]}) or []
    return {
        "id": row["id"],
        "status": row.get("status") or "pending",
        "order_type": row.get("order_type") or "delivery",
        "customer_name": row.get("customer_name") or "",
        "phone": row.get("phone") or "",
        "address": row.get("address") or "",
        "payment_method": row.get("payment_method") or "",
        "subtotal": _money(row.get("subtotal")),
        "delivery_charges": _money(row.get("delivery_charges")),
        "total": _money(row.get("total")),
        "currency": currency,
        "items": [
            {
                "name": it.get("item_name") or "",
                "variation": it.get("variation_name") or "",
                "addons": it.get("addons") or "",
                "unit_price": _money(it.get("unit_price")),
                "quantity": int(it.get("quantity") or 1),
                "line_total": _money(it.get("line_total")),
            }
            for it in items
        ],
    }


def tool_place_order(db, user_id, args=None):
    args = args or {}
    settings = _settings(db, user_id)
    currency = settings.get("currency_code") or "PKR"

    raw_items = args.get("items") or []
    if not isinstance(raw_items, list) or not raw_items:
        return {"success": False, "error": "items is required (a non-empty list)"}

    order_type = (args.get("order_type") or "delivery").strip().lower()
    if order_type not in ("delivery", "pickup"):
        order_type = "delivery"

    customer_name = (args.get("customer_name") or args.get("name") or "").strip()
    phone = (args.get("phone") or "").strip()
    address = (args.get("address") or "").strip()
    payment_method = (args.get("payment_method") or "").strip()
    notes = (args.get("notes") or "").strip()

    if not customer_name:
        return {"success": False, "error": "customer_name is required"}
    if order_type == "delivery" and not address:
        return {"success": False, "error": "address is required for delivery orders"}
    if not payment_method:
        return {"success": False, "error": "payment_method is required"}

    prepared = []
    subtotal = 0.0
    for entry in raw_items:
        if not isinstance(entry, dict):
            return {"success": False, "error": "each item must be an object"}
        menu_row = _find_menu_item(
            db,
            user_id,
            menu_item_id=entry.get("menu_item_id") or entry.get("id"),
            name=entry.get("name") or entry.get("item_name"),
        )
        if not menu_row:
            return {"success": False, "error": f"Menu item not found: {entry.get('name') or entry.get('menu_item_id')}"}
        if not menu_row.get("is_available", 1):
            return {"success": False, "error": f"{menu_row.get('name')} is currently unavailable"}

        qty = entry.get("quantity") or entry.get("qty") or 1
        try:
            qty = max(1, int(qty))
        except (TypeError, ValueError):
            qty = 1

        unit_price = _money(menu_row.get("price"))
        variation_name = ""
        variation = _find_variation(
            db,
            user_id,
            menu_row["id"],
            variation_id=entry.get("variation_id"),
            variation_name=entry.get("variation") or entry.get("variation_name"),
        )
        if (entry.get("variation_id") or entry.get("variation") or entry.get("variation_name")) and not variation:
            return {"success": False, "error": f"Variation not found for {menu_row.get('name')}"}
        if variation:
            unit_price += _money(variation.get("price_adjustment"))
            variation_name = variation.get("name") or ""

        addon_names = []
        addon_inputs = entry.get("addon_ids") or entry.get("addons") or []
        if isinstance(addon_inputs, (str, int)):
            addon_inputs = [addon_inputs]
        for a in addon_inputs:
            if isinstance(a, dict):
                addon = _find_addon(db, user_id, addon_id=a.get("id"), addon_name=a.get("name"))
            elif isinstance(a, int):
                addon = _find_addon(db, user_id, addon_id=a)
            else:
                addon = _find_addon(db, user_id, addon_name=str(a))
            if not addon:
                return {"success": False, "error": f"Add-on not found: {a}"}
            unit_price += _money(addon.get("price"))
            addon_names.append(addon.get("name") or "")

        line_total = unit_price * qty
        subtotal += line_total
        prepared.append(
            {
                "menu_item_id": menu_row["id"],
                "item_name": menu_row.get("name") or "",
                "variation_name": variation_name,
                "addons": ", ".join(addon_names),
                "unit_price": round(unit_price, 2),
                "quantity": qty,
                "line_total": round(line_total, 2),
            }
        )

    minimum_order = _money(settings.get("minimum_order"))
    if minimum_order and subtotal < minimum_order:
        return {
            "success": False,
            "error": "below_minimum_order",
            "message": f"Minimum order is {minimum_order} {currency}. Current subtotal is {round(subtotal, 2)} {currency}.",
            "minimum_order": minimum_order,
            "subtotal": round(subtotal, 2),
            "currency": currency,
        }

    delivery_charges = _money(settings.get("delivery_charges")) if order_type == "delivery" else 0.0
    total = round(subtotal + delivery_charges, 2)

    # Link/create a customer record
    customer_id = None
    if phone:
        existing = None
        for r in db.select(CUSTOMERS_TABLE, {"user_id": user_id}) or []:
            if (r.get("phone") or "").strip() == phone:
                existing = r
                break
        if existing:
            customer_id = existing["id"]
            if address and not (existing.get("address") or "").strip():
                db.update(CUSTOMERS_TABLE, {"address": address}, {"id": customer_id})
        else:
            customer_id = db.insert(
                CUSTOMERS_TABLE,
                {"user_id": user_id, "name": customer_name, "phone": phone, "address": address or None},
            )

    order_id = db.insert(
        ORDERS_TABLE,
        {
            "user_id": user_id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "phone": phone or None,
            "order_type": order_type,
            "address": address or None,
            "payment_method": payment_method,
            "status": "pending",
            "subtotal": round(subtotal, 2),
            "delivery_charges": delivery_charges,
            "total": total,
            "notes": notes or None,
        },
    )
    for it in prepared:
        db.insert(ORDER_ITEMS_TABLE, {"order_id": order_id, **it})

    row = db.row(ORDERS_TABLE, {"id": order_id})
    order = _public_order(db, row, currency)
    order["estimated_delivery_time"] = settings.get("estimated_delivery_time") or ""
    return {"success": True, "order": order}


def _find_order(db, user_id, *, order_id=None, phone=None):
    if order_id:
        row = db.row(ORDERS_TABLE, {"id": order_id, "user_id": user_id})
        if row:
            return row
    if phone:
        phone = str(phone).strip()
        db.cursor.execute(
            f"SELECT * FROM {ORDERS_TABLE} WHERE user_id=%s AND phone=%s ORDER BY created_at DESC, id DESC LIMIT 1",
            [user_id, phone],
        )
        return db.cursor.fetchone()
    return None


def tool_track_order(db, user_id, args=None):
    args = args or {}
    currency = _currency(db, user_id)
    row = _find_order(db, user_id, order_id=args.get("order_id") or args.get("id"), phone=args.get("phone"))
    if not row:
        return {"success": False, "error": "Order not found"}
    order = _public_order(db, row, currency)
    order["estimated_delivery_time"] = _settings(db, user_id).get("estimated_delivery_time") or ""
    return {"success": True, "order": order}


def tool_cancel_order(db, user_id, args=None):
    args = args or {}
    currency = _currency(db, user_id)
    row = _find_order(db, user_id, order_id=args.get("order_id") or args.get("id"), phone=args.get("phone"))
    if not row:
        return {"success": False, "error": "Order not found"}

    status = (row.get("status") or "pending").lower()
    if status == "cancelled":
        return {"success": True, "allowed": True, "already_cancelled": True, "order": _public_order(db, row, currency)}
    if status not in CANCELLABLE_STATUSES:
        return {
            "success": True,
            "allowed": False,
            "reason": f"Order is already '{status}' and can no longer be cancelled.",
            "order": _public_order(db, row, currency),
        }

    db.update(ORDERS_TABLE, {"status": "cancelled"}, {"id": row["id"], "user_id": user_id})
    updated = db.row(ORDERS_TABLE, {"id": row["id"]})
    return {"success": True, "allowed": True, "order": _public_order(db, updated, currency)}


# --------------------------------------------------------------------------- #
# Dispatch                                                                    #
# --------------------------------------------------------------------------- #


TOOL_HANDLERS = {
    "get_business_info": tool_get_business_info,
    "get_categories": tool_get_categories,
    "get_menu": tool_get_menu,
    "get_menu_item": tool_get_menu_item,
    "search_menu": tool_search_menu,
    "get_working_hours": tool_get_working_hours,
    "get_holidays": tool_get_holidays,
    "get_promotions": tool_get_promotions,
    "get_combo_deals": tool_get_combo_deals,
    "search_customer": tool_search_customer,
    "create_customer": tool_create_customer,
    "place_order": tool_place_order,
    "cancel_order": tool_cancel_order,
    "track_order": tool_track_order,
}


def run_tool(db, user_id, name, args=None):
    name = (name or "").strip()
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return {"success": False, "error": f"Unknown tool '{name}'", "available_tools": list(TOOL_NAMES)}
    try:
        return handler(db, user_id, args or {})
    except Exception as exc:
        return {"success": False, "error": str(exc), "tool": name}
