import json
import re
import smtplib
import ssl
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import Blueprint, request, jsonify
from db import Database
from user_meta import _upsert_meta

email_settings_bp = Blueprint("email_settings", __name__)

SMTP_SETTINGS_KEY = "smtp_settings"
SMTP_VALIDATED_KEY = "smtp_validated"
JOB_POSTING_HANDLER = "Job_posting"


def _user_meta_map(db, user_id):
    return {m["meta_key"]: m["meta_value"] for m in db.select("user_meta", {"user_id": user_id})}


def _load_smtp_settings(meta):
    raw = meta.get(SMTP_SETTINGS_KEY)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def load_smtp_settings(meta):
    """Public helper for other modules (e.g. Job_posting handler)."""
    return _load_smtp_settings(meta)


def _is_job_posting_user(db, user_id):
    meta = _user_meta_map(db, user_id)
    type_id = meta.get("chatbot_type_id")
    ctype = db.row("chatbot_types", {"id": type_id}) if type_id else None
    handler = (ctype or {}).get("handler_class")
    return handler == JOB_POSTING_HANDLER, handler, meta


def _public_settings(settings):
    """Return settings for the API without exposing the password."""
    safe = {
        "host": settings.get("host", ""),
        "port": settings.get("port", 587),
        "encryption": settings.get("encryption", "tls"),
        "username": settings.get("username", ""),
        "from_email": settings.get("from_email", ""),
        "from_name": settings.get("from_name", ""),
        "has_password": bool(settings.get("password")),
    }
    return safe


def _validate_email(email):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""))


def _attach_file(msg, attachment):
    part = MIMEBase("application", "octet-stream")
    part.set_payload(attachment["data"])
    encoders.encode_base64(part)
    filename = attachment.get("filename") or "attachment"
    part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    msg.attach(part)


def _send_smtp_email(settings, to_email, subject, body, attachment=None):
    host = (settings.get("host") or "").strip()
    port = int(settings.get("port") or 587)
    encryption = (settings.get("encryption") or "tls").strip().lower()
    username = (settings.get("username") or "").strip()
    password = settings.get("password") or ""
    from_email = (settings.get("from_email") or username or "").strip()
    from_name = (settings.get("from_name") or "React Bot").strip()

    if not host:
        return {"success": False, "message": "SMTP host is required"}
    if not from_email:
        return {"success": False, "message": "From email is required"}
    if not _validate_email(to_email):
        return {"success": False, "message": "Invalid recipient email address"}

    msg = MIMEMultipart()
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    if attachment and attachment.get("data"):
        _attach_file(msg, attachment)

    server = None
    try:
        if encryption == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.ehlo()
            if encryption == "tls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()

        if username:
            server.login(username, password)

        server.sendmail(from_email, [to_email], msg.as_string())
        return {"success": True, "message": f"Email sent to {to_email}"}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "message": "SMTP authentication failed. Check username and password."}
    except smtplib.SMTPConnectError:
        return {"success": False, "message": "Could not connect to SMTP server. Check host and port."}
    except Exception as e:
        return {"success": False, "message": f"SMTP error: {e}"}
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass


def send_smtp_email(settings, to_email, subject, body, attachment=None):
    """Public helper for other modules (e.g. Job_posting handler)."""
    return _send_smtp_email(settings, to_email, subject, body, attachment=attachment)


@email_settings_bp.route("/users/<int:user_id>/email-settings", methods=["GET"])
def get_email_settings(user_id):
    db = Database()
    try:
        is_job_posting, handler_class, meta = _is_job_posting_user(db, user_id)
        settings = _load_smtp_settings(meta)
        validated = meta.get(SMTP_VALIDATED_KEY) == "1"
    finally:
        db.close()

    return jsonify({
        "status": True,
        "is_job_posting": is_job_posting,
        "handler_class": handler_class,
        "settings": _public_settings(settings),
        "validated": validated,
    })


@email_settings_bp.route("/users/<int:user_id>/email-settings", methods=["POST"])
def save_email_settings(user_id):
    data = request.json or {}

    db = Database()
    try:
        is_job_posting, _, meta = _is_job_posting_user(db, user_id)
        if not is_job_posting:
            return jsonify({
                "status": False,
                "message": "Email settings are only available for Job Posting chatbot types",
            }), 403

        existing = _load_smtp_settings(meta)
        password = data.get("password")
        if password is None or password == "":
            password = existing.get("password", "")

        settings = {
            "host": (data.get("host") or "").strip(),
            "port": int(data.get("port") or 587),
            "encryption": (data.get("encryption") or "tls").strip().lower(),
            "username": (data.get("username") or "").strip(),
            "password": password,
            "from_email": (data.get("from_email") or "").strip(),
            "from_name": (data.get("from_name") or "").strip(),
        }

        if not settings["host"]:
            return jsonify({"status": False, "message": "SMTP host is required"}), 400
        if not settings["from_email"]:
            return jsonify({"status": False, "message": "From email is required"}), 400

        _upsert_meta(db, user_id, SMTP_SETTINGS_KEY, json.dumps(settings))
        _upsert_meta(db, user_id, SMTP_VALIDATED_KEY, "0")
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "SMTP settings saved",
        "settings": _public_settings(settings),
        "validated": False,
    })


@email_settings_bp.route("/users/<int:user_id>/email-settings/test", methods=["POST"])
def test_email_settings(user_id):
    data = request.json or {}
    to_email = (data.get("email") or "").strip()

    if not to_email:
        return jsonify({"status": False, "message": "Email address is required"}), 400
    if not _validate_email(to_email):
        return jsonify({"status": False, "message": "Invalid email address"}), 400

    db = Database()
    try:
        is_job_posting, _, meta = _is_job_posting_user(db, user_id)
        if not is_job_posting:
            return jsonify({
                "status": False,
                "message": "Email settings are only available for Job Posting chatbot types",
            }), 403

        settings = _load_smtp_settings(meta)
        if not settings.get("host"):
            return jsonify({"status": False, "message": "Save SMTP settings before sending a test email"}), 400

        result = _send_smtp_email(
            settings,
            to_email,
            "SMTP Test — React Bot",
            "This is a test email from your bot SMTP settings.\n\n"
            "If you received this message, your SMTP configuration is working correctly.",
        )

        if result.get("success"):
            _upsert_meta(db, user_id, SMTP_VALIDATED_KEY, "1")
        else:
            _upsert_meta(db, user_id, SMTP_VALIDATED_KEY, "0")
    finally:
        db.close()

    if not result.get("success"):
        return jsonify({"status": False, "message": result.get("message", "Failed to send test email")}), 400

    return jsonify({
        "status": True,
        "message": result.get("message"),
        "validated": True,
    })
