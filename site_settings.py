from flask import Blueprint, request, jsonify
from db import Database
import infra_settings
import secret_store

site_settings_bp = Blueprint("site_settings", __name__)

INFRA_KEYS = infra_settings.INFRA_KEYS

# Public setting name for Gemini (replaces legacy plaintext `gemini_key`)
GEMINI_API_KEY = "gemini_api_key"
LEGACY_GEMINI_KEY = "gemini_key"

SECRET_KEYS = secret_store.SECRET_SETTING_KEYS


def ensure_schema():
    """Create the site_settings table (key/value) if it does not exist.

    Stores global site configuration like site name, logo and the gemini key.
    LONGTEXT is used so the logo can be stored as a base64 data URL.
    """
    db = Database()
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS site_settings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                setting_key VARCHAR(191) NOT NULL UNIQUE,
                setting_value LONGTEXT DEFAULT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        _migrate_secrets(db)
    finally:
        db.close()


def _upsert(db, key, value):
    existing = db.row("site_settings", {"setting_key": key})
    if existing:
        db.update("site_settings", {"setting_value": value}, {"setting_key": key})
    else:
        db.insert("site_settings", {"setting_key": key, "setting_value": value})


def _delete_key(db, key):
    db.execute("DELETE FROM site_settings WHERE setting_key=%s", [key])


def _migrate_secrets(db):
    """Encrypt secret rows; drop leaked plaintext gemini_key after optional copy."""
    for key in ("agency_api_key", "agency_api_secret", GEMINI_API_KEY):
        row = db.row("site_settings", {"setting_key": key})
        if not row:
            continue
        raw = row.get("setting_value") or ""
        if not raw or secret_store.is_encrypted(raw):
            continue
        _upsert(db, key, secret_store.encrypt(raw))

    legacy = db.row("site_settings", {"setting_key": LEGACY_GEMINI_KEY})
    if legacy:
        raw = (legacy.get("setting_value") or "").strip()
        current = db.row("site_settings", {"setting_key": GEMINI_API_KEY})
        has_new = bool((current or {}).get("setting_value"))
        # Do not keep known-leaked plaintext keys. Force admin to paste a fresh key.
        # If a new encrypted key already exists, just drop legacy.
        if raw and not has_new:
            # Leave gemini_api_key empty so UI prompts for a new key (leaked keys must be rotated).
            pass
        _delete_key(db, LEGACY_GEMINI_KEY)
        infra_settings.clear_cache()


def _public_settings(rows):
    """Decrypt internally but only expose masks for secret keys."""
    settings = {}
    for row in rows:
        key = row["setting_key"]
        value = row["setting_value"]
        if key in SECRET_KEYS or key == GEMINI_API_KEY:
            plain = secret_store.decrypt(value) if value else ""
            settings[key] = ""
            settings[f"{key}_set"] = bool(plain)
            settings[f"{key}_preview"] = secret_store.mask_secret(value) if plain else ""
        else:
            settings[key] = value
    return settings


def get_secret_value(db, key):
    """Plaintext secret for server-side use only."""
    row = db.row("site_settings", {"setting_key": key})
    raw = (row or {}).get("setting_value") or ""
    return secret_store.decrypt(raw).strip() if raw else ""


@site_settings_bp.route("/site-settings", methods=["GET"])
def get_site_settings():
    db = Database()
    try:
        _migrate_secrets(db)
        rows = db.select("site_settings")
    finally:
        db.close()

    settings = _public_settings(rows)
    for key in INFRA_KEYS:
        if key in SECRET_KEYS:
            settings.setdefault(key, "")
            settings.setdefault(f"{key}_set", False)
            settings.setdefault(f"{key}_preview", "")
            # Prefer DB; if empty, mark set when env/default exists (do not leak value)
            if not settings.get(f"{key}_set"):
                fallback = (infra_settings.get(key) or "").strip()
                if fallback:
                    settings[f"{key}_set"] = True
                    settings[f"{key}_preview"] = secret_store.mask_secret(fallback)
        else:
            settings.setdefault(key, infra_settings.get(key))

    settings.setdefault(GEMINI_API_KEY, "")
    settings.setdefault(f"{GEMINI_API_KEY}_set", bool(settings.get(f"{GEMINI_API_KEY}_set")))
    settings.setdefault(f"{GEMINI_API_KEY}_preview", settings.get(f"{GEMINI_API_KEY}_preview") or "")
    # Back-compat aliases for older UI
    settings["gemini_key"] = ""
    settings["gemini_key_set"] = settings.get(f"{GEMINI_API_KEY}_set")
    settings["gemini_key_preview"] = settings.get(f"{GEMINI_API_KEY}_preview")

    return jsonify({"status": True, "settings": settings})


@site_settings_bp.route("/site-settings", methods=["POST"])
def save_site_settings():
    data = request.json or {}

    # Accepts { "settings": { "site_name": "...", "logo": "...", "gemini_api_key": "..." } }
    settings = data.get("settings")

    if not isinstance(settings, dict) or not settings:
        return jsonify({"status": False, "message": "No settings provided"}), 400

    # Map legacy UI field → new encrypted key
    if "gemini_key" in settings and GEMINI_API_KEY not in settings:
        settings[GEMINI_API_KEY] = settings.pop("gemini_key")
    elif "gemini_key" in settings:
        settings.pop("gemini_key", None)

    db = Database()
    try:
        for key, value in settings.items():
            key = str(key)
            if key.endswith("_set") or key.endswith("_preview"):
                continue

            if key in SECRET_KEYS or key == GEMINI_API_KEY:
                text = "" if value is None else str(value).strip()
                # Empty = keep existing encrypted secret
                if not text or text.startswith("•"):
                    continue
                _upsert(db, key, secret_store.encrypt(text))
                if key == GEMINI_API_KEY:
                    _delete_key(db, LEGACY_GEMINI_KEY)
            else:
                _upsert(db, key, value)

        infra_settings.clear_cache()
        rows = db.select("site_settings")
    finally:
        db.close()

    out = _public_settings(rows)
    for key in INFRA_KEYS:
        if key in SECRET_KEYS:
            out.setdefault(key, "")
            out.setdefault(f"{key}_set", False)
            out.setdefault(f"{key}_preview", "")
        else:
            out.setdefault(key, infra_settings.get(key))
    out.setdefault(GEMINI_API_KEY, "")
    out["gemini_key"] = ""
    out["gemini_key_set"] = out.get(f"{GEMINI_API_KEY}_set")
    out["gemini_key_preview"] = out.get(f"{GEMINI_API_KEY}_preview")

    return jsonify({
        "status": True,
        "message": "Site settings saved successfully",
        "settings": out,
    })
