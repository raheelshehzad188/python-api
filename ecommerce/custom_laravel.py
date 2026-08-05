import json
import urllib.request
import urllib.error


class Custom_laravel:
    """Ecommerce integration for a custom Laravel store.

    When Gemini's response has type == "sql", the query is passed to run_sql(),
    which POSTs the SQL to the store's "<domain>/sql" route and dumps the result.
    """

    name = "Custom_laravel"
    label = "Custom Laravel"

    def __init__(self, db=None, endpoint=""):
        self.db = db
        # The user sets only the store domain (e.g. "clickup.com.pk").
        self.endpoint = endpoint

    @staticmethod
    def _build_curl(url, headers, body):
        """Return the equivalent `curl` command for this request as a string."""
        parts = [f"curl --location '{url}'"]
        for key, value in headers.items():
            parts.append(f"--header '{key}: {value}'")
        parts.append(f"--data '{body}'")
        return " \\\n".join(parts)

    def _sql_url(self):
        """Build the full "<domain>/sql" URL from the saved domain."""
        domain = (self.endpoint or "").strip().rstrip("/")
        if not domain:
            return ""
        if not domain.startswith("http://") and not domain.startswith("https://"):
            domain = "https://" + domain
        return domain + "/sql"

    def run_sql(self, sql):
        url = self._sql_url()
        if not url:
            return {"success": False, "error": "No store domain configured"}
        if not sql:
            return {"success": False, "error": "No SQL query provided"}

        body_str = json.dumps({"sql": sql})
        payload = body_str.encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Some servers / WAFs close the connection for requests with no
            # User-Agent, which shows up as RemoteDisconnected.
            "User-Agent": "Mozilla/5.0 (compatible; ReactBot/1.0)",
        }

        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers=headers,
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                try:
                    data = json.loads(body) if body else {}
                except ValueError:
                    data = body  # store returned plain text
                result = {"success": True, "url": url, "sql": sql, "http_code": resp.getcode(), "data": data}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            result = {"success": False, "url": url, "sql": sql, "http_code": e.code, "error": err_body or "HTTP error"}
        except urllib.error.URLError as e:
            result = {"success": False, "url": url, "sql": sql, "error": str(e.reason)}
        except Exception as e:
            # Covers things like RemoteDisconnected (server closed the
            # connection without sending any response) so the request never
            # crashes the whole app.
            result = {"success": False, "url": url, "sql": sql, "error": f"{type(e).__name__}: {e}"}

        return result
