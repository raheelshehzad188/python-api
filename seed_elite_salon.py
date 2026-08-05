"""Populate Elite Salon & Spa demo data for the Services chatbot user (salon@test.com).

Service-module only — does not touch Ecommerce / Job Posting.
"""

from __future__ import annotations

from datetime import date, timedelta

from db import Database
from services_schema import ensure_services_schema
from services_settings import _ensure_user_defaults
from gemini_cache import update_user_cache

SALON_USER_EMAIL = "salon@test.com"
SALON_USER_ID_FALLBACK = 9

SERVICES = [
    ("Hair Cut", 60, 250, "Professional men's and women's haircut."),
    ("Shaving", 30, 200, "Clean traditional shave."),
    ("Beard Styling", 30, 300, "Beard trim and shape."),
    ("Hair Wash", 20, 150, "Shampoo and conditioning wash."),
    ("Hair Spa", 60, 1500, "Deep conditioning hair spa treatment."),
    ("Facial", 60, 1200, "Refreshing facial for glowing skin."),
    ("Hair Coloring", 120, 3500, "Full hair coloring service."),
    ("Keratin Treatment", 180, 8000, "Smooth keratin hair treatment."),
    ("Head Massage", 30, 500, "Relaxing head and scalp massage."),
    ("Kids Hair Cut", 45, 200, "Gentle haircut for children."),
]

PAYMENT_METHODS = [
    ("Cash", "Pay in cash at the salon"),
    ("Card", "Debit / credit card accepted"),
    ("JazzCash", "Mobile wallet payment"),
    ("EasyPaisa", "Mobile wallet payment"),
]

FAQS = [
    ("Do you offer parking?", "Yes — free customer parking is available."),
    ("Can I cancel my appointment?", "Yes. Appointments may be cancelled up to 2 hours before booking."),
    ("When should I arrive?", "Please arrive 10 minutes before your appointment."),
    ("What payment methods do you accept?", "Cash, Card, JazzCash, and EasyPaisa."),
    ("Are you open on Sunday?", "No — we are closed on Sundays."),
    ("Do you have memberships or packages?", "Ask about current memberships, packages, and promotions — we will share what is configured."),
]


def _resolve_user_id(db):
    user = db.row("admins", {"email": SALON_USER_EMAIL})
    if user:
        return int(user["id"])
    return SALON_USER_ID_FALLBACK


def _clear_user_rows(db, table, user_id):
    try:
        db.execute(f"DELETE FROM {table} WHERE user_id=%s", [user_id])
    except Exception:
        pass


def seed_elite_salon(refresh_cache=True):
    ensure_services_schema()
    db = Database()
    try:
        user_id = _resolve_user_id(db)
        _ensure_user_defaults(db, user_id)

        # Business profile
        profile = {
            "currency_code": "PKR",
            "business_name": "Elite Salon & Spa",
            "business_category": "Salon",
            "phone": "+92 300 1234567",
            "email": "info@elitesalon.pk",
            "city": "Lahore",
            "address": "123 MM Alam Road, Gulberg, Lahore",
            "about": (
                "Elite Salon & Spa provides premium grooming, hair styling, "
                "beard styling, facials and beauty services."
            ),
            "parking_info": "Free customer parking available.",
            "cancellation_policy": "Appointments may be cancelled up to 2 hours before booking.",
            "booking_rules": "Please arrive 10 minutes before your appointment.",
            "payment_methods": "Cash, Card, JazzCash, EasyPaisa",
            "website": "https://elitesalon.pk",
            "maps_link": "https://maps.google.com/?q=123+MM+Alam+Road+Gulberg+Lahore",
        }
        existing = db.row("services_settings", {"user_id": user_id})
        if existing:
            db.update("services_settings", profile, {"user_id": user_id})
        else:
            profile["user_id"] = user_id
            db.insert("services_settings", profile)

        # Working hours: Mon–Sat 09:00–18:00, Sunday closed (replace any duplicates)
        _clear_user_rows(db, "services_working_hours", user_id)
        for day in range(7):
            closed = 1 if day == 6 else 0
            db.insert(
                "services_working_hours",
                {
                    "user_id": user_id,
                    "day_of_week": day,
                    "open_time": "09:00:00",
                    "close_time": "18:00:00",
                    "break_start": None,
                    "break_end": None,
                    "is_closed": closed,
                },
            )

        # Replace catalog / related demo rows for a clean salon dataset
        for table in (
            "services_catalog",
            "services_holidays",
            "services_payment_methods",
            "services_policies",
            "services_faqs",
            "services_categories",
            "services_packages",
            "services_promotions",
            "services_memberships",
            "services_staff",
        ):
            _clear_user_rows(db, table, user_id)

        cat_id = db.insert(
            "services_categories",
            {
                "user_id": user_id,
                "name": "Grooming & Beauty",
                "description": "Hair, beard, facial and spa services",
                "sort_order": 1,
                "is_active": 1,
            },
        )

        service_ids = {}
        for name, duration, price, desc in SERVICES:
            sid = db.insert(
                "services_catalog",
                {
                    "user_id": user_id,
                    "name": name,
                    "duration_minutes": duration,
                    "price": price,
                    "description": desc,
                    "ai_context": desc,
                    "category_id": cat_id,
                    "status": "active",
                },
            )
            service_ids[name] = sid

        # Related suggestions
        if service_ids.get("Hair Cut") and service_ids.get("Shaving"):
            db.update(
                "services_catalog",
                {"related_service_ids": str(service_ids["Shaving"])},
                {"id": service_ids["Hair Cut"]},
            )
        if service_ids.get("Facial") and service_ids.get("Head Massage"):
            db.update(
                "services_catalog",
                {"related_service_ids": str(service_ids["Head Massage"])},
                {"id": service_ids["Facial"]},
            )

        # Demo holidays (relative to today + fixed national days)
        today = date.today()
        training = today + timedelta(days=3)
        while training.weekday() == 6:
            training += timedelta(days=1)
        holidays = [
            (training, "Staff Training Day", "Salon closed for staff training."),
            (date(today.year, 8, 14), "Independence Day", "National holiday — closed."),
            (date(today.year, 12, 25), "Christmas", "Closed for Christmas."),
            (date(today.year, 5, 1), "Labour Day", "Public holiday — closed."),
        ]
        for d, title, desc in holidays:
            db.insert(
                "services_holidays",
                {
                    "user_id": user_id,
                    "holiday_date": d.isoformat(),
                    "reason": title,
                    "title": title,
                    "description": desc,
                },
            )

        for i, (name, details) in enumerate(PAYMENT_METHODS):
            db.insert(
                "services_payment_methods",
                {
                    "user_id": user_id,
                    "name": name,
                    "details": details,
                    "is_active": 1,
                    "sort_order": i + 1,
                },
            )

        db.insert(
            "services_policies",
            {
                "user_id": user_id,
                "title": "Cancellation Policy",
                "policy_type": "cancellation",
                "content": "Appointments may be cancelled up to 2 hours before booking.",
                "is_active": 1,
            },
        )
        db.insert(
            "services_policies",
            {
                "user_id": user_id,
                "title": "Arrival Policy",
                "policy_type": "booking",
                "content": "Please arrive 10 minutes before your appointment. Late arrivals may have shortened service time.",
                "is_active": 1,
            },
        )

        for q, a in FAQS:
            db.insert("services_faqs", {"user_id": user_id, "question": q, "answer": a})

        db.insert(
            "services_packages",
            {
                "user_id": user_id,
                "name": "Grooming Combo",
                "price": 400,
                "includes": "Hair Cut + Shaving",
                "ai_context": "Save with Hair Cut and Shaving together.",
                "is_active": 1,
            },
        )
        db.insert(
            "services_promotions",
            {
                "user_id": user_id,
                "title": "Weekday Facial Special",
                "description": "10% off Facial Monday to Thursday.",
                "discount": "10%",
                "is_active": 1,
            },
        )
        db.insert(
            "services_memberships",
            {
                "user_id": user_id,
                "name": "Elite Club",
                "price": 5000,
                "benefits": "Priority booking + 1 free Hair Wash monthly",
                "is_active": 1,
            },
        )
        db.insert(
            "services_staff",
            {
                "user_id": user_id,
                "name": "Ali Khan",
                "role": "Senior Barber",
                "skills": "Hair Cut, Shaving, Beard Styling",
                "is_active": 1,
                "status": "active",
                "assigned_service_ids": ",".join(
                    str(service_ids[n])
                    for n in ("Hair Cut", "Shaving", "Beard Styling")
                    if n in service_ids
                ),
            },
        )

        # One existing demo customer for "existing customer" scenarios
        existing_cust = None
        for c in db.select("services_customers", {"user_id": user_id}) or []:
            if (c.get("phone") or "") == "923001112233":
                existing_cust = c
                break
        if not existing_cust:
            db.insert(
                "services_customers",
                {
                    "user_id": user_id,
                    "name": "Ahmed Raza",
                    "phone": "923001112233",
                    "email": "ahmed@example.com",
                    "notes": "Returning customer",
                },
            )

        cache_result = None
        if refresh_cache:
            cache_result = update_user_cache(db, user_id)

        return {
            "success": True,
            "user_id": user_id,
            "services": service_ids,
            "cache": cache_result,
        }
    finally:
        db.close()


if __name__ == "__main__":
    result = seed_elite_salon(refresh_cache=True)
    print(result)
