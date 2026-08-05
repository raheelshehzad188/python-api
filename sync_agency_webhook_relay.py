"""Point all AgencyWA sessions at the Hostinger SSL webhook relay.

Agency cannot POST to https://38.84.24.79:5000 (self-signed).
Relay: https://mediumturquoise-badger-120093.hostingersite.com/api/webhooks/...

Run on RDP after pulling python-api (uses DB + Agency API keys).
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

import infra_settings
from db import Database
from site_settings import ensure_schema, _upsert

RELAY = "https://mediumturquoise-badger-120093.hostingersite.com/api"
WA_TOKEN_KEY = "wa_webhook_token"


def _agency_request(method, path, body=None, query=None):
    base = (infra_settings.agency_api_base_url() or "").rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": infra_settings.agency_api_key(),
            "X-API-Secret": infra_settings.agency_api_secret(),
        },
    )
    ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=45) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {"message": raw[:500]}
        return e.code, payload


def _relay_url(user_id: int, token: str) -> str:
    return f"{RELAY}/webhooks/whatsapp/{user_id}/{token}"


def _token_for_user(db, user_id: int) -> str:
    row = db.row("user_meta", {"user_id": user_id, "meta_key": WA_TOKEN_KEY})
    return ((row or {}).get("meta_value") or "").strip()


def main():
    ensure_schema()
    db = Database()
    try:
        _upsert(db, "wa_app_public_url", RELAY)
        infra_settings.clear_cache()
        print("OK wa_app_public_url =", RELAY)

        status, data = _agency_request("GET", "sessions.php")
        sessions = ((data or {}).get("data") or {}).get("sessions") or []
        print(f"Agency sessions: {len(sessions)} (HTTP {status})")

        for s in sessions:
            name = (s.get("session_name") or "").strip()
            old = (s.get("webhook_url") or "").strip()
            m = re.search(r"/webhooks/whatsapp/(\d+)/([^/\s?#]+)", old)
            if m:
                user_id = int(m.group(1))
                token = m.group(2)
            else:
                um = re.match(r"user_(\d+)", name)
                if not um:
                    print("SKIP", name, "no user id")
                    continue
                user_id = int(um.group(1))
                token = _token_for_user(db, user_id)
                if not token:
                    print("SKIP", name, "no wa_webhook_token")
                    continue
            new = _relay_url(user_id, token)
            if old == new:
                print("OK already", name)
                continue
            code, out = _agency_request(
                "POST",
                "sessions.php",
                body={"session_name": name, "webhook_url": new},
            )
            ok = bool((out or {}).get("success"))
            print(("OK" if ok else "FAIL"), name, "HTTP", code, "->", new)
    finally:
        db.close()


if __name__ == "__main__":
    main()
