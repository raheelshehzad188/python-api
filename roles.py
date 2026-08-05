from flask import Blueprint, request, jsonify
from db import Database

roles_bp = Blueprint("roles", __name__)

# These two roles are seeded by default and can NEVER be deleted.
PROTECTED_ROLES = ["super admin", "user"]


def ensure_schema():
    """Create the roles table (if missing) and seed the protected roles."""
    db = Database()

    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS roles (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL UNIQUE,
                description VARCHAR(255) DEFAULT NULL,
                is_protected TINYINT(1) NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        for role_name in PROTECTED_ROLES:

            existing = db.row("roles", {"name": role_name})

            if not existing:
                db.insert(
                    "roles",
                    {
                        "name": role_name,
                        "description": f"Default {role_name} role",
                        "is_protected": 1,
                    },
                )
    finally:
        db.close()


@roles_bp.route("/roles", methods=["GET"])
def list_roles():

    db = Database()

    try:
        roles = db.select("roles")
    finally:
        db.close()

    return jsonify({
        "status": True,
        "roles": roles
    })


@roles_bp.route("/roles", methods=["POST"])
def create_role():

    data = request.json or {}

    name = (data.get("name") or "").strip()
    description = data.get("description")

    if not name:
        return jsonify({
            "status": False,
            "message": "Role name is required"
        }), 400

    db = Database()

    try:

        if db.row("roles", {"name": name}):
            return jsonify({
                "status": False,
                "message": "Role already exists"
            }), 409

        role_id = db.insert(
            "roles",
            {
                "name": name,
                "description": description,
                "is_protected": 0,
            },
        )

        role = db.row("roles", {"id": role_id})

    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "Role created successfully",
        "role": role
    })


@roles_bp.route("/roles/<int:role_id>", methods=["PUT"])
def update_role(role_id):

    data = request.json or {}

    db = Database()

    try:

        role = db.row("roles", {"id": role_id})

        if not role:
            return jsonify({
                "status": False,
                "message": "Role not found"
            }), 404

        update_data = {}

        if "name" in data:

            new_name = (data.get("name") or "").strip()

            if not new_name:
                return jsonify({
                    "status": False,
                    "message": "Role name cannot be empty"
                }), 400

            # Don't allow renaming to a name that already belongs to another role
            duplicate = db.row("roles", {"name": new_name})

            if duplicate and duplicate["id"] != role_id:
                return jsonify({
                    "status": False,
                    "message": "Another role with this name already exists"
                }), 409

            update_data["name"] = new_name

        if "description" in data:
            update_data["description"] = data.get("description")

        if not update_data:
            return jsonify({
                "status": False,
                "message": "Nothing to update"
            }), 400

        db.update("roles", update_data, {"id": role_id})

        role = db.row("roles", {"id": role_id})

    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "Role updated successfully",
        "role": role
    })


@roles_bp.route("/roles/<int:role_id>", methods=["DELETE"])
def delete_role(role_id):

    db = Database()

    try:

        role = db.row("roles", {"id": role_id})

        if not role:
            return jsonify({
                "status": False,
                "message": "Role not found"
            }), 404

        if role.get("is_protected"):
            return jsonify({
                "status": False,
                "message": "This role is protected and cannot be deleted"
            }), 403

        db.delete("roles", {"id": role_id})

    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "Role deleted successfully"
    })
