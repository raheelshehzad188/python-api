from flask import Flask, request, jsonify
from flask_cors import CORS
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from db import Database
from admin import Admin, ensure_schema as ensure_admins_schema
from roles import roles_bp, ensure_schema as ensure_roles_schema
from user_meta import user_meta_bp, ensure_schema as ensure_user_meta_schema
from users import users_bp
from chatbot_types import chatbot_types_bp, ensure_schema as ensure_chatbot_types_schema
from sub_categories import sub_categories_bp, ensure_schema as ensure_sub_categories_schema
from instructions import instructions_bp, ensure_schema as ensure_instructions_schema
from site_settings import site_settings_bp, ensure_schema as ensure_site_settings_schema
from gemini_cache import gemini_cache_bp
from chats import chats_bp, ensure_schema as ensure_chats_schema
from email_settings import email_settings_bp
from cv_settings import cv_settings_bp
from job_postings import job_postings_bp, ensure_schema as ensure_job_postings_schema
from webhook_logs import webhook_logs_bp, ensure_schema as ensure_webhook_logs_schema, create_webhook_log
from wa_messages import wa_messages_bp, ensure_schema as ensure_wa_messages_schema
from whatsapp import whatsapp_bp
from services_settings import services_settings_bp, ensure_schema as ensure_services_settings_schema
from services_crud import services_crud_bp
from bookings import bookings_bp, ensure_schema as ensure_bookings_schema
from restaurant_settings import restaurant_settings_bp, ensure_schema as ensure_restaurant_schema
from restaurant_crud import restaurant_crud_bp
from restaurant_dashboard import restaurant_dashboard_bp

app = Flask(__name__)
CORS(app)

app.register_blueprint(roles_bp)
app.register_blueprint(user_meta_bp)
app.register_blueprint(users_bp)
app.register_blueprint(chatbot_types_bp)
app.register_blueprint(sub_categories_bp)
app.register_blueprint(instructions_bp)
app.register_blueprint(site_settings_bp)
app.register_blueprint(gemini_cache_bp)
app.register_blueprint(chats_bp)
app.register_blueprint(email_settings_bp)
app.register_blueprint(cv_settings_bp)
app.register_blueprint(job_postings_bp)
app.register_blueprint(webhook_logs_bp)
app.register_blueprint(wa_messages_bp)
app.register_blueprint(whatsapp_bp)
app.register_blueprint(services_settings_bp)
app.register_blueprint(services_crud_bp)
app.register_blueprint(bookings_bp)
app.register_blueprint(restaurant_settings_bp)
app.register_blueprint(restaurant_crud_bp)
app.register_blueprint(restaurant_dashboard_bp)

ensure_roles_schema()
ensure_user_meta_schema()
ensure_admins_schema()
ensure_chatbot_types_schema()
ensure_sub_categories_schema()
ensure_instructions_schema()
ensure_site_settings_schema()
ensure_chats_schema()
ensure_job_postings_schema()
ensure_webhook_logs_schema()
ensure_wa_messages_schema()
ensure_services_settings_schema()
ensure_bookings_schema()
ensure_restaurant_schema()

@app.route("/health", methods=["GET"])
def health():
    db_error = ""
    try:
        db = Database()
        db.row("roles", {"id": 1})
        db.close()
        db_ok = True
    except Exception as e:
        db_ok = False
        db_error = str(e)

    if not db_ok:
        return jsonify({"status": False, "api": True, "database": False, "message": db_error}), 503

    return jsonify({"status": True, "api": True, "database": True})

@app.route("/login", methods=["POST"])
def login():
    data = request.json or {}
    email = data.get("email")
    password = data.get("password")

    admin = Admin()
    try:
        user = admin.login(
            email,
            password
        )
    finally:
        admin.db.close()

    if user:

        return jsonify({
            "status": True,
            "user": user
        })

    return jsonify({
        "status": False,
        "message": "Invalid Credentials"
    })