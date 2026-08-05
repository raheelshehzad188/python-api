"""QA smoke tests for the Restaurant chatbot tools & cache.

Runs every scenario from the Restaurant spec against the seeded Elite Pizza
user (pizza@test.com). Does not touch Services / Ecommerce / Job Posting.
"""

from __future__ import annotations

import json
import sys

from db import Database
from restaurant_tools import run_tool, TOOL_NAMES
from restaurant_schema import ensure_restaurant_schema

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label}  {detail}")


def main():
    ensure_restaurant_schema()
    db = Database()
    try:
        user = db.row("admins", {"email": "pizza@test.com"})
        if not user:
            print("ERROR: pizza@test.com not found — run seed_elite_pizza.py first")
            sys.exit(1)
        uid = user["id"]
        print(f"Restaurant QA | user_id={uid} | tools={len(TOOL_NAMES)}\n")

        # --- Business / delivery rules ------------------------------------ #
        bi = run_tool(db, uid, "get_business_info")
        check("get_business_info", bi.get("success"))
        b = bi.get("business") or {}
        check("Business name Elite Pizza", b.get("name") == "Elite Pizza")
        check("Delivery charges 250", b.get("delivery_charges") == 250)
        check("Minimum order 500", b.get("minimum_order") == 500)
        check("Estimated delivery set", "30" in (b.get("estimated_delivery_time") or ""))

        # --- Categories / menu -------------------------------------------- #
        cats = run_tool(db, uid, "get_categories")
        check("get_categories", cats.get("success") and len(cats.get("categories") or []) == 5)

        full = run_tool(db, uid, "get_menu")
        check("Show full menu", full.get("success") and full.get("count") == 16)

        pizza = run_tool(db, uid, "get_menu", {"category": "Pizza"})
        check("Show pizza menu", pizza.get("success") and pizza.get("count") == 5)

        search_pizza = run_tool(db, uid, "search_menu", {"query": "pizza"})
        check("Search pizza", search_pizza.get("success") and search_pizza.get("count") >= 5)

        search_burger = run_tool(db, uid, "search_menu", {"query": "burger"})
        check("Search burger", search_burger.get("success") and search_burger.get("count") == 3)

        item = run_tool(db, uid, "get_menu_item", {"name": "Margherita"})
        check("Select pizza (get_menu_item)", item.get("success"))
        mi = item.get("item") or {}
        check("Ask for size — Small/Medium/Large", [v["name"] for v in mi.get("variations") or []] == ["Small", "Medium", "Large"])
        check("Ask for addons — 5 available", len(mi.get("addons") or []) == 5)

        # --- Promotions / combos / hours / holidays ----------------------- #
        promos = run_tool(db, uid, "get_promotions")
        check("Show promotions", promos.get("success") and len(promos.get("promotions") or []) == 2)

        combos = run_tool(db, uid, "get_combo_deals")
        check(
            "Show combo deals",
            combos.get("success")
            and len(combos.get("combo_deals") or []) == 1
            and combos["combo_deals"][0]["price"] == 3999,
        )

        hours = run_tool(db, uid, "get_working_hours")
        check("Working hours", hours.get("success") and len(hours.get("working_hours") or []) == 7)

        holidays = run_tool(db, uid, "get_holidays")
        check("Holidays", holidays.get("success") and len(holidays.get("holidays") or []) >= 1)

        # --- Calculate total via place_order ------------------------------ #
        # Large Margherita = 1200 + 400 = 1600; Extra Cheese = 200; qty 1
        # Delivery 250 → total 2050
        placed = run_tool(
            db,
            uid,
            "place_order",
            {
                "items": [
                    {"name": "Margherita", "variation": "Large", "addons": ["Extra Cheese"], "quantity": 1}
                ],
                "order_type": "delivery",
                "address": "Main Boulevard Lahore",
                "payment_method": "JazzCash",
                "customer_name": "QA Tester",
                "phone": "03001112233",
            },
        )
        check("Place order", placed.get("success"), json.dumps(placed)[:200])
        order = placed.get("order") or {}
        check("Calculate total (1600+200+250=2050)", order.get("total") == 2050.0, f"got {order.get('total')}")
        check("Ask quantity applied", order.get("items", [{}])[0].get("quantity") == 1)
        check("Ask delivery address saved", order.get("address") == "Main Boulevard Lahore")
        check("Ask payment method saved", order.get("payment_method") == "JazzCash")
        oid = order.get("id")

        # --- Track / cancel ----------------------------------------------- #
        tracked = run_tool(db, uid, "track_order", {"order_id": oid})
        check("Track order", tracked.get("success") and tracked.get("order", {}).get("status") == "pending")

        cancelled = run_tool(db, uid, "cancel_order", {"order_id": oid})
        check("Cancel order", cancelled.get("success") and cancelled.get("allowed") is True)
        check("Cancelled status", cancelled.get("order", {}).get("status") == "cancelled")

        # --- Minimum order enforcement ------------------------------------ #
        below = run_tool(
            db,
            uid,
            "place_order",
            {
                "items": [{"name": "Pepsi", "quantity": 1}],
                "order_type": "pickup",
                "payment_method": "Cash",
                "customer_name": "Tiny Order",
            },
        )
        check("Minimum order enforced", below.get("error") == "below_minimum_order")

        # --- Pickup has no delivery charge -------------------------------- #
        pickup = run_tool(
            db,
            uid,
            "place_order",
            {
                "items": [{"name": "Zinger Burger", "quantity": 1}],
                "order_type": "pickup",
                "payment_method": "Cash",
                "customer_name": "Pickup Guy",
                "phone": "03005556677",
            },
        )
        check("Pickup order placed", pickup.get("success"))
        check("Pickup has 0 delivery charges", pickup.get("order", {}).get("delivery_charges") == 0)
        check("Pickup total = burger price", pickup.get("order", {}).get("total") == 650.0)

        print(f"\nResults: {PASS} passed, {FAIL} failed")
        return 0 if FAIL == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
