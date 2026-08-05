from flask import Blueprint, request, jsonify
from db import Database
from user_clone import clone_user
import hashlib

users_bp = Blueprint("users", __name__)


def _md5(value):
    return hashlib.md5(value.encode()).hexdigest()


def _serialize(user, roles_map):
    """Strip password and attach the role name."""
    user.pop("password", None)
    user["role"] = roles_map.get(user.get("role_id"))
    return user


@users_bp.route("/users", methods=["GET"])
def list_users():

    db = Database()

    try:
        users = db.select("admins")
        roles_map = {r["id"]: r["name"] for r in db.select("roles")}
    finally:
        db.close()

    # Omit large profile images from list — keeps /users fast for the UI.
    users = [_serialize(u, roles_map) for u in users]
    for user in users:
        user.pop("profile_pic", None)

    return jsonify({
        "status": True,
        "users": users
    })


@users_bp.route("/users", methods=["POST"])
def create_user():

    data = request.json or {}

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    role_id = data.get("role_id")

    if not name or not email or not password:
        return jsonify({
            "status": False,
            "message": "Name, email and password are required"
        }), 400

    db = Database()

    try:

        if db.row("admins", {"email": email}):
            return jsonify({
                "status": False,
                "message": "A user with this email already exists"
            }), 409

        user_id = db.insert(
            "admins",
            {
                "name": name,
                "email": email,
                "password": _md5(password),
                "role_id": role_id,
                "profile_pic": data.get("profile_pic"),
            },
        )

        roles_map = {r["id"]: r["name"] for r in db.select("roles")}
        user = _serialize(db.row("admins", {"id": user_id}), roles_map)

    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "User created successfully",
        "user": user
    })


@users_bp.route("/users/<int:user_id>", methods=["PUT"])
def update_user(user_id):

    data = request.json or {}

    db = Database()

    try:

        user = db.row("admins", {"id": user_id})

        if not user:
            return jsonify({
                "status": False,
                "message": "User not found"
            }), 404

        update_data = {}

        if "name" in data:
            name = (data.get("name") or "").strip()
            if not name:
                return jsonify({"status": False, "message": "Name cannot be empty"}), 400
            update_data["name"] = name

        if "email" in data:
            email = (data.get("email") or "").strip()
            if not email:
                return jsonify({"status": False, "message": "Email cannot be empty"}), 400

            duplicate = db.row("admins", {"email": email})
            if duplicate and duplicate["id"] != user_id:
                return jsonify({
                    "status": False,
                    "message": "Another user with this email already exists"
                }), 409

            update_data["email"] = email

        if "role_id" in data:
            update_data["role_id"] = data.get("role_id")

        if "profile_pic" in data:
            update_data["profile_pic"] = data.get("profile_pic")

        if data.get("password"):
            update_data["password"] = _md5(data.get("password"))

        if not update_data:
            return jsonify({"status": False, "message": "Nothing to update"}), 400

        db.update("admins", update_data, {"id": user_id})

        roles_map = {r["id"]: r["name"] for r in db.select("roles")}
        user = _serialize(db.row("admins", {"id": user_id}), roles_map)

    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "User updated successfully",
        "user": user
    })


@users_bp.route("/users/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):

    db = Database()

    try:

        user = db.row("admins", {"id": user_id})

        if not user:
            return jsonify({
                "status": False,
                "message": "User not found"
            }), 404

        db.delete("admins", {"id": user_id})

    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": "User deleted successfully"
    })


@users_bp.route("/users/<int:user_id>/clone", methods=["POST"])
def clone_existing_user(user_id):
    data = request.json or {}
    result, error = clone_user(
        user_id,
        data.get("name"),
        data.get("email"),
        data.get("password"),
    )
    if error:
        status_code = 404 if error == "Source user not found" else 409 if "already exists" in error else 400
        return jsonify({"status": False, "message": error}), status_code

    return jsonify({
        "status": True,
        "message": "User and business data cloned successfully",
        **result,
    })
