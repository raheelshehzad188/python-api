from ecommerce import get_class as get_ecommerce_class

ENDPOINT_KEY = "ecommerce_endpoint"


class Ecommerce:
    """Handler for ecommerce chatbot types — runs store SQL when Gemini asks."""

    name = "Ecommerce"
    label = "Ecommerce"

    def __init__(self, db=None, user_id=None, meta=None):
        self.db = db
        self.user_id = user_id
        self.meta = meta or {}

    def cache_payload(self):
        # Intentionally empty: keeps existing system-instruction behavior unchanged
        # unless we add structured ecommerce-specific cache data later.
        return ""

    def process(self, reply_json):
        reply_json = reply_json or {}
        rtype = reply_json.get("type")

        if rtype == "message":
            return {"type": "message", "message": reply_json.get("message", "")}

        if rtype == "sql":
            query = reply_json.get("query")
            result = self._run_sql(query)
            return {"type": "sql", "result": result}

        return {"type": rtype, "data": reply_json}

    def _run_sql(self, query):
        sub_type_id = self.meta.get("sub_type_id")
        sub = self.db.row("sub_categories", {"id": sub_type_id}) if sub_type_id else None

        if not sub or not sub.get("is_ecommerce") or not sub.get("ecommerce_class"):
            return {"success": False, "error": "No ecommerce integration set for this user's sub type"}

        cls = get_ecommerce_class(sub["ecommerce_class"])
        if not cls:
            return {
                "success": False,
                "error": f"Unknown ecommerce class '{sub['ecommerce_class']}'",
            }

        endpoint = self.meta.get(ENDPOINT_KEY) or ""
        store = cls(db=self.db, endpoint=endpoint)
        return store.run_sql(query)
