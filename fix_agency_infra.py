"""One-shot: lock AgencyWA + webhook URLs in site_settings (production)."""

from db import Database
from site_settings import ensure_schema, _upsert
import infra_settings
import secret_store

AGENCY_URL = "https://orange-rat-729701.hostingersite.com/api"
AGENCY_KEY = "agw_chatbot_integration_key_01"
AGENCY_SECRET = "chatbot_api_secret_9f3a2c1b8e7d6f5a4c3b2a1d0e9f8a7b"
WEBHOOK_BASE = "https://38.84.24.79:5000"
NOTIFY = "923004210607"


def main():
    ensure_schema()
    db = Database()
    try:
        _upsert(db, "api_base_url", "/api")
        _upsert(db, "agency_api_base_url", AGENCY_URL)
        _upsert(db, "wa_app_public_url", WEBHOOK_BASE)
        _upsert(db, "wa_webhook_notify_phone", NOTIFY)
        _upsert(db, "agency_api_key", secret_store.encrypt(AGENCY_KEY))
        _upsert(db, "agency_api_secret", secret_store.encrypt(AGENCY_SECRET))
        infra_settings.clear_cache()
        print("OK site_settings locked:")
        print("  agency_api_base_url =", AGENCY_URL)
        print("  wa_app_public_url   =", WEBHOOK_BASE)
        print("  api_base_url        = /api")
    finally:
        db.close()


if __name__ == "__main__":
    main()
