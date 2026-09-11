import json


JSON_BOT_TYPE_TITLE = "JSON Writer"

JSON_BOT_INSTRUCTIONS = """You are a JSON Writer assistant.

The user sends ANY text request. You fulfill it and return ONLY JSON.
No chatty sentences, no markdown, no sql/tool/job types.

Always respond with this exact wrapper:
{
  "type": "json",
  "data": { }
}

`data` is the real result — an object or an array.

=========================================================
HOW TO SHAPE `data`
=========================================================
- Infer a useful schema from the request. Do not ask clarifying questions unless the message is empty.
- If they give a product title / "write details for X" / "product: X", return rich product copy, for example:
  {
    "title": "",
    "short_description": "",
    "full_description": "",
    "bullet_points": [],
    "specifications": {},
    "category": "",
    "tags": [],
    "price_suggestion": "",
    "seo_title": "",
    "seo_description": ""
  }
- If they paste a schema or example JSON, follow that schema exactly and fill it.
- If they ask for a blog, email, caption, FAQs, listing, recipe, CV, etc. — return an object that fits that task.
- Write high-quality content. Match the user's language (Urdu or English). Do not mix unless they do.
- Fill realistic values. Never leave every field empty.
- `data` must not be a raw paragraph string unless they asked for a single text field.
"""


def _pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


class JsonBot:
    """Handler that turns any user text request into a JSON result."""

    name = "JsonBot"
    label = "JSON Writer"

    def __init__(self, db=None, user_id=None, meta=None):
        self.db = db
        self.user_id = user_id
        self.meta = meta or {}

    def cache_payload(self):
        return (
            "# JSON Writer Mode\n"
            "- Every reply is structured JSON in {\"type\":\"json\",\"data\":{...}}.\n"
            "- Infer schema from the request (product details, captions, FAQs, custom fields).\n"
            "- Never use sql, tool, or job types.\n"
        )

    def process(self, reply_json):
        payload = self._extract_payload(reply_json)
        return {"type": "message", "message": _pretty(payload)}

    @classmethod
    def result_from_resolved(cls, resolved):
        """Turn a chats.resolve_chat_reply result into a JSON object/array."""
        handler = cls()
        reply_json = (resolved or {}).get("reply_json")
        reply = (resolved or {}).get("reply")
        if isinstance(reply_json, dict) or isinstance(reply_json, list):
            payload = handler._extract_payload(reply_json)
            if payload not in (None, "", {}, []):
                return payload
        if isinstance(reply, str) and reply.strip():
            try:
                parsed = json.loads(reply)
                if isinstance(parsed, (dict, list)):
                    return handler._extract_payload(parsed)
            except (ValueError, TypeError):
                return {"result": reply.strip()}
            return handler._extract_payload(reply)
        return handler._extract_payload(reply_json or {})

    def _extract_payload(self, reply_json):
        if isinstance(reply_json, list):
            return reply_json
        if not isinstance(reply_json, dict):
            return {"result": reply_json}

        rtype = (reply_json.get("type") or "").strip().lower()
        if rtype == "json":
            if "data" in reply_json:
                return reply_json.get("data")
            return {k: v for k, v in reply_json.items() if k != "type"}

        if rtype == "message":
            msg = reply_json.get("message")
            if isinstance(msg, (dict, list)):
                return msg
            if isinstance(msg, str) and msg.strip():
                try:
                    parsed = json.loads(msg)
                    if isinstance(parsed, (dict, list)):
                        return parsed
                except (ValueError, TypeError):
                    pass
                return {"result": msg.strip()}

        data = reply_json.get("data")
        if isinstance(data, (dict, list)):
            return data

        extra = {k: v for k, v in reply_json.items() if k != "type"}
        return extra or reply_json
