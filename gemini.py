import json
import logging
import re
import time
import urllib.request
import urllib.error

from db import Database

logger = logging.getLogger("gemini")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Primary model first; fall back when Google reports overload / rate limits.
# When using cachedContent, ONLY the cache's model is allowed (no cross-model fallback).
# Do NOT list retired models (e.g. gemini-1.5-flash) — Google returns "not found".
DEFAULT_MODEL = "gemini-2.5-flash"
FALLBACK_MODELS = ("gemini-2.0-flash", "gemini-2.5-flash-lite")
RETIRED_MODELS = frozenset({
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-1.5-flash-latest",
    "gemini-pro",
})
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.5  # seconds; doubled after each retry

CACHE_NOT_FOUND_MARKERS = (
    "cachedcontent not found",
    "cached content not found",
    "cachedcontents/",
    "is expired",
    "cache is expired",
)


def get_api_key():
    """Read the Gemini API key from encrypted site_settings."""
    from site_settings import GEMINI_API_KEY, get_secret_value

    db = Database()
    try:
        key = get_secret_value(db, GEMINI_API_KEY)
        if key:
            return key
        # Legacy plaintext row (should be migrated/cleared)
        row = db.row("site_settings", {"setting_key": "gemini_key"})
        return ((row or {}).get("setting_value") or "").strip()
    finally:
        db.close()


def _truncate_for_log(value, limit=4000):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated {len(text) - limit} chars]"


class Gemini:
    """Gemini helper: generateContent + explicit context caching (REST v1beta)."""

    def __init__(self, api_key=None, model=DEFAULT_MODEL):
        self.api_key = api_key if api_key is not None else get_api_key()
        self.model = model

    @staticmethod
    def _is_retryable_error(message, http_code=0):
        """True when Gemini is overloaded or rate-limited — worth retrying."""
        msg = (message or "").lower()
        if http_code in (429, 500, 502, 503, 504):
            return True
        return any(
            phrase in msg
            for phrase in (
                "high demand",
                "rate limit",
                "quota",
                "resource exhausted",
                "overloaded",
                "unavailable",
                "try again",
            )
        )

    @staticmethod
    def is_cache_error(message, http_code=0):
        """True when the cachedContent id is missing/expired/invalid."""
        msg = (message or "").lower()
        if http_code == 404 and "cache" in msg:
            return True
        if "cachedcontent" in msg or "cached content" in msg:
            return True
        return any(marker in msg for marker in CACHE_NOT_FOUND_MARKERS)

    @staticmethod
    def _is_model_unavailable(message, http_code=0):
        """True when this model id is retired / not supported — try next model."""
        msg = (message or "").lower()
        if http_code == 404:
            return True
        return any(
            phrase in msg
            for phrase in (
                "is not found",
                "not supported for generatecontent",
                "not supported for",
                "no longer available",
                "invalid model",
            )
        )

    def _models_to_try(self, preferred=None, allow_fallback=True):
        """Primary model first, then fallbacks (no duplicates / no retired ids)."""
        seen = set()
        order = []
        primary = preferred or self.model
        if primary in RETIRED_MODELS:
            primary = DEFAULT_MODEL
        candidates = (primary,) if not allow_fallback else (primary, DEFAULT_MODEL, *FALLBACK_MODELS)
        for name in candidates:
            if not name or name in seen or name in RETIRED_MODELS:
                continue
            seen.add(name)
            order.append(name)
        return order or [DEFAULT_MODEL]

    # ------------------------------------------------------------------ #
    #  low level request helper                                          #
    # ------------------------------------------------------------------ #
    def _request(self, method, path, payload=None):
        url = f"{GEMINI_BASE}/{path}"
        url += ("&" if "?" in url else "?") + "key=" + (self.api_key or "")

        data = json.dumps(payload).encode("utf-8") if payload is not None else None

        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req) as resp:
                body = resp.read().decode("utf-8")
                return resp.getcode(), (json.loads(body) if body else {})
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = {"error": {"message": body or "HTTP error"}}
            return e.code, parsed
        except urllib.error.URLError as e:
            return 0, {"error": {"message": str(e.reason)}}

    # ------------------------------------------------------------------ #
    #  generateContent (chat)                                            #
    # ------------------------------------------------------------------ #
    def send(
        self,
        contents,
        cached_content="",
        system_instruction=None,
        json_output=True,
        model=None,
    ):
        """Call generateContent.

        Official cache rules:
        - Attach cachedContent by resource name (cachedContents/...).
        - Do NOT also send systemInstruction (cache already has it).
        - Use the SAME model the cache was created with.
        - contents should be the conversation turns only.
        """
        payload = {"contents": contents}

        if json_output:
            payload["generationConfig"] = {"responseMimeType": "application/json"}

        cache_id = (cached_content or "").strip()
        cache_attached = bool(cache_id)

        if cache_attached:
            # Must not overwrite / duplicate the cached system instruction.
            payload["cachedContent"] = cache_id
            # Pin to cache model — cross-model cachedContent is invalid.
            models = self._models_to_try(preferred=model or self.model, allow_fallback=False)
        elif system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
            models = self._models_to_try(preferred=model, allow_fallback=True)
        else:
            models = self._models_to_try(preferred=model, allow_fallback=True)

        logger.info(
            "Gemini generateContent start | cache_id=%s | cache_attached=%s | "
            "has_inline_system_instruction=%s | models=%s | contents_turns=%s",
            cache_id or None,
            cache_attached,
            (not cache_attached) and bool(system_instruction),
            models,
            len(contents or []),
        )
        logger.info("Gemini request payload: %s", _truncate_for_log(payload))

        last_error = "Gemini Error"
        last_response = None
        last_http = 0

        for model_name in models:
            for attempt in range(MAX_RETRIES):
                http_code, response = self._request(
                    "POST", f"models/{model_name}:generateContent", payload
                )
                last_http = http_code

                if isinstance(response, dict) and response.get("error"):
                    last_error = response["error"].get("message", "Gemini Error")
                    last_response = response
                    logger.error(
                        "Gemini error | model=%s | attempt=%s | http=%s | cache_id=%s | error=%s | response=%s",
                        model_name,
                        attempt + 1,
                        http_code,
                        cache_id or None,
                        last_error,
                        _truncate_for_log(response),
                    )

                    if self._is_retryable_error(last_error, http_code):
                        if attempt < MAX_RETRIES - 1:
                            time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                            continue
                        break  # exhausted retries for this model, try next

                    # Retired / unknown model → try next in the list
                    if self._is_model_unavailable(last_error, http_code):
                        break

                    return {
                        "success": False,
                        "error": last_error,
                        "response": response,
                        "http_code": http_code,
                        "cache_attached": cache_attached,
                        "cache_id": cache_id or None,
                        "cache_error": cache_attached and self.is_cache_error(last_error, http_code),
                        "model": model_name,
                    }

                usage = (response or {}).get("usageMetadata") or {}
                logger.info(
                    "Gemini success | model=%s | http=%s | cache_id=%s | cache_attached=%s | "
                    "cached_content_token_count=%s | prompt_token_count=%s | response=%s",
                    model_name,
                    http_code,
                    cache_id or None,
                    cache_attached,
                    usage.get("cachedContentTokenCount"),
                    usage.get("promptTokenCount"),
                    _truncate_for_log(response),
                )

                return {
                    "success": True,
                    "http_code": http_code,
                    "response": response,
                    "model": model_name,
                    "cache_attached": cache_attached,
                    "cache_id": cache_id or None,
                    "cached_content_token_count": usage.get("cachedContentTokenCount"),
                }

        return {
            "success": False,
            "error": last_error,
            "response": last_response,
            "http_code": last_http,
            "cache_attached": cache_attached,
            "cache_id": cache_id or None,
            "cache_error": cache_attached and self.is_cache_error(last_error, last_http),
        }

    def get_text(self, response):
        """Return customer-facing model text only (skip Gemini thought parts)."""
        try:
            parts = response["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError):
            return ""
        if not isinstance(parts, list):
            return ""

        texts = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            # Gemini 2.5+ may attach internal reasoning as thought parts.
            if part.get("thought") is True:
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
        return "\n".join(texts)

    def get_json(self, response):
        text = self.get_text(response).strip()
        return self._parse_json_payload(text)

    @staticmethod
    def _parse_json_payload(text):
        """Parse JSON even when the model prefixes internal reasoning before it."""
        if not text:
            return None
        cleaned = text.strip()
        cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^```\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except (ValueError, TypeError):
            pass

        # Extract the first balanced JSON object/array from mixed text.
        for opener, closer in (("{", "}"), ("[", "]")):
            start = cleaned.find(opener)
            if start < 0:
                continue
            depth = 0
            in_str = False
            escape = False
            for i in range(start, len(cleaned)):
                ch = cleaned[i]
                if in_str:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        chunk = cleaned[start : i + 1]
                        try:
                            return json.loads(chunk)
                        except (ValueError, TypeError):
                            break
        return None

    # ------------------------------------------------------------------ #
    #  context caching                                                   #
    # ------------------------------------------------------------------ #
    def create_cache(self, system_instruction, ttl_seconds=3600, contents=None, display_name=None):
        """Create explicit CachedContent.

        Official pattern: reusable business knowledge lives on the cache;
        generateContent later only sends conversation turns + cachedContent.

        Gemini requires a minimum token count (~1024). We therefore place the
        full instruction in systemInstruction AND mirror it in contents so
        shorter Service catalogs still meet the minimum.
        """
        mirrored = contents
        if not mirrored:
            mirrored = [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "CACHED BUSINESS KNOWLEDGE (authoritative — follow exactly):\n\n"
                                + (system_instruction or "")
                            )
                        }
                    ],
                }
            ]

        payload = {
            "model": f"models/{self.model}",
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": mirrored,
            "ttl": f"{ttl_seconds}s",
        }
        if display_name:
            payload["displayName"] = display_name

        logger.info(
            "Gemini create_cache | model=%s | ttl=%ss | display_name=%s | instruction_chars=%s",
            self.model,
            ttl_seconds,
            display_name,
            len(system_instruction or ""),
        )
        logger.info("Gemini create_cache payload: %s", _truncate_for_log(payload))

        http_code, response = self._request("POST", "cachedContents", payload)

        if isinstance(response, dict) and response.get("error"):
            err = response["error"].get("message", "Cache error")
            logger.error(
                "Gemini create_cache failed | http=%s | error=%s | response=%s",
                http_code,
                err,
                _truncate_for_log(response),
            )
            return {
                "success": False,
                "error": err,
                "response": response,
                "http_code": http_code,
            }

        logger.info(
            "Gemini create_cache ok | name=%s | expireTime=%s | response=%s",
            response.get("name"),
            response.get("expireTime"),
            _truncate_for_log(response),
        )
        return {
            "success": True,
            "name": response.get("name"),
            "expire_time": response.get("expireTime"),
            "model": self.model,
            "response": response,
        }

    def get_cache(self, name):
        """Fetch a CachedContent resource; used to verify it still exists."""
        if not name:
            return {"success": False, "error": "No cache name"}
        http_code, response = self._request("GET", name)
        if isinstance(response, dict) and response.get("error"):
            err = response["error"].get("message", "Cache get error")
            logger.warning(
                "Gemini get_cache failed | name=%s | http=%s | error=%s",
                name,
                http_code,
                err,
            )
            return {
                "success": False,
                "error": err,
                "http_code": http_code,
                "response": response,
            }
        return {
            "success": True,
            "name": response.get("name"),
            "expire_time": response.get("expireTime"),
            "model": response.get("model"),
            "response": response,
        }

    def update_cache_ttl(self, name, ttl_seconds=3600):
        http_code, response = self._request(
            "PATCH", f"{name}?updateMask=ttl", {"ttl": f"{ttl_seconds}s"}
        )
        if isinstance(response, dict) and response.get("error"):
            return {"success": False, "error": response["error"].get("message")}
        return {"success": True, "expire_time": response.get("expireTime"), "response": response}

    def delete_cache(self, name):
        if not name:
            return {"success": True}
        http_code, response = self._request("DELETE", name)
        if isinstance(response, dict) and response.get("error"):
            logger.warning(
                "Gemini delete_cache failed | name=%s | error=%s",
                name,
                response["error"].get("message"),
            )
            return {"success": False, "error": response["error"].get("message")}
        return {"success": True}
