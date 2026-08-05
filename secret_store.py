"""Encrypt sensitive site_settings values at rest (stdlib AES-free stream + HMAC).

Master key: SETTINGS_MASTER_KEY env, or python/.settings_master_key (auto-created).
Ciphertext format: enc:v1:<base64(nonce + mac + ciphertext)>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

PREFIX = "enc:v1:"

_KEY_FILE = Path(__file__).resolve().parent / ".settings_master_key"
_master = None


def _load_or_create_master_key() -> bytes:
    global _master
    if _master is not None:
        return _master

    env = (os.environ.get("SETTINGS_MASTER_KEY") or "").strip()
    if env:
        _master = hashlib.sha256(env.encode("utf-8")).digest()
        return _master

    if _KEY_FILE.exists():
        data = _KEY_FILE.read_bytes().strip()
        if data:
            # Accept raw 32 bytes, or text/base64 material → sha256
            if len(data) == 32:
                _master = data
            else:
                _master = hashlib.sha256(data).digest()
            return _master

    raw = secrets.token_bytes(32)
    _KEY_FILE.write_bytes(raw)
    try:
        os.chmod(_KEY_FILE, 0o600)
    except OSError:
        pass
    _master = raw
    return _master


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(plaintext: str) -> str:
    text = (plaintext or "").strip()
    if not text:
        return ""
    if is_encrypted(text):
        return text
    key = _load_or_create_master_key()
    data = text.encode("utf-8")
    nonce = secrets.token_bytes(16)
    stream = _keystream(key, nonce, len(data))
    ciphertext = bytes(a ^ b for a, b in zip(data, stream))
    mac = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    blob = base64.urlsafe_b64encode(nonce + mac + ciphertext).decode("ascii")
    return PREFIX + blob


def decrypt(value: str) -> str:
    text = value if isinstance(value, str) else ""
    if not text:
        return ""
    if not is_encrypted(text):
        return text
    key = _load_or_create_master_key()
    try:
        raw = base64.urlsafe_b64decode(text[len(PREFIX) :].encode("ascii"))
        nonce, mac, ciphertext = raw[:16], raw[16:48], raw[48:]
        expect = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expect):
            return ""
        stream = _keystream(key, nonce, len(ciphertext))
        plain = bytes(a ^ b for a, b in zip(ciphertext, stream))
        return plain.decode("utf-8")
    except Exception:
        return ""


def mask_secret(value: str, keep=4) -> str:
    plain = decrypt(value) if is_encrypted(value) else (value or "")
    plain = plain.strip()
    if not plain:
        return ""
    if len(plain) <= keep:
        return "•" * len(plain)
    return "•" * max(8, len(plain) - keep) + plain[-keep:]


# Setting keys that must never be returned plaintext to the UI / logs
SECRET_SETTING_KEYS = frozenset(
    {
        "gemini_api_key",
        "agency_api_key",
        "agency_api_secret",
        # legacy plaintext key name (read-only migration)
        "gemini_key",
    }
)
