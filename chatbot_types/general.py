class General:
    """Handler for general-purpose chatbot types — direct Gemini message replies."""

    name = "General"
    label = "General"

    def __init__(self, db=None, user_id=None, meta=None):
        self.db = db
        self.user_id = user_id
        self.meta = meta or {}

    def cache_payload(self):
        return ""

    def process(self, reply_json):
        reply_json = reply_json or {}
        rtype = reply_json.get("type")

        if rtype == "message":
            return {
                "type": "message",
                "message": (reply_json.get("message") or "").strip(),
            }

        # If Gemini returned plain text in common keys, treat as a direct reply.
        for key in ("message", "text", "reply", "content", "body"):
            val = reply_json.get(key)
            if isinstance(val, str) and val.strip():
                return {"type": "message", "message": val.strip()}

        if isinstance(reply_json, str) and reply_json.strip():
            return {"type": "message", "message": reply_json.strip()}

        return {"type": "message", "message": ""}
