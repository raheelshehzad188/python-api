"""Populate Elite Pizza demo data for the Restaurant chatbot.

Self-contained: creates the pizza test user (pizza@test.com / admin) if
missing, assigns the Restaurant chatbot type, seeds the full Pizza Shop
dataset and refreshes the Gemini cache.

Restaurant-module only — does not touch Services / Ecommerce / Job Posting.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

from db import Database
from restaurant_schema import ensure_restaurant_schema
from restaurant_settings import _ensure_user_defaults
from user_meta import _upsert_meta
from gemini_cache import update_user_cache

PIZZA_USER_EMAIL = "pizza@test.com"
PIZZA_USER_NAME = "Elite Pizza"
PIZZA_USER_PASSWORD = "admin"

CATEGORIES = [
    ("Pizza", "Hand-tossed pizzas baked fresh", 1),
    ("Burger", "Juicy burgers", 2),
    ("Fries", "Crispy fries", 3),
    ("Drinks", "Chilled beverages", 4),
    ("Desserts", "Sweet treats", 5),
]

MENU = {
    "Pizza": [
        ("Margherita", 1200, "Classic cheese & tomato"),
        ("Pepperoni", 1500, "Loaded pepperoni"),
        ("Chicken Fajita", 1700, "Spicy chicken fajita"),
        ("BBQ Chicken", 1800, "Smoky BBQ chicken"),
        ("Special Pizza", 2200, "House special loaded"),
    ],
    "Burger": [
        ("Zinger Burger", 650, "Crispy chicken fillet"),
        ("Beef Burger", 750, "Grilled beef patty"),
        ("Double Patty", 950, "Double beef patty"),
    ],
    "Fries": [
        ("Regular Fries", 250, "Classic salted fries"),
        ("Loaded Fries", 550, "Fries with cheese & sauce"),
    ],
    "Drinks": [
        ("Pepsi", 150, "330ml"),
        ("7UP", 150, "330ml"),
        ("Mountain Dew", 150, "330ml"),
        ("Mineral Water", 100, "500ml"),
    ],
    "Desserts": [
        ("Brownie", 350, "Chocolate fudge brownie"),
        ("Ice Cream", 300, "Vanilla scoop"),
    ],
}

# Variations for every Pizza item (price adjustment on the base price)
PIZZA_VARIATIONS = [
    ("Small", -200),
    ("Medium", 0),
    ("Large", 400),
]

ADDONS = [
    ("Extra Cheese", 200),
    ("Extra Chicken", 350),
    ("Olives", 150),
    ("Mushrooms", 180),
    ("Extra Sauce", 100),
]

PROMOTIONS = [
    ("20% Off Family Deal", "Get 20% off the Family Deal combo.", "20%"),
    ("Buy 2 Get 1 Drink Free", "Order any 2 pizzas and get a free drink.", "Free Drink"),
]

COMBOS = [
    ("Family Deal", "2 Large Pizzas + 2 Drinks", "2 Large Pizzas, 2 Drinks", 3999),
]

PAYMENT_METHODS = [
    ("Cash", "Pay cash on delivery / pickup"),
    ("Card", "Debit / credit card"),
    ("JazzCash", "Mobile wallet"),
    ("EasyPaisa", "Mobile wallet"),
]

FAQS = [
    ("What are your delivery charges?", "Delivery charges are 250 PKR."),
    ("What is the minimum order?", "Minimum order is 500 PKR."),
    ("How long does delivery take?", "Estimated delivery is 30-45 minutes."),
    ("Which payment methods do you accept?", "Cash, Card, JazzCash and EasyPaisa."),
    ("Do you offer pickup?", "Yes, you can choose pickup at checkout with no delivery charge."),
]


def _md5(value):
    return hashlib.md5(value.encode()).hexdigest()


def _resolve_restaurant_type_id(db):
    from chatbot_types.restaurant_seed import ensure_seed

    ensure_seed(db)
    row = db.row("chatbot_types", {"title": "Restaurant"})
    return row["id"] if row else None


def _resolve_user_id(db):
    user = db.row("admins", {"email": PIZZA_USER_EMAIL})
    if user:
        return int(user["id"])
    new_id = db.insert(
        "admins",
        {
            "name": PIZZA_USER_NAME,
            "email": PIZZA_USER_EMAIL,
            "password": _md5(PIZZA_USER_PASSWORD),
            "role_id": 2,
        },
    )
    return int(new_id)


def _clear(db, table, user_id):
    try:
        db.execute(f"DELETE FROM {table} WHERE user_id=%s", [user_id])
    except Exception:
        pass


def seed_elite_pizza(refresh_cache=True):
    ensure_restaurant_schema()
    db = Database()
    try:
        type_id = _resolve_restaurant_type_id(db)
        user_id = _resolve_user_id(db)

        # Assign the Restaurant chatbot type to this user
        if type_id:
            _upsert_meta(db, user_id, "chatbot_type_id", str(type_id))

        _ensure_user_defaults(db, user_id)

        # Business profile
        profile = {
            "currency_code": "PKR",
            "business_name": "Elite Pizza",
            "business_category": "Pizza Restaurant",
            "phone": "03001234567",
            "whatsapp": "03001234567",
            "email": "info@elitepizza.pk",
            "address": "Main Boulevard Lahore",
            "city": "Lahore",
            "about": "Elite Pizza serves fresh hand-tossed pizzas, burgers, fries, drinks and desserts.",
            "delivery_charges": 250,
            "minimum_order": 500,
            "estimated_delivery_time": "30-45 Minutes",
            "payment_methods": "Cash, Card, JazzCash, EasyPaisa",
            "delivery_rules": "Delivery available across the city. Delivery charge 250 PKR. Minimum order 500 PKR.",
        }
        existing = db.row("restaurant_settings", {"user_id": user_id})
        if existing:
            db.update("restaurant_settings", profile, {"user_id": user_id})
        else:
            profile["user_id"] = user_id
            db.insert("restaurant_settings", profile)

        # Working hours: every day 10:00 - 23:00
        _clear(db, "restaurant_working_hours", user_id)
        for day in range(7):
            db.insert(
                "restaurant_working_hours",
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

        # Clean demo tables for a fresh dataset
        for table in (
            "restaurant_variations",
            "restaurant_menu_items",
            "restaurant_categories",
            "restaurant_addons",
            "restaurant_combos",
            "restaurant_promotions",
            "restaurant_payment_methods",
            "restaurant_faqs",
            "restaurant_holidays",
        ):
            _clear(db, table, user_id)

        # Categories
        cat_ids = {}
        for name, desc, order in CATEGORIES:
            cat_ids[name] = db.insert(
                "restaurant_categories",
                {"user_id": user_id, "name": name, "description": desc, "sort_order": order, "is_active": 1},
            )

        # Menu items (+ pizza variations)
        for cat_name, items in MENU.items():
            for name, price, desc in items:
                item_id = db.insert(
                    "restaurant_menu_items",
                    {
                        "user_id": user_id,
                        "category_id": cat_ids[cat_name],
                        "name": name,
                        "description": desc,
                        "price": price,
                        "prep_time_minutes": 20 if cat_name == "Pizza" else 10,
                        "is_available": 1,
                        "is_featured": 1 if name in ("Special Pizza", "Zinger Burger") else 0,
                    },
                )
                if cat_name == "Pizza":
                    for i, (vname, adj) in enumerate(PIZZA_VARIATIONS):
                        db.insert(
                            "restaurant_variations",
                            {
                                "user_id": user_id,
                                "menu_item_id": item_id,
                                "name": vname,
                                "price_adjustment": adj,
                                "sort_order": i,
                                "is_active": 1,
                            },
                        )

        # Add-ons
        for i, (name, price) in enumerate(ADDONS):
            db.insert(
                "restaurant_addons",
                {"user_id": user_id, "name": name, "price": price, "sort_order": i, "is_active": 1},
            )

        # Promotions
        for title, desc, discount in PROMOTIONS:
            db.insert(
                "restaurant_promotions",
                {"user_id": user_id, "title": title, "description": desc, "discount": discount, "is_active": 1},
            )

        # Combos
        for name, desc, includes, price in COMBOS:
            db.insert(
                "restaurant_combos",
                {
                    "user_id": user_id,
                    "name": name,
                    "description": desc,
                    "includes": includes,
                    "price": price,
                    "is_active": 1,
                },
            )

        # Payment methods
        for i, (name, details) in enumerate(PAYMENT_METHODS):
            db.insert(
                "restaurant_payment_methods",
                {"user_id": user_id, "name": name, "details": details, "sort_order": i + 1, "is_active": 1},
            )

        # FAQs
        for q, a in FAQS:
            db.insert("restaurant_faqs", {"user_id": user_id, "question": q, "answer": a})

        # A demo holiday
        today = date.today()
        holiday = today + timedelta(days=5)
        db.insert(
            "restaurant_holidays",
            {
                "user_id": user_id,
                "holiday_date": holiday.isoformat(),
                "title": "Annual Maintenance",
                "description": "Kitchen closed for annual maintenance.",
            },
        )

        # One demo customer
        has_cust = False
        for c in db.select("restaurant_customers", {"user_id": user_id}) or []:
            if (c.get("phone") or "") == "03009998877":
                has_cust = True
                break
        if not has_cust:
            db.insert(
                "restaurant_customers",
                {
                    "user_id": user_id,
                    "name": "Bilal Ahmed",
                    "phone": "03009998877",
                    "email": "bilal@example.com",
                    "address": "House 12, Gulberg, Lahore",
                    "notes": "Returning customer",
                },
            )

        cache_result = None
        if refresh_cache:
            cache_result = update_user_cache(db, user_id)

        return {
            "success": True,
            "user_id": user_id,
            "type_id": type_id,
            "email": PIZZA_USER_EMAIL,
            "password": PIZZA_USER_PASSWORD,
            "cache": cache_result,
        }
    finally:
        db.close()


if __name__ == "__main__":
    result = seed_elite_pizza(refresh_cache=True)
    print(result)
