"""Create / update a demo Agent Tester user (tester@test.com / admin)."""

import hashlib

from db import Database
from admin import ensure_schema as ensure_admins
from chatbot_types.routes import ensure_schema as ensure_types
from chatbot_types.tester import TESTER_TYPE_TITLE
from user_meta import _upsert_meta


def _md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()


def main():
    ensure_admins()
    ensure_types()
    db = Database()
    try:
        ctype = db.row("chatbot_types", {"title": TESTER_TYPE_TITLE})
        if not ctype:
            raise SystemExit("Tester chatbot type missing — restart API once to seed")

        email = "tester@test.com"
        user = db.row("admins", {"email": email})
        if not user:
            uid = db.insert(
                "admins",
                {
                    "name": "Agent Tester",
                    "email": email,
                    "password": _md5("admin"),
                    "role_id": 2,
                },
            )
            user = db.row("admins", {"id": uid})
            print("created", email, "id", uid)
        else:
            print("exists", email, "id", user["id"])

        _upsert_meta(db, user["id"], "chatbot_type_id", str(ctype["id"]))
        print("assigned chatbot_type_id", ctype["id"], TESTER_TYPE_TITLE)
        print("login:", email, "/ admin")
    finally:
        db.close()


if __name__ == "__main__":
    main()
