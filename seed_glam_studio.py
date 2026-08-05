"""Populate the Glam Studio (Dubai Beauty Salon & Spa) demo account.

Service-module only. Creates ``beauty@test.com`` if missing, otherwise refreshes
all dummy data. Interconnected: customers → bookings → services + staff +
payments + chairs/rooms, plus products, chats, reviews, promotions and
notifications.

Run:  python3 seed_glam_studio.py
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

try:
    from gemini_cache import update_user_cache
except Exception:  # pragma: no cover
    update_user_cache = None

try:
    from chatbot_types.services_seed import ensure_seed as ensure_services_type
except Exception:  # pragma: no cover
    ensure_services_type = None

RNG = random.Random(20260717)

USER_EMAIL = "beauty@test.com"
USER_NAME = "Sophia Williams"
USER_PASSWORD = "admin"
CURRENCY = "AED"

# (name, duration_minutes, price AED, description, category_key)
SERVICES = [
    ("Hair Cut", 45, 99, "Precision cut for any hair length.", "hair"),
    ("Hair Wash", 20, 50, "Shampoo and conditioning wash.", "hair"),
    ("Hair Styling", 45, 149, "Blow-dry and styling for any occasion.", "hair"),
    ("Hair Coloring", 120, 399, "Full colour with premium brands.", "hair"),
    ("Hair Highlights", 150, 499, "Balayage or foil highlights.", "hair"),
    ("Hair Keratin", 180, 699, "Smoothing keratin treatment.", "hair"),
    ("Hair Botox", 120, 499, "Deep repair hair botox treatment.", "hair"),
    ("Hair Spa", 60, 199, "Nourishing spa for scalp and hair.", "hair"),
    ("Hair Smoothening", 150, 499, "Long-lasting smooth finish.", "hair"),
    ("Hair Rebonding", 180, 699, "Permanent straightening rebonding.", "hair"),
    ("Beard Trim", 30, 50, "Shape and tidy beard trim.", "grooming"),
    ("Shaving", 30, 75, "Classic clean shave.", "grooming"),
    ("Facial", 60, 149, "Classic facial for glowing skin.", "skin"),
    ("Hydra Facial", 75, 299, "HydraFacial for deep hydration.", "skin"),
    ("Gold Facial", 90, 399, "Luxury 24K gold facial.", "skin"),
    ("Cleanup", 45, 99, "Quick facial cleanup.", "skin"),
    ("Manicure", 45, 75, "Classic manicure.", "nails"),
    ("Pedicure", 60, 99, "Classic pedicure.", "nails"),
    ("Gel Polish", 45, 149, "Long-wear gel polish.", "nails"),
    ("Nail Extensions", 90, 249, "Acrylic or gel extensions.", "nails"),
    ("Nail Art", 30, 99, "Custom nail art design.", "nails"),
    ("Eyebrow Threading", 15, 50, "Precise eyebrow threading.", "beauty"),
    ("Waxing", 30, 75, "Face or body waxing.", "beauty"),
    ("Full Body Wax", 90, 249, "Full body waxing session.", "beauty"),
    ("Makeup", 60, 199, "Everyday glam makeup.", "makeup"),
    ("Bridal Makeup", 150, 699, "Full bridal look with trial option.", "makeup"),
    ("Party Makeup", 75, 299, "Party and event makeup.", "makeup"),
    ("Henna", 45, 99, "Natural henna application.", "beauty"),
    ("Massage", 60, 199, "Relaxing full-body massage.", "spa"),
    ("Body Spa", 90, 299, "Complete body spa ritual.", "spa"),
    ("Skin Treatment", 75, 249, "Targeted skin treatment session.", "skin"),
]

PAYMENT_METHODS = [
    ("Cash", "Pay at reception in cash."),
    ("Card", "Debit / credit card at the salon."),
    ("Apple Pay", "Contactless Apple Pay."),
    ("Google Pay", "Contactless Google Pay."),
    ("Stripe", "Secure online card via Stripe."),
    ("Online Payment", "Pay via payment link before visit."),
]

AREAS = [
    "Jumeirah", "Dubai Marina", "Business Bay", "Downtown", "JVC",
    "Palm Jumeirah", "Al Barsha", "JLT", "Deira", "Mirdif",
]

FEMALE_FIRST = [
    "Sophia", "Emma", "Olivia", "Ava", "Mia", "Isabella", "Amelia", "Harper",
    "Fatima", "Aisha", "Mariam", "Sara", "Layla", "Noura", "Huda", "Zainab",
    "Priya", "Anjali", "Ananya", "Elena", "Grace", "Chloe", "Nora", "Yasmin",
]
MALE_FIRST = [
    "Ahmed", "Mohammed", "Ali", "Omar", "Khalid", "Yousef", "Hassan", "Ibrahim",
    "John", "Michael", "David", "James", "Daniel", "Ryan", "Noah", "Liam",
]
LAST_NAMES = [
    "Williams", "Johnson", "Smith", "Brown", "Patel", "Sharma", "Khan", "Hassan",
    "Al Maktoum", "Al Nahyan", "Rahman", "Sheikh", "Wong", "Garcia", "Kumar",
    "Farooq", "Saleh", "Nasser", "Iqbal", "Mansoor",
]

CUSTOMER_NOTES = [
    "Prefers senior stylist.", "Sensitive scalp — use mild products.",
    "VIP client.", "Allergic to ammonia dyes.", "Prefers morning slots.",
    "Regular monthly facial.", "Bring same nail artist if possible.",
    "Bridal package client.", "Student discount eligible.", "", "",
]

STAFF_PLAN = [
    ("Salon Manager", 1, "Operations, Scheduling, Quality"),
    ("Senior Hair Stylist", 2, "Colour, Keratin, Cuts"),
    ("Hair Stylist", 3, "Cuts, Styling, Blow-dry"),
    ("Barber", 1, "Beard Trim, Shaving, Men's Cuts"),
    ("Beautician", 2, "Facials, Waxing, Threading"),
    ("Nail Artist", 1, "Manicure, Pedicure, Nail Art"),
    ("Makeup Artist", 1, "Bridal, Party, Everyday Makeup"),
    ("Spa Therapist", 1, "Body Spa, Skin Treatments"),
    ("Massage Therapist", 1, "Massage, Relaxation"),
    ("Receptionist", 1, "Bookings, Front Desk"),
    ("Cleaner", 1, "Salon Hygiene"),
]

PRODUCTS = [
    ("Shampoo", "Hair Care", 89, 40),
    ("Conditioner", "Hair Care", 89, 35),
    ("Hair Serum", "Hair Care", 149, 25),
    ("Hair Mask", "Hair Care", 129, 20),
    ("Hair Oil", "Hair Care", 99, 30),
    ("Hair Spray", "Hair Care", 75, 28),
    ("Face Wash", "Skin Care", 65, 45),
    ("Facial Kit", "Skin Care", 199, 15),
    ("Skin Cream", "Skin Care", 149, 22),
    ("Wax", "Beauty", 49, 50),
    ("Nail Polish", "Nails", 55, 60),
    ("Hair Color", "Hair Care", 179, 18),
    ("Beard Oil", "Grooming", 79, 20),
    ("Hair Dryer", "Tools", 299, 8),
    ("Straightener", "Tools", 349, 6),
]

PROMOTIONS = [
    ("20% Off Hair Coloring", "Save 20% on all hair colouring this month.", "20%"),
    ("Free Hair Spa", "Complimentary Hair Spa with any colouring service.", "Free spa"),
    ("Bridal Package", "Bridal Makeup + Hair Styling + Manicure special.", "AED 999"),
    ("Weekend Facial Offer", "15% off facials on Friday and Saturday.", "15%"),
    ("Student Discount", "10% off for students with valid ID.", "10%"),
    ("First Visit Special", "AED 50 off your first booking.", "AED 50"),
    ("Keratin Combo", "Hair Keratin + Hair Spa package price.", "AED 799"),
    ("Nail Art Week", "Buy Gel Polish, get Nail Art free.", "Free art"),
    ("Spa Day Deal", "Massage + Body Spa for AED 399.", "AED 399"),
    ("Loyalty Double Points", "Earn 2x loyalty points this week.", "2x points"),
]

REVIEW_COMMENTS = {
    5: [
        "Amazing stylist, best haircut in Dubai!",
        "Loved my bridal makeup — felt like a princess.",
        "HydraFacial was incredible, skin glowing!",
        "Super friendly staff and beautiful salon.",
        "Nail art was perfect, will come back.",
        "Relaxing massage, highly recommend Glam Studio.",
    ],
    4: [
        "Great service, slightly delayed start.",
        "Colour turned out well, friendly team.",
        "Good facial, salon is clean and modern.",
        "Happy with the manicure, fair prices.",
    ],
    3: [
        "Decent cut but waited longer than expected.",
        "Okay experience, communication could improve.",
        "Average styling, product options limited.",
    ],
}

WHATSAPP_SCRIPTS = [
    [("Hi", "Hello! Welcome to Glam Studio. How can I help you today?"),
     ("I'd like to book a haircut.", "Of course! Hair Cut is AED 99 and takes about 45 minutes. What day works for you?"),
     ("Tomorrow afternoon.", "We have 3 PM available. Shall I confirm?"),
     ("Yes please.", "Booked for tomorrow at 3 PM. See you at Glam Studio!")],
    [("How much is a facial?", "Our classic Facial is AED 149. Hydra Facial is AED 299 and Gold Facial is AED 399."),
     ("Hydra Facial please.", "Great choice! When would you like to come in?"),
     ("Saturday morning.", "You're booked Saturday at 10 AM. Thank you!")],
    [("Do you have appointments today?", "Yes! We still have slots this evening. Which service?"),
     ("Manicure.", "Manicure is AED 75. We can take you at 6 PM today."),
     ("Perfect.", "Confirmed for 6 PM. Looking forward to seeing you!")],
    [("Can I reschedule?", "Of course. May I have your name or phone number?"),
     ("Sara, 0508881122.", "Found your booking. What new time do you prefer?"),
     ("Friday 4 PM.", "Rescheduled to Friday at 4 PM. Thank you!")],
    [("Thank you.", "You're welcome! Message us anytime for bookings."),
     ("Do you do bridal makeup?", "Yes! Bridal Makeup is AED 699. Would you like a consultation?"),
     ("Yes this week.", "Booked a consultation. We'll confirm the details shortly.")],
    [("Hair keratin price?", "Hair Keratin is AED 699 and takes about 3 hours."),
     ("Any discount?", "We have a Keratin Combo with Hair Spa for AED 799. Interested?"),
     ("Yes book it.", "Booked! Please arrive with clean dry hair. Thank you!")],
]

FACEBOOK_SCRIPTS = [
    [("Hello, are you open?", "Hi! Yes — open every day 9 AM to 10 PM. How can we help?"),
     ("Need party makeup.", "Party Makeup is AED 299. What date is your event?"),
     ("Friday night.", "Booked for Friday. Thank you for messaging Glam Studio!")],
    [("Nail extensions available?", "Yes! Nail Extensions are AED 249. Would you like to book?"),
     ("Tomorrow.", "Confirmed for tomorrow. See you soon!")],
    [("Do you sell hair serum?", "Yes, Hair Serum is AED 149 in our retail shelf."),
     ("I'll visit today.", "Perfect, our receptionist can help you. Thank you!")],
    [("Hi", "Hello! Welcome to Glam Studio Beauty Salon & Spa."),
     ("How much for eyebrow threading?", "Eyebrow Threading is AED 50. Walk-ins welcome too."),
     ("Thank you.", "You're welcome!")],
    [("Gold facial?", "Gold Facial is AED 399 for about 90 minutes. Shall I book you in?"),
     ("Sunday please.", "You're booked Sunday. Looking forward to pampering you!")],
]

NOTIFICATION_TEMPLATES = [
    ("New Booking", "New booking: {service} for {customer}."),
    ("Booking Reminder", "Reminder: {customer} has {service} tomorrow."),
    ("Appointment Cancelled", "{customer} cancelled their {service} appointment."),
    ("Customer Review", "{customer} left a {rating}-star review."),
    ("Payment Received", "Payment of AED {price} received from {customer}."),
    ("Birthday Reminder", "Birthday soon: {customer}. Send a special offer!"),
    ("Low Product Stock", "Low stock: {product} — reorder soon."),
    ("Staff Leave", "{staff} marked leave — reassign appointments."),
]


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
    for col, definition in (
        ("owner_name", "owner_name VARCHAR(255) DEFAULT NULL"),
        ("country", "country VARCHAR(120) DEFAULT NULL"),
        ("timezone", "timezone VARCHAR(80) DEFAULT NULL"),
        ("business_type", "business_type VARCHAR(120) DEFAULT NULL"),
        ("whatsapp", "whatsapp VARCHAR(50) DEFAULT NULL"),
        ("tax_percent", "tax_percent DECIMAL(5,2) NOT NULL DEFAULT 0"),
    ):
        _ensure_column(db, "services_settings", col, definition)

    for col, definition in (
        ("payment_status", "payment_status VARCHAR(20) NOT NULL DEFAULT 'pending'"),
        ("payment_method", "payment_method VARCHAR(50) DEFAULT NULL"),
        ("chair_number", "chair_number VARCHAR(50) DEFAULT NULL"),
        ("room_id", "room_id INT DEFAULT NULL"),
        ("duration_minutes", "duration_minutes INT DEFAULT NULL"),
    ):
        _ensure_column(db, "services_bookings", col, definition)

    for col, definition in (
        ("photo_url", "photo_url VARCHAR(500) DEFAULT NULL"),
        ("rating", "rating DECIMAL(3,2) NOT NULL DEFAULT 0"),
        ("completed_jobs", "completed_jobs INT NOT NULL DEFAULT 0"),
        ("commission_percent", "commission_percent DECIMAL(5,2) NOT NULL DEFAULT 0"),
    ):
        _ensure_column(db, "services_staff", col, definition)

    for col, definition in (
        ("area", "area VARCHAR(120) DEFAULT NULL"),
        ("building", "building VARCHAR(255) DEFAULT NULL"),
        ("apartment", "apartment VARCHAR(50) DEFAULT NULL"),
        ("address", "address TEXT DEFAULT NULL"),
        ("gender", "gender VARCHAR(20) DEFAULT NULL"),
        ("birthday", "birthday DATE DEFAULT NULL"),
        ("favorite_services", "favorite_services TEXT DEFAULT NULL"),
        ("loyalty_points", "loyalty_points INT NOT NULL DEFAULT 0"),
        ("total_visits", "total_visits INT NOT NULL DEFAULT 0"),
        ("lifetime_spend", "lifetime_spend DECIMAL(12,2) NOT NULL DEFAULT 0"),
    ):
        _ensure_column(db, "services_customers", col, definition)

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS services_products (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            name VARCHAR(255) NOT NULL,
            category VARCHAR(120) DEFAULT NULL,
            price DECIMAL(10,2) NOT NULL DEFAULT 0,
            stock INT NOT NULL DEFAULT 0,
            low_stock_threshold INT NOT NULL DEFAULT 10,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS services_rooms (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            name VARCHAR(255) NOT NULL,
            room_type VARCHAR(50) NOT NULL,
            number VARCHAR(50) NOT NULL,
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


def seed_glam_studio(refresh_cache=True):
    ensure_services_schema()
    db = Database()
    try:
        _ensure_extra_schema(db)
        type_id = _resolve_type_id(db)
        user_id, created = _resolve_user_id(db)

        db.update(
            "admins",
            {"name": USER_NAME, "password": _md5(USER_PASSWORD), "role_id": 2},
            {"id": user_id},
        )
        if type_id:
            _upsert_meta(db, user_id, "chatbot_type_id", str(type_id))

        _ensure_user_defaults(db, user_id)

        profile = {
            "currency_code": CURRENCY,
            "business_name": "Glam Studio",
            "business_category": "Beauty Salon & Spa",
            "business_type": "Service",
            "owner_name": USER_NAME,
            "phone": "+971 50 888 1122",
            "whatsapp": "+971 50 888 1122",
            "email": USER_EMAIL,
            "country": "United Arab Emirates",
            "city": "Dubai",
            "address": "Jumeirah, Dubai, UAE",
            "timezone": "Asia/Dubai",
            "website": "https://glamstudio.ae",
            "maps_link": "https://maps.google.com/?q=Jumeirah+Dubai",
            "logo_url": "https://ui-avatars.com/api/?name=Glam+Studio&background=ec4899&color=fff&size=256",
            "about": (
                "Glam Studio is a modern Beauty Salon & Spa in Jumeirah, Dubai. "
                "We offer hair, skin, nails, makeup and spa treatments with "
                "expert stylists in a luxury setting."
            ),
            "parking_info": "Valet and street parking available on Jumeirah Beach Road.",
            "cancellation_policy": "Free cancellation up to 3 hours before appointment.",
            "booking_rules": "Open every day 09:00 AM – 10:00 PM. Please arrive 10 minutes early.",
            "payment_methods": "Cash, Card, Apple Pay, Google Pay, Stripe, Online Payment",
            "tax_percent": 5.0,
            "primary_color": "#ec4899",
            "secondary_color": "#a855f7",
            "accent_color": "#f59e0b",
            "app_background": "#fff7fb",
        }
        if db.row("services_settings", {"user_id": user_id}):
            db.update("services_settings", profile, {"user_id": user_id})
        else:
            profile["user_id"] = user_id
            db.insert("services_settings", profile)

        # Mon–Sun 09:00–22:00
        _clear(db, "services_working_hours", user_id)
        for day in range(7):
            db.insert(
                "services_working_hours",
                {
                    "user_id": user_id,
                    "day_of_week": day,
                    "open_time": "09:00:00",
                    "close_time": "22:00:00",
                    "break_start": None,
                    "break_end": None,
                    "is_closed": 0,
                },
            )

        for table in (
            "services_catalog", "services_categories", "services_holidays",
            "services_payment_methods", "services_policies", "services_faqs",
            "services_packages", "services_promotions", "services_memberships",
            "services_staff", "services_customers", "services_bookings",
            "services_products", "services_rooms", "services_reviews",
            "services_notifications",
        ):
            _clear(db, table, user_id)

        cat_ids = {}
        for i, (key, name) in enumerate([
            ("hair", "Hair Services"),
            ("grooming", "Grooming"),
            ("skin", "Skin & Facial"),
            ("nails", "Nails"),
            ("beauty", "Beauty"),
            ("makeup", "Makeup"),
            ("spa", "Spa & Wellness"),
        ]):
            cat_ids[key] = db.insert("services_categories", {
                "user_id": user_id, "name": name, "sort_order": i + 1, "is_active": 1,
            })

        service_ids = {}
        service_meta = {}
        for name, duration, price, desc, cat_key in SERVICES:
            sid = db.insert("services_catalog", {
                "user_id": user_id, "name": name, "duration_minutes": duration,
                "price": price, "description": desc, "ai_context": desc,
                "category_id": cat_ids[cat_key], "status": "active",
            })
            service_ids[name] = sid
            service_meta[name] = {"id": sid, "duration": duration, "price": price}
        service_list = list(service_meta.items())

        for i, (name, details) in enumerate(PAYMENT_METHODS):
            db.insert("services_payment_methods", {
                "user_id": user_id, "name": name, "details": details,
                "is_active": 1, "sort_order": i + 1,
            })

        # Rooms & chairs
        rooms = []
        for i in range(1, 11):
            rid = db.insert("services_rooms", {
                "user_id": user_id, "name": f"Styling Chair {i}",
                "room_type": "styling_chair", "number": f"C{i:02d}",
                "status": RNG.choice(["available", "available", "occupied", "reserved", "maintenance"]),
            })
            rooms.append({"id": rid, "number": f"C{i:02d}", "type": "styling_chair"})
        for i in range(1, 6):
            rid = db.insert("services_rooms", {
                "user_id": user_id, "name": f"Spa Room {i}",
                "room_type": "spa_room", "number": f"S{i:02d}",
                "status": RNG.choice(["available", "available", "occupied", "reserved"]),
            })
            rooms.append({"id": rid, "number": f"S{i:02d}", "type": "spa_room"})
        for i in range(1, 4):
            rid = db.insert("services_rooms", {
                "user_id": user_id, "name": f"Makeup Room {i}",
                "room_type": "makeup_room", "number": f"M{i:02d}",
                "status": RNG.choice(["available", "occupied", "reserved"]),
            })
            rooms.append({"id": rid, "number": f"M{i:02d}", "type": "makeup_room"})
        for i in range(1, 5):
            rid = db.insert("services_rooms", {
                "user_id": user_id, "name": f"Nail Station {i}",
                "room_type": "nail_station", "number": f"N{i:02d}",
                "status": RNG.choice(["available", "available", "occupied", "maintenance"]),
            })
            rooms.append({"id": rid, "number": f"N{i:02d}", "type": "nail_station"})

        chairs = [r for r in rooms if r["type"] == "styling_chair"]
        spa_rooms = [r for r in rooms if r["type"] == "spa_room"]
        makeup_rooms = [r for r in rooms if r["type"] == "makeup_room"]
        nail_stations = [r for r in rooms if r["type"] == "nail_station"]

        # Staff
        staff = []
        idx = 0
        for role, count, skills in STAFF_PLAN:
            for _ in range(count):
                idx += 1
                gender = "female" if role not in ("Barber",) else "male"
                fname = RNG.choice(FEMALE_FIRST if gender == "female" else MALE_FIRST)
                sname = f"{fname} {RNG.choice(LAST_NAMES)}"
                wh = json.dumps({"days": "Mon-Sun", "start": "09:00", "end": "22:00"})
                assigned = ""
                if "Hair" in role or role == "Barber":
                    assigned = ",".join(str(service_ids[n]) for n in (
                        "Hair Cut", "Hair Wash", "Hair Styling", "Beard Trim", "Shaving")
                        if n in service_ids)
                elif role == "Beautician":
                    assigned = ",".join(str(service_ids[n]) for n in (
                        "Facial", "Hydra Facial", "Waxing", "Eyebrow Threading")
                        if n in service_ids)
                elif role == "Nail Artist":
                    assigned = ",".join(str(service_ids[n]) for n in (
                        "Manicure", "Pedicure", "Gel Polish", "Nail Art")
                        if n in service_ids)
                elif "Makeup" in role:
                    assigned = ",".join(str(service_ids[n]) for n in (
                        "Makeup", "Bridal Makeup", "Party Makeup")
                        if n in service_ids)
                elif "Spa" in role or "Massage" in role:
                    assigned = ",".join(str(service_ids[n]) for n in (
                        "Massage", "Body Spa", "Skin Treatment")
                        if n in service_ids)
                sid = db.insert("services_staff", {
                    "user_id": user_id, "name": sname, "role": role, "skills": skills,
                    "gender": gender, "phone": _phone(),
                    "email": f"{fname.lower()}{idx}@glamstudio.ae",
                    "working_hours": wh, "assigned_service_ids": assigned,
                    "is_active": 1, "status": "active",
                    "photo_url": f"https://ui-avatars.com/api/?name={sname.replace(' ', '+')}&background=ec4899&color=fff",
                    "rating": round(RNG.uniform(3.9, 5.0), 2),
                    "completed_jobs": RNG.randint(40, 500),
                    "commission_percent": round(RNG.uniform(8, 25), 1),
                    "ai_context": f"{role} — {skills}",
                })
                staff.append({"id": sid, "name": sname, "role": role})

        stylists = [s for s in staff if s["role"] in (
            "Hair Stylist", "Senior Hair Stylist", "Barber", "Beautician",
            "Nail Artist", "Makeup Artist", "Spa Therapist", "Massage Therapist",
        )]

        def _room_for_service(sname):
            if sname in ("Massage", "Body Spa", "Skin Treatment"):
                return RNG.choice(spa_rooms) if spa_rooms else None
            if "Makeup" in sname:
                return RNG.choice(makeup_rooms) if makeup_rooms else None
            if sname in ("Manicure", "Pedicure", "Gel Polish", "Nail Extensions", "Nail Art"):
                return RNG.choice(nail_stations) if nail_stations else None
            return RNG.choice(chairs) if chairs else None

        def _staff_for_service(sname):
            if sname in ("Beard Trim", "Shaving"):
                pool = [s for s in staff if s["role"] == "Barber"] or stylists
            elif "Makeup" in sname:
                pool = [s for s in staff if "Makeup" in s["role"]] or stylists
            elif sname in ("Manicure", "Pedicure", "Gel Polish", "Nail Extensions", "Nail Art"):
                pool = [s for s in staff if s["role"] == "Nail Artist"] or stylists
            elif sname in ("Massage", "Body Spa"):
                pool = [s for s in staff if "Therapist" in s["role"]] or stylists
            elif sname in ("Facial", "Hydra Facial", "Gold Facial", "Cleanup", "Waxing",
                           "Full Body Wax", "Eyebrow Threading", "Henna"):
                pool = [s for s in staff if s["role"] == "Beautician"] or stylists
            else:
                pool = [s for s in staff if "Hair" in s["role"] or s["role"] == "Barber"] or stylists
            return RNG.choice(pool) if pool else None

        # Customers (150)
        customers = []
        used_emails = set()
        for i in range(150):
            gender = RNG.choices(["female", "male"], weights=[75, 25])[0]
            fname = RNG.choice(FEMALE_FIRST if gender == "female" else MALE_FIRST)
            name = f"{fname} {RNG.choice(LAST_NAMES)}"
            email = f"{fname.lower()}.{i}@example.com"
            while email in used_emails:
                email = f"{fname.lower()}.{RNG.randint(1, 9999)}@example.com"
            used_emails.add(email)
            area = RNG.choice(AREAS)
            address = f"{RNG.randint(1, 99)} {area} Street, {area}, Dubai, UAE"
            favs = RNG.sample([s[0] for s in SERVICES], k=RNG.randint(1, 3))
            bday = date(RNG.randint(1985, 2005), RNG.randint(1, 12), RNG.randint(1, 28))
            note = RNG.choice(CUSTOMER_NOTES)
            phone = _phone()
            cid = db.insert("services_customers", {
                "user_id": user_id, "name": name, "phone": phone, "email": email,
                "gender": gender, "birthday": bday.isoformat(),
                "area": area, "address": address,
                "favorite_services": ", ".join(favs),
                "loyalty_points": RNG.randint(0, 2500),
                "total_visits": 0, "lifetime_spend": 0,
                "notes": note or f"Area: {area}",
            })
            customers.append({"id": cid, "name": name, "phone": phone, "favs": favs})

        # Bookings (300)
        today = date.today()
        pay_methods = [p[0] for p in PAYMENT_METHODS]
        booking_rows = []
        spend_map = {}  # customer_id -> (visits, spend)

        for _ in range(300):
            cust = RNG.choice(customers)
            sname, meta = RNG.choice(service_list)
            duration = meta["duration"]
            price = meta["price"]
            sid = meta["id"]

            offset = RNG.randint(-75, 21)
            bdate = today + timedelta(days=offset)

            if bdate < today:
                status = RNG.choices(
                    ["completed", "cancelled", "no_show", "confirmed"],
                    weights=[78, 10, 5, 7],
                )[0]
            elif bdate == today:
                status = RNG.choice(["checked_in", "in_progress", "confirmed", "pending"])
            else:
                status = RNG.choices(
                    ["pending", "confirmed"], weights=[40, 60],
                )[0]

            start_hour = RNG.randint(9, 20)
            start_t = time(start_hour, RNG.choice([0, 30]))
            end_t = _add_minutes(start_t, duration)
            if end_t.hour < start_t.hour or (end_t.hour == 0 and duration > 0):
                end_t = time(22, 0)

            assignee = _staff_for_service(sname)
            staff_id = assignee["id"] if (assignee and status not in ("pending", "cancelled")) else None
            room = _room_for_service(sname)
            chair_number = room["number"] if room else None
            room_id = room["id"] if room else None

            if status == "completed":
                pay_status = "paid"
            elif status in ("cancelled", "no_show"):
                pay_status = RNG.choice(["refunded", "pending"])
            else:
                pay_status = RNG.choice(["pending", "paid"])
            pay_method = RNG.choice(pay_methods)

            bid = db.insert("services_bookings", {
                "user_id": user_id, "service_id": sid, "customer_id": cust["id"],
                "customer_name": cust["name"], "phone": cust["phone"] or _phone(),
                "booking_date": bdate.isoformat(),
                "start_time": start_t.strftime("%H:%M:%S"),
                "end_time": end_t.strftime("%H:%M:%S"),
                "status": status, "notes": f"{sname} appointment.",
                "price": price, "staff_id": staff_id,
                "payment_status": pay_status, "payment_method": pay_method,
                "chair_number": chair_number, "room_id": room_id,
                "duration_minutes": duration,
            })
            booking_rows.append({
                "id": bid, "customer": cust, "service_name": sname, "service_id": sid,
                "staff": assignee, "status": status, "price": price,
                "date": bdate, "pay_status": pay_status,
            })
            if status == "completed":
                visits, spend = spend_map.get(cust["id"], (0, 0.0))
                spend_map[cust["id"]] = (visits + 1, spend + float(price))

        # Update customer loyalty from completed bookings
        for cid, (visits, spend) in spend_map.items():
            points = int(spend // 10)
            db.update("services_customers", {
                "total_visits": visits,
                "lifetime_spend": round(spend, 2),
                "loyalty_points": points + RNG.randint(0, 100),
            }, {"id": cid})

        # Products
        product_rows = []
        for name, category, price, stock in PRODUCTS:
            pid = db.insert("services_products", {
                "user_id": user_id, "name": name, "category": category,
                "price": price, "stock": stock,
                "low_stock_threshold": 10 if stock > 10 else 5,
                "status": "active",
            })
            product_rows.append({"id": pid, "name": name, "stock": stock})

        # Promotions (10 active)
        for title, desc, discount in PROMOTIONS:
            db.insert("services_promotions", {
                "user_id": user_id, "title": title, "description": desc,
                "discount": discount, "is_active": 1,
                "start_date": (today - timedelta(days=7)).isoformat(),
                "end_date": (today + timedelta(days=45)).isoformat(),
            })

        # Packages / memberships / FAQs / policies
        db.insert("services_packages", {
            "user_id": user_id, "name": "Bridal Glow Package", "price": 999,
            "includes": "Bridal Makeup + Hair Styling + Manicure + Pedicure",
            "ai_context": "Complete bridal package.", "is_active": 1,
        })
        db.insert("services_packages", {
            "user_id": user_id, "name": "Spa Escape", "price": 399,
            "includes": "Massage + Body Spa",
            "ai_context": "Relaxing spa combo.", "is_active": 1,
        })
        db.insert("services_memberships", {
            "user_id": user_id, "name": "Glam Club", "price": 799,
            "benefits": "Priority booking + 1 free Hair Wash monthly + 10% off retail",
            "is_active": 1,
        })
        for q, a in [
            ("What are your hours?", "We are open every day from 09:00 AM to 10:00 PM."),
            ("Where are you located?", "Jumeirah, Dubai, UAE — see glamstudio.ae for map."),
            ("Do you offer bridal packages?", "Yes — Bridal Glow Package includes makeup, hair and nails."),
            ("What payment methods do you accept?", "Cash, Card, Apple Pay, Google Pay, Stripe and Online Payment."),
            ("Can I cancel or reschedule?", "Yes, free of charge up to 3 hours before your appointment."),
            ("Do you sell hair products?", "Yes, we stock shampoo, serum, masks, colour and tools."),
        ]:
            db.insert("services_faqs", {"user_id": user_id, "question": q, "answer": a})
        db.insert("services_policies", {
            "user_id": user_id, "title": "Cancellation Policy", "policy_type": "cancellation",
            "content": "Free cancellation up to 3 hours before appointment.", "is_active": 1,
        })
        db.insert("services_policies", {
            "user_id": user_id, "title": "Late Arrival", "policy_type": "booking",
            "content": "Late arrivals may have shortened service time to keep the schedule.", "is_active": 1,
        })
        db.insert("services_holidays", {
            "user_id": user_id, "holiday_date": date(today.year, 12, 2).isoformat(),
            "reason": "UAE National Day", "title": "UAE National Day",
            "description": "Special hours may apply.",
        })

        # Reviews (60 from completed)
        completed = [b for b in booking_rows if b["status"] == "completed"]
        RNG.shuffle(completed)
        for b in completed[:60]:
            rating = RNG.choices([5, 4, 3], weights=[65, 28, 7])[0]
            db.insert("services_reviews", {
                "user_id": user_id, "customer_id": b["customer"]["id"],
                "booking_id": b["id"], "service_id": b["service_id"],
                "customer_name": b["customer"]["name"], "rating": rating,
                "comment": RNG.choice(REVIEW_COMMENTS[rating]),
            })

        # Chats
        db.cursor.execute("SELECT id FROM chats WHERE user_id=%s", [user_id])
        old_ids = [r["id"] for r in db.cursor.fetchall()]
        for cid in old_ids:
            db.delete("chat_history", {"chat_id": cid})
        if old_ids:
            db.execute("DELETE FROM chats WHERE user_id=%s", [user_id])

        def _seed_chats(count, chat_type, scripts):
            for _ in range(count):
                cust = RNG.choice(customers)
                chat_data = {"user_id": user_id, "title": cust["name"], "chat_type": chat_type}
                if chat_type == "whatsapp":
                    chat_data["user_number"] = "9715" + str(RNG.randint(10000000, 99999999))
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
                db.execute(
                    "UPDATE chats SET lastmsg_at=%s WHERE id=%s",
                    [last_ts.strftime("%Y-%m-%d %H:%M:%S"), chat_id],
                )

        _seed_chats(60, "whatsapp", WHATSAPP_SCRIPTS)
        _seed_chats(40, "facebook", FACEBOOK_SCRIPTS)

        # Notifications
        sample = booking_rows[:]
        RNG.shuffle(sample)
        for i in range(30):
            title, msg = RNG.choice(NOTIFICATION_TEMPLATES)
            b = sample[i % len(sample)]
            prod = product_rows[i % len(product_rows)]
            message = msg.format(
                service=b["service_name"],
                customer=b["customer"]["name"],
                staff=(b["staff"]["name"] if b["staff"] else "A stylist"),
                price=int(b["price"]),
                rating=RNG.choice([3, 4, 5]),
                product=prod["name"],
            )
            db.insert("services_notifications", {
                "user_id": user_id, "title": title, "message": message,
                "type": title.lower().replace(" ", "_"),
                "is_read": RNG.choice([0, 0, 0, 1]),
                "created_at": (datetime.now() - timedelta(hours=RNG.randint(0, 120)))
                .strftime("%Y-%m-%d %H:%M:%S"),
            })

        cache_result = None
        if refresh_cache and update_user_cache:
            try:
                cache_result = update_user_cache(db, user_id)
            except Exception as exc:  # pragma: no cover
                cache_result = {"success": False, "error": str(exc)}

        return {
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
                "rooms_chairs": len(rooms),
                "products": len(product_rows),
                "promotions": len(PROMOTIONS),
                "reviews": min(60, len(completed)),
                "whatsapp_chats": 60,
                "facebook_chats": 40,
                "notifications": 30,
            },
            "cache": cache_result,
        }
    finally:
        db.close()


if __name__ == "__main__":
    import pprint
    pprint.pprint(seed_glam_studio(refresh_cache=True))
