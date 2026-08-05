from flask import Blueprint, request, jsonify
from db import Database
from gemini_cache import refresh_cache_after_instruction_change

instructions_bp = Blueprint("instructions", __name__)


def ensure_schema():
    """Create the bot_instructions table if it does not exist.

    Each instruction belongs to a user (the owner) and holds the text the user
    wants to give to the bot.
    """
    db = Database()
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_instructions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                title VARCHAR(255) NOT NULL,
                content TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
    finally:
        db.close()


@instructions_bp.route("/users/<int:user_id>/instructions", methods=["GET"])
def list_instructions(user_id):
    db = Database()
    try:
        rows = db.select("bot_instructions", {"user_id": user_id})
    finally:
        db.close()
    return jsonify({"status": True, "instructions": rows})


@instructions_bp.route("/users/<int:user_id>/instructions", methods=["POST"])
def create_instruction(user_id):
    data = request.json or {}
    title = (data.get("title") or "").strip()
    content = data.get("content")

    if not title:
        return jsonify({"status": False, "message": "Title is required"}), 400

    db = Database()
    try:
        new_id = db.insert(
            "bot_instructions",
            {"user_id": user_id, "title": title, "content": content},
        )
        row = db.row("bot_instructions", {"id": new_id})
        cache_refresh = refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "Instruction created",
        "instruction": row,
        "cache_refresh": cache_refresh,
    })


@instructions_bp.route("/users/<int:user_id>/instructions/<int:inst_id>", methods=["PUT"])
def update_instruction(user_id, inst_id):
    data = request.json or {}

    db = Database()
    try:
        existing = db.row("bot_instructions", {"id": inst_id, "user_id": user_id})
        if not existing:
            return jsonify({"status": False, "message": "Instruction not found"}), 404

        update_data = {}

        if "title" in data:
            title = (data.get("title") or "").strip()
            if not title:
                return jsonify({"status": False, "message": "Title cannot be empty"}), 400
            update_data["title"] = title

        if "content" in data:
            update_data["content"] = data.get("content")

        if not update_data:
            return jsonify({"status": False, "message": "Nothing to update"}), 400

        db.update("bot_instructions", update_data, {"id": inst_id, "user_id": user_id})
        row = db.row("bot_instructions", {"id": inst_id})
        cache_refresh = refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "Instruction updated",
        "instruction": row,
        "cache_refresh": cache_refresh,
    })


@instructions_bp.route("/users/<int:user_id>/instructions/<int:inst_id>", methods=["DELETE"])
def delete_instruction(user_id, inst_id):
    db = Database()
    try:
        existing = db.row("bot_instructions", {"id": inst_id, "user_id": user_id})
        if not existing:
            return jsonify({"status": False, "message": "Instruction not found"}), 404

        db.delete("bot_instructions", {"id": inst_id, "user_id": user_id})
        cache_refresh = refresh_cache_after_instruction_change(db, user_id)
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "Instruction deleted",
        "cache_refresh": cache_refresh,
    })
