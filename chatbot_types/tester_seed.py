from .tester import TESTER_TYPE_TITLE, TESTER_INSTRUCTIONS


def ensure_seed(db):
    """Ensure the Tester chatbot type row exists with handler_class=Tester."""
    ctype = db.row("chatbot_types", {"title": TESTER_TYPE_TITLE})
    if not ctype:
        # Also match legacy title variants
        for title in ("Agent Tester", "Tester Bot"):
            ctype = db.row("chatbot_types", {"title": title})
            if ctype:
                break

    if not ctype:
        db.insert(
            "chatbot_types",
            {
                "title": TESTER_TYPE_TITLE,
                "instructions": TESTER_INSTRUCTIONS,
                "handler_class": "Tester",
            },
        )
        return

    updates = {}
    if ctype.get("handler_class") != "Tester":
        updates["handler_class"] = "Tester"
    if (ctype.get("title") or "").strip() != TESTER_TYPE_TITLE:
        updates["title"] = TESTER_TYPE_TITLE
    if (ctype.get("instructions") or "").strip() != TESTER_INSTRUCTIONS.strip():
        updates["instructions"] = TESTER_INSTRUCTIONS
    if updates:
        db.update("chatbot_types", updates, {"id": ctype["id"]})
