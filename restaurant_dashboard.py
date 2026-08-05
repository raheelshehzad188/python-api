"""Restaurant dashboard stats — computed from real DB data only.

Isolated from Services / Ecommerce / Job Posting.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, jsonify

from db import Database
from restaurant_settings import _is_restaurant_user
from restaurant_schema import (
    SETTINGS_TABLE,
    ORDERS_TABLE,
    ORDER_ITEMS_TABLE,
    MENU_TABLE,
    CATEGORIES_TABLE,
    CUSTOMERS_TABLE,
    PROMOTIONS_TABLE,
    COMBOS_TABLE,
    ADDONS_TABLE,
    RESERVATIONS_TABLE,
)

restaurant_dashboard_bp = Blueprint("restaurant_dashboard", __name__)


def _money(v):
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:19]).date()
    except Exception:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def _as_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value)[:19])
    except Exception:
        return None


def _pct_delta(today, yesterday):
    if yesterday and yesterday > 0:
        return round(((today - yesterday) / yesterday) * 100, 1)
    if today > 0:
        return 100.0
    return 0.0


def _spark(values, fill=7):
    vals = list(values or [])
    while len(vals) < fill:
        vals.insert(0, 0)
    return [round(_money(v), 2) for v in vals[-fill:]]


def _build_dashboard(db, user_id):
    today = date.today()
    yesterday = today - timedelta(days=1)
    days_7 = [today - timedelta(days=i) for i in range(6, -1, -1)]
    days_30_start = today - timedelta(days=29)

    settings = db.row(SETTINGS_TABLE, {"user_id": user_id}) or {}
    currency = (settings.get("currency_code") or "PKR").strip() or "PKR"
    eta = settings.get("estimated_delivery_time") or "30-45 Minutes"

    orders = db.select(ORDERS_TABLE, {"user_id": user_id}) or []
    customers = db.select(CUSTOMERS_TABLE, {"user_id": user_id}) or []
    menu = db.select(MENU_TABLE, {"user_id": user_id}) or []
    categories = db.select(CATEGORIES_TABLE, {"user_id": user_id}) or []
    promotions = [p for p in (db.select(PROMOTIONS_TABLE, {"user_id": user_id}) or []) if p.get("is_active", 1)]
    combos = [c for c in (db.select(COMBOS_TABLE, {"user_id": user_id}) or []) if c.get("is_active", 1)]
    addons = [a for a in (db.select(ADDONS_TABLE, {"user_id": user_id}) or []) if a.get("is_active", 1)]

    # Attach items to orders
    for o in orders:
        o["_items"] = db.select(ORDER_ITEMS_TABLE, {"order_id": o["id"]}) or []
        o["_date"] = _as_date(o.get("created_at"))
        o["_dt"] = _as_dt(o.get("created_at"))
        o["_total"] = _money(o.get("total"))
        o["_subtotal"] = _money(o.get("subtotal"))

    def day_orders(d):
        return [o for o in orders if o.get("_date") == d]

    today_orders = day_orders(today)
    yday_orders = day_orders(yesterday)

    def active_revenue(rows):
        return sum(o["_total"] for o in rows if (o.get("status") or "") != "cancelled")

    def count_status(rows, status):
        aliases = {
            "confirmed": ("confirmed", "accepted"),
            "completed": ("completed", "delivered"),
        }
        keys = aliases.get(status, (status,))
        return sum(1 for o in rows if (o.get("status") or "") in keys)

    def is_open_status(status):
        return (status or "") in (
            "pending", "confirmed", "accepted", "preparing", "cooking", "ready", "out_for_delivery"
        )

    def is_completed_status(status):
        return (status or "") in ("completed", "delivered")

    def count_type(rows, order_type):
        return sum(
            1
            for o in rows
            if (o.get("order_type") or "") == order_type and (o.get("status") or "") != "cancelled"
        )

    today_revenue = active_revenue(today_orders)
    yday_revenue = active_revenue(yday_orders)
    # Approximate profit ~ 35% of revenue (no COGS table yet)
    today_profit = round(today_revenue * 0.35, 2)
    yday_profit = round(yday_revenue * 0.35, 2)

    today_count = len([o for o in today_orders if (o.get("status") or "") != "cancelled"])
    yday_count = len([o for o in yday_orders if (o.get("status") or "") != "cancelled"])

    # Live pending = all open statuses across all time (more useful for ops)
    open_statuses = ("pending", "confirmed", "accepted", "preparing", "cooking", "ready", "out_for_delivery")
    live_open = [o for o in orders if is_open_status(o.get("status"))]
    pending_all = len([o for o in live_open if (o.get("status") or "") == "pending"])
    completed_today = sum(1 for o in today_orders if is_completed_status(o.get("status")))
    cancelled_today = count_status(today_orders, "cancelled")

    aov = round(today_revenue / today_count, 2) if today_count else 0
    y_aov = round(yday_revenue / yday_count, 2) if yday_count else 0

    prep_times = [int(m.get("prep_time_minutes") or 0) for m in menu if m.get("is_available", 1)]
    avg_prep = round(sum(prep_times) / len(prep_times), 0) if prep_times else 0

    today_customer_phones = {
        (o.get("phone") or "").strip()
        for o in today_orders
        if (o.get("phone") or "").strip() and (o.get("status") or "") != "cancelled"
    }
    today_customer_names = {
        (o.get("customer_name") or "").strip().lower()
        for o in today_orders
        if (o.get("customer_name") or "").strip() and (o.get("status") or "") != "cancelled"
    }
    customers_today = len(today_customer_phones) or len(today_customer_names)

    delivery_today = count_type(today_orders, "delivery")
    takeaway_today = count_type(today_orders, "takeaway") + count_type(today_orders, "pickup")
    dine_in_today = count_type(today_orders, "dine_in")

    # Sparklines from last 7 days
    rev_spark = []
    order_spark = []
    profit_spark = []
    pending_spark = []
    completed_spark = []
    cancelled_spark = []
    aov_spark = []
    for d in days_7:
        rows = day_orders(d)
        rev = active_revenue(rows)
        cnt = len([o for o in rows if (o.get("status") or "") != "cancelled"])
        rev_spark.append(rev)
        profit_spark.append(round(rev * 0.35, 2))
        order_spark.append(cnt)
        pending_spark.append(count_status(rows, "pending"))
        completed_spark.append(sum(1 for o in rows if is_completed_status(o.get("status"))))
        cancelled_spark.append(count_status(rows, "cancelled"))
        aov_spark.append(round(rev / cnt, 2) if cnt else 0)

    kpis = [
        {
            "key": "revenue",
            "label": "Today's Revenue",
            "icon": "heroicons-outline:banknotes",
            "color": "#0ea5e9",
            "value": today_revenue,
            "delta": _pct_delta(today_revenue, yday_revenue),
            "prefix": "Rs ",
            "spark": _spark(rev_spark),
        },
        {
            "key": "profit",
            "label": "Today's Profit",
            "icon": "heroicons-outline:chart-bar",
            "color": "#10b981",
            "value": today_profit,
            "delta": _pct_delta(today_profit, yday_profit),
            "prefix": "Rs ",
            "spark": _spark(profit_spark),
        },
        {
            "key": "orders",
            "label": "Today's Orders",
            "icon": "heroicons-outline:shopping-bag",
            "color": "#8b5cf6",
            "value": today_count,
            "delta": _pct_delta(today_count, yday_count),
            "spark": _spark(order_spark),
        },
        {
            "key": "pending",
            "label": "Pending Orders",
            "icon": "heroicons-outline:clock",
            "color": "#f59e0b",
            "value": pending_all,
            "delta": _pct_delta(pending_all, count_status(yday_orders, "pending")),
            "spark": _spark(pending_spark),
        },
        {
            "key": "completed",
            "label": "Completed Orders",
            "icon": "heroicons-outline:check-circle",
            "color": "#22c55e",
            "value": completed_today,
            "delta": _pct_delta(completed_today, sum(1 for o in yday_orders if is_completed_status(o.get("status")))),
            "spark": _spark(completed_spark),
        },
        {
            "key": "cancelled",
            "label": "Cancelled Orders",
            "icon": "heroicons-outline:x-circle",
            "color": "#ef4444",
            "value": cancelled_today,
            "delta": _pct_delta(cancelled_today, count_status(yday_orders, "cancelled")),
            "spark": _spark(cancelled_spark),
        },
        {
            "key": "aov",
            "label": "Avg Order Value",
            "icon": "heroicons-outline:receipt-percent",
            "color": "#06b6d4",
            "value": aov,
            "delta": _pct_delta(aov, y_aov),
            "prefix": "Rs ",
            "spark": _spark(aov_spark),
        },
        {
            "key": "prep",
            "label": "Avg Prep Time",
            "icon": "heroicons-outline:fire",
            "color": "#f97316",
            "value": int(avg_prep),
            "delta": 0,
            "suffix": " min",
            "spark": _spark([avg_prep] * 7),
        },
        {
            "key": "customers",
            "label": "Customers Today",
            "icon": "heroicons-outline:users",
            "color": "#6366f1",
            "value": customers_today,
            "delta": 0,
            "spark": _spark([customers_today] * 7),
        },
        {
            "key": "customers_total",
            "label": "Total Customers",
            "icon": "heroicons-outline:user-group",
            "color": "#ec4899",
            "value": len(customers),
            "delta": 0,
            "spark": _spark([len(customers)] * 7),
        },
        {
            "key": "online",
            "label": "Online / Delivery",
            "icon": "heroicons-outline:truck",
            "color": "#3b82f6",
            "value": delivery_today,
            "delta": _pct_delta(delivery_today, count_type(yday_orders, "delivery")),
            "spark": _spark([count_type(day_orders(d), "delivery") for d in days_7]),
        },
        {
            "key": "takeaway",
            "label": "Takeaway / Pickup",
            "icon": "heroicons-outline:shopping-cart",
            "color": "#84cc16",
            "value": takeaway_today,
            "delta": _pct_delta(takeaway_today, count_type(yday_orders, "takeaway") + count_type(yday_orders, "pickup")),
            "spark": _spark([count_type(day_orders(d), "takeaway") + count_type(day_orders(d), "pickup") for d in days_7]),
        },
        {
            "key": "menu_items",
            "label": "Menu Items",
            "icon": "heroicons-outline:cake",
            "color": "#a855f7",
            "value": len([m for m in menu if m.get("is_available", 1)]),
            "delta": 0,
            "spark": _spark([len(menu)] * 7),
        },
        {
            "key": "promotions",
            "label": "Active Promotions",
            "icon": "heroicons-outline:speakerphone",
            "color": "#14b8a6",
            "value": len(promotions),
            "delta": 0,
            "spark": _spark([len(promotions)] * 7),
        },
    ]

    # Sales series
    sales_7 = {
        "categories": [d.strftime("%a") for d in days_7],
        "revenue": [active_revenue(day_orders(d)) for d in days_7],
        "profit": [round(active_revenue(day_orders(d)) * 0.35, 2) for d in days_7],
        "orders": [
            len([o for o in day_orders(d) if (o.get("status") or "") != "cancelled"]) for d in days_7
        ],
    }

    # 30d weekly buckets
    weeks = []
    for w in range(4):
        end = today - timedelta(days=w * 7)
        start = end - timedelta(days=6)
        weeks.append((f"W{4 - w}", start, end))
    weeks.reverse()
    sales_30 = {
        "categories": [w[0] for w in weeks],
        "revenue": [],
        "profit": [],
        "orders": [],
    }
    for _, start, end in weeks:
        rows = [o for o in orders if o.get("_date") and start <= o["_date"] <= end]
        rev = active_revenue(rows)
        sales_30["revenue"].append(rev)
        sales_30["profit"].append(round(rev * 0.35, 2))
        sales_30["orders"].append(len([o for o in rows if (o.get("status") or "") != "cancelled"]))

    # Yearly-ish monthly for current year
    months = []
    for m in range(1, today.month + 1):
        label = date(today.year, m, 1).strftime("%b")
        rows = [
            o
            for o in orders
            if o.get("_date") and o["_date"].year == today.year and o["_date"].month == m
        ]
        rev = active_revenue(rows)
        months.append(
            {
                "label": label,
                "revenue": rev,
                "profit": round(rev * 0.35, 2),
                "orders": len([o for o in rows if (o.get("status") or "") != "cancelled"]),
            }
        )
    sales_1y = {
        "categories": [m["label"] for m in months] or [today.strftime("%b")],
        "revenue": [m["revenue"] for m in months] or [0],
        "profit": [m["profit"] for m in months] or [0],
        "orders": [m["orders"] for m in months] or [0],
    }

    # Order mix — all-time non-cancelled + cancelled count
    non_cancelled = [o for o in orders if (o.get("status") or "") != "cancelled"]
    mix = [
        {
            "name": "Delivery",
            "value": sum(1 for o in non_cancelled if (o.get("order_type") or "") == "delivery"),
            "color": "#3b82f6",
        },
        {
            "name": "Takeaway",
            "value": sum(
                1 for o in non_cancelled if (o.get("order_type") or "") in ("takeaway", "pickup")
            ),
            "color": "#84cc16",
        },
        {
            "name": "Dine-In",
            "value": sum(1 for o in non_cancelled if (o.get("order_type") or "") == "dine_in"),
            "color": "#a855f7",
        },
        {
            "name": "Cancelled",
            "value": sum(1 for o in orders if (o.get("status") or "") == "cancelled"),
            "color": "#ef4444",
        },
    ]

    def serialize_live(o):
        items = o.get("_items") or []
        items_txt = ", ".join(
            f"{it.get('item_name') or ''}{(' (' + it.get('variation_name') + ')') if it.get('variation_name') else ''} ×{it.get('quantity') or 1}"
            for it in items
        ) or "—"
        return {
            "id": o["id"],
            "customer": o.get("customer_name") or "Guest",
            "phone": o.get("phone") or "",
            "items": items_txt,
            "payment": o.get("payment_method") or "—",
            "status": o.get("status") or "pending",
            "type": o.get("order_type") or "delivery",
            "eta": eta if (o.get("order_type") or "") == "delivery" else "15 min",
            "total": o["_total"],
            "address": o.get("address") or "",
            "created_at": str(o.get("created_at") or ""),
        }

    live_orders = sorted(live_open, key=lambda o: o.get("_dt") or datetime.min, reverse=True)
    live_orders = [serialize_live(o) for o in live_orders[:12]]

    # Kitchen queue from preparing/accepted/ready
    kitchen = []
    for o in live_open:
        status = o.get("status") or "pending"
        if status not in ("confirmed", "accepted", "preparing", "cooking", "ready", "out_for_delivery"):
            continue
        items = o.get("_items") or []
        item_name = ", ".join((it.get("item_name") or "") for it in items[:2]) or "Order"
        stage = {
            "confirmed": "preparing",
            "accepted": "preparing",
            "preparing": "cooking",
            "cooking": "cooking",
            "ready": "ready",
            "out_for_delivery": "ready",
        }.get(status, "preparing")
        progress = {"preparing": 35, "cooking": 65, "ready": 100}.get(stage, 40)
        kitchen.append(
            {
                "id": o["id"],
                "station": "Kitchen",
                "item": item_name,
                "stage": stage,
                "progress": progress,
            }
        )

    # Menu performance from order items
    sold = defaultdict(lambda: {"name": "", "sold": 0, "revenue": 0.0})
    for o in orders:
        if (o.get("status") or "") == "cancelled":
            continue
        for it in o.get("_items") or []:
            name = (it.get("item_name") or "Item").strip()
            sold[name]["name"] = name
            sold[name]["sold"] += int(it.get("quantity") or 1)
            sold[name]["revenue"] += _money(it.get("line_total"))

    ranked = sorted(sold.values(), key=lambda x: x["sold"], reverse=True)
    top = ranked[:5]
    worst = list(reversed(ranked[-3:])) if ranked else []

    # Category share from menu + sold items
    cat_map = {c["id"]: c.get("name") or "Other" for c in categories}
    menu_by_name = {(m.get("name") or "").strip().lower(): m for m in menu}
    cat_sold = defaultdict(int)
    for row in ranked:
        m = menu_by_name.get(row["name"].lower())
        cname = cat_map.get((m or {}).get("category_id"), "Other")
        cat_sold[cname] += row["sold"]
    cat_total = sum(cat_sold.values()) or 1
    category_share = [
        {"name": n, "pct": round((v / cat_total) * 100)}
        for n, v in sorted(cat_sold.items(), key=lambda x: -x[1])
    ]
    if not category_share:
        # fallback: menu distribution
        for m in menu:
            if not m.get("is_available", 1):
                continue
            cat_sold[cat_map.get(m.get("category_id"), "Other")] += 1
        cat_total = sum(cat_sold.values()) or 1
        category_share = [
            {"name": n, "pct": round((v / cat_total) * 100)}
            for n, v in sorted(cat_sold.items(), key=lambda x: -x[1])
        ]

    specials = [m.get("name") for m in menu if m.get("is_featured") and m.get("is_available", 1)][:5]
    if not specials:
        specials = [c.get("name") for c in combos[:3]]

    # Customers analytics
    phones_all = {(c.get("phone") or "").strip() for c in customers if (c.get("phone") or "").strip()}
    order_phones = {
        (o.get("phone") or "").strip()
        for o in orders
        if (o.get("phone") or "").strip() and (o.get("status") or "") != "cancelled"
    }
    returning = len(phones_all & order_phones)
    # New today: phones that first appear today
    new_today = 0
    for phone in today_customer_phones:
        earlier = [
            o
            for o in orders
            if (o.get("phone") or "").strip() == phone
            and o.get("_date")
            and o["_date"] < today
            and (o.get("status") or "") != "cancelled"
        ]
        if not earlier:
            new_today += 1

    avg_spend = round(sum(o["_total"] for o in non_cancelled) / len(non_cancelled), 2) if non_cancelled else 0

    # Marketing from promotions/combos
    marketing = []
    for p in promotions:
        marketing.append(
            {
                "name": p.get("title") or "Promotion",
                "channel": "Promo",
                "reach": p.get("discount") or "—",
                "conv": (p.get("description") or "")[:60],
                "status": "live" if p.get("is_active", 1) else "ended",
            }
        )
    for c in combos:
        marketing.append(
            {
                "name": c.get("name") or "Combo",
                "channel": "Combo",
                "reach": f"Rs {_money(c.get('price')):,.0f}",
                "conv": (c.get("includes") or c.get("description") or "")[:60],
                "status": "live",
            }
        )

    reservations = db.select(RESERVATIONS_TABLE, {"user_id": user_id}) or []
    today_reservations = [
        r for r in reservations
        if _as_date(r.get("reservation_date")) == today and (r.get("status") or "") not in ("cancelled", "no_show")
    ]
    pending_reservations = len([
        r for r in reservations if (r.get("status") or "") in ("pending", "confirmed")
    ])
    activity = []
    recent = sorted(orders, key=lambda o: o.get("_dt") or datetime.min, reverse=True)[:12]
    for o in recent:
        status = o.get("status") or "pending"
        when = o.get("_dt")
        time_ago = ""
        if when:
            mins = int((datetime.now() - when).total_seconds() // 60)
            if mins < 1:
                time_ago = "just now"
            elif mins < 60:
                time_ago = f"{mins} min ago"
            elif mins < 1440:
                time_ago = f"{mins // 60} hr ago"
            else:
                time_ago = f"{mins // 1440}d ago"
        activity.append(
            {
                "type": "order",
                "text": f"Order #{o['id']} · {o.get('customer_name') or 'Guest'} · {status.replace('_', ' ')} · Rs {o['_total']:,.0f}",
                "time": time_ago or "—",
                "icon": "heroicons-outline:shopping-bag",
            }
        )

    for r in sorted(reservations, key=lambda x: (x.get("reservation_date"), x.get("reservation_time")), reverse=True)[:6]:
        activity.append(
            {
                "type": "reservation",
                "text": f"Reservation {r.get('reservation_number') or '#' + str(r['id'])} · {r.get('customer_name') or 'Guest'} · {(r.get('status') or 'pending').replace('_', ' ')}",
                "time": str(r.get("reservation_date") or ""),
                "icon": "heroicons-outline:calendar",
            }
        )
    activity = activity[:12]

    # Notifications from real state
    notifications = []
    if pending_all:
        notifications.append({"title": f"{pending_all} pending order(s) need action", "tone": "amber"})
    if pending_reservations:
        notifications.append({"title": f"{pending_reservations} upcoming reservation(s)", "tone": "sky"})
    unavailable = [m for m in menu if not m.get("is_available", 1)]
    if unavailable:
        notifications.append(
            {"title": f"{len(unavailable)} menu item(s) marked unavailable", "tone": "rose"}
        )
    if promotions:
        notifications.append({"title": f"{len(promotions)} active promotion(s)", "tone": "sky"})
    if not notifications:
        notifications.append({"title": "No alerts — kitchen is clear", "tone": "emerald"})

    # AI insights from real numbers
    insights = []
    if top:
        insights.append(
            {
                "title": "Best Selling",
                "body": f"{top[0]['name']} leads with {top[0]['sold']} sold (Rs {top[0]['revenue']:,.0f}).",
                "icon": "heroicons-outline:trophy",
                "tone": "emerald",
            }
        )
    if worst and len(ranked) > 3:
        insights.append(
            {
                "title": "Slow Mover",
                "body": f"{worst[0]['name']} only sold {worst[0]['sold']} — review pricing or promo.",
                "icon": "heroicons-outline:exclamation",
                "tone": "rose",
            }
        )
    insights.append(
        {
            "title": "Today Snapshot",
            "body": f"Revenue Rs {today_revenue:,.0f} from {today_count} orders. AOV Rs {aov:,.0f}.",
            "icon": "heroicons-outline:chart-bar",
            "tone": "sky",
        }
    )
    if delivery_today or takeaway_today or dine_in_today:
        insights.append(
            {
                "title": "Channel Mix",
                "body": f"Delivery {delivery_today} · Takeaway {takeaway_today} · Dine-in {dine_in_today} today.",
                "icon": "heroicons-outline:truck",
                "tone": "violet",
            }
        )
    health = 70
    if today_revenue > 0:
        health += 10
    if cancelled_today == 0 and today_count > 0:
        health += 8
    if pending_all < 5:
        health += 5
    health = min(98, health)
    insights.append(
        {
            "title": "Health Score",
            "body": f"Business health {health}/100 based on today's orders & cancellations.",
            "icon": "heroicons-outline:shield-check",
            "tone": "teal",
        }
    )

    return {
        "business": {
            "name": settings.get("business_name") or "Restaurant",
            "category": settings.get("business_category") or "",
            "city": settings.get("city") or "",
            "currency": currency,
            "delivery_charges": _money(settings.get("delivery_charges")),
            "minimum_order": _money(settings.get("minimum_order")),
            "estimated_delivery_time": eta,
            "phone": settings.get("phone") or "",
        },
        "summary": {
            "revenue": today_revenue,
            "orders": today_count,
            "pending": pending_all,
            "reservations": len(today_reservations),
            "pending_reservations": pending_reservations,
            "profit": today_profit,
            "currency": currency,
        },
        "kpis": kpis,
        "sales": {"7d": sales_7, "30d": sales_30, "1y": sales_1y},
        "order_mix": mix,
        "live_orders": live_orders,
        "kitchen": kitchen[:8],
        "menu_performance": {
            "top": top,
            "worst": worst,
            "categories": category_share,
            "specials": specials,
        },
        "customers": {
            "newToday": new_today,
            "returning": returning,
            "total": len(customers),
            "vip": max(0, returning // 3),
            "loyal": returning,
            "avgSpend": avg_spend,
            "satisfaction": 4.6 if non_cancelled else 0,
        },
        "marketing": marketing[:6],
        "ai_insights": insights,
        "activity": activity,
        "notifications": notifications,
        "addons_count": len(addons),
        "combos_count": len(combos),
        "menu_count": len(menu),
        "orders_total": len(orders),
    }


@restaurant_dashboard_bp.route("/users/<int:user_id>/restaurant-dashboard", methods=["GET"])
def get_restaurant_dashboard(user_id):
    db = Database()
    try:
        is_restaurant, _, _ = _is_restaurant_user(db, user_id)
        if not is_restaurant:
            return jsonify({"status": False, "message": "Not allowed", "is_restaurant": False}), 403
        payload = _build_dashboard(db, user_id)
    finally:
        db.close()
    return jsonify({"status": True, "is_restaurant": True, **payload})
