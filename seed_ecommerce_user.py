"""Create / refresh an Ecommerce chatbot demo user with full access.

Email: ecommerce@test.com
Password: admin

Sets:
  - Ecommerce chatbot type (handler_class=Ecommerce)
  - Ecommerce sub-category (is_ecommerce + Custom_laravel)
  - User meta: chatbot_type_id, sub_type_id, ecommerce_endpoint
"""

from __future__ import annotations

import hashlib
import sys

from db import Database
from user_meta import _upsert_meta

EMAIL = "ecommerce@test.com"
PASSWORD = "admin"
NAME = "Ecommerce Demo"
ENDPOINT = "https://demo.shop.test"  # placeholder store domain; change in Bot Settings

ECOM_INSTRUCTIONS = """You are an AI shopping assistant for an online store.

When the customer asks about products, prices, stock, orders, or categories,
respond with a SQL query JSON so the backend can fetch live store data:

{"type": "sql", "query": "SELECT ..."}

For normal chat (greetings, help), reply:
{"type": "message", "message": "your reply"}

Rules:
- Never invent products or prices — use SQL for catalog data.
- Keep replies short and helpful.
- Always return valid JSON only.
"""


def _md5(pw: str) -> str:
    return hashlib.md5(pw.encode()).hexdigest()


def ensure_ecommerce_user():
    db = Database()
    try:
        # 1) Chatbot type
        ctype = db.row("chatbot_types", {"title": "Ecommerce"})
        if not ctype:
            type_id = db.insert(
                "chatbot_types",
                {
                    "title": "Ecommerce",
                    "instructions": ECOM_INSTRUCTIONS,
                    "handler_class": "Ecommerce",
                },
            )
            print(f"Created Ecommerce chatbot type id={type_id}")
        else:
            type_id = ctype["id"]
            updates = {}
            if (ctype.get("handler_class") or "") != "Ecommerce":
                updates["handler_class"] = "Ecommerce"
            if not ctype.get("instructions"):
                updates["instructions"] = ECOM_INSTRUCTIONS
            if updates:
                db.update("chatbot_types", updates, {"id": type_id})
            print(f"Using Ecommerce chatbot type id={type_id}")

        # 2) Sub-category (ecommerce integration)
        sub = None
        for row in db.select("sub_categories", {"main_type_id": type_id}) or []:
            if row.get("is_ecommerce"):
                sub = row
                break
        if not sub:
            # Fallback: any titled Ecommerce under this type
            for row in db.select("sub_categories", {}) or []:
                if (row.get("title") or "").lower() == "ecommerce store" and int(row.get("main_type_id") or 0) == int(type_id):
                    sub = row
                    break

        if not sub:
            sub_id = db.insert(
                "sub_categories",
                {
                    "title": "Ecommerce Store",
                    "main_type_id": type_id,
                    "instructions": "Use Custom Laravel store SQL endpoint for product queries.",
                    "is_ecommerce": 1,
                    "ecommerce_class": "Custom_laravel",
                },
            )
            print(f"Created Ecommerce sub-category id={sub_id}")
        else:
            sub_id = sub["id"]
            db.update(
                "sub_categories",
                {"is_ecommerce": 1, "ecommerce_class": "Custom_laravel"},
                {"id": sub_id},
            )
            print(f"Using Ecommerce sub-category id={sub_id}")

        # 3) Role: chatbot user (role_id=2) — same as other demo bots
        role_id = 2
        role = db.row("roles", {"id": 2})
        if not role:
            # fall back to first non-admin role
            roles = db.select("roles", {}) or []
            for r in roles:
                if "admin" not in (r.get("name") or "").lower():
                    role_id = r["id"]
                    break

        # 4) Admin user
        user = db.row("admins", {"email": EMAIL})
        if not user:
            user_id = db.insert(
                "admins",
                {
                    "name": NAME,
                    "email": EMAIL,
                    "password": _md5(PASSWORD),
                    "role_id": role_id,
                },
            )
            print(f"Created user id={user_id} {EMAIL}")
        else:
            user_id = user["id"]
            db.update(
                "admins",
                {
                    "name": NAME,
                    "password": _md5(PASSWORD),
                    "role_id": role_id,
                },
                {"id": user_id},
            )
            print(f"Updated user id={user_id} {EMAIL}")

        # 5) Access meta
        _upsert_meta(db, user_id, "chatbot_type_id", str(type_id))
        _upsert_meta(db, user_id, "sub_type_id", str(sub_id))
        _upsert_meta(db, user_id, "ecommerce_endpoint", ENDPOINT)

        print("OK")
        print(f"  Email:    {EMAIL}")
        print(f"  Password: {PASSWORD}")
        print(f"  Type:     Ecommerce (id={type_id})")
        print(f"  Sub:      {sub_id} (Custom_laravel)")
        print(f"  Endpoint: {ENDPOINT}")
        return {"user_id": user_id, "type_id": type_id, "sub_id": sub_id}
    finally:
        db.close()


if __name__ == "__main__":
    try:
        ensure_ecommerce_user()
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
