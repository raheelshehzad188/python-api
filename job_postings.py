from flask import Blueprint, request, jsonify
from db import Database

job_postings_bp = Blueprint("job_postings", __name__)

TABLE = "job_posting"


def ensure_schema():
    db = Database()
    try:
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                company_email VARCHAR(255) NOT NULL,
                subject VARCHAR(500) NOT NULL,
                body TEXT NOT NULL,
                status TINYINT(1) NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    finally:
        db.close()


def create_job_posting(db, user_id, company_email, subject, body, status):
    """Insert a job application record. status=True when email was sent."""
    return db.insert(
        TABLE,
        {
            "user_id": user_id,
            "company_email": company_email,
            "subject": subject,
            "body": body,
            "status": 1 if status else 0,
        },
    )


@job_postings_bp.route("/users/<int:user_id>/job-postings", methods=["GET"])
def list_job_postings(user_id):
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 10)), 1), 50)
    offset = (page - 1) * per_page

    db = Database()
    try:
        db.cursor.execute(
            f"SELECT COUNT(*) AS total FROM {TABLE} WHERE user_id=%s",
            [user_id],
        )
        total = db.cursor.fetchone()["total"]

        db.cursor.execute(
            f"""
            SELECT id, user_id, company_email, subject, status, created_at
            FROM {TABLE}
            WHERE user_id=%s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            [user_id, per_page, offset],
        )
        rows = db.cursor.fetchall()
    finally:
        db.close()

    return jsonify({
        "status": True,
        "job_postings": rows,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": max((total + per_page - 1) // per_page, 1),
        },
    })


@job_postings_bp.route("/job-postings/<int:posting_id>", methods=["GET"])
def get_job_posting(posting_id):
    db = Database()
    try:
        row = db.row(TABLE, {"id": posting_id})
    finally:
        db.close()

    if not row:
        return jsonify({"status": False, "message": "Job posting not found"}), 404

    return jsonify({"status": True, "job_posting": row})


@job_postings_bp.route("/users/<int:user_id>/job-postings", methods=["DELETE"])
def clear_job_postings(user_id):
    """Delete all job application records for one user."""
    db = Database()
    try:
        db.cursor.execute(f"SELECT COUNT(*) AS total FROM {TABLE} WHERE user_id=%s", [user_id])
        total = db.cursor.fetchone()["total"]
        db.execute(f"DELETE FROM {TABLE} WHERE user_id=%s", [user_id])
    finally:
        db.close()

    return jsonify({
        "status": True,
        "message": f"Cleared {total} job posting(s)",
        "deleted": total,
    })
