import logging
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify
from db import Database
from gemini import Gemini, DEFAULT_MODEL
from user_meta import _upsert_meta

logger = logging.getLogger("gemini_cache")

gemini_cache_bp = Blueprint("gemini_cache", __name__)

CACHE_ID_KEY = "gemini_cache_id"
CACHE_EXPIRES_KEY = "gemini_cache_expires"
CACHE_MODEL_KEY = "gemini_cache_model"
SYSTEM_KEY = "gemini_system_instruction"
DEFAULT_TTL = 3600  # seconds


def _parse_expire_time(value):
    """Parse Gemini expireTime (RFC3339) to aware UTC datetime, or None."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def is_cache_expired(expire_time, skew_seconds=30):
    """True if expire_time is missing/unparseable/past (with small skew)."""
    dt = _parse_expire_time(expire_time)
    if not dt:
        return True
    now = datetime.now(timezone.utc)
    return dt.timestamp() <= (now.timestamp() + skew_seconds)


def build_type_cache_payload(db, user_id, meta=None):
    """Get extra cache payload from the assigned chatbot type handler."""
    meta = meta or {}
    if not meta:
        meta_rows = db.select("user_meta", {"user_id": user_id})
        meta = {m["meta_key"]: m["meta_value"] for m in meta_rows}

    type_id = meta.get("chatbot_type_id")
    ctype = db.row("chatbot_types", {"id": type_id}) if type_id else None
    handler_name = (ctype or {}).get("handler_class")
    if not handler_name:
        return ""

    from chatbot_types import get_class as get_handler_class

    cls = get_handler_class(handler_name)
    if not cls:
        return ""

    handler = cls(db=db, user_id=user_id, meta=meta)
    payload = getattr(handler, "cache_payload", lambda: "")()
    return payload.strip() if isinstance(payload, str) else ""


def build_system_instruction(db, user_id):
    """Combine base instructions + chatbot type handler cache_payload.

    Final cache =:
      user intro
      + chatbot type instructions (DB)
      + sub type instructions
      + additional bot_instructions
      + handler.cache_payload()   ← Services class data is MIXED here (never replaces)
    """
    parts = []

    user = db.row("admins", {"id": user_id})
    if user:
        parts.append(f"You are an assistant for the user '{user.get('name')}'.")

    meta_rows = db.select("user_meta", {"user_id": user_id})
    meta = {m["meta_key"]: m["meta_value"] for m in meta_rows}

    # Main chatbot type (DB instructions)
    if meta.get("chatbot_type_id"):
        ctype = db.row("chatbot_types", {"id": meta["chatbot_type_id"]})
        if ctype:
            parts.append(f"# Chatbot Type: {ctype.get('title')}")
            if ctype.get("instructions"):
                parts.append(ctype["instructions"])

    # Sub type
    if meta.get("sub_type_id"):
        sub = db.row("sub_categories", {"id": meta["sub_type_id"]})
        if sub:
            parts.append(f"# Sub Type: {sub.get('title')}")
            if sub.get("instructions"):
                parts.append(sub["instructions"])

    # Instructions: every instruction created by a super admin (role_id 1),
    # for any user, PLUS the instructions this specific user created themselves.
    instruction_rows = []
    seen_ids = set()

    def _collect(rows):
        for ins in rows:
            if ins["id"] in seen_ids:
                continue
            seen_ids.add(ins["id"])
            instruction_rows.append(ins)

    # Global instructions authored by any super admin
    for admin in db.select("admins", {"role_id": 1}):
        _collect(db.select("bot_instructions", {"user_id": admin["id"]}))

    # The user's own instructions
    _collect(db.select("bot_instructions", {"user_id": user_id}))

    if instruction_rows:
        parts.append("# Additional Instructions")
        for ins in instruction_rows:
            line = f"- {ins.get('title')}"
            if ins.get("content"):
                line += f": {ins['content']}"
            parts.append(line)

    # Always append type-class payload (Services: hours, holidays, catalog, etc.)
    # This is concatenated with the above — it does NOT replace them.
    type_payload = build_type_cache_payload(db, user_id, meta)
    if type_payload:
        parts.append(type_payload)

    return "\n\n".join(p for p in parts if p)


def update_user_cache(db, user_id, ttl_seconds=DEFAULT_TTL):
    """(Re)build the Gemini cache for a user and store its id in user_meta.

    Returns a result dict.
    """
    system_instruction = build_system_instruction(db, user_id)

    if not system_instruction.strip():
        return {"success": False, "message": "No instructions to cache for this user"}

    gemini = Gemini()

    if not gemini.api_key:
        return {"success": False, "message": "Gemini API key is not configured in Site Settings"}

    # Always keep the latest combined instruction so chat can fall back to
    # sending it inline when there is no cache.
    _upsert_meta(db, user_id, SYSTEM_KEY, system_instruction)

    # Delete the previous cache (if any) so we don't pile up caches
    old = db.row("user_meta", {"user_id": user_id, "meta_key": CACHE_ID_KEY})
    if old and old.get("meta_value"):
        gemini.delete_cache(old["meta_value"])

    result = gemini.create_cache(
        system_instruction,
        ttl_seconds=ttl_seconds,
        display_name=f"user-{user_id}",
    )

    if not result.get("success"):
        error = result.get("error", "Failed to create cache")
        err_l = error.lower()
        # Fall back to inline systemInstruction (no cachedContent):
        # - instructions too short for Google's min token cache
        # - free-tier / quota cannot create cachedContents
        inline_ok = any(
            marker in err_l
            for marker in (
                "too small",
                "min_total_token_count",
                "limit exceeded",
                "storage tokens",
                "cachedcontentstoragetokens",
                "quota",
                "resource exhausted",
                "free tier",
            )
        )
        if inline_ok:
            _upsert_meta(db, user_id, CACHE_ID_KEY, "")
            _upsert_meta(db, user_id, CACHE_EXPIRES_KEY, "")
            _upsert_meta(db, user_id, CACHE_MODEL_KEY, DEFAULT_MODEL)
            return {
                "success": True,
                "cached": False,
                "cache_id": None,
                "expire_time": None,
                "cache_model": DEFAULT_MODEL,
                "instruction_preview": system_instruction,
                "message": (
                    "Instructions saved without Gemini context cache "
                    f"(reason: {error[:180]}). Chat will send them inline."
                ),
            }
        return {"success": False, "message": error}

    cache_id = result.get("name")
    cache_model = result.get("model") or gemini.model or DEFAULT_MODEL
    _upsert_meta(db, user_id, CACHE_ID_KEY, cache_id)
    _upsert_meta(db, user_id, CACHE_EXPIRES_KEY, result.get("expire_time") or "")
    _upsert_meta(db, user_id, CACHE_MODEL_KEY, cache_model)

    logger.info(
        "User cache updated | user_id=%s | cache_id=%s | model=%s | expire=%s",
        user_id,
        cache_id,
        cache_model,
        result.get("expire_time"),
    )

    return {
        "success": True,
        "cached": True,
        "cache_id": cache_id,
        "expire_time": result.get("expire_time"),
        "cache_model": cache_model,
        "instruction_preview": system_instruction,
        "message": "Gemini cache updated",
    }


def get_user_cache_state(db, user_id):
    """Read stored cache meta for a user."""
    rows = db.select("user_meta", {"user_id": user_id})
    meta = {m["meta_key"]: m["meta_value"] for m in rows}
    return {
        "cache_id": (meta.get(CACHE_ID_KEY) or "").strip(),
        "expire_time": (meta.get(CACHE_EXPIRES_KEY) or "").strip(),
        "cache_model": (meta.get(CACHE_MODEL_KEY) or "").strip() or DEFAULT_MODEL,
        "system_instruction": meta.get(SYSTEM_KEY) or "",
    }


def ensure_user_cache(db, user_id, ttl_seconds=DEFAULT_TTL, force_refresh=False):
    """Ensure a valid Gemini cache exists for chat.

    - If missing/expired/invalid on Google's side → recreate and save new id.
    - Returns cache_id, cache_model, system_instruction for generateContent.
    """
    state = get_user_cache_state(db, user_id)
    system_instruction = state["system_instruction"] or build_system_instruction(db, user_id)
    if system_instruction and system_instruction != state["system_instruction"]:
        _upsert_meta(db, user_id, SYSTEM_KEY, system_instruction)
        state["system_instruction"] = system_instruction

    cache_id = state["cache_id"]
    expire_time = state["expire_time"]
    cache_model = state["cache_model"]

    # Missing expire_time alone is not fatal — probe Google first.
    needs_refresh = force_refresh or not cache_id
    if cache_id and expire_time and is_cache_expired(expire_time):
        logger.info(
            "Cache expired locally | user_id=%s | cache_id=%s | expire=%s → recreating",
            user_id,
            cache_id,
            expire_time,
        )
        needs_refresh = True

    if cache_id and not needs_refresh:
        # Trust local expire_time when present and still valid.
        # Probe Google only when expire_time is missing (legacy rows).
        if expire_time:
            logger.info(
                "Using existing cache | user_id=%s | cache_id=%s | model=%s | expire=%s",
                user_id,
                cache_id,
                cache_model,
                expire_time,
            )
            return {
                "success": True,
                "cache_id": cache_id,
                "cache_model": cache_model,
                "expire_time": expire_time,
                "system_instruction": system_instruction,
                "refreshed": False,
            }

        gemini = Gemini()
        if gemini.api_key:
            probe = gemini.get_cache(cache_id)
            if probe.get("success"):
                # Sync expire / model from live resource when available.
                live_expire = probe.get("expire_time") or expire_time
                live_model = (probe.get("model") or "").replace("models/", "") or cache_model
                if live_expire and live_expire != expire_time:
                    _upsert_meta(db, user_id, CACHE_EXPIRES_KEY, live_expire)
                    expire_time = live_expire
                if live_model and live_model != cache_model:
                    _upsert_meta(db, user_id, CACHE_MODEL_KEY, live_model)
                    cache_model = live_model
                if is_cache_expired(expire_time):
                    needs_refresh = True
                else:
                    logger.info(
                        "Using existing cache | user_id=%s | cache_id=%s | model=%s | expire=%s",
                        user_id,
                        cache_id,
                        cache_model,
                        expire_time,
                    )
                    return {
                        "success": True,
                        "cache_id": cache_id,
                        "cache_model": cache_model,
                        "expire_time": expire_time,
                        "system_instruction": system_instruction,
                        "refreshed": False,
                    }
            else:
                logger.warning(
                    "Stored cache invalid | user_id=%s | cache_id=%s | error=%s → recreating",
                    user_id,
                    cache_id,
                    probe.get("error"),
                )
                needs_refresh = True
        else:
            # No API key to probe — use stored id; generateContent will surface errors.
            return {
                "success": True,
                "cache_id": cache_id,
                "cache_model": cache_model,
                "expire_time": expire_time,
                "system_instruction": system_instruction,
                "refreshed": False,
            }

    if needs_refresh:
        result = update_user_cache(db, user_id, ttl_seconds=ttl_seconds)
        if not result.get("success"):
            # Clear stale expired cache ids so we never pin generateContent to them.
            _upsert_meta(db, user_id, CACHE_ID_KEY, "")
            _upsert_meta(db, user_id, CACHE_EXPIRES_KEY, "")
            _upsert_meta(db, user_id, CACHE_MODEL_KEY, DEFAULT_MODEL)
            return {
                "success": True,
                "cache_id": "",
                "cache_model": DEFAULT_MODEL,
                "expire_time": "",
                "system_instruction": system_instruction,
                "refreshed": False,
                "cached": False,
                "message": result.get("message"),
            }
        return {
            "success": True,
            "cache_id": result.get("cache_id") or "",
            "cache_model": result.get("cache_model") or DEFAULT_MODEL,
            "expire_time": result.get("expire_time") or "",
            "system_instruction": result.get("instruction_preview") or system_instruction,
            "refreshed": True,
            "cached": result.get("cached", True),
            "message": result.get("message"),
        }

    return {
        "success": True,
        "cache_id": cache_id,
        "cache_model": cache_model,
        "expire_time": expire_time,
        "system_instruction": system_instruction,
        "refreshed": False,
    }


def users_with_chatbot_type(db, type_id):
    """User ids that have this chatbot type assigned in user_meta."""
    rows = db.select("user_meta", {"meta_key": "chatbot_type_id", "meta_value": str(type_id)})
    return [r["user_id"] for r in rows]


def refresh_caches_for_chatbot_type(db, type_id, ttl_seconds=DEFAULT_TTL):
    """Rebuild Gemini cache for every user assigned to this chatbot type."""
    refreshed = []
    for user_id in users_with_chatbot_type(db, type_id):
        result = update_user_cache(db, user_id, ttl_seconds=ttl_seconds)
        refreshed.append({"user_id": user_id, **result})
    return refreshed


def users_with_role(db, role_id):
    """All admin user ids with the given role_id."""
    return [r["id"] for r in db.select("admins", {"role_id": role_id})]


def refresh_caches_for_role(db, role_id, ttl_seconds=DEFAULT_TTL):
    """Rebuild Gemini cache for every user with this role."""
    refreshed = []
    for user_id in users_with_role(db, role_id):
        result = update_user_cache(db, user_id, ttl_seconds=ttl_seconds)
        refreshed.append({"user_id": user_id, **result})
    return refreshed


def refresh_cache_after_instruction_change(db, owner_user_id, ttl_seconds=DEFAULT_TTL):
    """After an instruction is created/updated/deleted:
    - Super admin (role 1) -> refresh all role-2 bot users
    - Role-2 user -> refresh only their own cache
    """
    owner = db.row("admins", {"id": owner_user_id})
    if owner and owner.get("role_id") == 1:
        return refresh_caches_for_role(db, 2, ttl_seconds=ttl_seconds)
    result = update_user_cache(db, owner_user_id, ttl_seconds=ttl_seconds)
    return [{"user_id": owner_user_id, **result}]


@gemini_cache_bp.route("/users/<int:user_id>/gemini-cache", methods=["GET"])
def get_gemini_cache(user_id):
    db = Database()
    try:
        rows = db.select("user_meta", {"user_id": user_id})
        meta = {m["meta_key"]: m["meta_value"] for m in rows}
        # Live rebuild = base instructions MIXED with class cache_payload
        live_preview = build_system_instruction(db, user_id)
        stored = meta.get(SYSTEM_KEY) or ""
    finally:
        db.close()

    return jsonify({
        "status": True,
        "cache_id": meta.get(CACHE_ID_KEY) or "",
        "expire_time": meta.get(CACHE_EXPIRES_KEY) or "",
        "cache_model": meta.get(CACHE_MODEL_KEY) or "",
        "stored_instruction": stored,
        # Prefer last saved (what Gemini currently has); fall back to live mix
        "instruction_preview": stored or live_preview,
        "live_preview": live_preview,
    })


@gemini_cache_bp.route("/users/<int:user_id>/gemini-cache", methods=["POST"])
def refresh_gemini_cache(user_id):
    data = request.json or {}
    ttl = data.get("ttl_seconds", DEFAULT_TTL)

    db = Database()
    try:
        result = update_user_cache(db, user_id, ttl_seconds=ttl)
    finally:
        db.close()

    if not result.get("success"):
        return jsonify({"status": False, "message": result.get("message")}), 400

    return jsonify({
        "status": True,
        "cached": result.get("cached", True),
        "message": result.get("message", "Gemini cache updated"),
        "cache_id": result.get("cache_id"),
        "expire_time": result.get("expire_time"),
        "cache_model": result.get("cache_model"),
        "instruction_preview": result.get("instruction_preview"),
    })
