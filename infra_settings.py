"""Runtime infrastructure URLs — DB (site_settings) with env/file fallbacks."""

import os

_DEFAULTS = {
    "api_base_url": os.environ.get("API_BASE_URL", "http://127.0.0.1:5000"),
    "agency_api_base_url": os.environ.get(
        "AGENCY_API_BASE_URL", "http://localhost/agencywa/api"
    ),
    "agency_api_key": os.environ.get(
        "AGENCY_API_KEY", "agw_chatbot_integration_key_01"
    ),
    "agency_api_secret": os.environ.get(
        "AGENCY_API_SECRET",
        "chatbot_api_secret_9f3a2c1b8e7d6f5a4c3b2a1d0e9f8a7b",
    ),
    "wa_app_public_url": os.environ.get("WA_APP_PUBLIC_URL", "http://127.0.0.1:5000"),
    "wa_webhook_notify_phone": os.environ.get("WA_WEBHOOK_NOTIFY_PHONE", ""),
}

INFRA_KEYS = tuple(_DEFAULTS.keys())
_cache = {}


def clear_cache():
    _cache.clear()


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
                out[key] = raw
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
