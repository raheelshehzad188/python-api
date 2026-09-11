"""HTTP API for JSON Writer chatbot users.

Public:  POST /v1/json   (API key)
Admin:   GET  /users/<id>/json-api
         POST /users/<id>/json-api/key
         POST /users/<id>/json-api/test
         GET  /users/<id>/json-api/logs
         GET  /users/<id>/json-api/logs/<log_id>
         DELETE /users/<id>/json-api/logs/<log_id>
         DELETE /users/<id>/json-api/logs
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time

from flask import Blueprint, jsonify, request

import infra_settings
import secret_store
from chatbot_types.json_bot import JsonBot
from chats import resolve_chat_reply
from db import Database
from gemini import Gemini
from gemini_cache import ensure_user_cache
from user_meta import _upsert_meta

logger = logging.getLogger("json_bot_api")

json_bot_api_bp = Blueprint("json_bot_api", __name__)

API_PATH = "/v1/json"
META_KEY = "json_bot_api_key"
META_HASH = "json_bot_api_key_hash"
KEY_PREFIX = "jb_"
LOGS_TABLE = "json_api_logs"
_MAX_BODY = 100_000


def ensure_schema():
    db = Database()
    try:
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {LOGS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                source VARCHAR(20) NOT NULL DEFAULT 'api',
                success TINYINT(1) NOT NULL DEFAULT 0,
                status_code INT NOT NULL DEFAULT 200,
                duration_ms INT DEFAULT NULL,
                ip VARCHAR(64) DEFAULT NULL,
                content_type VARCHAR(120) DEFAULT NULL,
                request_text LONGTEXT,
                request_body LONGTEXT,
                response_json LONGTEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_json_api_user_created (user_id, created_at)
            )
            """
        )
    finally:
        db.close()


def _truncate(value, limit=_MAX_BODY):
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated {len(text) - limit} chars]"


def _dump(value):
    if value is None:
        return None
    if isinstance(value, str):
        return _truncate(value)
    try:
        return _truncate(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return _truncate(str(value))


def _parse_stored_json(raw):
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _client_ip():
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or (request.remote_addr or "")


def _read_request_payload():
    raw_text = request.get_data(as_text=True) or ""
    raw_json = None
    if raw_text:
        try:
            raw_json = json.loads(raw_text)
        except (ValueError, TypeError):
            raw_json = None
    prompt = _input_to_prompt(raw_json, raw_text)
    return prompt, raw_text, raw_json


def insert_json_api_log(
    db,
    user_id,
    *,
    source,
    success,
    status_code,
    duration_ms=None,
    request_text=None,
    request_body=None,
    response=None,
    error_message=None,
):
    return db.insert(
        LOGS_TABLE,
        {
            "user_id": user_id,
            "source": source or "api",
            "success": 1 if success else 0,
            "status_code": int(status_code or 0),
            "duration_ms": duration_ms,
            "ip": _client_ip() or None,
            "content_type": (request.content_type or "")[:120] or None,
            "request_text": _truncate(request_text),
            "request_body": _dump(request_body if request_body is not None else request_text),
            "response_json": _dump(response),
            "error_message": _truncate(error_message, 4000) if error_message else None,
        },
    )


def _serialize_log(row, include_bodies=True):
    request_text = row.get("request_text") or ""
    item = {
        "id": row["id"],
        "user_id": row["user_id"],
        "source": row.get("source") or "api",
        "success": bool(row.get("success")),
        "status_code": row.get("status_code"),
        "duration_ms": row.get("duration_ms"),
        "ip": row.get("ip") or "",
        "content_type": row.get("content_type") or "",
        "request_text": request_text,
        "request_preview": (request_text[:180] + "…") if len(request_text) > 180 else request_text,
        "error_message": row.get("error_message") or "",
        "created_at": row.get("created_at"),
    }
    response = _parse_stored_json(row.get("response_json"))
    if include_bodies:
        item["request_body"] = _parse_stored_json(row.get("request_body"))
        if item["request_body"] is None:
            item["request_body"] = row.get("request_body") or ""
        item["response"] = response
    else:
        if isinstance(response, (dict, list)):
            preview = json.dumps(response, ensure_ascii=False)
        else:
            preview = str(response or "")
        item["response_preview"] = (preview[:180] + "…") if len(preview) > 180 else preview
    return item


def _paginate_user_logs(db, user_id, page=1, per_page=20, include_bodies=False):
    try:
        page = max(int(page or 1), 1)
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = min(max(int(per_page or 20), 1), 50)
    except (TypeError, ValueError):
        per_page = 20
    offset = (page - 1) * per_page
    db.cursor.execute(
        f"SELECT COUNT(*) AS total FROM {LOGS_TABLE} WHERE user_id=%s",
        [user_id],
    )
    total = db.cursor.fetchone()["total"]
    db.cursor.execute(
        f"SELECT * FROM {LOGS_TABLE} WHERE user_id=%s ORDER BY id DESC LIMIT %s OFFSET %s",
        [user_id, per_page, offset],
    )
    rows = db.cursor.fetchall() or []
    logs = [_serialize_log(r, include_bodies=include_bodies) for r in rows]
    return logs, total, page, per_page


def _meta_map(db, user_id):
    return {m["meta_key"]: m["meta_value"] for m in db.select("user_meta", {"user_id": user_id})}


def _json_bot_type(db, user_id, meta=None):
    meta = meta if meta is not None else _meta_map(db, user_id)
    type_id = meta.get("chatbot_type_id")
    ctype = db.row("chatbot_types", {"id": type_id}) if type_id else None
    if not ctype:
        return None
    if (ctype.get("handler_class") or "").strip() == JsonBot.name:
        return ctype
    return None


def _hash_key(api_key):
    return hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()


def _new_api_key():
    return KEY_PREFIX + secrets.token_urlsafe(32)


def _save_api_key(db, user_id, api_key):
    _upsert_meta(db, user_id, META_KEY, secret_store.encrypt(api_key))
    _upsert_meta(db, user_id, META_HASH, _hash_key(api_key))
    return api_key


def ensure_api_key(db, user_id, meta=None):
    """Return (full_key, created). Creates a key if this JSON bot user has none."""
    meta = meta if meta is not None else _meta_map(db, user_id)
    stored = secret_store.decrypt(meta.get(META_KEY) or "")
    if stored:
        if not (meta.get(META_HASH) or "").strip():
            _upsert_meta(db, user_id, META_HASH, _hash_key(stored))
        return stored, False
    key = _new_api_key()
    _save_api_key(db, user_id, key)
    return key, True


def _lookup_user_by_api_key(db, api_key):
    api_key = (api_key or "").strip()
    if not api_key:
        return None
    digest = _hash_key(api_key)
    db.cursor.execute(
        "SELECT user_id FROM user_meta WHERE meta_key=%s AND meta_value=%s LIMIT 1",
        [META_HASH, digest],
    )
    row = db.cursor.fetchone()
    if row:
        return row.get("user_id")

    # Fallback for keys saved before hash existed
    db.cursor.execute(
        "SELECT user_id, meta_value FROM user_meta WHERE meta_key=%s",
        [META_KEY],
    )
    for item in db.cursor.fetchall() or []:
        plain = secret_store.decrypt(item.get("meta_value") or "")
        if plain and hmac.compare_digest(plain, api_key):
            uid = item.get("user_id")
            _upsert_meta(db, uid, META_HASH, digest)
            return uid
    return None


def _extract_bearer(value):
    text = (value or "").strip()
    if text.lower().startswith("bearer "):
        return text[7:].strip()
    return text


def _request_api_key():
    header = (request.headers.get("X-API-Key") or "").strip()
    if header:
        return header
    auth = _extract_bearer(request.headers.get("Authorization") or "")
    if auth:
        return auth
    return (request.args.get("api_key") or "").strip()


def public_endpoint_url():
    base = (infra_settings.wa_app_public_url() or "").strip().rstrip("/")
    if not base:
        base = (request.host_url or "").rstrip("/")
    return base + API_PATH


def _input_to_prompt(raw_json, raw_text):
    if raw_json is None and raw_text:
        try:
            raw_json = json.loads(raw_text)
        except (ValueError, TypeError):
            return raw_text.strip()

    if isinstance(raw_json, str):
        return raw_json.strip()

    if isinstance(raw_json, list):
        return json.dumps(raw_json, ensure_ascii=False, indent=2)

    if isinstance(raw_json, dict):
        schema = raw_json.get("schema")
        prompt = None
        for key in ("prompt", "text", "message", "input", "query", "request"):
            if key not in raw_json:
                continue
            val = raw_json[key]
            if isinstance(val, str):
                prompt = val.strip()
            else:
                prompt = json.dumps(val, ensure_ascii=False, indent=2)
            break
        if prompt and schema is not None:
            return (
                prompt
                + "\n\nReturn JSON matching this schema:\n"
                + json.dumps(schema, ensure_ascii=False, indent=2)
            )
        if prompt:
            return prompt
        return json.dumps(raw_json, ensure_ascii=False, indent=2)

    return (raw_text or "").strip()


def _attach_title_test_marker(db, user_id, data):
    """Append 'send by AI' to zenvekllo Product SEO titles so tests can spot AI output."""
    if not isinstance(data, dict) or data.get("error") or not data.get("title"):
        return data
    try:
        from chatbot_types.product_seo_seed import PRODUCT_SEO_USER_EMAIL, TITLE_TEST_SUFFIX
    except Exception:
        return data
    user = db.row("admins", {"id": user_id})
    if not user or (user.get("email") or "").strip().lower() != PRODUCT_SEO_USER_EMAIL.lower():
        return data
    title = str(data.get("title") or "").strip()
    marker = TITLE_TEST_SUFFIX.strip()
    if not title:
        return data
    if marker.lower() not in title.lower():
        data["title"] = f"{title} {marker}"
    return data


def run_json_bot(db, user_id, prompt):
    prompt = (prompt or "").strip()
    if not prompt:
        return {"success": False, "error": "Prompt is required", "status_code": 400}

    gemini = Gemini()
    if not gemini.api_key:
        return {
            "success": False,
            "error": "Gemini API key is not configured in Site Settings",
            "status_code": 503,
        }

    cache_state = ensure_user_cache(db, user_id)
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    resolved = resolve_chat_reply(
        gemini,
        contents,
        user_id,
        cache_state.get("cache_id") or "",
        cache_state.get("system_instruction") or "",
        db=db,
        cache_model=cache_state.get("cache_model") or None,
    )
    if not resolved.get("success"):
        return {
            "success": False,
            "error": resolved.get("error") or "Gemini error",
            "status_code": 502,
        }

    data = JsonBot.result_from_resolved(resolved)
    data = _attach_title_test_marker(db, user_id, data)
    return {"success": True, "data": data}


def _examples(endpoint, api_key):
    key = api_key or "jb_YOUR_API_KEY"
    return {
        "curl_text": (
            f"curl -X POST '{endpoint}' \\\n"
            f"  -H 'X-API-Key: {key}' \\\n"
            f"  -H 'Content-Type: text/plain' \\\n"
            f"  --data 'Nike Air Max 90'"
        ),
        "curl_json": (
            f"curl -X POST '{endpoint}' \\\n"
            f"  -H 'X-API-Key: {key}' \\\n"
            f"  -H 'Content-Type: application/json' \\\n"
            f"  --data '{{\"prompt\":\"Nike Air Max 90\"}}'"
        ),
        "json_prompt": {"prompt": "Nike Air Max 90"},
        "json_object": {
            "title": "Nike Air Max 90",
            "brand": "Nike",
        },
        "json_schema": {
            "prompt": "Write product details for Nike Air Max 90",
            "schema": {
                "title": "string",
                "short_description": "string",
                "bullet_points": ["string"],
                "price_suggestion": "string",
            },
        },
    }


def _dashboard_payload(db, user_id, user, ctype, api_key):
    endpoint = public_endpoint_url()
    return {
        "status": True,
        "is_json_bot": True,
        "user_id": user_id,
        "user_name": (user or {}).get("name") or "",
        "chatbot_type": {
            "id": ctype.get("id"),
            "title": ctype.get("title"),
            "handler_class": ctype.get("handler_class"),
        },
        "endpoint": endpoint,
        "local_endpoint": (request.host_url or "").rstrip("/") + API_PATH,
        "path": API_PATH,
        "method": "POST",
        "api_key": api_key,
        "api_key_preview": secret_store.mask_secret(api_key),
        "has_api_key": bool(api_key),
        "auth": {
            "header": "X-API-Key",
            "header_alt": "Authorization: Bearer <api_key>",
        },
        "accepts": ["application/json", "text/plain"],
        "logs_path": f"/users/{user_id}/json-api/logs",
        "examples": _examples(endpoint, api_key),
    }


@json_bot_api_bp.route("/users/<int:user_id>/json-api", methods=["GET"])
def get_json_api(user_id):
    db = Database()
    try:
        user = db.row("admins", {"id": user_id})
        if not user:
            return jsonify({"status": False, "message": "User not found"}), 404
        meta = _meta_map(db, user_id)
        ctype = _json_bot_type(db, user_id, meta)
        if not ctype:
            return jsonify({
                "status": False,
                "is_json_bot": False,
                "message": "This user is not connected to a JSON Writer chatbot",
            }), 403
        api_key, _created = ensure_api_key(db, user_id, meta)
        return jsonify(_dashboard_payload(db, user_id, user, ctype, api_key))
    finally:
        db.close()


@json_bot_api_bp.route("/users/<int:user_id>/json-api/key", methods=["POST"])
def regenerate_json_api_key(user_id):
    db = Database()
    try:
        user = db.row("admins", {"id": user_id})
        if not user:
            return jsonify({"status": False, "message": "User not found"}), 404
        meta = _meta_map(db, user_id)
        ctype = _json_bot_type(db, user_id, meta)
        if not ctype:
            return jsonify({
                "status": False,
                "is_json_bot": False,
                "message": "This user is not connected to a JSON Writer chatbot",
            }), 403
        api_key = _save_api_key(db, user_id, _new_api_key())
        payload = _dashboard_payload(db, user_id, user, ctype, api_key)
        payload["message"] = "API key regenerated. Update any saved integrations."
        payload["regenerated"] = True
        return jsonify(payload)
    finally:
        db.close()


@json_bot_api_bp.route("/users/<int:user_id>/json-api/test", methods=["POST"])
def test_json_api(user_id):
    db = Database()
    try:
        user = db.row("admins", {"id": user_id})
        if not user:
            return jsonify({"status": False, "message": "User not found"}), 404
        ctype = _json_bot_type(db, user_id)
        if not ctype:
            return jsonify({
                "status": False,
                "is_json_bot": False,
                "message": "This user is not connected to a JSON Writer chatbot",
            }), 403
        prompt, raw_text, raw_json = _read_request_payload()
        started = time.time()
        result = run_json_bot(db, user_id, prompt)
        duration_ms = int((time.time() - started) * 1000)
        if not result.get("success"):
            status_code = result.get("status_code") or 500
            insert_json_api_log(
                db,
                user_id,
                source="test",
                success=False,
                status_code=status_code,
                duration_ms=duration_ms,
                request_text=prompt,
                request_body=raw_json if raw_json is not None else raw_text,
                error_message=result.get("error"),
            )
            return jsonify({
                "status": False,
                "message": result.get("error") or "Failed",
            }), status_code
        insert_json_api_log(
            db,
            user_id,
            source="test",
            success=True,
            status_code=200,
            duration_ms=duration_ms,
            request_text=prompt,
            request_body=raw_json if raw_json is not None else raw_text,
            response=result.get("data"),
        )
        return jsonify({"status": True, "data": result.get("data")})
    finally:
        db.close()


@json_bot_api_bp.route(API_PATH, methods=["POST"])
def public_json_bot():
    api_key = _request_api_key()
    if not api_key:
        return jsonify({
            "status": False,
            "message": "API key required. Send X-API-Key or Authorization: Bearer <key>.",
        }), 401

    db = Database()
    try:
        user_id = _lookup_user_by_api_key(db, api_key)
        if not user_id:
            return jsonify({"status": False, "message": "Invalid API key"}), 401
        ctype = _json_bot_type(db, user_id)
        if not ctype:
            return jsonify({
                "status": False,
                "message": "This API key is not linked to a JSON Writer chatbot",
            }), 403
        prompt, raw_text, raw_json = _read_request_payload()
        logger.info("JSON API | user_id=%s | prompt=%s", user_id, (prompt or "")[:200])
        started = time.time()
        result = run_json_bot(db, user_id, prompt)
        duration_ms = int((time.time() - started) * 1000)
        if not result.get("success"):
            status_code = result.get("status_code") or 500
            insert_json_api_log(
                db,
                user_id,
                source="api",
                success=False,
                status_code=status_code,
                duration_ms=duration_ms,
                request_text=prompt,
                request_body=raw_json if raw_json is not None else raw_text,
                error_message=result.get("error"),
            )
            return jsonify({
                "status": False,
                "message": result.get("error") or "Failed",
            }), status_code
        insert_json_api_log(
            db,
            user_id,
            source="api",
            success=True,
            status_code=200,
            duration_ms=duration_ms,
            request_text=prompt,
            request_body=raw_json if raw_json is not None else raw_text,
            response=result.get("data"),
        )
        return jsonify({
            "status": True,
            "data": result.get("data"),
            "user_id": user_id,
        })
    finally:
        db.close()


def _json_bot_or_error(db, user_id):
    user = db.row("admins", {"id": user_id})
    if not user:
        return None, None, (jsonify({"status": False, "message": "User not found"}), 404)
    ctype = _json_bot_type(db, user_id)
    if not ctype:
        return user, None, (jsonify({
            "status": False,
            "is_json_bot": False,
            "message": "This user is not connected to a JSON Writer chatbot",
        }), 403)
    return user, ctype, None


@json_bot_api_bp.route("/users/<int:user_id>/json-api/logs", methods=["GET"])
def list_json_api_logs(user_id):
    db = Database()
    try:
        _user, _ctype, err = _json_bot_or_error(db, user_id)
        if err:
            return err
        logs, total, page, per_page = _paginate_user_logs(
            db,
            user_id,
            page=request.args.get("page", 1),
            per_page=request.args.get("per_page", 20),
            include_bodies=True,
        )
    finally:
        db.close()

    return jsonify({
        "status": True,
        "logs": logs,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": max((total + per_page - 1) // per_page, 1),
        },
    })


@json_bot_api_bp.route("/users/<int:user_id>/json-api/logs/<int:log_id>", methods=["GET"])
def get_json_api_log(user_id, log_id):
    db = Database()
    try:
        _user, _ctype, err = _json_bot_or_error(db, user_id)
        if err:
            return err
        row = db.row(LOGS_TABLE, {"id": log_id, "user_id": user_id})
        if not row:
            return jsonify({"status": False, "message": "Log not found"}), 404
        log = _serialize_log(row, include_bodies=True)
    finally:
        db.close()
    return jsonify({"status": True, "log": log})


@json_bot_api_bp.route("/users/<int:user_id>/json-api/logs/<int:log_id>", methods=["DELETE"])
def delete_json_api_log(user_id, log_id):
    db = Database()
    try:
        _user, _ctype, err = _json_bot_or_error(db, user_id)
        if err:
            return err
        deleted = db.delete(LOGS_TABLE, {"id": log_id, "user_id": user_id})
    finally:
        db.close()
    if not deleted:
        return jsonify({"status": False, "message": "Log not found"}), 404
    return jsonify({"status": True, "message": "Log deleted"})


@json_bot_api_bp.route("/users/<int:user_id>/json-api/logs", methods=["DELETE"])
def clear_json_api_logs(user_id):
    db = Database()
    try:
        _user, _ctype, err = _json_bot_or_error(db, user_id)
        if err:
            return err
        db.cursor.execute(f"DELETE FROM {LOGS_TABLE} WHERE user_id=%s", [user_id])
        deleted = db.cursor.rowcount
        db.conn.commit()
    finally:
        db.close()
    return jsonify({
        "status": True,
        "message": f"Deleted {deleted} log(s)",
        "deleted": deleted,
    })
