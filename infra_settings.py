"""Runtime infrastructure URLs — DB (site_settings) with env/file fallbacks."""

import os
import re

try:
    from config import (
        AGENCY_API_BASE_URL as _CFG_AGENCY_URL,
        AGENCY_API_KEY as _CFG_AGENCY_KEY,
        AGENCY_API_SECRET as _CFG_AGENCY_SECRET,
        WA_APP_PUBLIC_URL as _CFG_WA_PUBLIC,
        WA_WEBHOOK_NOTIFY_PHONE as _CFG_NOTIFY,
        API_BASE_URL as _CFG_API_BASE,
    )
except Exception:
    _CFG_AGENCY_URL = "https://orange-rat-729701.hostingersite.com/api"
    _CFG_AGENCY_KEY = "agw_chatbot_integration_key_01"
    _CFG_AGENCY_SECRET = "chatbot_api_secret_9f3a2c1b8e7d6f5a4c3b2a1d0e9f8a7b"
    _CFG_WA_PUBLIC = (
        "https://mediumturquoise-badger-120093.hostingersite.com/api"
    )
    _CFG_NOTIFY = "923004210607"
    _CFG_API_BASE = "/api"

_DEFAULTS = {
    "api_base_url": os.environ.get("API_BASE_URL", _CFG_API_BASE),
    "agency_api_base_url": os.environ.get("AGENCY_API_BASE_URL", _CFG_AGENCY_URL),
    "agency_api_key": os.environ.get("AGENCY_API_KEY", _CFG_AGENCY_KEY),
    "agency_api_secret": os.environ.get(
        "AGENCY_API_SECRET", _CFG_AGENCY_SECRET
    ),
    "wa_app_public_url": os.environ.get("WA_APP_PUBLIC_URL", _CFG_WA_PUBLIC),
    "wa_webhook_notify_phone": os.environ.get(
        "WA_WEBHOOK_NOTIFY_PHONE", _CFG_NOTIFY
    ),
}

INFRA_KEYS = tuple(_DEFAULTS.keys())
_cache = {}

_REMOTE_IP = "38.84.24.79"
_PROD_PYTHON = f"https://{_REMOTE_IP}:5000"
_PROD_AGENCY = "https://orange-rat-729701.hostingersite.com/api"
# AgencyWA must POST to a trusted SSL host; Hostinger /api proxies to Python.
_PROD_WEBHOOK_RELAY = (
    "https://mediumturquoise-badger-120093.hostingersite.com/api"
)


def clear_cache():
    _cache.clear()


def normalize_infra_value(key, value):
    """Force production-safe URLs so bad UI input cannot break the stack."""
    text = "" if value is None else str(value).strip()

    if key == "api_base_url":
        # Browser must always use Hostinger PHP proxy
        return "/api"

    if key == "agency_api_base_url":
        if not text:
            return _PROD_AGENCY
        text = text.rstrip("/")
        # Root Hostinger AgencyWA site without /api
        if re.match(r"^https?://orange-rat-729701\.hostingersite\.com/?$", text, re.I):
            return _PROD_AGENCY
        if text.lower().endswith("/agencywa"):
            return text + "/api"
        return text

    if key == "wa_app_public_url":
        text = text.rstrip("/")
        if not text:
            return _PROD_WEBHOOK_RELAY
        # Self-signed RDP IP — AgencyWA delivery fails; use Hostinger relay
        if _REMOTE_IP in text:
            return _PROD_WEBHOOK_RELAY
        # Agency panel is not a webhook receiver
        if "orange-rat" in text.lower():
            return _PROD_WEBHOOK_RELAY
        # Vite / legacy panel
        if ":5173" in text or ":8001" in text:
            return _PROD_WEBHOOK_RELAY
        # React Hostinger (with or without /api) → canonical relay
        if "mediumturquoise-badger" in text.lower():
            return _PROD_WEBHOOK_RELAY
        if text.endswith("/api") and "hostingersite.com" in text.lower():
            return _PROD_WEBHOOK_RELAY
        return text

    return text


def _load_db():
    from db import Database
    import secret_store

    db = Database()
    try:
        rows = db.select("site_settings")
        out = {}
        for r in rows:
            key = r["setting_key"]
            if key not in _DEFAULTS:
                continue
            raw = r["setting_value"] or ""
            if key in ("agency_api_key", "agency_api_secret"):
                out[key] = secret_store.decrypt(raw) if raw else ""
            else:
                out[key] = normalize_infra_value(key, raw)
        return out
    finally:
        db.close()


def get(key, default=None):
    if key not in _DEFAULTS:
        return default
    if not _cache:
        _cache.update(_load_db())
    val = _cache.get(key)
    if val is None or val == "":
        val = _DEFAULTS[key]
    if key in ("api_base_url", "agency_api_base_url", "wa_app_public_url"):
        val = normalize_infra_value(key, val)
    return val if val is not None else default


def agency_api_base_url():
    return get("agency_api_base_url")


def agency_api_key():
    return (get("agency_api_key") or "").strip()


def agency_api_secret():
    return (get("agency_api_secret") or "").strip()


def wa_app_public_url():
    return get("wa_app_public_url")


def wa_webhook_notify_phone():
    return get("wa_webhook_notify_phone")


def api_base_url():
    return get("api_base_url")


def agency_configured():
    return bool(agency_api_base_url() and agency_api_key() and agency_api_secret())


def infra_snapshot():
    return {k: get(k) for k in INFRA_KEYS}
