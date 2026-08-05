import hashlib

from user_meta import _upsert_meta

GENERAL_TYPE_TITLE = "General"
GENERAL_USER_EMAIL = "general@test.com"
GENERAL_USER_PASSWORD = "123456"

GENERAL_INSTRUCTIONS = """You are a friendly general-purpose assistant.
Reply naturally and helpfully to the user.

Always respond in JSON using this exact format:
{"type": "message", "message": "your reply here"}

Do not use sql, job posting, or other special types — only simple chat messages."""


def ensure_seed(db):
    """Create General chatbot type and a sample bot user if missing."""
    ctype = db.row("chatbot_types", {"title": GENERAL_TYPE_TITLE})
    if not ctype:
        type_id = db.insert(
            "chatbot_types",
            {
                "title": GENERAL_TYPE_TITLE,
                "instructions": GENERAL_INSTRUCTIONS,
                "handler_class": "General",
            },
        )
    else:
        type_id = ctype["id"]
        updates = {}
        if not ctype.get("handler_class"):
            updates["handler_class"] = "General"
        if not ctype.get("instructions"):
            updates["instructions"] = GENERAL_INSTRUCTIONS
        if updates:
            db.update("chatbot_types", updates, {"id": type_id})

    user = db.row("admins", {"email": GENERAL_USER_EMAIL})
    if not user:
        user_id = db.insert(
            "admins",
            {
                "name": "General User",
                "email": GENERAL_USER_EMAIL,
                "password": hashlib.md5(GENERAL_USER_PASSWORD.encode()).hexdigest(),
                "role_id": 2,
            },
        )
    else:
        user_id = user["id"]

    _upsert_meta(db, user_id, "chatbot_type_id", str(type_id))
    return {"type_id": type_id, "user_id": user_id}
