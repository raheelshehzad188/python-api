from flask import Blueprint, request, jsonify
from db import Database
from ecommerce import available_classes
import config

sub_categories_bp = Blueprint("sub_categories", __name__)


def _ensure_column(db, table, column, definition):
    exists = db.row(
        "information_schema.columns",
        {"table_schema": config.DB_NAME, "table_name": table, "column_name": column},
    )
    if not exists:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_schema():
    """Create the sub_categories table (and add the instructions column) if missing.

    Each sub category belongs to a main type (a chatbot_types row) and has a title
    plus its own instructions text.
    """
    db = Database()
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS sub_categories (
                id INT AUTO_INCREMENT PRIMARY KEY,
                main_type_id INT NOT NULL,
                title VARCHAR(255) NOT NULL,
                instructions TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Migration: add instructions to pre-existing tables
        _ensure_column(db, "sub_categories", "instructions", "TEXT DEFAULT NULL")
        # Ecommerce flag + integration class
        _ensure_column(db, "sub_categories", "is_ecommerce", "TINYINT(1) NOT NULL DEFAULT 0")
        _ensure_column(db, "sub_categories", "ecommerce_class", "VARCHAR(255) DEFAULT NULL")
    finally:
        db.close()


def _serialize(row, types_map):
    row["main_type"] = types_map.get(row.get("main_type_id"))
    return row


@sub_categories_bp.route("/ecommerce-classes", methods=["GET"])
def ecommerce_classes():
    return jsonify({"status": True, "classes": available_classes()})


@sub_categories_bp.route("/sub-categories", methods=["GET"])
def list_sub_categories():
    db = Database()
    try:
        rows = db.select("sub_categories")
        types_map = {t["id"]: t["title"] for t in db.select("chatbot_types")}
    finally:
        db.close()

    rows = [_serialize(r, types_map) for r in rows]
    return jsonify({"status": True, "sub_categories": rows})


@sub_categories_bp.route("/sub-categories", methods=["POST"])
def create_sub_category():
    data = request.json or {}
    title = (data.get("title") or "").strip()
    main_type_id = data.get("main_type_id")
    instructions = data.get("instructions")
    is_ecommerce = 1 if data.get("is_ecommerce") else 0
    ecommerce_class = (data.get("ecommerce_class") or "").strip() or None
    if not is_ecommerce:
        ecommerce_class = None

    if not title:
        return jsonify({"status": False, "message": "Title is required"}), 400
    if not main_type_id:
        return jsonify({"status": False, "message": "Main type is required"}), 400

    db = Database()
    try:
        if not db.row("chatbot_types", {"id": main_type_id}):
            return jsonify({"status": False, "message": "Selected main type does not exist"}), 400

        new_id = db.insert(
            "sub_categories",
            {
                "main_type_id": main_type_id,
                "title": title,
                "instructions": instructions,
                "is_ecommerce": is_ecommerce,
                "ecommerce_class": ecommerce_class,
            },
        )
        types_map = {t["id"]: t["title"] for t in db.select("chatbot_types")}
        row = _serialize(db.row("sub_categories", {"id": new_id}), types_map)
    finally:
        db.close()

    return jsonify({"status": True, "message": "Sub category created", "sub_category": row})


@sub_categories_bp.route("/sub-categories/<int:sub_id>", methods=["PUT"])
def update_sub_category(sub_id):
    data = request.json or {}

    db = Database()
    try:
        if not db.row("sub_categories", {"id": sub_id}):
            return jsonify({"status": False, "message": "Sub category not found"}), 404

        update_data = {}

        if "title" in data:
            title = (data.get("title") or "").strip()
            if not title:
                return jsonify({"status": False, "message": "Title cannot be empty"}), 400
            update_data["title"] = title

        if "main_type_id" in data:
            main_type_id = data.get("main_type_id")
            if not main_type_id or not db.row("chatbot_types", {"id": main_type_id}):
                return jsonify({"status": False, "message": "Selected main type does not exist"}), 400
            update_data["main_type_id"] = main_type_id

        if "instructions" in data:
            update_data["instructions"] = data.get("instructions")

        if "is_ecommerce" in data:
            update_data["is_ecommerce"] = 1 if data.get("is_ecommerce") else 0

        if "ecommerce_class" in data:
            update_data["ecommerce_class"] = (data.get("ecommerce_class") or "").strip() or None

        # If ecommerce is turned off, clear the class
        if update_data.get("is_ecommerce") == 0:
            update_data["ecommerce_class"] = None

        if not update_data:
            return jsonify({"status": False, "message": "Nothing to update"}), 400

        db.update("sub_categories", update_data, {"id": sub_id})
        types_map = {t["id"]: t["title"] for t in db.select("chatbot_types")}
        row = _serialize(db.row("sub_categories", {"id": sub_id}), types_map)
    finally:
        db.close()

    return jsonify({"status": True, "message": "Sub category updated", "sub_category": row})


@sub_categories_bp.route("/sub-categories/<int:sub_id>", methods=["DELETE"])
def delete_sub_category(sub_id):
    db = Database()
    try:
        if not db.row("sub_categories", {"id": sub_id}):
            return jsonify({"status": False, "message": "Sub category not found"}), 404

        db.delete("sub_categories", {"id": sub_id})
    finally:
        db.close()

    return jsonify({"status": True, "message": "Sub category deleted"})
