import json

from flask import Blueprint, request, jsonify
from db import Database

webhook_logs_bp = Blueprint("webhook_logs", __name__)

TABLE = "wa_webhook_logs"


def ensure_schema():
    db = Database()
    try:
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                sender VARCHAR(255) DEFAULT NULL,
                message_text TEXT,
                payload LONGTEXT,
                forwarded TINYINT(1) NOT NULL DEFAULT 0,
                notify_phone VARCHAR(50) DEFAULT NULL,
                panel_response TEXT,
                log_status VARCHAR(50) NOT NULL DEFAULT 'received',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_user_created (user_id, created_at)
            )
            """
        )
    finally:
        db.close()


def create_webhook_log(
    db,
    user_id,
    sender,
    message_text,
    payload,
    forwarded=False,
    notify_phone=None,
    panel_response=None,
    log_status="received",
):
    return db.insert(
        TABLE,
        {
            "user_id": user_id,
            "sender": sender,
            "message_text": message_text,
            "payload": json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            "forwarded": 1 if forwarded else 0,
            "notify_phone": notify_phone,
            "panel_response": json.dumps(panel_response, ensure_ascii=False) if panel_response is not None else None,
            "log_status": log_status,
        },
    )


def _serialize_log(row, users_map=None):
    item = {
        "id": row["id"],
        "user_id": row["user_id"],
        "sender": row.get("sender") or "",
        "message_text": row.get("message_text") or "",
        "forwarded": bool(row.get("forwarded")),
        "notify_phone": row.get("notify_phone") or "",
        "log_status": row.get("log_status") or "received",
        "created_at": row.get("created_at"),
    }
    if users_map is not None:
        user = users_map.get(row["user_id"]) or {}
        item["user_name"] = user.get("name") or ""
        item["user_email"] = user.get("email") or ""
    return item


def _paginate_logs(db, where_sql="", params=None, page=1, per_page=20, include_user=False):
    params = list(params or [])
    page = max(page, 1)
    per_page = min(max(per_page, 1), 50)
    offset = (page - 1) * per_page

    count_sql = f"SELECT COUNT(*) AS total FROM {TABLE}"
    list_sql = f"SELECT * FROM {TABLE}"
    if where_sql:
        count_sql += f" WHERE {where_sql}"
        list_sql += f" WHERE {where_sql}"
    list_sql += " ORDER BY created_at DESC LIMIT %s OFFSET %s"

    db.cursor.execute(count_sql, params)
    total = db.cursor.fetchone()["total"]

    db.cursor.execute(list_sql, params + [per_page, offset])
    rows = db.cursor.fetchall()

    users_map = {}
    if include_user and rows:
        user_ids = list({r["user_id"] for r in rows})
        placeholders = ", ".join(["%s"] * len(user_ids))
        db.cursor.execute(
            f"SELECT id, name, email FROM admins WHERE id IN ({placeholders})",
            user_ids,
        )
        users_map = {u["id"]: u for u in db.cursor.fetchall()}

    logs = [_serialize_log(r, users_map if include_user else None) for r in rows]
    return logs, total, page, per_page


@webhook_logs_bp.route("/users/<int:user_id>/whatsapp/webhook-logs", methods=["GET"])
def list_user_webhook_logs(user_id):
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 20)), 1), 50)

    db = Database()
    try:
        logs, total, page, per_page = _paginate_logs(
            db,
            "user_id=%s",
            [user_id],
            page=page,
            per_page=per_page,
        )
    finally:
        db.close()

    return jsonify({
        "status": True,
        "logs": logs,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": max((total + per_page - 1) // per_page, 1),
        },
    })


@webhook_logs_bp.route("/whatsapp/webhook-logs", methods=["GET"])
def list_all_webhook_logs():
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 20)), 1), 50)
    user_id = request.args.get("user_id")

    where_sql = ""
    params = []
    if user_id:
        where_sql = "user_id=%s"
        params = [int(user_id)]

    db = Database()
    try:
        logs, total, page, per_page = _paginate_logs(
            db,
            where_sql,
            params,
            page=page,
            per_page=per_page,
            include_user=True,
        )
    finally:
        db.close()

    return jsonify({
        "status": True,
        "logs": logs,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": max((total + per_page - 1) // per_page, 1),
        },
    })


@webhook_logs_bp.route("/whatsapp/webhook-logs/<int:log_id>", methods=["GET"])
def get_webhook_log(log_id):
    db = Database()
    try:
        row = db.row(TABLE, {"id": log_id})
        if not row:
            return jsonify({"status": False, "message": "Log not found"}), 404

        user = db.row("admins", {"id": row["user_id"]})
        log = _serialize_log(row, {row["user_id"]: user} if user else None)
        try:
            log["payload"] = json.loads(row.get("payload") or "{}")
        except (TypeError, ValueError):
            log["payload"] = row.get("payload")
        try:
            log["panel_response"] = json.loads(row.get("panel_response") or "{}")
        except (TypeError, ValueError):
            log["panel_response"] = row.get("panel_response")
    finally:
        db.close()

    return jsonify({"status": True, "log": log})


@webhook_logs_bp.route("/users/<int:user_id>/whatsapp/webhook-logs/<int:log_id>", methods=["GET"])
def get_user_webhook_log(user_id, log_id):
    db = Database()
    try:
        row = db.row(TABLE, {"id": log_id, "user_id": user_id})
        if not row:
            return jsonify({"status": False, "message": "Log not found"}), 404

        log = _serialize_log(row)
        try:
            log["payload"] = json.loads(row.get("payload") or "{}")
        except (TypeError, ValueError):
            log["payload"] = row.get("payload")
        try:
            log["panel_response"] = json.loads(row.get("panel_response") or "{}")
        except (TypeError, ValueError):
            log["panel_response"] = row.get("panel_response")
    finally:
        db.close()

    return jsonify({"status": True, "log": log})


@webhook_logs_bp.route("/users/<int:user_id>/whatsapp/webhook-logs/<int:log_id>", methods=["DELETE"])
def delete_user_webhook_log(user_id, log_id):
    db = Database()
    try:
        deleted = db.delete(TABLE, {"id": log_id, "user_id": user_id})
    finally:
        db.close()

    if not deleted:
        return jsonify({"status": False, "message": "Log not found"}), 404
    return jsonify({"status": True, "message": "Webhook log deleted"})


@webhook_logs_bp.route("/users/<int:user_id>/whatsapp/webhook-logs", methods=["DELETE"])
def clear_user_webhook_logs(user_id):
    db = Database()
    try:
        db.cursor.execute(f"DELETE FROM {TABLE} WHERE user_id=%s", [user_id])
        deleted = db.cursor.rowcount
        db.conn.commit()
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": f"Deleted {deleted} webhook log(s)",
        "deleted": deleted,
    })


@webhook_logs_bp.route("/whatsapp/webhook-logs/<int:log_id>", methods=["DELETE"])
def delete_webhook_log(log_id):
    db = Database()
    try:
        deleted = db.delete(TABLE, {"id": log_id})
    finally:
        db.close()

    if not deleted:
        return jsonify({"status": False, "message": "Log not found"}), 404
    return jsonify({"status": True, "message": "Webhook log deleted"})


@webhook_logs_bp.route("/whatsapp/webhook-logs", methods=["DELETE"])
def clear_all_webhook_logs():
    user_id = request.args.get("user_id")
    db = Database()
    try:
        if user_id:
            db.cursor.execute(f"DELETE FROM {TABLE} WHERE user_id=%s", [int(user_id)])
        else:
            db.cursor.execute(f"DELETE FROM {TABLE}")
        deleted = db.cursor.rowcount
        db.conn.commit()
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": f"Deleted {deleted} webhook log(s)",
        "deleted": deleted,
    })
