"""Services chatbot schema — isolated tables for the Service receptionist panel.

Does not touch Ecommerce / Job Posting tables.
"""

from db import Database

SETTINGS_TABLE = "services_settings"
CATALOG_TABLE = "services_catalog"
WORKING_HOURS_TABLE = "services_working_hours"
HOLIDAYS_TABLE = "services_holidays"
CATEGORIES_TABLE = "services_categories"
STAFF_TABLE = "services_staff"
CUSTOMERS_TABLE = "services_customers"
BOOKINGS_TABLE = "services_bookings"
PACKAGES_TABLE = "services_packages"
PROMOTIONS_TABLE = "services_promotions"
MEMBERSHIPS_TABLE = "services_memberships"
FAQS_TABLE = "services_faqs"
POLICIES_TABLE = "services_policies"
PAYMENTS_TABLE = "services_payment_methods"


def _ensure_column(db, table, column, definition):
    db.cursor.execute(
        "SELECT COUNT(*) AS c FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        [table, column],
    )
    if db.cursor.fetchone()["c"] == 0:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def ensure_services_schema():
    db = Database()
    try:
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE} (
                user_id INT NOT NULL PRIMARY KEY,
                currency_code VARCHAR(10) NOT NULL DEFAULT 'USD',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        for col, definition in (
            ("business_name", "business_name VARCHAR(255) DEFAULT NULL"),
            ("business_category", "business_category VARCHAR(255) DEFAULT NULL"),
            ("about", "about TEXT DEFAULT NULL"),
            ("address", "address TEXT DEFAULT NULL"),
            ("city", "city VARCHAR(255) DEFAULT NULL"),
            ("phone", "phone VARCHAR(50) DEFAULT NULL"),
            ("email", "email VARCHAR(255) DEFAULT NULL"),
            ("website", "website VARCHAR(500) DEFAULT NULL"),
            ("maps_link", "maps_link VARCHAR(500) DEFAULT NULL"),
            ("logo_url", "logo_url VARCHAR(500) DEFAULT NULL"),
            ("parking_info", "parking_info TEXT DEFAULT NULL"),
            ("booking_rules", "booking_rules TEXT DEFAULT NULL"),
            # legacy free-text fields (migrated toward policies/payments tables)
            ("payment_methods", "payment_methods TEXT DEFAULT NULL"),
            ("cancellation_policy", "cancellation_policy TEXT DEFAULT NULL"),
            ("primary_color", "primary_color VARCHAR(20) NOT NULL DEFAULT '#0ea5e9'"),
            ("secondary_color", "secondary_color VARCHAR(20) NOT NULL DEFAULT '#2563eb'"),
            ("accent_color", "accent_color VARCHAR(20) NOT NULL DEFAULT '#10b981'"),
            ("app_background", "app_background VARCHAR(20) NOT NULL DEFAULT '#f8fbff'"),
        ):
            _ensure_column(db, SETTINGS_TABLE, col, definition)

        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {CATEGORIES_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                description TEXT DEFAULT NULL,
                sort_order INT NOT NULL DEFAULT 0,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {CATALOG_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                duration_minutes INT NOT NULL DEFAULT 0,
                price DECIMAL(10,2) NOT NULL DEFAULT 0,
                ai_context TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        for col, definition in (
            ("category_id", "category_id INT DEFAULT NULL"),
            ("description", "description TEXT DEFAULT NULL"),
            ("related_service_ids", "related_service_ids VARCHAR(255) DEFAULT NULL"),
            ("image_url", "image_url VARCHAR(500) DEFAULT NULL"),
            ("status", "status VARCHAR(20) NOT NULL DEFAULT 'active'"),
        ):
            _ensure_column(db, CATALOG_TABLE, col, definition)

        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {WORKING_HOURS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                day_of_week TINYINT NOT NULL,
                open_time TIME DEFAULT NULL,
                break_start TIME DEFAULT NULL,
                break_end TIME DEFAULT NULL,
                close_time TIME DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        _ensure_column(db, WORKING_HOURS_TABLE, "is_closed", "is_closed TINYINT(1) NOT NULL DEFAULT 0")

        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {HOLIDAYS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                holiday_date DATE NOT NULL,
                reason VARCHAR(255) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        for col, definition in (
            ("title", "title VARCHAR(255) DEFAULT NULL"),
            ("description", "description TEXT DEFAULT NULL"),
        ):
            _ensure_column(db, HOLIDAYS_TABLE, col, definition)

        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {STAFF_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                role VARCHAR(255) DEFAULT NULL,
                skills TEXT DEFAULT NULL,
                gender VARCHAR(50) DEFAULT NULL,
                ai_context TEXT DEFAULT NULL,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        for col, definition in (
            ("phone", "phone VARCHAR(50) DEFAULT NULL"),
            ("email", "email VARCHAR(255) DEFAULT NULL"),
            ("working_hours", "working_hours TEXT DEFAULT NULL"),
            ("assigned_service_ids", "assigned_service_ids VARCHAR(500) DEFAULT NULL"),
            ("status", "status VARCHAR(20) NOT NULL DEFAULT 'active'"),
            ("photo_url", "photo_url VARCHAR(500) DEFAULT NULL"),
            ("rating", "rating DECIMAL(3,2) NOT NULL DEFAULT 0"),
            ("completed_jobs", "completed_jobs INT NOT NULL DEFAULT 0"),
            ("commission_percent", "commission_percent DECIMAL(5,2) NOT NULL DEFAULT 0"),
            # Per-staff scheduling
            ("department", "department VARCHAR(120) DEFAULT NULL"),
            ("working_days", "working_days VARCHAR(40) DEFAULT NULL"),  # CSV of 0..6 (Mon=0)
            ("work_start", "work_start TIME DEFAULT NULL"),
            ("work_end", "work_end TIME DEFAULT NULL"),
            ("break_start", "break_start TIME DEFAULT NULL"),
            ("break_end", "break_end TIME DEFAULT NULL"),
            ("max_bookings_per_slot", "max_bookings_per_slot INT NOT NULL DEFAULT 1"),
            ("max_hours_per_day", "max_hours_per_day INT NOT NULL DEFAULT 0"),
        ):
            _ensure_column(db, STAFF_TABLE, col, definition)

        # Pivot: staff_services (normalized many-to-many)
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_services (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                staff_id INT NOT NULL,
                service_id INT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_staff_service (staff_id, service_id),
                INDEX idx_user_staff (user_id, staff_id),
                INDEX idx_user_service (user_id, service_id)
            )
            """
        )

        # Per-staff leave / unavailable dates
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS services_staff_leaves (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                staff_id INT NOT NULL,
                leave_type VARCHAR(40) NOT NULL DEFAULT 'vacation',
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                reason VARCHAR(255) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_user_staff_leave (user_id, staff_id),
                INDEX idx_leave_dates (start_date, end_date)
            )
            """
        )

        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {CUSTOMERS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                phone VARCHAR(50) DEFAULT NULL,
                email VARCHAR(255) DEFAULT NULL,
                notes TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
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
            _ensure_column(db, CUSTOMERS_TABLE, col, definition)

        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {BOOKINGS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                service_id INT DEFAULT NULL,
                customer_name VARCHAR(255) NOT NULL,
                phone VARCHAR(50) DEFAULT NULL,
                booking_date DATE NOT NULL,
                start_time TIME NOT NULL,
                end_time TIME NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                notes TEXT DEFAULT NULL,
                price DECIMAL(10,2) NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        for col, definition in (
            ("staff_id", "staff_id INT DEFAULT NULL"),
            ("customer_id", "customer_id INT DEFAULT NULL"),
            ("payment_status", "payment_status VARCHAR(20) NOT NULL DEFAULT 'pending'"),
            ("payment_method", "payment_method VARCHAR(50) DEFAULT NULL"),
            ("chair_number", "chair_number VARCHAR(50) DEFAULT NULL"),
            ("room_id", "room_id INT DEFAULT NULL"),
            ("duration_minutes", "duration_minutes INT DEFAULT NULL"),
        ):
            _ensure_column(db, BOOKINGS_TABLE, col, definition)

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
            f"""
            CREATE TABLE IF NOT EXISTS {PACKAGES_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                price DECIMAL(10,2) NOT NULL DEFAULT 0,
                includes TEXT DEFAULT NULL,
                ai_context TEXT DEFAULT NULL,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {PROMOTIONS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                title VARCHAR(255) NOT NULL,
                description TEXT DEFAULT NULL,
                discount VARCHAR(255) DEFAULT NULL,
                start_date DATE DEFAULT NULL,
                end_date DATE DEFAULT NULL,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {MEMBERSHIPS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                price DECIMAL(10,2) NOT NULL DEFAULT 0,
                benefits TEXT DEFAULT NULL,
                ai_context TEXT DEFAULT NULL,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAQS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                question VARCHAR(500) NOT NULL,
                answer TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {POLICIES_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                title VARCHAR(255) NOT NULL,
                content TEXT NOT NULL,
                policy_type VARCHAR(100) DEFAULT NULL,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {PAYMENTS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                details TEXT DEFAULT NULL,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                sort_order INT NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
    finally:
        db.close()
