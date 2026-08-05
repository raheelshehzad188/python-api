import base64
import json
import os
import re
import uuid

from flask import Blueprint, request, jsonify
from db import Database
from user_meta import _upsert_meta
from email_settings import _is_job_posting_user

cv_settings_bp = Blueprint("cv_settings", __name__)

CV_META_KEY = "user_cv"
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads", "cv")
MAX_CV_BYTES = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx"}
ALLOWED_MIME = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _ensure_upload_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def _safe_filename(name):
    name = os.path.basename(name or "cv.pdf")
    name = re.sub(r"[^\w.\- ]", "_", name).strip() or "cv.pdf"
    if not os.path.splitext(name)[1]:
        name += ".pdf"
    return name


def _parse_data_url(data_url):
    """Decode a data URL into (mime_type, bytes)."""
    if not data_url or not isinstance(data_url, str):
        return None, None
    match = re.match(r"^data:([^;]+);base64,(.+)$", data_url.strip(), re.DOTALL)
    if not match:
        return None, None
    mime_type = match.group(1).strip().lower()
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except Exception:
        return None, None
    return mime_type, raw


def _load_cv_meta(meta):
    raw = (meta or {}).get(CV_META_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _cv_file_path(stored_name):
    return os.path.join(UPLOAD_DIR, stored_name)


def _public_cv_info(cv_meta):
    if not cv_meta:
        return {"has_cv": False, "filename": "", "mime_type": ""}
    return {
        "has_cv": True,
        "filename": cv_meta.get("filename") or "",
        "mime_type": cv_meta.get("mime_type") or "",
    }


def load_cv_attachment(meta):
    """Return attachment dict for SMTP, or None if no CV on disk."""
    cv_meta = _load_cv_meta(meta)
    if not cv_meta:
        return None

    stored_name = cv_meta.get("stored_name")
    if not stored_name:
        return None

    path = _cv_file_path(stored_name)
    if not os.path.isfile(path):
        return None

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None

    if not data:
        return None

    return {
        "filename": _safe_filename(cv_meta.get("filename") or stored_name),
        "mime_type": cv_meta.get("mime_type") or "application/octet-stream",
        "data": data,
    }


def _delete_cv_file(cv_meta):
    if not cv_meta:
        return
    stored_name = cv_meta.get("stored_name")
    if not stored_name:
        return
    path = _cv_file_path(stored_name)
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


@cv_settings_bp.route("/users/<int:user_id>/cv-settings", methods=["GET"])
def get_cv_settings(user_id):
    db = Database()
    try:
        is_job_posting, handler_class, meta = _is_job_posting_user(db, user_id)
        cv_meta = _load_cv_meta(meta)
    finally:
        db.close()

    return jsonify({
        "status": True,
        "is_job_posting": is_job_posting,
        "handler_class": handler_class,
        **_public_cv_info(cv_meta),
    })


@cv_settings_bp.route("/users/<int:user_id>/cv-settings", methods=["POST"])
def save_cv_settings(user_id):
    data = request.json or {}
    data_url = data.get("cv_data") or data.get("data_url") or ""
    filename = _safe_filename(data.get("filename") or "cv.pdf")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({
            "status": False,
            "message": "Only PDF, DOC, and DOCX files are allowed",
        }), 400

    mime_type, raw = _parse_data_url(data_url)
    if not raw:
        return jsonify({"status": False, "message": "Invalid CV file data"}), 400
    if len(raw) > MAX_CV_BYTES:
        return jsonify({"status": False, "message": "CV must be smaller than 5MB"}), 400
    if mime_type and mime_type not in ALLOWED_MIME:
        return jsonify({"status": False, "message": "Unsupported CV file type"}), 400

    db = Database()
    try:
        is_job_posting, _, meta = _is_job_posting_user(db, user_id)
        if not is_job_posting:
            return jsonify({
                "status": False,
                "message": "CV upload is only available for Job Posting chatbot types",
            }), 403

        _ensure_upload_dir()
        old_meta = _load_cv_meta(meta)
        _delete_cv_file(old_meta)

        stored_name = f"{user_id}_{uuid.uuid4().hex}{ext}"
        path = _cv_file_path(stored_name)
        with open(path, "wb") as f:
            f.write(raw)

        cv_meta = {
            "filename": filename,
            "mime_type": mime_type or "application/octet-stream",
            "stored_name": stored_name,
        }
        _upsert_meta(db, user_id, CV_META_KEY, json.dumps(cv_meta))
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "CV saved successfully",
        **_public_cv_info(cv_meta),
    })


@cv_settings_bp.route("/users/<int:user_id>/cv-settings", methods=["DELETE"])
def delete_cv_settings(user_id):
    db = Database()
    try:
        is_job_posting, _, meta = _is_job_posting_user(db, user_id)
        if not is_job_posting:
            return jsonify({
                "status": False,
                "message": "CV upload is only available for Job Posting chatbot types",
            }), 403

        cv_meta = _load_cv_meta(meta)
        if not cv_meta:
            return jsonify({"status": False, "message": "No CV found"}), 404

        _delete_cv_file(cv_meta)
        db.delete("user_meta", {"user_id": user_id, "meta_key": CV_META_KEY})
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "CV removed",
        "has_cv": False,
        "filename": "",
        "mime_type": "",
    })
