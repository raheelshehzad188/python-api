import hashlib
import hmac
import json
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, request, jsonify

import config
import infra_settings
from chats import get_or_create_whatsapp_chat, normalize_whatsapp_contact, process_chat_message
from db import Database
from user_meta import _upsert_meta
from webhook_logs import create_webhook_log
from wa_messages import store_from_webhook_payload, store_message

whatsapp_bp = Blueprint("whatsapp", __name__)

WA_SESSION_KEY = "wa_session"
WA_WEBHOOK_TOKEN_KEY = "wa_webhook_token"
WA_REPLY_API_KEY = "wa_reply_api_key"  # legacy; AgencyWA uses global admin keys
WA_AUTOMATION_KEY = "wa_automation_enabled"

CONNECTED_STATUSES = {"connected", "CONNECTED", "open", "OPEN", "inChat", "isLogged"}


def _user_meta_map(db, user_id):
    return {m["meta_key"]: m["meta_value"] for m in db.select("user_meta", {"user_id": user_id})}


def _is_chatbot_user(db, user_id):
    user = db.row("admins", {"id": user_id})
    return bool(user and user.get("role_id") == config.CHATBOT_ROLE_ID), user


def _load_session(meta):
    raw = (meta or {}).get(WA_SESSION_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _save_session(db, user_id, session_data):
    _upsert_meta(db, user_id, WA_SESSION_KEY, json.dumps(session_data))


def _get_or_create_webhook_token(db, user_id, meta):
    token = (meta or {}).get(WA_WEBHOOK_TOKEN_KEY)
    if not token:
        token = secrets.token_urlsafe(24)
        _upsert_meta(db, user_id, WA_WEBHOOK_TOKEN_KEY, token)
    return token


def _webhook_url(user_id, token):
    base = infra_settings.wa_app_public_url().rstrip("/")
    return f"{base}/webhooks/whatsapp/{user_id}/{token}"


def _session_name(user_id):
    return f"user_{user_id}"


def _agency_base():
    return infra_settings.agency_api_base_url().rstrip("/")


def _agency_headers():
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-API-Key": infra_settings.agency_api_key(),
        "X-API-Secret": infra_settings.agency_api_secret(),
    }


def _agency_request(method, path, body=None, query=None, timeout=30):
    if not infra_settings.agency_configured():
        return 0, {
            "success": False,
            "ok": False,
            "error": "agency_not_configured",
            "message": (
                "AgencyWA is not configured. Super Admin must set Agency API URL, "
                "API Key and API Secret in Site Settings."
            ),
        }

    url = f"{_agency_base()}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"

    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=payload, headers=_agency_headers(), method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw or not raw.strip():
                return resp.status, {
                    "success": False,
                    "ok": False,
                    "error": "empty_response",
                    "message": "AgencyWA returned an empty response",
                }
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                snippet = raw[:200].strip().replace("\n", " ")
                return resp.status, {
                    "success": False,
                    "ok": False,
                    "error": "invalid_json",
                    "message": (
                        f"AgencyWA is not responding with JSON at {_agency_base()}. "
                        f"({snippet})"
                    ),
                }
            if isinstance(data, dict) and "ok" not in data:
                data["ok"] = bool(data.get("success"))
            return resp.status, data if isinstance(data, dict) else {"success": False, "data": data}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        try:
            data = json.loads(raw) if raw and raw.strip() else {}
        except (TypeError, ValueError):
            data = {"message": (raw or str(e))[:300]}
        if not isinstance(data, dict):
            data = {"message": str(data)}
        data.setdefault("success", False)
        data.setdefault("ok", False)
        if not data.get("message") and not data.get("error"):
            data["message"] = f"AgencyWA HTTP {e.code}"
        return e.code, data
    except urllib.error.URLError as e:
        return 0, {
            "success": False,
            "ok": False,
            "error": "agency_unreachable",
            "message": (
                f"Cannot reach AgencyWA at {_agency_base()}: {e.reason}. "
                "Check the Agency API URL in Site Settings."
            ),
        }
    except (TimeoutError, socket.timeout):
        return 0, {
            "success": False,
            "ok": False,
            "error": "agency_timeout",
            "message": f"AgencyWA timed out after {timeout}s at {_agency_base()}.",
        }


def _is_connected_status(status):
    return str(status or "").strip().lower() in {s.lower() for s in CONNECTED_STATUSES}


def _normalize_qr_image(qr):
    """AgencyWA may return data-URL or raw base64 — normalize for <img src>."""
    if not qr:
        return ""
    qr = str(qr).strip()
    if qr.startswith("data:"):
        return qr
    if qr.startswith("http://") or qr.startswith("https://"):
        return qr
    # Likely raw base64 PNG
    if len(qr) > 80 and " " not in qr[:40]:
        return f"data:image/png;base64,{qr}"
    return qr


def _session_payload(agency_session, fallback_name="", previous=None):
    """Map AgencyWA session object → local session dict used by the React UI."""
    previous = previous or {}
    s = agency_session if isinstance(agency_session, dict) else {}
    status = s.get("status") or "pending"
    qr = _normalize_qr_image(s.get("qr_code") or s.get("qrcode") or "")
    connect_url = (
        s.get("connect_url")
        or s.get("qr_link")
        or previous.get("qr_link")
        or ""
    )
    connected = _is_connected_status(status) or bool(s.get("connected"))
    if connected:
        message = "WhatsApp is connected"
        qr = ""
    elif qr or status == "qr":
        message = "Scan the QR code with WhatsApp → Linked Devices"
    elif status == "pending":
        message = "Waiting for AgencyWA QR… Open QR page or wait a few seconds"
    elif status == "disconnected":
        message = "WhatsApp disconnected — click Connect / Refresh QR to restart"
    else:
        message = s.get("message") or f"Status: {status}"

    return {
        "session_name": s.get("session_name") or fallback_name or previous.get("session_name") or "",
        "connected": connected,
        "status": status,
        "qr_link": connect_url,
        "qrcode": qr,
        "message": message,
        "phone": s.get("phone"),
        "display_name": s.get("display_name") or previous.get("display_name") or "",
        "public_token": s.get("public_token") or previous.get("public_token") or "",
    }


def _agency_data_session(resp):
    data = (resp or {}).get("data") or {}
    if isinstance(data, dict) and isinstance(data.get("session"), dict):
        return data["session"]
    if isinstance(data, dict) and data.get("session_name"):
        return data
    return {}


def _get_handler_class(db, user_id, meta=None):
    meta = meta or _user_meta_map(db, user_id)
    type_id = meta.get("chatbot_type_id")
    ctype = db.row("chatbot_types", {"id": type_id}) if type_id else None
    return (ctype or {}).get("handler_class") or ""


def _normalize_phone(phone):
    phone = (phone or "").strip()
    if "@" in phone:
        phone = phone.split("@")[0]
    digits = "".join(c for c in phone if c.isdigit())
    return digits or phone


def _is_lid_identity(value):
    text = str(value or "").strip().lower()
    return text.endswith("@lid") or (text.isdigit() and len(text) >= 14)


def _reply_phone(sender, contact):
    sender = (sender or "").strip()
    if sender and sender != "unknown":
        return _normalize_phone(sender)
    return _normalize_phone(contact or sender)


def _extract_reply_phone(payload, sender="", contact=""):
    """Reply target from webhook `from` (supports @lid + @c.us). Always prefer `from`."""
    raw = ""

    # Fast path: walk nested payloads for explicit `from`
    if isinstance(payload, dict):
        for obj in _walk_nested_dicts(payload):
            val = obj.get("from")
            if isinstance(val, dict):
                val = val.get("id") or ""
            if isinstance(val, str) and val.strip() and not val.lower().endswith("@newsletter"):
                # Prefer customer `from` over our own @c.us `to`
                if val.lower().endswith("@lid") or val.lower().endswith("@c.us"):
                    raw = val.strip()
                    if val.lower().endswith("@lid"):
                        break
                    # keep @c.us but continue looking for @lid
        if not raw:
            chat = _extract_chat_message(payload, allow_from_me=False)
            if not chat:
                chat = _extract_chat_message(payload, allow_from_me=True)
            if isinstance(chat, dict):
                for key in ("from", "chatId", "author"):
                    val = chat.get(key)
                    if isinstance(val, dict):
                        val = val.get("id") or ""
                    if val:
                        raw = str(val).strip()
                        break

    if not raw:
        raw = (sender or contact or "").strip()

    phone = _normalize_phone(raw)
    is_lid = (
        str(raw).lower().endswith("@lid")
        or ("@" not in str(raw) and len(phone) >= 14)
    )
    return phone, bool(is_lid and phone)


def _fetch_agency_session(session_name):
    status_code, status_data = _get_agency_session(session_name)
    if not _agency_ok(status_code, status_data):
        return None
    return _agency_data_session(status_data) or None


def _ensure_connected_session(db, user_id, meta, session_data):
    """Use a connected AgencyWA session; repair local meta if it points at a dead name."""
    session_data = dict(session_data or {})
    candidates = []
    current = (session_data.get("session_name") or "").strip()
    if current:
        candidates.append(current)
    default_name = _session_name(user_id)
    if default_name and default_name not in candidates:
        candidates.append(default_name)

    chosen = None
    for name in candidates:
        live = _fetch_agency_session(name)
        if not live:
            continue
        if _is_connected_status(live.get("status")):
            chosen = live
            break
        if chosen is None:
            chosen = live

    if not chosen:
        return session_data

    mapped = _session_payload(chosen, chosen.get("session_name") or current, previous=session_data)
    if mapped.get("session_name") != current or bool(mapped.get("connected")) != bool(session_data.get("connected")):
        _save_session(db, user_id, mapped)
    return mapped


def _automation_enabled(meta):
    return (meta or {}).get(WA_AUTOMATION_KEY, "1") != "0"


def _public_session(session_data, webhook_url):
    return {
        "session_name": session_data.get("session_name") or "",
        "connected": bool(session_data.get("connected")),
        "status": session_data.get("status") or "CLOSED",
        "qr_link": session_data.get("qr_link") or "",
        "qrcode": session_data.get("qrcode") or "",
        "message": session_data.get("message") or "",
        "has_session": bool(session_data.get("session_name")),
        "webhook_url": webhook_url,
        "phone": session_data.get("phone") or "",
    }


def _walk_nested_dicts(payload, max_depth=6):
    """Yield dicts in AgencyWA/CenterWA nested payload.payload.payload… order."""
    if not isinstance(payload, dict):
        return
    seen = set()
    stack = [(payload, 0)]
    while stack:
        obj, depth = stack.pop(0)
        if not isinstance(obj, dict) or id(obj) in seen or depth > max_depth:
            continue
        seen.add(id(obj))
        yield obj
        for nest_key in ("payload", "data", "message"):
            nested = obj.get(nest_key)
            if isinstance(nested, dict):
                stack.append((nested, depth + 1))


def _extract_chat_message(payload, allow_from_me=False):
    """Deepest dict that looks like a WhatsApp chat/media message."""
    best = None
    for obj in _walk_nested_dicts(payload):
        if not isinstance(obj, dict):
            continue
        if obj.get("fromMe") is True and not allow_from_me:
            continue

        raw_type = str(obj.get("type") or "").lower()
        has_body = isinstance(obj.get("body"), str) and bool(obj.get("body").strip())
        has_caption = isinstance(obj.get("caption"), str) and bool(obj.get("caption").strip())
        is_media = raw_type in (
            "image", "video", "audio", "ptt", "document", "sticker", "gif",
        ) or bool(obj.get("mimetype"))
        has_sender = any(
            obj.get(k)
            for k in ("from", "sender", "chatId", "chat_id", "author", "phone")
        )
        if not (has_body or has_caption or is_media):
            continue
        if has_sender or raw_type in ("chat", "text", "conversation", "") or is_media:
            best = obj
    return best


def _extract_incoming_text(payload):
    if not isinstance(payload, dict):
        return str(payload)

    chat = _extract_chat_message(payload)
    if chat:
        for key in ("caption", "body", "message", "text", "content"):
            val = chat.get(key)
            if isinstance(val, str) and val.strip():
                if len(val) > 500 and (val.startswith("/9j/") or "base64" in val[:40].lower()):
                    continue
                return val.strip()
        raw_type = str(chat.get("type") or "chat").lower()
        if raw_type and raw_type not in ("chat", "text", "conversation"):
            return f"[{raw_type}]"

    for obj in _walk_nested_dicts(payload):
        for key in ("body", "message", "text", "content", "caption"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                # Skip dumping whole JSON blobs stored under message
                if key == "message" and val.lstrip().startswith("{"):
                    continue
                if len(val) > 500 and (val.startswith("/9j/") or "base64" in val[:40].lower()):
                    continue
                return val.strip()

    return json.dumps(payload, ensure_ascii=False)[:500]


def _extract_sender(payload):
    if not isinstance(payload, dict):
        return "unknown"

    def pick(obj):
        if not isinstance(obj, dict):
            return ""
        for key in ("from", "chatId", "chat_id", "author", "phone", "sender"):
            val = obj.get(key)
            if isinstance(val, dict):
                val = val.get("id") or val.get("phone") or val.get("number") or ""
            if val and not isinstance(val, (dict, list)):
                return str(val)
        return ""

    chat = _extract_chat_message(payload)
    if chat:
        sender = pick(chat)
        if sender:
            return sender

    for obj in _walk_nested_dicts(payload):
        sender = pick(obj)
        if sender:
            return sender

    return "unknown"


def _extract_notify_name(payload):
    """WhatsApp contact display name (notifyName / pushname) for chat title."""
    if not isinstance(payload, dict):
        return ""

    chat = _extract_chat_message(payload)
    candidates = []
    if chat:
        candidates.append(chat)
        sender_obj = chat.get("sender")
        if isinstance(sender_obj, dict):
            candidates.append(sender_obj)

    for obj in candidates:
        for key in ("notifyName", "pushname", "formattedName", "name", "shortName"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

    for obj in _walk_nested_dicts(payload):
        for key in ("notifyName", "pushname", "formattedName"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

    return ""


def _create_agency_session(session_name, webhook_url, display_name=""):
    return _agency_request(
        "POST",
        "create-session.php",
        body={
            "display_name": display_name or session_name,
            "session_name": session_name,
            "webhook_url": webhook_url,
        },
        timeout=90,
    )


def _update_agency_session(session_name, webhook_url=None, display_name=None):
    body = {"session_name": session_name}
    if webhook_url is not None:
        body["webhook_url"] = webhook_url
    if display_name is not None:
        body["display_name"] = display_name
    return _agency_request("POST", "sessions.php", body=body, timeout=30)


def _get_agency_session(session_name):
    return _agency_request(
        "GET",
        "sessions.php",
        query={"session_name": session_name},
        timeout=30,
    )


def _agency_session_missing(status_code, data):
    if status_code == 404:
        return True
    msg = str((data or {}).get("message") or "").lower()
    return "not found" in msg or "does not exist" in msg or "unknown session" in msg


def _poll_agency_session_for_qr(session_name, attempts=8, delay=1.25):
    """Poll AgencyWA until qr_code / connected / qr status appears."""
    last_status, last_data = 0, {}
    for i in range(max(attempts, 1)):
        last_status, last_data = _get_agency_session(session_name)
        if _agency_ok(last_status, last_data):
            s = _agency_data_session(last_data)
            status = str(s.get("status") or "").lower()
            if s.get("qr_code") or s.get("qrcode") or status in ("qr", "connected") or _is_connected_status(status):
                return last_status, last_data
        if i < attempts - 1:
            time.sleep(delay)
    return last_status, last_data


def _ensure_agency_ready_for_qr(session_name, existing_status="", force_restart=False):
    """Restart when disconnected (or forced), then poll until QR (or connected) is available."""
    status = str(existing_status or "").lower()
    if force_restart or status == "disconnected":
        _restart_agency_session(session_name)
        time.sleep(1.0)
    return _poll_agency_session_for_qr(session_name)


def _delete_agency_session(session_name):
    return _agency_request(
        "POST",
        "delete-session.php",
        body={"session_name": session_name},
        timeout=60,
    )


def _restart_agency_session(session_name):
    return _agency_request(
        "POST",
        "restart-session.php",
        body={"session_name": session_name},
        timeout=60,
    )


def _send_agency_message(session_name, phone, message, is_lid=False):
    body = {
        "session_name": session_name,
        "phone": _normalize_phone(phone) if not str(phone).lower().endswith("@lid") else _normalize_phone(phone),
        "message": message,
    }
    # Always prefer digits in phone; flag LID for WPPConnect
    if is_lid or _is_lid_identity(phone) or str(phone).lower().endswith("@lid"):
        body["isLid"] = True
        body["is_lid"] = True
        body["phone"] = _normalize_phone(phone)
    return _agency_request(
        "POST",
        "send-message.php",
        body=body,
        timeout=60,
    )


def _agency_ok(status_code, data):
    return status_code in (200, 201) and bool(
        (data or {}).get("success") or (data or {}).get("ok")
    )


def _unlink_whatsapp_session(db, user_id, meta=None):
    """Delete AgencyWA session and clear local session for this user."""
    meta = meta or _user_meta_map(db, user_id)
    existing = _load_session(meta)
    session_name = existing.get("session_name") or _session_name(user_id)
    panel = {}

    if existing.get("session_name") or existing.get("connected"):
        delete_status, delete_data = _delete_agency_session(session_name)
        panel = {"delete_status": delete_status, "panel": delete_data}
        offline = delete_data.get("error") in (
            "agency_unreachable",
            "agency_timeout",
            "agency_not_configured",
        ) or delete_status == 0
        deleted_ok = (
            _agency_ok(delete_status, delete_data)
            or delete_status == 404
            or "not found" in str(delete_data.get("message") or "").lower()
        )
        if not deleted_ok and not offline:
            err = (
                delete_data.get("error")
                or delete_data.get("message")
                or "Failed to delete WhatsApp session"
            )
            return False, panel, err
        if offline:
            panel["warning"] = (
                "AgencyWA was unreachable; local session cleared. "
                "Fix Agency URL/keys in Site Settings, then reconnect."
            )

    db.delete("user_meta", {"user_id": user_id, "meta_key": WA_SESSION_KEY})
    db.delete("user_meta", {"user_id": user_id, "meta_key": WA_REPLY_API_KEY})
    return True, panel, None


def _verify_agency_signature(raw_body, signature_header):
    secret = infra_settings.agency_api_secret()
    if not secret:
        return False
    sig = (signature_header or "").strip()
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        raw_body if isinstance(raw_body, (bytes, bytearray)) else raw_body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


@whatsapp_bp.route("/users/<int:user_id>/whatsapp", methods=["GET"])
def get_whatsapp_settings(user_id):
    db = Database()
    try:
        is_chatbot, user = _is_chatbot_user(db, user_id)
        if not is_chatbot:
            return jsonify({
                "status": False,
                "message": "WhatsApp connect is only available for chatbot users",
            }), 403

        meta = _user_meta_map(db, user_id)
        token = _get_or_create_webhook_token(db, user_id, meta)
        session_data = _load_session(meta)
        webhook_url = _webhook_url(user_id, token)

        # Live-sync from AgencyWA so QR / connect_url stay current
        if session_data.get("session_name") and infra_settings.agency_configured():
            status_code, status_data = _get_agency_session(session_data["session_name"])
            if _agency_ok(status_code, status_data):
                session_data = {
                    **session_data,
                    **_session_payload(
                        _agency_data_session(status_data),
                        session_data["session_name"],
                        previous=session_data,
                    ),
                }
                _save_session(db, user_id, session_data)
    finally:
        db.close()

    return jsonify({
        "status": True,
        "is_chatbot": True,
        "user_name": user.get("name") if user else "",
        "automation_enabled": _automation_enabled(meta),
        "panel_base_url": infra_settings.agency_api_base_url(),
        "agency_configured": infra_settings.agency_configured(),
        "notify_phone": infra_settings.wa_webhook_notify_phone(),
        "session": _public_session(session_data, webhook_url),
        "has_api_key": infra_settings.agency_configured(),
        "api_key_preview": "",
    })


@whatsapp_bp.route("/users/<int:user_id>/whatsapp/automation", methods=["PUT"])
def update_whatsapp_automation(user_id):
    data = request.json or {}
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"status": False, "message": "enabled must be true or false"}), 400

    db = Database()
    try:
        is_chatbot, _ = _is_chatbot_user(db, user_id)
        if not is_chatbot:
            return jsonify({"status": False, "message": "Not allowed"}), 403
        _upsert_meta(db, user_id, WA_AUTOMATION_KEY, "1" if enabled else "0")
    finally:
        db.close()

    return jsonify({
        "status": True,
        "automation_enabled": enabled,
        "message": f"WhatsApp automation {'enabled' if enabled else 'disabled'}",
    })


@whatsapp_bp.route("/users/<int:user_id>/whatsapp/api-key", methods=["POST"])
def save_whatsapp_api_key(user_id):
    """Deprecated: AgencyWA keys are set by Super Admin in Site Settings."""
    return jsonify({
        "status": False,
        "message": (
            "Per-user API keys are no longer used. "
            "Super Admin configures AgencyWA API Key / Secret in Site Settings."
        ),
    }), 400


@whatsapp_bp.route("/users/<int:user_id>/whatsapp/connect", methods=["POST"])
def connect_whatsapp(user_id):
    """AgencyWA flow: status check → create (+ webhook) → return QR/connect_url."""
    db = Database()
    try:
        is_chatbot, user = _is_chatbot_user(db, user_id)
        if not is_chatbot:
            return jsonify({
                "status": False,
                "message": "WhatsApp connect is only available for chatbot users",
            }), 403

        if not infra_settings.agency_configured():
            return jsonify({
                "status": False,
                "message": (
                    "AgencyWA is not configured. Ask Super Admin to set "
                    "Agency API URL, Key and Secret in Site Settings."
                ),
            }), 400

        meta = _user_meta_map(db, user_id)
        token = _get_or_create_webhook_token(db, user_id, meta)
        webhook_url = _webhook_url(user_id, token)
        existing = _load_session(meta)
        # Prefer saved AgencyWA session_name (may be unique after CenterWA conflict)
        session_name = (existing.get("session_name") or "").strip() or _session_name(user_id)
        display_name = (user or {}).get("name") or session_name
        created = False

        # 1) Status check first — GET /sessions.php?session_name=
        status_code, status_data = _get_agency_session(session_name)
        live = _agency_data_session(status_data) if _agency_ok(status_code, status_data) else {}
        live_status = str((live or {}).get("status") or "").lower()

        # deleted / missing → must create a fresh AgencyWA session
        needs_create = (
            not _agency_ok(status_code, status_data)
            or _agency_session_missing(status_code, status_data)
            or live_status in ("deleted", "")
        )

        if not needs_create and _agency_ok(status_code, status_data):
            live_webhook = (live.get("webhook_url") or "").strip()
            # Keep webhook_url in sync with this chatbot user
            if live_webhook != webhook_url:
                _update_agency_session(session_name, webhook_url=webhook_url, display_name=display_name)
                status_code, status_data = _get_agency_session(session_name)
                live = _agency_data_session(status_data) if _agency_ok(status_code, status_data) else live

            session_data = _session_payload(live, session_name, previous=existing)

            if session_data.get("connected"):
                _save_session(db, user_id, session_data)
                return jsonify({
                    "status": True,
                    "message": "WhatsApp already connected",
                    "session": _public_session(session_data, webhook_url),
                    "agency_action": "status",
                })

            if str(session_data.get("status") or "").lower() == "disconnected":
                # Reconnect via AgencyWA restart-session.php
                _restart_agency_session(session_name)
                time.sleep(1.0)
                status_code, status_data = _poll_agency_session_for_qr(session_name)
                if _agency_ok(status_code, status_data):
                    session_data = _session_payload(
                        _agency_data_session(status_data),
                        session_name,
                        previous=session_data,
                    )
                _save_session(db, user_id, session_data)
                return jsonify({
                    "status": True,
                    "message": "Session restarted via AgencyWA. Click Get QR when status is qr.",
                    "session": _public_session(session_data, webhook_url),
                    "agency_action": "restart-session",
                })

            if str(session_data.get("status") or "").lower() in ("pending", "qr"):
                if not session_data.get("qrcode"):
                    status_code, status_data = _poll_agency_session_for_qr(
                        session_name, attempts=6, delay=1.0
                    )
                    if _agency_ok(status_code, status_data):
                        session_data = _session_payload(
                            _agency_data_session(status_data),
                            session_name,
                            previous=session_data,
                        )
                _save_session(db, user_id, session_data)
                status_label = str(session_data.get("status") or "").lower()
                msg = (
                    "QR ready — click Get QR to open AgencyWA connect page."
                    if status_label == "qr"
                    else "Session pending on AgencyWA. Wait for status qr, then Get QR."
                )
                return jsonify({
                    "status": True,
                    "message": msg,
                    "session": _public_session(session_data, webhook_url),
                    "agency_action": "status",
                })

            # Unknown non-deleted status — fall through to create
            needs_create = True

        if needs_create:
            if (status_data or {}).get("error") in (
                "agency_unreachable",
                "agency_timeout",
                "agency_not_configured",
            ):
                err = (
                    (status_data or {}).get("message")
                    or (status_data or {}).get("error")
                    or "AgencyWA unreachable"
                )
                return jsonify({"status": False, "message": err, "panel": status_data}), 400

            # If AgencyWA still has a deleted/stale row, delete then create
            if live_status == "deleted" or (
                _agency_ok(status_code, status_data)
                and live_status not in ("pending", "qr", "connected", "disconnected")
            ):
                _delete_agency_session(session_name)
                time.sleep(0.5)

            # 2) Create via AgencyWA POST /create-session.php
            create_status, create_data = _create_agency_session(
                session_name, webhook_url, display_name
            )
            msg = str(create_data.get("message") or "").lower()

            # Name conflict / CenterWA stale for same session_name → unique name
            centerwa_fail = (
                create_status in (502, 500)
                or "centerwa" in msg
                or "invalid" in msg
            )
            if create_status in (409, 422) or "already" in msg or "exists" in msg or centerwa_fail:
                _delete_agency_session(session_name)
                time.sleep(0.5)
                # Prefer stable name first retry, then unique
                create_status, create_data = _create_agency_session(
                    session_name, webhook_url, display_name
                )
                msg2 = str(create_data.get("message") or "").lower()
                if not _agency_ok(create_status, create_data) and (
                    create_status in (409, 422, 500, 502)
                    or "already" in msg2
                    or "exists" in msg2
                    or "centerwa" in msg2
                    or "invalid" in msg2
                ):
                    session_name = f"user_{user_id}_{secrets.token_hex(3)}"
                    create_status, create_data = _create_agency_session(
                        session_name, webhook_url, display_name
                    )

            if not _agency_ok(create_status, create_data):
                err = (
                    create_data.get("error")
                    or create_data.get("message")
                    or "Failed to create WhatsApp session via AgencyWA"
                )
                return jsonify({
                    "status": False,
                    "message": err,
                    "panel": create_data,
                    "agency_action": "create-session",
                }), 400

            created = True
            session_data = _session_payload(
                _agency_data_session(create_data), session_name, previous=existing
            )
            # Always persist the AgencyWA session_name we actually created
            session_data["session_name"] = (
                session_data.get("session_name") or session_name
            )
            if not session_data.get("qrcode") and not session_data.get("connected"):
                poll_status, poll_data = _poll_agency_session_for_qr(
                    session_data["session_name"], attempts=8, delay=1.25
                )
                if _agency_ok(poll_status, poll_data):
                    session_data = _session_payload(
                        _agency_data_session(poll_data),
                        session_data["session_name"],
                        previous=session_data,
                    )
            _save_session(db, user_id, session_data)
        else:
            session_data = existing
    finally:
        db.close()

    status_label = str((session_data or {}).get("status") or "").lower()
    if status_label == "qr":
        out_msg = "Session created on AgencyWA. Click Get QR to open connect page."
    else:
        out_msg = (
            "AgencyWA create-session done. Wait for status qr, then click Get QR."
            if created
            else "Scan the AgencyWA QR code to connect."
        )

    return jsonify({
        "status": True,
        "message": out_msg,
        "session": _public_session(session_data, webhook_url),
        "agency_action": "create-session" if created else "status",
    })


@whatsapp_bp.route("/users/<int:user_id>/whatsapp/status", methods=["GET"])
def whatsapp_status(user_id):
    db = Database()
    status_code = 0
    status_data = {}
    try:
        is_chatbot, _ = _is_chatbot_user(db, user_id)
        if not is_chatbot:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        meta = _user_meta_map(db, user_id)
        token = _get_or_create_webhook_token(db, user_id, meta)
        webhook_url = _webhook_url(user_id, token)
        session_data = _load_session(meta)

        if not session_data.get("session_name"):
            return jsonify({
                "status": True,
                "session": _public_session(session_data, webhook_url),
            })

        session_name = session_data.get("session_name") or _session_name(user_id)
        status_code, status_data = _get_agency_session(session_name)

        if _agency_ok(status_code, status_data):
            session_data = {
                **session_data,
                **_session_payload(
                    _agency_data_session(status_data),
                    session_name,
                    previous=session_data,
                ),
            }
            _save_session(db, user_id, session_data)
    finally:
        db.close()

    return jsonify({
        "status": True,
        "session": _public_session(session_data, webhook_url),
        "panel": status_data if status_code == 200 else {},
    })


@whatsapp_bp.route("/users/<int:user_id>/whatsapp/qr", methods=["GET"])
def whatsapp_qr(user_id):
    db = Database()
    qr_code = 0
    qr_data = {}
    try:
        is_chatbot, _ = _is_chatbot_user(db, user_id)
        if not is_chatbot:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        meta = _user_meta_map(db, user_id)
        token = _get_or_create_webhook_token(db, user_id, meta)
        webhook_url = _webhook_url(user_id, token)
        session_data = _load_session(meta)

        if not session_data.get("session_name"):
            return jsonify({"status": False, "message": "Create a session first"}), 400

        session_name = session_data.get("session_name") or _session_name(user_id)
        force_restart = str(request.args.get("restart") or "").lower() in ("1", "true", "yes")

        if force_restart or str(session_data.get("status") or "").lower() == "disconnected":
            # Manual refresh / disconnected → AgencyWA restart then wait for QR
            qr_code, qr_data = _ensure_agency_ready_for_qr(
                session_name,
                session_data.get("status") or "",
                force_restart=force_restart,
            )
        else:
            qr_code, qr_data = _get_agency_session(session_name)

        if _agency_ok(qr_code, qr_data):
            session_data = {
                **session_data,
                **_session_payload(
                    _agency_data_session(qr_data),
                    session_name,
                    previous=session_data,
                ),
            }
            _save_session(db, user_id, session_data)
    finally:
        db.close()

    return jsonify({
        "status": qr_code == 200,
        "session": _public_session(session_data, webhook_url),
        "panel": qr_data,
    })


@whatsapp_bp.route("/users/<int:user_id>/whatsapp/change-number", methods=["POST"])
def change_whatsapp_number(user_id):
    """Unlink current WhatsApp and create a fresh session so user can scan with a new number."""
    db = Database()
    try:
        is_chatbot, user = _is_chatbot_user(db, user_id)
        if not is_chatbot:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        meta = _user_meta_map(db, user_id)
        token = _get_or_create_webhook_token(db, user_id, meta)
        webhook_url = _webhook_url(user_id, token)
        session_name = _session_name(user_id)
        display_name = (user or {}).get("name") or session_name

        ok, panel, err = _unlink_whatsapp_session(db, user_id, meta)
        if not ok:
            return jsonify({"status": False, "message": err, **panel}), 400

        create_status, create_data = _create_agency_session(
            session_name, webhook_url, display_name
        )
        if not _agency_ok(create_status, create_data):
            err = create_data.get("message") or create_data.get("error") or "Failed to create WhatsApp session"
            if create_data.get("error") == "agency_unreachable":
                err = "AgencyWA is not reachable. Check Agency API URL in Site Settings."
            return jsonify({"status": False, "message": err, "panel": create_data}), 400

        session_data = _session_payload(_agency_data_session(create_data), session_name)
        session_data["message"] = (
            "Old WhatsApp unlinked. Scan QR with your new number to connect."
        )
        _save_session(db, user_id, session_data)
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": session_data["message"],
        "session": _public_session(session_data, webhook_url),
        "has_api_key": infra_settings.agency_configured(),
        "api_key_preview": "",
    })


@whatsapp_bp.route("/users/<int:user_id>/whatsapp/unlink", methods=["POST"])
def unlink_whatsapp(user_id):
    """Unlink WhatsApp number only — does not create a new session."""
    db = Database()
    try:
        is_chatbot, _ = _is_chatbot_user(db, user_id)
        if not is_chatbot:
            return jsonify({"status": False, "message": "Not allowed"}), 403

        meta = _user_meta_map(db, user_id)
        token = _get_or_create_webhook_token(db, user_id, meta)
        webhook_url = _webhook_url(user_id, token)

        ok, panel, err = _unlink_whatsapp_session(db, user_id, meta)
        if not ok:
            return jsonify({"status": False, "message": err, **panel}), 400
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "WhatsApp number unlinked. Use Connect WhatsApp to link again.",
        "session": _public_session({}, webhook_url),
        "has_api_key": infra_settings.agency_configured(),
        "api_key_preview": "",
        "panel": panel,
    })


@whatsapp_bp.route("/webhooks/whatsapp/<int:user_id>/<token>", methods=["POST"])
def whatsapp_webhook(user_id, token):
    raw_body = request.get_data() or b""
    signature = request.headers.get("X-AgencyWA-Signature") or ""

    # Prefer AgencyWA HMAC when secret is configured; fall back to URL token only
    if infra_settings.agency_api_secret() and signature:
        if not _verify_agency_signature(raw_body, signature):
            db = Database()
            try:
                create_webhook_log(
                    db, user_id, "unknown", "", {"raw": "signature_fail"},
                    forwarded=False, log_status="invalid_signature",
                )
            finally:
                db.close()
            return jsonify({"status": False, "message": "Invalid signature"}), 401

    payload = request.get_json(silent=True)
    if payload is None:
        raw = raw_body.decode("utf-8", errors="replace") if raw_body else ""
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except (TypeError, ValueError):
            payload = {"raw_body": raw}

    event = (
        request.headers.get("X-AgencyWA-Event")
        or (payload.get("event") if isinstance(payload, dict) else None)
        or (payload.get("normalized_event") if isinstance(payload, dict) else None)
        or ""
    )
    event = str(event).strip().lower()

    db = Database()
    try:
        meta = _user_meta_map(db, user_id)
        expected = meta.get(WA_WEBHOOK_TOKEN_KEY)
        if not expected or not secrets.compare_digest(expected, token):
            create_webhook_log(
                db, user_id, "unknown", "", payload,
                forwarded=False, log_status="invalid_token",
            )
            return jsonify({"status": False, "message": "Invalid webhook token"}), 403

        session_data = _load_session(meta)
        session_data = _ensure_connected_session(db, user_id, meta, session_data)
        session_name = session_data.get("session_name") or _session_name(user_id)

        # Keep local session in sync when AgencyWA includes session on the webhook
        if isinstance(payload, dict) and isinstance(payload.get("session"), dict):
            synced = _session_payload(payload["session"], session_name, previous=session_data)
            if synced.get("session_name"):
                session_data = synced
                session_name = synced["session_name"]
                _save_session(db, user_id, session_data)

        # Session lifecycle events from AgencyWA
        if event.startswith("session.") and isinstance(payload, dict):
            agency_session = payload.get("session") if isinstance(payload.get("session"), dict) else {}
            if agency_session:
                session_data = {
                    **session_data,
                    **_session_payload(agency_session, session_name),
                }
                _save_session(db, user_id, session_data)
            create_webhook_log(
                db, user_id, "", event, payload,
                forwarded=True, log_status=event.replace(".", "_"),
            )
            return jsonify({"status": True, "ok": True, "event": event})

        # Store inbox rows for receive + send; ignore ack/presence noise
        normalized_msg_event = event.replace("_", ".")
        if normalized_msg_event in ("message.received", "message.sent", "message", "") or event in (
            "message.received",
            "message.sent",
            "message_received",
            "message_sent",
            "message",
            "",
        ):
            try:
                store_event = normalized_msg_event if normalized_msg_event.startswith("message.") else (
                    "message.received" if "received" in event else (
                        "message.sent" if "sent" in event else (event or "message.received")
                    )
                )
                store_from_webhook_payload(
                    db,
                    user_id,
                    payload,
                    store_event,
                    {
                        "extract_chat": _extract_chat_message,
                        "extract_sender": _extract_sender,
                        "extract_notify_name": _extract_notify_name,
                        "extract_text": _extract_incoming_text,
                    },
                )
            except Exception:
                pass

        # Only run bot automation on inbound message.received
        if event and event not in ("message.received", "message_received", "message", ""):
            create_webhook_log(
                db, user_id, "", event, payload,
                forwarded=False, log_status="ignored_event",
            )
            return jsonify({"status": True, "ok": True, "event": event, "ignored": True})

        sender = _extract_sender(payload)
        text = _extract_incoming_text(payload)

        contact = normalize_whatsapp_contact(sender)
        sent = False
        send_data = {}
        chat_id = None
        chat_created = False
        reply_phone = ""
        reply_is_lid = False
        auto_reply = ""
        reply_sent = False
        reply_send_data = {}
        ai_result = None
        handler_class = ""
        action_only = False
        automation_enabled = True

        automation_enabled = _automation_enabled(meta)
        if automation_enabled and not infra_settings.agency_configured():
            create_webhook_log(
                db, user_id, sender, text, payload,
                forwarded=False, log_status="agency_not_configured",
            )
            return jsonify({
                "status": False,
                "message": "AgencyWA API credentials are not configured",
            }), 404

        reply_phone, reply_is_lid = _extract_reply_phone(payload, sender, contact)
        handler_class = _get_handler_class(db, user_id, meta)
        action_only = handler_class == "Job_posting"

        # Re-check connected session right before send
        session_data = _ensure_connected_session(db, user_id, meta, session_data)
        session_name = session_data.get("session_name") or session_name

        if reply_phone and text:
            db.cursor.execute(
                """
                SELECT id FROM chats
                WHERE user_id=%s AND chat_type='whatsapp' AND user_number=%s
                LIMIT 1
                """,
                [user_id, contact or normalize_whatsapp_contact(sender)],
            )
            had_chat = bool(db.cursor.fetchone())

            notify_name = _extract_notify_name(payload)
            chat = get_or_create_whatsapp_chat(
                db, user_id, contact or sender, name=notify_name or None
            )
            if chat:
                chat_id = chat["id"]
                chat_created = not had_chat

                # Blocked contacts: no history, no AI, no reply
                if chat.get("blocked"):
                    create_webhook_log(
                        db, user_id, sender, text, payload,
                        forwarded=False, log_status="blocked",
                    )
                    return jsonify({
                        "status": True,
                        "ok": True,
                        "event": event,
                        "blocked": True,
                        "chat_id": chat_id,
                    })

                ignore_ai = bool(chat.get("ignore_ai")) or not automation_enabled

                if ignore_ai:
                    # Save inbound only — staff can reply from chat UI
                    db.insert(
                        "chat_history",
                        {
                            "chat_id": chat_id,
                            "request_text": text,
                            "response_text": None,
                            "gemini_response": None,
                        },
                    )
                    db.execute(
                        "UPDATE chats SET lastmsg_at=CURRENT_TIMESTAMP WHERE id=%s",
                        [chat_id],
                    )
                elif automation_enabled:
                    ai_result = process_chat_message(db, chat_id, text, save=True)
                    if not action_only and ai_result.get("success"):
                        auto_reply = (ai_result.get("reply") or "").strip()

                    if auto_reply:
                        reply_status, reply_send_data = _send_agency_message(
                            session_name, reply_phone, auto_reply, is_lid=reply_is_lid
                        )
                        reply_sent = _agency_ok(reply_status, reply_send_data) or bool(
                            reply_send_data.get("sent")
                        )
                        if reply_sent:
                            try:
                                store_message(
                                    db,
                                    user_id,
                                    direction="send",
                                    content_type="text",
                                    sender_id=session_data.get("phone") or session_name,
                                    sender_name="Bot",
                                    message_text=auto_reply,
                                    event_name="bot.reply",
                                    wa_message_id=f"bot-reply-{chat_id}-{int(time.time()*1000)}",
                                )
                            except Exception:
                                pass
                else:
                    db.insert(
                        "chat_history",
                        {
                            "chat_id": chat_id,
                            "request_text": text,
                            "response_text": None,
                            "gemini_response": None,
                        },
                    )
                    db.execute(
                        "UPDATE chats SET lastmsg_at=CURRENT_TIMESTAMP WHERE id=%s",
                        [chat_id],
                    )

        if chat and chat.get("blocked"):
            log_status = "blocked"
        elif chat and chat.get("ignore_ai"):
            log_status = "ignore_ai"
        elif not automation_enabled:
            log_status = "automation_off"
        elif reply_sent:
            log_status = "replied"
        elif action_only and chat_id and text:
            log_status = "action_processed" if (ai_result or {}).get("success") else "action_failed"
        elif not reply_phone and text and not action_only and automation_enabled:
            log_status = "reply_no_phone"  # missing from/phone
        elif reply_phone and text and not action_only:
            log_status = "reply_failed"
        else:
            log_status = "received"

        create_webhook_log(
            db,
            user_id,
            sender,
            text,
            payload,
            forwarded=reply_sent or bool(action_only and (ai_result or {}).get("success")),
            notify_phone=infra_settings.wa_webhook_notify_phone(),
            panel_response={
                "notify": send_data,
                "reply": reply_send_data,
                "ai": {
                    "handler_class": handler_class,
                    "action_only": action_only,
                    "success": (ai_result or {}).get("success"),
                    "reply_json": (ai_result or {}).get("reply_json"),
                },
                "event": event,
            },
            log_status=log_status,
        )
    finally:
        db.close()

    return jsonify({
        "status": True,
        "received": True,
        "forwarded": sent,
        "notify_phone": infra_settings.wa_webhook_notify_phone(),
        "panel_send": send_data,
        "chat_id": chat_id,
        "chat_created": chat_created,
        "contact": contact,
        "reply_phone": reply_phone,
        "auto_reply": auto_reply,
        "reply_sent": reply_sent,
        "reply_panel_send": reply_send_data,
        "handler_class": handler_class,
        "action_only": action_only,
        "automation_enabled": automation_enabled,
        "ai_processed": bool(ai_result and ai_result.get("success")),
        "event": event,
    })
