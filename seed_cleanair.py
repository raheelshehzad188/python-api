"""Populate the CleanAir (Dubai cleaning company) demo account.

Service-module only. Creates the demo user ``service@test.com`` if it does not
exist, otherwise just refreshes the dummy data. All generated data is
interconnected: customers -> bookings -> services + staff + payments, plus
chats, reviews, vehicles and notifications.

Run directly:  python3 seed_cleanair.py
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import date, datetime, time, timedelta

from db import Database
from services_schema import ensure_services_schema
from services_settings import _ensure_user_defaults
from user_meta import _upsert_meta

try:  # cache refresh is optional (needs Gemini key)
    from gemini_cache import update_user_cache
except Exception:  # pragma: no cover
    update_user_cache = None

try:
    from chatbot_types.services_seed import ensure_seed as ensure_services_type
except Exception:  # pragma: no cover
    ensure_services_type = None

# Deterministic output so re-seeding produces a stable dataset.
RNG = random.Random(20260717)

USER_EMAIL = "service@test.com"
USER_NAME = "Ahmed Hassan"
USER_PASSWORD = "admin"

CURRENCY = "AED"

# --------------------------------------------------------------------------- #
# Static demo data                                                            #
# --------------------------------------------------------------------------- #

# (name, duration_minutes, price AED, description)
SERVICES = [
    ("Home Deep Cleaning", 240, 399, "Top-to-bottom deep clean for your entire home."),
    ("Apartment Cleaning", 120, 149, "Standard cleaning for apartments and flats."),
    ("Villa Cleaning", 300, 499, "Full villa cleaning with dedicated team."),
    ("Office Cleaning", 180, 299, "Professional workspace and office cleaning."),
    ("Move In Cleaning", 240, 399, "Fresh, sanitised home before you move in."),
    ("Move Out Cleaning", 240, 399, "Deposit-ready deep clean when moving out."),
    ("Window Cleaning", 90, 149, "Streak-free interior and exterior windows."),
    ("Sofa Cleaning", 90, 199, "Deep upholstery shampoo for sofas."),
    ("Carpet Cleaning", 90, 199, "Hot-water extraction carpet cleaning."),
    ("Mattress Cleaning", 60, 149, "Anti-dust-mite mattress sanitisation."),
    ("Kitchen Deep Cleaning", 150, 299, "Degrease and sanitise the whole kitchen."),
    ("Bathroom Sanitization", 90, 149, "Descale and disinfect bathrooms."),
    ("AC Duct Cleaning", 180, 349, "Clean ducts for cleaner airflow."),
    ("AC Filter Cleaning", 60, 99, "Wash and refit AC filters."),
    ("AC Coil Cleaning", 120, 249, "Restore cooling with coil cleaning."),
    ("Post Construction Cleaning", 360, 499, "Remove dust and debris after renovation."),
    ("Commercial Cleaning", 240, 399, "Retail, warehouse and commercial spaces."),
    ("Disinfection Service", 120, 249, "Hospital-grade disinfection fogging."),
    ("Water Tank Cleaning", 150, 299, "Drain, scrub and disinfect water tanks."),
    ("Curtain Cleaning", 90, 149, "On-site or pickup curtain cleaning."),
]

PAYMENT_METHODS = [
    ("Cash", "Pay the crew in cash after the job."),
    ("Card", "Debit / credit card on completion."),
    ("Stripe", "Secure online card payment via Stripe."),
    ("Online Payment", "Pay via payment link before the visit."),
    ("Bank Transfer", "Direct bank transfer to CleanAir account."),
]

AREAS = [
    "Business Bay", "Downtown", "Dubai Marina", "JVC", "JLT",
    "Palm Jumeirah", "Deira", "Al Barsha", "Mirdif", "Silicon Oasis",
]

BUILDINGS = [
    "Marina Heights", "Bay Square", "Boulevard Plaza", "Sunrise Tower",
    "Palm Residence", "Oasis Court", "Emerald Villa", "Sapphire Tower",
    "Golden Sands", "Pearl Residency", "Al Noor Building", "Silver Heights",
]

FIRST_NAMES = [
    "Ahmed", "Mohammed", "Ali", "Omar", "Khalid", "Yousef", "Hassan", "Ibrahim",
    "Fatima", "Aisha", "Mariam", "Sara", "Layla", "Noura", "Huda", "Zainab",
    "John", "Michael", "David", "Priya", "Rahul", "Anjali", "Chen", "Maria",
    "James", "Sophia", "Daniel", "Ananya", "Sanjay", "Elena", "Ronaldo", "Grace",
]

LAST_NAMES = [
    "Hassan", "Khan", "Al Maktoum", "Al Nahyan", "Rahman", "Sheikh", "Farooq",
    "Smith", "Johnson", "Patel", "Sharma", "Wong", "Garcia", "Kumar", "Ahmadi",
    "Saleh", "Nasser", "Baloch", "Iqbal", "Mansoor",
]

CUSTOMER_NOTES = [
    "Prefers weekend slots.", "Has a pet dog, please ring bell.",
    "Allergic to strong chemicals, use eco products.", "Parking available in basement B2.",
    "Call before arrival.", "Regular monthly customer.", "Leave keys with security.",
    "VIP client - assign senior crew.", "Building requires visitor pass.",
    "Prefers morning slots only.", "", "", "Wants same crew each time.",
]

# roles -> (count, [skills])
STAFF_PLAN = [
    ("Operations Manager", 2, "Scheduling, Quality Control, Team Management"),
    ("Supervisor", 2, "Team Lead, Quality Inspection, Client Handling"),
    ("Cleaner", 8, "Deep Cleaning, Sanitisation, Upholstery"),
    ("AC Technician", 3, "AC Duct, AC Coil, AC Filter"),
    ("Driver", 3, "Logistics, Equipment Transport"),
    ("Customer Support", 2, "Bookings, Chat Support, Follow-up"),
]

STATUSES = [
    "pending", "confirmed", "assigned", "on_the_way",
    "in_progress", "completed", "cancelled",
]

REVIEW_COMMENTS = {
    5: [
        "Excellent service, spotless results!", "The crew was professional and on time.",
        "Best cleaning company in Dubai, highly recommend.", "My apartment looks brand new.",
        "Very thorough and friendly team.", "Booked again, always reliable.",
    ],
    4: [
        "Good job overall, small spot missed.", "Great service, arrived a bit late.",
        "Happy with the cleaning, will use again.", "Professional team, fair price.",
    ],
    3: [
        "Decent cleaning but rushed at the end.", "Average service, could be more detailed.",
        "Okay, but crew arrived late.", "Cleaning was fine, communication could improve.",
    ],
}

WHATSAPP_SCRIPTS = [
    [("Hi", "Hello! Welcome to CleanAir. How can I help you today?"),
     ("I need AC cleaning.", "Sure! Our AC Duct Cleaning is AED 349 and AC Filter Cleaning is AED 99. Which one would you like?"),
     ("Duct cleaning please.", "Great. What date works for you?"),
     ("Tomorrow morning.", "Booked! Our technician will arrive between 09:00 and 11:00. Thank you!")],
    [("How much for sofa cleaning?", "Sofa Cleaning is AED 199 for a standard 3-seater. Would you like to book?"),
     ("Yes tomorrow.", "Perfect, may I have your name and area?"),
     ("Omar, Dubai Marina.", "All set, Omar! Our team will visit tomorrow. Thank you for choosing CleanAir.")],
    [("Can someone come today?", "Let me check availability. Which service do you need?"),
     ("Apartment cleaning.", "We have a slot at 4 PM today for Apartment Cleaning (AED 149). Shall I confirm?"),
     ("Yes please.", "Confirmed! See you at 4 PM.")],
    [("I want to book tomorrow.", "Of course! Which service would you like to book?"),
     ("Kitchen deep cleaning.", "Kitchen Deep Cleaning is AED 299 and takes about 2.5 hours. What time suits you?"),
     ("10 AM.", "Booked for 10 AM tomorrow. Thank you!")],
    [("Do you clean water tanks?", "Yes! Water Tank Cleaning is AED 299. Would you like to schedule it?"),
     ("Maybe next week.", "No problem, just message us when ready. Thank you!"),
     ("Thank you.", "You're welcome! Have a great day.")],
    [("Villa cleaning price?", "Villa Cleaning starts at AED 499 with a dedicated team. When would you like it?"),
     ("This weekend.", "We have Saturday morning available. Shall I book it?"),
     ("Yes.", "Great, you're booked for Saturday. Thank you!")],
]

FACEBOOK_SCRIPTS = [
    [("Hello, are you open?", "Hi! Yes, we're open Monday to Saturday, 8 AM to 8 PM. How can we help?"),
     ("Need office cleaning.", "Office Cleaning is AED 299. What's the size and preferred date?"),
     ("Small office, Friday.", "Booked for Friday. Thank you for reaching out!")],
    [("Do you do disinfection?", "Yes, our Disinfection Service is AED 249 using hospital-grade products."),
     ("Great, book for Monday.", "Done! Our team will visit Monday. Thank you!")],
    [("Carpet cleaning available?", "Yes! Carpet Cleaning is AED 199. Would you like to schedule?"),
     ("Yes tomorrow afternoon.", "Confirmed for tomorrow afternoon. Thank you!")],
    [("Move out cleaning cost?", "Move Out Cleaning is AED 399 and is deposit-ready. When are you moving?"),
     ("End of month.", "Noted! Message us to lock the date. Thank you!")],
    [("Hi", "Hello! Welcome to CleanAir. How may we assist you today?"),
     ("Window cleaning price?", "Window Cleaning is AED 149. Would you like to book?"),
     ("Thank you.", "You're welcome! We're here whenever you're ready.")],
]

NOTIFICATION_TEMPLATES = [
    ("New Booking", "New booking received for {service} from {customer}."),
    ("Booking Confirmed", "Booking for {service} with {customer} has been confirmed."),
    ("Employee Assigned", "{staff} has been assigned to {customer}'s {service}."),
    ("Customer Message", "New WhatsApp message from {customer}."),
    ("Payment Received", "Payment of AED {price} received from {customer}."),
    ("Reminder", "Upcoming {service} for {customer} tomorrow."),
    ("Booking Cancelled", "{customer} cancelled their {service} booking."),
]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()


def _ensure_column(db, table, column, definition):
    db.cursor.execute(
        "SELECT COUNT(*) AS c FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        [table, column],
    )
    if db.cursor.fetchone()["c"] == 0:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _ensure_extra_schema(db):
    """Extra columns + tables the CleanAir dataset needs."""
    # Settings extras (owner / country / timezone / business_type / whatsapp)
    for col, definition in (
        ("owner_name", "owner_name VARCHAR(255) DEFAULT NULL"),
        ("country", "country VARCHAR(120) DEFAULT NULL"),
        ("timezone", "timezone VARCHAR(80) DEFAULT NULL"),
        ("business_type", "business_type VARCHAR(120) DEFAULT NULL"),
        ("whatsapp", "whatsapp VARCHAR(50) DEFAULT NULL"),
        ("tax_percent", "tax_percent DECIMAL(5,2) NOT NULL DEFAULT 0"),
    ):
        _ensure_column(db, "services_settings", col, definition)

    # Booking payment tracking
    for col, definition in (
        ("payment_status", "payment_status VARCHAR(20) NOT NULL DEFAULT 'pending'"),
        ("payment_method", "payment_method VARCHAR(50) DEFAULT NULL"),
    ):
        _ensure_column(db, "services_bookings", col, definition)

    # Staff performance
    for col, definition in (
        ("photo_url", "photo_url VARCHAR(500) DEFAULT NULL"),
        ("rating", "rating DECIMAL(3,2) NOT NULL DEFAULT 0"),
        ("completed_jobs", "completed_jobs INT NOT NULL DEFAULT 0"),
    ):
        _ensure_column(db, "services_staff", col, definition)

    # Customer address fields
    for col, definition in (
        ("area", "area VARCHAR(120) DEFAULT NULL"),
        ("building", "building VARCHAR(255) DEFAULT NULL"),
        ("apartment", "apartment VARCHAR(50) DEFAULT NULL"),
        ("address", "address TEXT DEFAULT NULL"),
    ):
        _ensure_column(db, "services_customers", col, definition)

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS services_vehicles (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            vehicle_number VARCHAR(50) NOT NULL,
            driver_id INT DEFAULT NULL,
            driver_name VARCHAR(255) DEFAULT NULL,
            current_location VARCHAR(255) DEFAULT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'available',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS services_reviews (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            customer_id INT DEFAULT NULL,
            booking_id INT DEFAULT NULL,
            service_id INT DEFAULT NULL,
            customer_name VARCHAR(255) DEFAULT NULL,
            rating TINYINT NOT NULL DEFAULT 5,
            comment TEXT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS services_notifications (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            title VARCHAR(255) NOT NULL,
            message TEXT DEFAULT NULL,
            type VARCHAR(50) DEFAULT NULL,
            is_read TINYINT(1) NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _resolve_type_id(db):
    if ensure_services_type:
        ensure_services_type(db)
    row = db.row("chatbot_types", {"title": "Services"})
    return row["id"] if row else None


def _resolve_user_id(db):
    user = db.row("admins", {"email": USER_EMAIL})
    if user:
        return int(user["id"]), False
    new_id = db.insert(
        "admins",
        {
            "name": USER_NAME,
            "email": USER_EMAIL,
            "password": _md5(USER_PASSWORD),
            "role_id": 2,
        },
    )
    return int(new_id), True


def _clear(db, table, user_id):
    try:
        db.execute(f"DELETE FROM {table} WHERE user_id=%s", [user_id])
    except Exception:
        pass


def _phone():
    return "+9715" + str(RNG.choice([0, 2, 4, 5, 6])) + " " + \
        f"{RNG.randint(100, 999)} {RNG.randint(1000, 9999)}"


def _add_minutes(start: time, minutes: int) -> time:
    dt = datetime.combine(date.today(), start) + timedelta(minutes=minutes)
    return dt.time()


# --------------------------------------------------------------------------- #
# Main seed                                                                   #
# --------------------------------------------------------------------------- #


def seed_cleanair(refresh_cache=True):
    ensure_services_schema()
    db = Database()
    try:
        _ensure_extra_schema(db)
        type_id = _resolve_type_id(db)
        user_id, created = _resolve_user_id(db)

        # Make sure password/name are correct even if user already existed
        db.update(
            "admins",
            {"name": USER_NAME, "password": _md5(USER_PASSWORD), "role_id": 2},
            {"id": user_id},
        )
        if type_id:
            _upsert_meta(db, user_id, "chatbot_type_id", str(type_id))

        _ensure_user_defaults(db, user_id)

        # ---- Business profile -------------------------------------------- #
        profile = {
            "currency_code": CURRENCY,
            "business_name": "CleanAir",
            "business_category": "Cleaning Services",
            "business_type": "Service",
            "owner_name": USER_NAME,
            "phone": "+971 50 123 4567",
            "whatsapp": "+971 50 123 4567",
            "email": USER_EMAIL,
            "country": "United Arab Emirates",
            "city": "Dubai",
            "address": "Business Bay, Dubai, UAE",
            "timezone": "Asia/Dubai",
            "website": "https://cleanair.ae",
            "maps_link": "https://maps.google.com/?q=Business+Bay+Dubai",
            "logo_url": "https://ui-avatars.com/api/?name=Clean+Air&background=0ea5e9&color=fff&size=256",
            "about": (
                "CleanAir is a professional cleaning company based in Business Bay, Dubai. "
                "We provide home, villa, office, AC, and specialised deep-cleaning services "
                "across Dubai with trained crews and eco-friendly products."
            ),
            "parking_info": "Visitor parking available at most buildings; crews carry their own equipment.",
            "cancellation_policy": "Free cancellation up to 4 hours before the scheduled slot.",
            "booking_rules": "Working hours Monday to Saturday, 08:00 AM - 08:00 PM. Closed Sunday.",
            "payment_methods": "Cash, Card, Stripe, Online Payment, Bank Transfer",
            "tax_percent": 5.0,
            "primary_color": "#0ea5e9",
            "secondary_color": "#2563eb",
            "accent_color": "#10b981",
        }
        if db.row("services_settings", {"user_id": user_id}):
            db.update("services_settings", profile, {"user_id": user_id})
        else:
            profile["user_id"] = user_id
            db.insert("services_settings", profile)

        # ---- Working hours: Mon-Sat 08:00-20:00, Sun closed -------------- #
        _clear(db, "services_working_hours", user_id)
        for day in range(7):
            closed = 1 if day == 6 else 0  # 6 = Sunday
            db.insert(
                "services_working_hours",
                {
                    "user_id": user_id,
                    "day_of_week": day,
                    "open_time": "08:00:00",
                    "close_time": "20:00:00",
                    "break_start": None,
                    "break_end": None,
                    "is_closed": closed,
                },
            )

        # ---- Clear all catalog / related data ---------------------------- #
        for table in (
            "services_catalog", "services_categories", "services_holidays",
            "services_payment_methods", "services_policies", "services_faqs",
            "services_packages", "services_promotions", "services_memberships",
            "services_staff", "services_customers", "services_bookings",
            "services_vehicles", "services_reviews", "services_notifications",
        ):
            _clear(db, table, user_id)

        # ---- Categories --------------------------------------------------- #
        cat_home = db.insert("services_categories", {
            "user_id": user_id, "name": "Home & Apartment", "sort_order": 1, "is_active": 1})
        cat_ac = db.insert("services_categories", {
            "user_id": user_id, "name": "AC Services", "sort_order": 2, "is_active": 1})
        cat_special = db.insert("services_categories", {
            "user_id": user_id, "name": "Specialised Cleaning", "sort_order": 3, "is_active": 1})

        def _cat_for(name):
            if name.startswith("AC "):
                return cat_ac
            if name in ("Home Deep Cleaning", "Apartment Cleaning", "Villa Cleaning",
                        "Move In Cleaning", "Move Out Cleaning", "Kitchen Deep Cleaning",
                        "Bathroom Sanitization"):
                return cat_home
            return cat_special

        # ---- Services catalog -------------------------------------------- #
        service_ids = {}
        for name, duration, price, desc in SERVICES:
            sid = db.insert("services_catalog", {
                "user_id": user_id, "name": name, "duration_minutes": duration,
                "price": price, "description": desc, "ai_context": desc,
                "category_id": _cat_for(name), "status": "active",
            })
            service_ids[name] = sid
        service_list = list(service_ids.items())  # [(name, id)]

        # ---- Payment methods --------------------------------------------- #
        for i, (name, details) in enumerate(PAYMENT_METHODS):
            db.insert("services_payment_methods", {
                "user_id": user_id, "name": name, "details": details,
                "is_active": 1, "sort_order": i + 1})

        # ---- Staff -------------------------------------------------------- #
        staff = []  # list of dicts {id, name, role}
        idx = 0
        for role, count, skills in STAFF_PLAN:
            for _ in range(count):
                idx += 1
                sname = f"{RNG.choice(FIRST_NAMES)} {RNG.choice(LAST_NAMES)}"
                wh = json.dumps({"days": "Mon-Sat", "start": "08:00", "end": "20:00"})
                assigned = ""
                if role == "Cleaner":
                    assigned = ",".join(str(service_ids[n]) for n in (
                        "Home Deep Cleaning", "Apartment Cleaning", "Villa Cleaning") )
                elif role == "AC Technician":
                    assigned = ",".join(str(service_ids[n]) for n in (
                        "AC Duct Cleaning", "AC Filter Cleaning", "AC Coil Cleaning"))
                sid = db.insert("services_staff", {
                    "user_id": user_id, "name": sname, "role": role, "skills": skills,
                    "phone": _phone(), "email": f"{sname.split()[0].lower()}{idx}@cleanair.ae",
                    "working_hours": wh, "assigned_service_ids": assigned,
                    "is_active": 1, "status": "active",
                    "photo_url": f"https://ui-avatars.com/api/?name={sname.replace(' ', '+')}&background=random",
                    "rating": round(RNG.uniform(3.8, 5.0), 2),
                    "completed_jobs": RNG.randint(20, 400),
                    "ai_context": f"{role} — {skills}",
                })
                staff.append({"id": sid, "name": sname, "role": role})

        cleaners = [s for s in staff if s["role"] in ("Cleaner", "Supervisor")]
        ac_techs = [s for s in staff if s["role"] == "AC Technician"]
        drivers = [s for s in staff if s["role"] == "Driver"]
        field_staff = cleaners + ac_techs

        # ---- Customers ---------------------------------------------------- #
        customers = []  # {id, name, phone}
        used_emails = set()
        for i in range(100):
            name = f"{RNG.choice(FIRST_NAMES)} {RNG.choice(LAST_NAMES)}"
            base = name.lower().replace(" ", ".")
            email = f"{base}{i}@example.com"
            while email in used_emails:
                email = f"{base}{RNG.randint(1, 9999)}@example.com"
            used_emails.add(email)
            area = RNG.choice(AREAS)
            building = RNG.choice(BUILDINGS)
            apartment = f"{RNG.randint(1, 40)}{RNG.choice('ABCD')}-{RNG.randint(1, 25):02d}"
            phone = _phone()
            address = f"Apt {apartment}, {building}, {area}, Dubai, UAE"
            note = RNG.choice(CUSTOMER_NOTES)
            notes = f"Area: {area} | Building: {building} | Apt: {apartment}"
            if note:
                notes += f" | {note}"
            cid = db.insert("services_customers", {
                "user_id": user_id, "name": name, "phone": phone, "email": email,
                "area": area, "building": building, "apartment": apartment,
                "address": address, "notes": notes,
            })
            customers.append({"id": cid, "name": name, "phone": phone})

        # ---- Bookings ----------------------------------------------------- #
        # Weighted status: mostly completed in the past, pending/confirmed ahead.
        today = date.today()
        pay_methods = [p[0] for p in PAYMENT_METHODS]
        booking_rows = []  # keep for reviews/notifications/analytics linkage
        for _ in range(250):
            cust = RNG.choice(customers)
            sname, sid = RNG.choice(service_list)
            duration = next(d for n, d, p, desc in SERVICES if n == sname)
            price = next(p for n, d, p, desc in SERVICES if n == sname)

            # Date: -60..+21 days, skip Sundays (weekday 6)
            offset = RNG.randint(-60, 21)
            bdate = today + timedelta(days=offset)
            while bdate.weekday() == 6:
                bdate += timedelta(days=1)

            # Status based on time position
            if bdate < today:
                status = RNG.choices(
                    ["completed", "cancelled", "confirmed"], weights=[80, 12, 8])[0]
            elif bdate == today:
                status = RNG.choice(["assigned", "on_the_way", "in_progress", "confirmed"])
            else:
                status = RNG.choices(
                    ["pending", "confirmed", "assigned"], weights=[45, 40, 15])[0]

            start_hour = RNG.randint(8, 18)
            start_t = time(start_hour, RNG.choice([0, 30]))
            end_t = _add_minutes(start_t, duration)
            if end_t <= start_t:  # duration rolled past midnight, clamp
                end_t = time(20, 0)

            # Assign staff (AC services -> technician)
            pool = ac_techs if sname.startswith("AC ") else field_staff
            assignee = RNG.choice(pool) if pool else None
            staff_id = assignee["id"] if (assignee and status != "pending") else None

            # Payment
            if status == "completed":
                pay_status = "paid"
            elif status == "cancelled":
                pay_status = RNG.choice(["refunded", "pending"])
            else:
                pay_status = RNG.choice(["pending", "paid"])
            pay_method = RNG.choice(pay_methods)

            bid = db.insert("services_bookings", {
                "user_id": user_id, "service_id": sid, "customer_id": cust["id"],
                "customer_name": cust["name"], "phone": cust["phone"],
                "booking_date": bdate.isoformat(),
                "start_time": start_t.strftime("%H:%M:%S"),
                "end_time": end_t.strftime("%H:%M:%S"),
                "status": status, "notes": f"{sname} at customer location.",
                "price": price, "staff_id": staff_id,
                "payment_status": pay_status, "payment_method": pay_method,
            })
            booking_rows.append({
                "id": bid, "customer": cust, "service_name": sname, "service_id": sid,
                "staff": assignee, "status": status, "price": price,
                "date": bdate, "pay_status": pay_status,
            })

        # ---- Packages / Promotions / Memberships ------------------------- #
        db.insert("services_packages", {
            "user_id": user_id, "name": "Full Home Bundle", "price": 699,
            "includes": "Home Deep Cleaning + Sofa Cleaning + Carpet Cleaning",
            "ai_context": "Save with a full home cleaning bundle.", "is_active": 1})
        db.insert("services_packages", {
            "user_id": user_id, "name": "AC Care Package", "price": 599,
            "includes": "AC Duct Cleaning + AC Coil Cleaning + AC Filter Cleaning",
            "ai_context": "Complete AC maintenance package.", "is_active": 1})
        db.insert("services_promotions", {
            "user_id": user_id, "title": "Summer AC Special",
            "description": "20% off all AC services during summer.",
            "discount": "20%", "is_active": 1})
        db.insert("services_promotions", {
            "user_id": user_id, "title": "First Booking Offer",
            "description": "AED 50 off your first home cleaning.",
            "discount": "AED 50", "is_active": 1})
        db.insert("services_memberships", {
            "user_id": user_id, "name": "CleanAir Plus", "price": 999,
            "benefits": "4 apartment cleanings/month + priority booking + 10% off add-ons",
            "is_active": 1})

        # ---- FAQs / Policies --------------------------------------------- #
        faqs = [
            ("What areas do you cover?", "We cover all of Dubai including Marina, Downtown, Business Bay, JVC, JLT, Palm Jumeirah and more."),
            ("Do you bring your own equipment?", "Yes, our crews arrive fully equipped with eco-friendly supplies."),
            ("What are your working hours?", "Monday to Saturday, 08:00 AM to 08:00 PM. Closed on Sunday."),
            ("How do I pay?", "We accept Cash, Card, Stripe, Online Payment and Bank Transfer."),
            ("Can I reschedule?", "Yes, free rescheduling up to 4 hours before your slot."),
            ("Do you offer AC cleaning?", "Yes — AC Duct, Coil and Filter cleaning are available."),
        ]
        for q, a in faqs:
            db.insert("services_faqs", {"user_id": user_id, "question": q, "answer": a})
        db.insert("services_policies", {
            "user_id": user_id, "title": "Cancellation Policy", "policy_type": "cancellation",
            "content": "Free cancellation up to 4 hours before the scheduled slot.", "is_active": 1})
        db.insert("services_policies", {
            "user_id": user_id, "title": "Satisfaction Guarantee", "policy_type": "quality",
            "content": "Not happy? We re-clean the area free of charge within 24 hours.", "is_active": 1})

        # ---- Holidays ----------------------------------------------------- #
        for d, title, desc in [
            (date(today.year, 12, 2), "UAE National Day", "Closed for National Day."),
            (date(today.year, 12, 3), "National Day Holiday", "Closed."),
        ]:
            db.insert("services_holidays", {
                "user_id": user_id, "holiday_date": d.isoformat(),
                "reason": title, "title": title, "description": desc})

        # ---- Vehicles ----------------------------------------------------- #
        for i in range(10):
            drv = drivers[i % len(drivers)] if drivers else None
            db.insert("services_vehicles", {
                "user_id": user_id,
                "vehicle_number": f"Dubai {RNG.choice('ABCDEFGH')} {RNG.randint(10000, 99999)}",
                "driver_id": drv["id"] if drv else None,
                "driver_name": drv["name"] if drv else None,
                "current_location": RNG.choice(AREAS),
                "status": RNG.choice(["available", "on_job", "available", "maintenance"]),
            })

        # ---- Reviews (linked to completed bookings) ---------------------- #
        completed = [b for b in booking_rows if b["status"] == "completed"]
        RNG.shuffle(completed)
        for b in completed[:40]:
            rating = RNG.choices([5, 4, 3], weights=[60, 30, 10])[0]
            db.insert("services_reviews", {
                "user_id": user_id, "customer_id": b["customer"]["id"],
                "booking_id": b["id"], "service_id": b["service_id"],
                "customer_name": b["customer"]["name"], "rating": rating,
                "comment": RNG.choice(REVIEW_COMMENTS[rating]),
            })

        # ---- Chats: 50 WhatsApp + 30 Facebook ---------------------------- #
        # Clean previous chats for this user
        db.cursor.execute("SELECT id FROM chats WHERE user_id=%s", [user_id])
        old_ids = [r["id"] for r in db.cursor.fetchall()]
        for cid in old_ids:
            db.delete("chat_history", {"chat_id": cid})
        if old_ids:
            db.execute("DELETE FROM chats WHERE user_id=%s", [user_id])

        def _seed_chats(count, chat_type, scripts):
            for _ in range(count):
                cust = RNG.choice(customers)
                number = "9715" + str(RNG.randint(10000000, 99999999))
                chat_data = {"user_id": user_id, "title": cust["name"], "chat_type": chat_type}
                if chat_type == "whatsapp":
                    chat_data["user_number"] = number
                chat_id = db.insert("chats", chat_data)
                script = RNG.choice(scripts)
                last_ts = datetime.now() - timedelta(hours=RNG.randint(0, 240))
                for req, resp in script:
                    last_ts += timedelta(minutes=RNG.randint(1, 8))
                    db.insert("chat_history", {
                        "chat_id": chat_id, "request_text": req,
                        "response_text": resp, "gemini_response": None,
                        "created_at": last_ts.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                db.execute("UPDATE chats SET lastmsg_at=%s WHERE id=%s",
                           [last_ts.strftime("%Y-%m-%d %H:%M:%S"), chat_id])

        _seed_chats(50, "whatsapp", WHATSAPP_SCRIPTS)
        _seed_chats(30, "facebook", FACEBOOK_SCRIPTS)

        # ---- Notifications ------------------------------------------------ #
        sample = booking_rows[:]
        RNG.shuffle(sample)
        for i, tmpl in enumerate([RNG.choice(NOTIFICATION_TEMPLATES) for _ in range(25)]):
            title, msg = tmpl
            b = sample[i % len(sample)]
            message = msg.format(
                service=b["service_name"], customer=b["customer"]["name"],
                staff=(b["staff"]["name"] if b["staff"] else "A crew member"),
                price=int(b["price"]))
            db.insert("services_notifications", {
                "user_id": user_id, "title": title, "message": message,
                "type": title.lower().replace(" ", "_"),
                "is_read": RNG.choice([0, 0, 0, 1]),
                "created_at": (datetime.now() - timedelta(hours=RNG.randint(0, 120)))
                .strftime("%Y-%m-%d %H:%M:%S"),
            })

        # ---- Gemini cache refresh (best effort) -------------------------- #
        cache_result = None
        if refresh_cache and update_user_cache:
            try:
                cache_result = update_user_cache(db, user_id)
            except Exception as exc:  # pragma: no cover
                cache_result = {"success": False, "error": str(exc)}

        summary = {
            "success": True,
            "user_id": user_id,
            "user_created": created,
            "email": USER_EMAIL,
            "password": USER_PASSWORD,
            "counts": {
                "services": len(service_ids),
                "customers": len(customers),
                "staff": len(staff),
                "bookings": len(booking_rows),
                "reviews": len(completed[:40]),
                "vehicles": 10,
                "whatsapp_chats": 50,
                "facebook_chats": 30,
            },
            "cache": cache_result,
        }
        return summary
    finally:
        db.close()


if __name__ == "__main__":
    import pprint
    pprint.pprint(seed_cleanair(refresh_cache=True))
