from flask import Blueprint, request, jsonify
from db import Database
import config
from gemini_cache import refresh_caches_for_chatbot_type
from . import available_classes

chatbot_types_bp = Blueprint("chatbot_types", __name__)


def _ensure_column(db, table, column, definition):
    exists = db.row(
        "information_schema.columns",
        {"table_schema": config.DB_NAME, "table_name": table, "column_name": column},
    )
    if not exists:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_schema():
    """Create the chatbot_types table (and add the instructions column) if missing."""
    db = Database()
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS chatbot_types (
                id INT AUTO_INCREMENT PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                instructions TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _ensure_column(db, "chatbot_types", "instructions", "TEXT DEFAULT NULL")
        _ensure_column(db, "chatbot_types", "handler_class", "VARCHAR(255) DEFAULT NULL")

        from .general_seed import ensure_seed as ensure_general_seed
        from .services_seed import ensure_seed as ensure_services_seed
        from .restaurant_seed import ensure_seed as ensure_restaurant_seed
        ensure_general_seed(db)
        ensure_services_seed(db)
        ensure_restaurant_seed(db)
    finally:
        db.close()


@chatbot_types_bp.route("/chatbot-type-handlers", methods=["GET"])
def list_handlers():
    return jsonify({"status": True, "handlers": available_classes()})


@chatbot_types_bp.route("/chatbot-types", methods=["GET"])
def list_chatbot_types():
    db = Database()
    try:
        rows = db.select("chatbot_types")
    finally:
        db.close()
    return jsonify({"status": True, "chatbot_types": rows})


@chatbot_types_bp.route("/chatbot-types", methods=["POST"])
def create_chatbot_type():
    data = request.json or {}
    title = (data.get("title") or "").strip()
    instructions = data.get("instructions")
    handler_class = (data.get("handler_class") or "").strip() or None

    if not title:
        return jsonify({"status": False, "message": "Title is required"}), 400

    db = Database()
    try:
        if db.row("chatbot_types", {"title": title}):
            return jsonify({"status": False, "message": "This chatbot type already exists"}), 409

        new_id = db.insert(
            "chatbot_types",
            {"title": title, "instructions": instructions, "handler_class": handler_class},
        )
        row = db.row("chatbot_types", {"id": new_id})
    finally:
        db.close()

    return jsonify({"status": True, "message": "Chatbot type created", "chatbot_type": row})


@chatbot_types_bp.route("/chatbot-types/<int:type_id>", methods=["PUT"])
def update_chatbot_type(type_id):
    data = request.json or {}
    title = (data.get("title") or "").strip()

    if not title:
        return jsonify({"status": False, "message": "Title is required"}), 400

    db = Database()
    try:
        if not db.row("chatbot_types", {"id": type_id}):
            return jsonify({"status": False, "message": "Chatbot type not found"}), 404

        duplicate = db.row("chatbot_types", {"title": title})
        if duplicate and duplicate["id"] != type_id:
            return jsonify({"status": False, "message": "Another chatbot type with this title exists"}), 409

        update_data = {"title": title}
        if "instructions" in data:
            update_data["instructions"] = data.get("instructions")
        if "handler_class" in data:
            update_data["handler_class"] = (data.get("handler_class") or "").strip() or None

        db.update("chatbot_types", update_data, {"id": type_id})
        row = db.row("chatbot_types", {"id": type_id})

        cache_results = refresh_caches_for_chatbot_type(db, type_id)
        refreshed_count = sum(1 for r in cache_results if r.get("success"))
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": f"Chatbot type updated. {refreshed_count} user cache(s) refreshed.",
        "chatbot_type": row,
        "cache_refresh": {
            "total_users": len(cache_results),
            "refreshed": refreshed_count,
            "results": cache_results,
        },
    })


@chatbot_types_bp.route("/chatbot-types/<int:type_id>", methods=["DELETE"])
def delete_chatbot_type(type_id):
    db = Database()
    try:
        if not db.row("chatbot_types", {"id": type_id}):
            return jsonify({"status": False, "message": "Chatbot type not found"}), 404

        child = db.row("sub_categories", {"main_type_id": type_id})
        if child:
            return jsonify({
                "status": False,
                "message": "Cannot delete: sub categories are using this type",
            }), 409

        db.delete("chatbot_types", {"id": type_id})
    finally:
        db.close()

    return jsonify({"status": True, "message": "Chatbot type deleted"})
