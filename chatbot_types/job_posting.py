from cv_settings import load_cv_attachment
from email_settings import SMTP_VALIDATED_KEY, load_smtp_settings, send_smtp_email
from job_postings import create_job_posting


def _truthy_job_flag(value):
    """Accept true / \"true\" / 1 from Gemini JSON."""
    if value is True or value == 1:
        return True
    if isinstance(value, str) and value.strip().lower() in ("true", "1", "yes"):
        return True
    return False


class Job_posting:
    """Handler for job-posting chatbot types."""

    name = "Job_posting"
    label = "Job Posting"

    def __init__(self, db=None, user_id=None, meta=None):
        self.db = db
        self.user_id = user_id
        self.meta = meta or {}

    def cache_payload(self):
        # Intentionally empty: keeps existing system-instruction behavior unchanged.
        return ""

    def process(self, reply_json):
        reply_json = reply_json or {}
        rtype = reply_json.get("type")

        if rtype == "message" and "is_job" not in reply_json:
            return {"type": "message", "message": reply_json.get("message", "")}

        if _truthy_job_flag(reply_json.get("is_job")):
            apply_result = self.apply_via_email(reply_json)

            if apply_result.get("applied"):
                return {
                    "type": "message",
                    "message": apply_result.get("message", "Applied successfully."),
                    "job_data": reply_json,
                }

            if apply_result.get("smtp_error"):
                return {
                    "type": "message",
                    "message": apply_result.get("message", "SMTP error"),
                    "job_data": reply_json,
                }

            # Job detected but email not sent (SMTP missing / not validated / no CV)
            msg = self._format_job_reply(reply_json)
            hint = apply_result.get("message")
            if hint:
                msg = f"{msg}\n\n{hint}"
            return {"type": "job", "data": reply_json, "message": msg}

        if reply_json.get("is_job") is False or (
            isinstance(reply_json.get("is_job"), str)
            and reply_json.get("is_job").strip().lower() in ("false", "0", "no")
        ):
            msg = reply_json.get("message") or "This message does not look like a job posting."
            return {"type": "message", "message": msg}

        if rtype == "message":
            return {"type": "message", "message": reply_json.get("message", "")}

        return {"type": rtype, "data": reply_json}

    def apply_via_email(self, reply_json):
        """Send job application email with CV when is_job is true and SMTP is validated."""
        reply_json = reply_json or {}

        if not _truthy_job_flag(reply_json.get("is_job")):
            return {"applied": False, "message": "Not a job posting"}

        to_email = (reply_json.get("apply_email") or "").strip()
        subject = (reply_json.get("subject") or "").strip()
        body = (reply_json.get("body") or "").strip()

        if not to_email:
            return {"applied": False, "message": "No apply email address found in Gemini response."}
        if not subject or not body:
            return {
                "applied": False,
                "message": "Subject and body are required in Gemini response to apply via email.",
            }

        settings = load_smtp_settings(self.meta)
        if self.meta.get(SMTP_VALIDATED_KEY) != "1" or not settings.get("host"):
            self._save_record(to_email, subject, body, False)
            if self.meta.get(SMTP_VALIDATED_KEY) != "1":
                return {
                    "applied": False,
                    "message": "SMTP settings are not validated. Please configure and send a test email in Email Setup.",
                }
            return {"applied": False, "message": "SMTP settings are not configured."}

        attachment = load_cv_attachment(self.meta)
        if not attachment:
            self._save_record(to_email, subject, body, False)
            return {
                "applied": False,
                "message": "CV is not uploaded. Upload your CV in Settings before applying to jobs.",
            }

        result = send_smtp_email(settings, to_email, subject, body, attachment=attachment)
        sent = bool(result.get("success"))
        self._save_record(to_email, subject, body, sent)

        if sent:
            return {
                "applied": True,
                "message": f"Applied successfully. Email sent to {to_email} with your CV attached.",
            }

        return {
            "applied": False,
            "smtp_error": True,
            "message": result.get("message", "SMTP error"),
        }

    def _save_record(self, company_email, subject, body, status):
        if self.db and self.user_id:
            try:
                create_job_posting(self.db, self.user_id, company_email, subject, body, status)
            except Exception:
                # Never fail the apply flow because logging failed
                pass

    @staticmethod
    def _format_job_reply(data):
        title = data.get("job_title") or "Position"
        company = data.get("company_name") or ""
        email = data.get("apply_email") or ""
        whatsapp = data.get("apply_whatsapp") or ""
        link = data.get("apply_link") or ""

        lines = [f"Job posting detected: {title}"]
        if company:
            lines.append(f"Company: {company}")
        if email:
            lines.append(f"Apply email: {email}")
        if whatsapp:
            lines.append(f"Apply WhatsApp: {whatsapp}")
        if link:
            lines.append(f"Apply link: {link}")
        if data.get("subject"):
            lines.append(f"Suggested subject: {data['subject']}")
        return "\n".join(lines)
