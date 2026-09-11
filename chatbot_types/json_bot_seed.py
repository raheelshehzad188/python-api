import hashlib

from user_meta import _upsert_meta

from .json_bot import JSON_BOT_INSTRUCTIONS, JSON_BOT_TYPE_TITLE

JSON_BOT_USER_EMAIL = "json@test.com"
JSON_BOT_USER_PASSWORD = "admin"
JSON_BOT_USER_NAME = "JSON Writer"


def ensure_seed(db):
    """Create JSON Writer chatbot type and a sample bot user if missing."""
    ctype = db.row("chatbot_types", {"title": JSON_BOT_TYPE_TITLE})
    if not ctype:
        type_id = db.insert(
            "chatbot_types",
            {
                "title": JSON_BOT_TYPE_TITLE,
                "instructions": JSON_BOT_INSTRUCTIONS,
                "handler_class": "JsonBot",
            },
        )
    else:
        type_id = ctype["id"]
        updates = {}
        if ctype.get("handler_class") != "JsonBot":
            updates["handler_class"] = "JsonBot"
        if (ctype.get("instructions") or "").strip() != JSON_BOT_INSTRUCTIONS.strip():
            updates["instructions"] = JSON_BOT_INSTRUCTIONS
        if updates:
            db.update("chatbot_types", updates, {"id": type_id})

    user = db.row("admins", {"email": JSON_BOT_USER_EMAIL})
    if not user:
        user_id = db.insert(
            "admins",
            {
                "name": JSON_BOT_USER_NAME,
                "email": JSON_BOT_USER_EMAIL,
                "password": hashlib.md5(JSON_BOT_USER_PASSWORD.encode()).hexdigest(),
                "role_id": 2,
            },
        )
    else:
        user_id = user["id"]

    _upsert_meta(db, user_id, "chatbot_type_id", str(type_id))
    from json_bot_api import ensure_api_key

    ensure_api_key(db, user_id)
    return {"type_id": type_id, "user_id": user_id}
