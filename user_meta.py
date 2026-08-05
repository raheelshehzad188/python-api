from flask import Blueprint, request, jsonify
from db import Database

user_meta_bp = Blueprint("user_meta", __name__)


def ensure_schema():
    """Create the user_meta table (if missing).

    Stores arbitrary extra data for a user as key/value pairs.
    A user can have only one value per meta_key (UNIQUE constraint),
    so saving the same key again updates the existing value.
    """
    db = Database()

    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_meta (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                meta_key VARCHAR(191) NOT NULL,
                meta_value TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_user_key (user_id, meta_key)
            )
            """
        )
    finally:
        db.close()


def _upsert_meta(db, user_id, meta_key, meta_value):
    """Insert a meta row, or update it if the key already exists for the user."""
    existing = db.row("user_meta", {"user_id": user_id, "meta_key": meta_key})

    if existing:
        db.update(
            "user_meta",
            {"meta_value": meta_value},
            {"user_id": user_id, "meta_key": meta_key},
        )
    else:
        db.insert(
            "user_meta",
            {"user_id": user_id, "meta_key": meta_key, "meta_value": meta_value},
        )


@user_meta_bp.route("/users/<int:user_id>/meta", methods=["GET"])
def get_user_meta(user_id):

    db = Database()

    try:
        rows = db.select("user_meta", {"user_id": user_id})
    finally:
        db.close()

    # Also return a convenient { key: value } map
    meta = {row["meta_key"]: row["meta_value"] for row in rows}

    return jsonify({
        "status": True,
        "user_id": user_id,
        "meta": meta,
        "rows": rows
    })


@user_meta_bp.route("/users/<int:user_id>/meta", methods=["POST"])
def set_user_meta(user_id):

    data = request.json or {}

    # Supports two shapes:
    #   { "meta_key": "phone", "meta_value": "123" }
    #   { "meta": { "phone": "123", "city": "Lahore" } }
    bulk = data.get("meta")

    if bulk is None:
        meta_key = (data.get("meta_key") or "").strip()

        if not meta_key:
            return jsonify({
                "status": False,
                "message": "meta_key is required"
            }), 400

        bulk = {meta_key: data.get("meta_value")}

    if not isinstance(bulk, dict) or not bulk:
        return jsonify({
            "status": False,
            "message": "No meta data provided"
        }), 400

    db = Database()

    try:
        for key, value in bulk.items():
            _upsert_meta(db, user_id, str(key), value)

        rows = db.select("user_meta", {"user_id": user_id})
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "User meta saved successfully",
        "meta": {row["meta_key"]: row["meta_value"] for row in rows}
    })


@user_meta_bp.route("/users/<int:user_id>/meta/<meta_key>", methods=["DELETE"])
def delete_user_meta(user_id, meta_key):

    db = Database()

    try:
        existing = db.row("user_meta", {"user_id": user_id, "meta_key": meta_key})

        if not existing:
            return jsonify({
                "status": False,
                "message": "Meta key not found for this user"
            }), 404

        db.delete("user_meta", {"user_id": user_id, "meta_key": meta_key})
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "User meta deleted successfully"
    })
