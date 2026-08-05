import json
import logging
import re
from flask import Blueprint, request, jsonify, abort, make_response
from db import Database
from pprint import pformat
from gemini import Gemini
from gemini_cache import ensure_user_cache, update_user_cache
from chatbot_types import get_class as get_handler_class
from user_meta import _upsert_meta

logger = logging.getLogger("chats")

chats_bp = Blueprint("chats", __name__)

# user_meta key where each user's ecommerce store endpoint URL is saved
ENDPOINT_KEY = "ecommerce_endpoint"

# Lines / openings that are model chain-of-thought — never show to customers.
_INTERNAL_REASONING_RE = re.compile(
    r"(?is)^\s*("
    r"the user wants\b|"
    r"the customer wants\b|"
    r"user (is asking|wants|requested)\b|"
    r"i (should|need to|will|shall|'ll|am going to)\b|"
    r"let me (call|check|use|look|ask)\b|"
    r"i('ll| will) (call|use|check|book|ask)\b|"
    r"(reasoning|thought|internal|analysis|plan)\s*:|"
    r"my (plan|reasoning|thought)\b|"
    r"next (i |step\b)|"
    r"looking at (the )?(cache|business knowledge|tool|catalog)\b|"
    r"based on (the )?(tool|cache|business knowledge)\b|"
    r"calling (the )?tool\b|"
    r"tool(_|\s*)(call|result)\b|"
    r"according to (my|the) (instructions|rules)\b"
    r").*"
)


def dd(*values):
    """PHP-style dump & die: pretty-prints the value(s) and stops the request,
    sending the dump back as the HTTP response. Call it like `dd(message)` —
    no `return` needed, execution halts right there."""
    body = "\n\n".join(pformat(v) for v in values)
    resp = make_response(f"<pre>{body}</pre>")
    resp.mimetype = "text/html"
    abort(resp)


def decide_next(reply_json, user_id, db=None):
    """Route Gemini's structured reply through the handler class assigned to
    the user's chatbot type (chatbot_types.handler_class)."""
    close_db = False
    if db is None:
        db = Database()
        close_db = True

    try:
        meta = {m["meta_key"]: m["meta_value"] for m in db.select("user_meta", {"user_id": user_id})}
        type_id = meta.get("chatbot_type_id")
        ctype = db.row("chatbot_types", {"id": type_id}) if type_id else None
        handler_name = (ctype or {}).get("handler_class")

        if handler_name:
            cls = get_handler_class(handler_name)
            if cls:
                handler = cls(db=db, user_id=user_id, meta=meta)
                return handler.process(reply_json)

        # No handler configured — basic fallback.
        reply_json = reply_json or {}
        rtype = reply_json.get("type")
        if rtype == "message":
            return {"type": "message", "message": reply_json.get("message", "")}
        return {"type": rtype, "data": reply_json}
    finally:
        if close_db:
            db.close()


def _user_ecommerce_info(db, user_id):
    """Whether the user's sub type is an ecommerce store, its class and the
    currently saved endpoint."""
    meta = {m["meta_key"]: m["meta_value"] for m in db.select("user_meta", {"user_id": user_id})}
    sub_type_id = meta.get("sub_type_id")
    sub = db.row("sub_categories", {"id": sub_type_id}) if sub_type_id else None
    return {
        "is_ecommerce": bool(sub and sub.get("is_ecommerce")),
        "ecommerce_class": (sub or {}).get("ecommerce_class"),
        "endpoint": meta.get(ENDPOINT_KEY) or "",
    }


@chats_bp.route("/users/<int:user_id>/ecommerce", methods=["GET"])
def get_user_ecommerce(user_id):
    db = Database()
    try:
        info = _user_ecommerce_info(db, user_id)
    finally:
        db.close()
    return jsonify({"status": True, **info})


@chats_bp.route("/users/<int:user_id>/ecommerce", methods=["POST"])
def set_user_ecommerce(user_id):
    data = request.json or {}
    endpoint = (data.get("endpoint") or "").strip()
    db = Database()
    try:
        _upsert_meta(db, user_id, ENDPOINT_KEY, endpoint)
    finally:
        db.close()
    return jsonify({"status": True, "message": "Endpoint saved"})

# Allowed chat sources
CHAT_TYPES = ("whatsapp", "facebook", "insta", "web")

# Max Gemini round-trips when tools/SQL results need another model turn.
MAX_GEMINI_STEPS = 8


def _gemini_call(gemini, contents, cache_id, system_instruction, cache_model=None):
    """Send conversation turns only; business knowledge comes from cachedContent."""
    if cache_id:
        return gemini.send(
            contents,
            cached_content=cache_id,
            system_instruction=None,  # never overwrite cached system instruction
            json_output=True,
            model=cache_model,
        )
    return gemini.send(
        contents,
        system_instruction=system_instruction,
        json_output=True,
        model=cache_model,
    )


def _looks_like_internal_reasoning(text):
    if not text or not isinstance(text, str):
        return False
    sample = text.strip()
    if not sample:
        return False
    first = sample.split("\n", 1)[0].strip()
    if _INTERNAL_REASONING_RE.match(first) or _INTERNAL_REASONING_RE.match(sample):
        return True
    # Whole blob is a tool/sql JSON payload — never customer-facing.
    if re.match(r'(?is)^\s*\{\s*"type"\s*:\s*"(tool|sql|job)"', sample):
        return True
    return False


def sanitize_customer_message(text):
    """Strip internal thoughts / tool JSON. Customers only see the real reply."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    t = text.strip()
    if not t:
        return ""

    # Prefer structured JSON message when present (even with leading junk).
    parsed = Gemini._parse_json_payload(t)
    if isinstance(parsed, dict):
        rtype = (parsed.get("type") or "").strip().lower()
        if rtype in ("tool", "sql", "job", "error"):
            return ""
        for key in ("message", "text", "reply", "content", "body"):
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                t = val.strip()
                break
        else:
            return ""

    # Drop reasoning lines / leading thought paragraphs.
    lines = []
    for line in t.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines:
                lines.append(line)
            continue
        if _looks_like_internal_reasoning(stripped):
            continue
        if re.match(r'(?is)^\{\s*"type"\s*:\s*"(tool|sql)"', stripped):
            continue
        lines.append(line)
    t = "\n".join(lines).strip()

    # If first paragraph is still reasoning, keep the rest.
    parts = re.split(r"\n\s*\n", t, maxsplit=1)
    if len(parts) == 2 and _looks_like_internal_reasoning(parts[0]):
        t = parts[1].strip()

    if _looks_like_internal_reasoning(t) and "\n" not in t:
        return ""

    return t


def _is_job_payload(obj):
    """True when Gemini returned a job-detection object (with or without type)."""
    if not isinstance(obj, dict):
        return False
    if "is_job" not in obj:
        return False
    # Job payloads use apply_email / subject / body — do NOT treat body as chat message.
    return True


def _normalize_reply_json(reply_json, reply_text):
    """Ensure we always have a usable structured reply.

    When Gemini returns plain text (or JSON parse fails), wrap sanitized text
    instead of inventing a generic greeting. Preserve tool/sql/job types.
    Never expose chain-of-thought as the customer message.
    """
    if isinstance(reply_json, dict):
        # Job-posting detection JSON must stay intact (has is_job + apply fields).
        # Previously "body" was mistaken for a chat message and emails never sent.
        if _is_job_payload(reply_json):
            return reply_json
        rtype = (reply_json.get("type") or "").strip().lower()
        if rtype in ("sql", "job", "error", "tool"):
            return reply_json
        msg = reply_json.get("message")
        if rtype == "message" and isinstance(msg, str) and msg.strip():
            clean = sanitize_customer_message(msg)
            return {"type": "message", "message": clean}
        for key in ("message", "text", "reply", "content"):
            val = reply_json.get(key)
            if isinstance(val, str) and val.strip():
                clean = sanitize_customer_message(val)
                return {"type": "message", "message": clean}
        # Only treat "body" as chat text when this is NOT a job payload
        # (job payloads already returned above).
        body_val = reply_json.get("body")
        if isinstance(body_val, str) and body_val.strip() and "is_job" not in reply_json:
            clean = sanitize_customer_message(body_val)
            return {"type": "message", "message": clean}

    # Try to recover JSON buried after reasoning in raw text.
    recovered = Gemini._parse_json_payload(reply_text or "")
    if isinstance(recovered, dict):
        if _is_job_payload(recovered):
            return recovered
        rtype = (recovered.get("type") or "").strip().lower()
        if rtype in ("sql", "job", "error", "tool"):
            return recovered
        for key in ("message", "text", "reply", "content"):
            val = recovered.get(key)
            if isinstance(val, str) and val.strip():
                return {
                    "type": "message",
                    "message": sanitize_customer_message(val),
                }

    text = sanitize_customer_message(reply_text or "")
    if text:
        return {"type": "message", "message": text}
    return {"type": "message", "message": ""}


def _products_from_sql_result(sql_result):
    """Extract the product rows list from a Custom_laravel run_sql result."""
    if not sql_result or not isinstance(sql_result, dict):
        return []
    payload = sql_result.get("data")
    if isinstance(payload, dict):
        rows = payload.get("data")
        if isinstance(rows, list):
            return rows
    if isinstance(payload, list):
        return payload
    return []


def resolve_chat_reply(
    gemini,
    contents,
    user_id,
    cache_id,
    system_instruction,
    db=None,
    cache_model=None,
):
    """Call Gemini, run SQL when needed, feed products back, and keep going
    until Gemini returns a user-facing message (type == "message")."""
    sql_result = None
    last_reply_json = None
    last_reply_text = ""
    last_response = None
    steps = []
    active_cache_id = cache_id
    active_model = cache_model

    for _ in range(MAX_GEMINI_STEPS):
        result = _gemini_call(
            gemini, contents, active_cache_id, system_instruction, cache_model=active_model
        )

        # Expired / missing cache → recreate once, then retry this turn.
        if (
            not result.get("success")
            and result.get("cache_error")
            and db is not None
            and active_cache_id
        ):
            logger.warning(
                "Cache error on generateContent | user_id=%s | cache_id=%s | error=%s → recreating",
                user_id,
                active_cache_id,
                result.get("error"),
            )
            refreshed = update_user_cache(db, user_id)
            if refreshed.get("success") and refreshed.get("cache_id"):
                active_cache_id = refreshed.get("cache_id") or ""
                active_model = refreshed.get("cache_model") or active_model
                system_instruction = (
                    refreshed.get("instruction_preview") or system_instruction
                )
                result = _gemini_call(
                    gemini,
                    contents,
                    active_cache_id,
                    system_instruction,
                    cache_model=active_model,
                )
            else:
                # Last resort: inline system instruction (no cache) on current model.
                from gemini import DEFAULT_MODEL as _DEFAULT_MODEL

                active_cache_id = ""
                active_model = _DEFAULT_MODEL
                result = _gemini_call(
                    gemini,
                    contents,
                    "",
                    system_instruction,
                    cache_model=active_model,
                )

        if not result.get("success"):
            return {"success": False, "error": result.get("error", "Gemini error")}

        response = result.get("response")
        reply_text = gemini.get_text(response)
        reply_json = _normalize_reply_json(gemini.get_json(response), reply_text)
        decision = decide_next(reply_json, user_id, db=db)

        last_reply_json = reply_json
        last_reply_text = reply_text
        last_response = response
        steps.append({
            "reply_json": reply_json,
            "decision": decision,
            "cache_id": result.get("cache_id"),
            "cache_attached": result.get("cache_attached"),
            "cached_content_token_count": result.get("cached_content_token_count"),
            "model": result.get("model"),
        })

        logger.info(
            "Gemini chat step | user_id=%s | cache_attached=%s | cache_id=%s | "
            "cached_tokens=%s | decision_type=%s | reply_preview=%s",
            user_id,
            result.get("cache_attached"),
            result.get("cache_id"),
            result.get("cached_content_token_count"),
            decision.get("type"),
            (decision.get("message") or reply_text or "")[:300],
        )

        if decision.get("type") == "message":
            # Never fall back to raw reply_text — it may contain chain-of-thought.
            final_message = sanitize_customer_message(decision.get("message") or "")
            if not final_message:
                final_message = sanitize_customer_message(
                    (reply_json or {}).get("message") if isinstance(reply_json, dict) else ""
                )
            return {
                "success": True,
                "reply": final_message,
                "reply_json": {
                    "type": "message",
                    "message": final_message,
                },
                "gemini_response": response,
                "sql_result": sql_result,
                "steps": steps,
                "cache_id": result.get("cache_id"),
                "cache_attached": result.get("cache_attached"),
            }

        if decision.get("type") == "sql":
            sql_result = decision.get("result")
            products = _products_from_sql_result(sql_result)

            # Tell Gemini what came back from the store so it can reply naturally.
            contents.append({"role": "model", "parts": [{"text": reply_text}]})
            follow_up = {
                "type": "sql_result",
                "sql": (sql_result or {}).get("sql"),
                "products": products,
                "count": len(products),
            }
            contents.append({
                "role": "user",
                "parts": [{"text": json.dumps(follow_up, ensure_ascii=False)}],
            })
            continue

        if decision.get("type") == "tool_result":
            # Services tools (and any future handler tools): feed result back to Gemini.
            contents.append({"role": "model", "parts": [{"text": reply_text}]})
            follow_up = {
                "type": "tool_result",
                "tool": decision.get("tool"),
                "args": decision.get("args") or {},
                "result": decision.get("result"),
            }
            contents.append({
                "role": "user",
                "parts": [{"text": json.dumps(follow_up, ensure_ascii=False)}],
            })

            # Booking mutations change availability — refresh cache when possible.
            tool_name = (decision.get("tool") or "").strip()
            tool_result = decision.get("result") or {}
            if (
                db is not None
                and tool_name in ("book_appointment", "cancel_booking", "reschedule_booking")
                and tool_result.get("success")
            ):
                try:
                    refreshed = update_user_cache(db, user_id)
                    if refreshed.get("success") and refreshed.get("cache_id"):
                        active_cache_id = refreshed.get("cache_id") or active_cache_id
                        active_model = refreshed.get("cache_model") or active_model
                        system_instruction = (
                            refreshed.get("instruction_preview") or system_instruction
                        )
                except Exception as exc:
                    logger.warning("Cache refresh after %s failed: %s", tool_name, exc)
            continue

        if decision.get("type") == "error":
            return {
                "success": False,
                "error": decision.get("message", "Unknown error"),
                "sql_result": sql_result,
            }

        if decision.get("type") == "job":
            job_data = decision.get("data") or {}
            final_message = sanitize_customer_message(
                decision.get("message") or reply_text or "Job posting received."
            ) or "Job posting received."
            return {
                "success": True,
                "reply": final_message,
                "reply_json": reply_json,
                "gemini_response": response,
                "sql_result": sql_result,
                "job_data": job_data,
                "steps": steps,
            }

        # Unknown type — never leak raw model reasoning / tool JSON.
        safe = sanitize_customer_message(decision.get("message") or reply_text or "")
        return {
            "success": True,
            "reply": safe,
            "reply_json": {"type": "message", "message": safe},
            "gemini_response": response,
            "sql_result": sql_result,
            "steps": steps,
        }

    return {
        "success": False,
        "error": "Too many Gemini steps (tool/SQL loop did not resolve to a message)",
        "reply": sanitize_customer_message(last_reply_text),
        "reply_json": last_reply_json,
        "gemini_response": last_response,
        "sql_result": sql_result,
        "steps": steps,
    }


def normalize_whatsapp_contact(raw):
    """Normalize WhatsApp ids for storage/lookup. Keeps @lid contacts as-is."""
    if not raw:
        return ""
    value = str(raw).strip()
    if not value or value == "unknown":
        return ""
    if value.endswith("@lid"):
        return value
    if "@" in value:
        value = value.split("@", 1)[0]
    return value.lstrip("+")


def get_or_create_whatsapp_chat(db, user_id, contact, name=None):
    """Find an existing WhatsApp chat for this contact or create a new one.

    ``name`` is preferred for chats.title (e.g. WhatsApp notifyName).
    """
    contact = normalize_whatsapp_contact(contact)
    if not contact:
        return None

    title = (name or "").strip() or f"WhatsApp {contact}"

    db.cursor.execute(
        """
        SELECT * FROM chats
        WHERE user_id=%s AND chat_type='whatsapp' AND user_number=%s
        ORDER BY COALESCE(lastmsg_at, created_at) DESC
        LIMIT 1
        """,
        [user_id, contact],
    )
    row = db.cursor.fetchone()
    if row:
        # Upgrade placeholder titles when we learn the contact's display name
        current = (row.get("title") or "").strip()
        if (name or "").strip() and (
            not current
            or current == f"WhatsApp {contact}"
            or current == contact
        ):
            db.execute("UPDATE chats SET title=%s WHERE id=%s", [title, row["id"]])
            row = db.row("chats", {"id": row["id"]}) or row
        return row

    new_id = db.insert(
        "chats",
        {
            "user_id": user_id,
            "title": title,
            "chat_type": "whatsapp",
            "user_number": contact,
        },
    )
    return db.row("chats", {"id": new_id})


def process_chat_message(db, chat_id, message, save=True):
    """Run Gemini for a chat message, optionally persist to chat_history."""
    message = (message or "").strip()
    if not message:
        return {"success": False, "error": "Message is required"}

    chat = db.row("chats", {"id": chat_id})
    if not chat:
        return {"success": False, "error": "Chat not found"}

    user_id = chat["user_id"]

    db.cursor.execute(
        "SELECT * FROM chat_history WHERE chat_id=%s ORDER BY created_at ASC",
        [chat_id],
    )
    history = db.cursor.fetchall()

    # Conversation turns only — business knowledge comes from cachedContent.
    contents = []
    for h in history:
        if h.get("request_text"):
            contents.append({"role": "user", "parts": [{"text": h["request_text"]}]})
        if h.get("response_text"):
            contents.append({"role": "model", "parts": [{"text": h["response_text"]}]})
    contents.append({"role": "user", "parts": [{"text": message}]})

    gemini = Gemini()
    if not gemini.api_key:
        return {"success": False, "error": "Gemini API key is not configured in Site Settings"}

    cache_state = ensure_user_cache(db, user_id)
    cache_id = cache_state.get("cache_id") or ""
    cache_model = cache_state.get("cache_model") or None
    system_instruction = cache_state.get("system_instruction") or ""

    logger.info(
        "process_chat_message | user_id=%s | chat_id=%s | cache_id=%s | "
        "cache_model=%s | cache_refreshed=%s | history_turns=%s | message=%s",
        user_id,
        chat_id,
        cache_id or None,
        cache_model,
        cache_state.get("refreshed"),
        len(contents),
        message[:200],
    )

    resolved = resolve_chat_reply(
        gemini,
        contents,
        user_id,
        cache_id,
        system_instruction,
        db=db,
        cache_model=cache_model,
    )
    if not resolved.get("success"):
        return {
            "success": False,
            "error": resolved.get("error", "Gemini error"),
            "sql_result": resolved.get("sql_result"),
        }

    reply = resolved.get("reply") or ""
    reply_json = resolved.get("reply_json")
    response = resolved.get("gemini_response")
    sql_result = resolved.get("sql_result")

    new_id = None
    if save:
        new_id = db.insert(
            "chat_history",
            {
                "chat_id": chat_id,
                "request_text": message,
                "response_text": reply,
                "gemini_response": json.dumps(response) if response else None,
            },
        )
        db.execute("UPDATE chats SET lastmsg_at=CURRENT_TIMESTAMP WHERE id=%s", [chat_id])

    return {
        "success": True,
        "id": new_id,
        "reply": reply,
        "reply_json": reply_json,
        "gemini_response": response,
        "sql_result": sql_result,
        "chat_id": chat_id,
        "cache_id": resolved.get("cache_id") or cache_id,
        "cache_attached": resolved.get("cache_attached"),
    }


def _ensure_column(db, table, column, ddl):
    db.cursor.execute(
        "SELECT COUNT(*) AS c FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        [table, column],
    )
    if db.cursor.fetchone()["c"] == 0:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def ensure_schema():
    """Create the chats + chat_history tables if they do not exist.

    chats        -> one row per conversation (per bot user / channel)
    chat_history -> every request/response exchange inside a chat
    """
    db = Database()
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                title VARCHAR(255) DEFAULT NULL,
                chat_type ENUM('whatsapp','facebook','insta','web') NOT NULL DEFAULT 'web',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                lastmsg_at TIMESTAMP NULL DEFAULT NULL
            )
            """
        )
        # Add title for databases created before this column existed
        _ensure_column(db, "chats", "title", "title VARCHAR(255) DEFAULT NULL AFTER user_id")
        _ensure_column(
            db,
            "chats",
            "user_number",
            "user_number VARCHAR(50) DEFAULT NULL AFTER chat_type",
        )
        _ensure_column(
            db,
            "chats",
            "ignore_ai",
            "ignore_ai TINYINT(1) NOT NULL DEFAULT 0 AFTER user_number",
        )
        _ensure_column(
            db,
            "chats",
            "blocked",
            "blocked TINYINT(1) NOT NULL DEFAULT 0 AFTER ignore_ai",
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                chat_id INT NOT NULL,
                request_text TEXT DEFAULT NULL,
                response_text TEXT DEFAULT NULL,
                gemini_response LONGTEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    finally:
        db.close()


@chats_bp.route("/chats", methods=["GET"])
def list_chats():
    """List chats. Pass ?user_id=X to filter to one user; omit it (super admin)
    to get every chat in the system."""
    user_id = request.args.get("user_id")

    db = Database()
    try:
        base = (
            "SELECT c.*, a.name AS user_name, "
            "h.request_text AS last_request, h.response_text AS last_response "
            "FROM chats c "
            "LEFT JOIN admins a ON a.id = c.user_id "
            "LEFT JOIN chat_history h ON h.id = ("
            "SELECT MAX(h2.id) FROM chat_history h2 WHERE h2.chat_id = c.id"
            ") "
        )
        order = " ORDER BY COALESCE(c.lastmsg_at, c.created_at) DESC"
        if user_id:
            db.cursor.execute(base + "WHERE c.user_id=%s" + order, [user_id])
        else:
            db.cursor.execute(base + order)
        rows = db.cursor.fetchall()
    finally:
        db.close()

    return jsonify({"status": True, "chats": rows})


@chats_bp.route("/chats", methods=["POST"])
def create_chat():
    data = request.json or {}
    user_id = data.get("user_id")
    chat_type = (data.get("chat_type") or "web").lower()
    title = (data.get("title") or "").strip() or None
    user_number = normalize_whatsapp_contact(data.get("user_number")) or None

    if not user_id:
        return jsonify({"status": False, "message": "user_id is required"}), 400
    if chat_type not in CHAT_TYPES:
        chat_type = "web"

    db = Database()
    try:
        insert_data = {"user_id": user_id, "title": title, "chat_type": chat_type}
        if user_number:
            insert_data["user_number"] = user_number
        new_id = db.insert("chats", insert_data)
        row = db.row("chats", {"id": new_id})
    finally:
        db.close()

    return jsonify({"status": True, "message": "Chat created", "chat": row})


@chats_bp.route("/chats/<int:chat_id>", methods=["DELETE"])
def delete_chat(chat_id):
    db = Database()
    try:
        db.delete("chat_history", {"chat_id": chat_id})
        db.delete("chats", {"id": chat_id})
    finally:
        db.close()
    return jsonify({"status": True, "message": "Chat deleted"})


@chats_bp.route("/users/<int:user_id>/chats", methods=["DELETE"])
def delete_all_user_chats(user_id):
    """Delete every chat (and its history) belonging to one bot user."""
    db = Database()
    try:
        db.cursor.execute("SELECT id FROM chats WHERE user_id=%s", [user_id])
        chat_ids = [row["id"] for row in db.cursor.fetchall()]
        for chat_id in chat_ids:
            db.delete("chat_history", {"chat_id": chat_id})
        if chat_ids:
            db.execute("DELETE FROM chats WHERE user_id=%s", [user_id])
    finally:
        db.close()

    count = len(chat_ids)
    return jsonify({
        "status": True,
        "message": f"Deleted {count} chat(s)",
        "deleted": count,
    })


@chats_bp.route("/chats/<int:chat_id>", methods=["GET"])
def get_chat(chat_id):
    db = Database()
    try:
        db.cursor.execute(
            """
            SELECT c.*, a.name AS user_name
            FROM chats c
            LEFT JOIN admins a ON a.id = c.user_id
            WHERE c.id=%s
            LIMIT 1
            """,
            [chat_id],
        )
        row = db.cursor.fetchone()
    finally:
        db.close()
    if not row:
        return jsonify({"status": False, "message": "Chat not found"}), 404
    return jsonify({"status": True, "chat": row})


@chats_bp.route("/chats/<int:chat_id>/flags", methods=["PUT"])
def update_chat_flags(chat_id):
    """Toggle ignore_ai / blocked for a chat."""
    data = request.json or {}
    db = Database()
    try:
        chat = db.row("chats", {"id": chat_id})
        if not chat:
            return jsonify({"status": False, "message": "Chat not found"}), 404

        updates = {}
        if "ignore_ai" in data:
            updates["ignore_ai"] = 1 if data.get("ignore_ai") else 0
        if "blocked" in data:
            updates["blocked"] = 1 if data.get("blocked") else 0
            # Blocking also turns off AI
            if updates["blocked"]:
                updates["ignore_ai"] = 1

        if not updates:
            return jsonify({"status": False, "message": "No flags provided"}), 400

        db.update("chats", updates, {"id": chat_id})
        row = db.row("chats", {"id": chat_id})
    finally:
        db.close()

    return jsonify({
        "status": True,
        "chat": row,
        "message": "Chat flags updated",
    })


@chats_bp.route("/chats/<int:chat_id>/human-reply", methods=["POST"])
def human_reply(chat_id):
    """Staff reply: save to history and send to WhatsApp when chat is WhatsApp."""
    data = request.json or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"status": False, "message": "Message is required"}), 400

    db = Database()
    try:
        chat = db.row("chats", {"id": chat_id})
        if not chat:
            return jsonify({"status": False, "message": "Chat not found"}), 404
        if chat.get("blocked"):
            return jsonify({"status": False, "message": "Chat is blocked"}), 403

        hist_id = db.insert(
            "chat_history",
            {
                "chat_id": chat_id,
                "request_text": None,
                "response_text": message,
                "gemini_response": json.dumps({"source": "human"}),
            },
        )
        db.execute(
            "UPDATE chats SET lastmsg_at=CURRENT_TIMESTAMP WHERE id=%s",
            [chat_id],
        )

        wa_sent = False
        wa_data = {}
        if (chat.get("chat_type") or "") == "whatsapp" and chat.get("user_number"):
            from whatsapp import (
                _user_meta_map,
                _load_session,
                _ensure_connected_session,
                _send_agency_message,
                _agency_ok,
                _is_lid_identity,
                _normalize_phone,
            )

            user_id = chat["user_id"]
            meta = _user_meta_map(db, user_id)
            session_data = _ensure_connected_session(db, user_id, meta, _load_session(meta))
            session_name = session_data.get("session_name") or f"user_{user_id}"
            phone_raw = chat.get("user_number") or ""
            is_lid = _is_lid_identity(phone_raw) or str(phone_raw).lower().endswith("@lid")
            phone = _normalize_phone(phone_raw)
            if phone:
                status_code, wa_data = _send_agency_message(
                    session_name, phone, message, is_lid=is_lid
                )
                wa_sent = _agency_ok(status_code, wa_data) or bool(wa_data.get("sent"))
    finally:
        db.close()

    return jsonify({
        "status": True,
        "id": hist_id,
        "reply": message,
        "whatsapp_sent": wa_sent,
        "whatsapp": wa_data,
        "message": "Reply saved" + (" and sent to WhatsApp" if wa_sent else ""),
    })


@chats_bp.route("/chats/<int:chat_id>/messages", methods=["POST"])
def send_message(chat_id):
    """Send a message in an existing chat. The whole conversation is replayed
    to Gemini (with the user's cached / system instruction) so the chat
    continues from where it left off, then the exchange is saved."""
    data = request.json or {}
    message = (data.get("message") or "").strip()
    save = data.get("save", True)
    if not message:
        return jsonify({"status": False, "message": "Message is required"}), 400

    db = Database()
    try:
        resolved = process_chat_message(db, chat_id, message, save=save)
        if not resolved.get("success"):
            return jsonify({
                "status": False,
                "message": resolved.get("error", "Gemini error"),
                "sql_result": resolved.get("sql_result"),
            }), 400
    finally:
        db.close()

    return jsonify({
        "status": True,
        "id": resolved.get("id"),
        "reply": resolved.get("reply"),
        "reply_json": resolved.get("reply_json"),
        "sql_result": resolved.get("sql_result"),
    })
