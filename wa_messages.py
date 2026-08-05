"""Normalized WhatsApp inbox messages for chatbot users (role 2)."""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from db import Database

wa_messages_bp = Blueprint("wa_messages", __name__)

TABLE = "wa_messages"

MEDIA_TYPES = {
    "image": "image",
    "video": "video",
    "audio": "audio",
    "ptt": "audio",
    "document": "document",
    "sticker": "sticker",
    "gif": "image",
}


def ensure_schema():
    db = Database()
    try:
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                direction VARCHAR(16) NOT NULL,
                content_type VARCHAR(32) NOT NULL DEFAULT 'text',
                sender_id VARCHAR(255) DEFAULT NULL,
                sender_name VARCHAR(255) DEFAULT NULL,
                message_text TEXT,
                media_url TEXT,
                media_mime VARCHAR(120) DEFAULT NULL,
                media_filename VARCHAR(255) DEFAULT NULL,
                media_thumb LONGTEXT,
                wa_message_id VARCHAR(191) DEFAULT NULL,
                event_name VARCHAR(64) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_wa_msg_user_id (user_id, id),
                UNIQUE KEY uq_wa_msg_user_waid (user_id, wa_message_id)
            )
            """
        )
    finally:
        db.close()


def _serialize(row):
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "direction": row.get("direction") or "receive",
        "content_type": row.get("content_type") or "text",
        "sender_id": row.get("sender_id") or "",
        "sender_name": row.get("sender_name") or "",
        "message_text": row.get("message_text") or "",
        "media_url": row.get("media_url") or "",
        "media_mime": row.get("media_mime") or "",
        "media_filename": row.get("media_filename") or "",
        "media_thumb": row.get("media_thumb") or "",
        "wa_message_id": row.get("wa_message_id") or "",
        "event_name": row.get("event_name") or "",
        "created_at": row.get("created_at"),
    }


def _wa_message_id(chat: dict) -> str:
    if not isinstance(chat, dict):
        return ""
    mid = chat.get("id")
    if isinstance(mid, dict):
        parts = [
            str(mid.get("fromMe")),
            str(mid.get("remote") or ""),
            str(mid.get("id") or ""),
        ]
        joined = "|".join(parts).strip("|")
        return joined[:190] if joined.replace("|", "").replace("None", "") else ""
    if mid:
        return str(mid)[:190]
    # Fallback: timestamp + from + body snippet
    body = (chat.get("body") or "")[:40]
    raw = f"{chat.get('t') or ''}|{chat.get('from') or ''}|{body}"
    return raw[:190] if raw.strip("|") else ""


def _pick_media_url(chat: dict) -> str:
    if not isinstance(chat, dict):
        return ""
    for key in (
        "deprecatedMms3Url",
        "clientUrl",
        "directPath",
        "mediaUrl",
        "media_url",
        "url",
        "fileUrl",
    ):
        val = chat.get(key)
        if isinstance(val, str) and val.strip().startswith(("http://", "https://", "data:")):
            return val.strip()

    media_data = chat.get("mediaData")
    if isinstance(media_data, dict):
        for key in ("mediaBlob", "preview", "url"):
            val = media_data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    if isinstance(media_data, list) and media_data:
        first = media_data[0]
        if isinstance(first, dict):
            for key in ("url", "mediaUrl", "preview"):
                val = first.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        elif isinstance(first, str) and first.strip():
            return first.strip()

    # body sometimes holds base64 for stickers/images
    body = chat.get("body")
    if isinstance(body, str) and body.strip().startswith(("data:image", "/9j/", "iVBOR")):
        if body.startswith("/9j/") or body.startswith("iVBOR"):
            return f"data:image/jpeg;base64,{body.strip()}"
        return body.strip()
    return ""


def _pick_thumb(chat: dict) -> str:
    if not isinstance(chat, dict):
        return ""
    for key in ("thumbnail", "bodyPreview", "preview"):
        val = chat.get(key)
        if isinstance(val, str) and len(val.strip()) > 20:
            t = val.strip()
            if t.startswith("data:"):
                return t
            if t.startswith("/9j/") or t.startswith("iVBOR"):
                return f"data:image/jpeg;base64,{t}"
            # raw base64-ish
            if re.fullmatch(r"[A-Za-z0-9+/=\s]+", t[:80] or ""):
                return f"data:image/jpeg;base64,{t}"
    return ""


def classify_content(chat: dict) -> tuple[str, str, str]:
    """Return (content_type, text, media_url)."""
    if not isinstance(chat, dict):
        return "text", "", ""

    raw_type = str(chat.get("type") or "chat").lower().strip()
    content_type = MEDIA_TYPES.get(raw_type, "text" if raw_type in ("chat", "text", "conversation", "") else "other")
    caption = ""
    for key in ("caption", "body", "text", "content"):
        val = chat.get(key)
        if isinstance(val, str) and val.strip():
            # Don't treat huge base64 as text caption
            if len(val) > 500 and (val.startswith("/9j/") or "base64" in val[:40].lower()):
                continue
            caption = val.strip()
            break

    media_url = _pick_media_url(chat) if content_type != "text" else ""
    if content_type == "text" and media_url:
        content_type = "image" if "image" in (chat.get("mimetype") or media_url) else "other"

    if content_type != "text" and not caption:
        caption = chat.get("filename") or chat.get("caption") or f"[{content_type}]"

    return content_type, caption, media_url


def store_message(
    db,
    user_id,
    *,
    direction,
    content_type="text",
    sender_id="",
    sender_name="",
    message_text="",
    media_url="",
    media_mime="",
    media_filename="",
    media_thumb="",
    wa_message_id="",
    event_name="",
):
    direction = "send" if str(direction).lower() in ("send", "sent", "out", "outbound") else "receive"
    content_type = (content_type or "text").lower()[:32]
    wa_message_id = (wa_message_id or "").strip()[:190] or None

    data = {
        "user_id": int(user_id),
        "direction": direction,
        "content_type": content_type,
        "sender_id": (sender_id or "")[:255],
        "sender_name": (sender_name or "")[:255],
        "message_text": message_text or "",
        "media_url": media_url or None,
        "media_mime": (media_mime or "")[:120] or None,
        "media_filename": (media_filename or "")[:255] or None,
        "media_thumb": media_thumb or None,
        "wa_message_id": wa_message_id,
        "event_name": (event_name or "")[:64] or None,
    }

    if wa_message_id:
        db.cursor.execute(
            f"SELECT id FROM {TABLE} WHERE user_id=%s AND wa_message_id=%s LIMIT 1",
            [user_id, wa_message_id],
        )
        existing = db.cursor.fetchone()
        if existing:
            return existing["id"]

    try:
        return db.insert(TABLE, data)
    except Exception:
        # Race on unique key
        if wa_message_id:
            db.cursor.execute(
                f"SELECT id FROM {TABLE} WHERE user_id=%s AND wa_message_id=%s LIMIT 1",
                [user_id, wa_message_id],
            )
            row = db.cursor.fetchone()
            if row:
                return row["id"]
        raise


def store_from_webhook_payload(db, user_id, payload, event, extractors):
    """
    extractors: dict with callables from whatsapp.py:
      extract_chat(payload, allow_from_me=False)
      extract_sender, extract_notify_name, extract_text
    """
    extract_chat = extractors["extract_chat"]
    allow_from_me = event in ("message.sent", "message.delivered", "message.read")
    direction = "send" if event.startswith("message.sent") or event == "message.sent" else "receive"

    # For receive prefer inbound; for sent allow fromMe
    chat = extract_chat(payload, allow_from_me=True if direction == "send" else False)
    if not chat and direction == "receive":
        chat = extract_chat(payload, allow_from_me=True)

    if not chat:
        # Still try text-only path for receive
        text = extractors["extract_text"](payload)
        sender = extractors["extract_sender"](payload)
        name = extractors["extract_notify_name"](payload)
        if not text or text.startswith("{"):
            return None
        return store_message(
            db,
            user_id,
            direction=direction,
            content_type="text",
            sender_id=sender,
            sender_name=name or sender,
            message_text=text,
            event_name=event,
        )

    # fromMe on received event → treat as send (echo)
    if chat.get("fromMe") is True:
        direction = "send"

    content_type, text, media_url = classify_content(chat)
    sender = ""
    for key in ("from", "chatId", "author"):
        val = chat.get(key)
        if isinstance(val, dict):
            val = val.get("id") or ""
        if val:
            sender = str(val)
            break
    if not sender:
        sender = extractors["extract_sender"](payload)

    name = ""
    for key in ("notifyName", "pushname"):
        if isinstance(chat.get(key), str) and chat.get(key).strip():
            name = chat[key].strip()
            break
    if not name:
        sender_obj = chat.get("sender")
        if isinstance(sender_obj, dict):
            name = (
                sender_obj.get("pushname")
                or sender_obj.get("formattedName")
                or sender_obj.get("name")
                or ""
            )
    if not name:
        name = extractors["extract_notify_name"](payload)

    if direction == "send" and not name:
        name = "You"

    return store_message(
        db,
        user_id,
        direction=direction,
        content_type=content_type,
        sender_id=sender,
        sender_name=name or sender,
        message_text=text,
        media_url=media_url,
        media_mime=str(chat.get("mimetype") or "")[:120],
        media_filename=str(chat.get("filename") or chat.get("name") or "")[:255],
        media_thumb=_pick_thumb(chat),
        wa_message_id=_wa_message_id(chat),
        event_name=event,
    )


@wa_messages_bp.route("/users/<int:user_id>/whatsapp/messages", methods=["GET"])
def list_user_messages(user_id):
    """Newest first. Use before_id for older page; after_id for live newer."""
    try:
        limit = int(request.args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = min(max(limit, 1), 50)

    before_id = request.args.get("before_id")
    after_id = request.args.get("after_id")
    try:
        before_id = int(before_id) if before_id not in (None, "") else None
    except (TypeError, ValueError):
        before_id = None
    try:
        after_id = int(after_id) if after_id not in (None, "") else None
    except (TypeError, ValueError):
        after_id = None

    db = Database()
    try:
        where = ["user_id=%s"]
        params = [user_id]

        if after_id:
            where.append("id > %s")
            params.append(after_id)
            sql = (
                f"SELECT * FROM {TABLE} WHERE {' AND '.join(where)} "
                f"ORDER BY id ASC LIMIT %s"
            )
            db.cursor.execute(sql, params + [limit])
            rows = db.cursor.fetchall() or []
            messages = [_serialize(r) for r in rows]
            return jsonify({
                "status": True,
                "messages": messages,
                "has_more": False,
                "mode": "newer",
            })

        if before_id:
            where.append("id < %s")
            params.append(before_id)

        sql = (
            f"SELECT * FROM {TABLE} WHERE {' AND '.join(where)} "
            f"ORDER BY id DESC LIMIT %s"
        )
        db.cursor.execute(sql, params + [limit + 1])
        rows = db.cursor.fetchall() or []
        has_more = len(rows) > limit
        rows = rows[:limit]
        messages = [_serialize(r) for r in rows]
        return jsonify({
            "status": True,
            "messages": messages,
            "has_more": has_more,
            "mode": "page",
        })
    finally:
        db.close()
