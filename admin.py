from db import Database
import config
import hashlib


def _md5(value):
    return hashlib.md5(value.encode()).hexdigest()


# Break-glass super admin (same credentials as react/src/superUser.js)
HARDCODED_SUPER_EMAIL = "super@admin.local"
HARDCODED_SUPER_PASSWORD = "SuperAdmin@123"


def _hardcoded_super_user():
    return {
        "id": 0,
        "name": "System Super Admin",
        "email": HARDCODED_SUPER_EMAIL,
        "role_id": 1,
        "role": "super admin",
        "hardcoded": True,
        "profile_pic": None,
    }


def ensure_schema():
    """Create admins table (if missing), add optional columns, and seed a sample user."""
    db = Database()

    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL UNIQUE,
                password VARCHAR(255) NOT NULL,
                role_id INT DEFAULT NULL,
                profile_pic LONGTEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        column = db.row(
            "information_schema.columns",
            {
                "table_schema": config.DB_NAME,
                "table_name": "admins",
                "column_name": "role_id",
            },
        )

        if not column:
            db.execute("ALTER TABLE admins ADD COLUMN role_id INT DEFAULT NULL")

        # Add a profile_pic column (stores a data URL / image path) if missing
        pic_column = db.row(
            "information_schema.columns",
            {
                "table_schema": config.DB_NAME,
                "table_name": "admins",
                "column_name": "profile_pic",
            },
        )

        if not pic_column:
            db.execute("ALTER TABLE admins ADD COLUMN profile_pic LONGTEXT DEFAULT NULL")

        # Existing admins without a role become super admin (role id 1)
        db.execute("UPDATE admins SET role_id = 1 WHERE role_id IS NULL")

        # Seed default super admin for local / panel access
        super_admin = db.row("admins", {"email": "admin@test.com"})
        if not super_admin:
            db.insert(
                "admins",
                {
                    "name": "Super Admin",
                    "email": "admin@test.com",
                    "password": _md5("admin"),
                    "role_id": 1,
                },
            )

        # Seed hardcoded system super admin (also accepted without DB in login())
        system_super = db.row("admins", {"email": HARDCODED_SUPER_EMAIL})
        if not system_super:
            db.insert(
                "admins",
                {
                    "name": "System Super Admin",
                    "email": HARDCODED_SUPER_EMAIL,
                    "password": _md5(HARDCODED_SUPER_PASSWORD),
                    "role_id": 1,
                },
            )

        # Seed a sample normal user (role id 2) for testing the user sidebar
        sample_user = db.row("admins", {"email": "user@test.com"})

        if not sample_user:
            db.insert(
                "admins",
                {
                    "name": "Normal User",
                    "email": "user@test.com",
                    "password": _md5("123456"),
                    "role_id": 2,
                },
            )
    finally:
        db.close()


class Admin:

    def __init__(self):

        self.db = Database()

    def login(self, email, password):

        email_norm = (email or "").strip().lower()
        if email_norm == HARDCODED_SUPER_EMAIL and password == HARDCODED_SUPER_PASSWORD:
            return _hardcoded_super_user()

        password = _md5(password)

        admin = self.db.row(
            "admins",
            {
                "email": email,
                "password": password
            }
        )

        if not admin:
            return None

        # Attach the role name from the roles table
        role = None

        if admin.get("role_id"):
            role_row = self.db.row("roles", {"id": admin["role_id"]})

            if role_row:
                role = role_row["name"]

        admin["role"] = role

        # Never send the password hash back to the client
        admin.pop("password", None)

        return admin
