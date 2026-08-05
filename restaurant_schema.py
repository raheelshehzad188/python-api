"""Restaurant chatbot schema — isolated tables for the AI ordering system.

Completely independent from Services / Ecommerce / Job Posting tables.
Every table is prefixed `restaurant_`.
"""

from db import Database

SETTINGS_TABLE = "restaurant_settings"
CATEGORIES_TABLE = "restaurant_categories"
MENU_TABLE = "restaurant_menu_items"
VARIATIONS_TABLE = "restaurant_variations"
ADDONS_TABLE = "restaurant_addons"
COMBOS_TABLE = "restaurant_combos"
PROMOTIONS_TABLE = "restaurant_promotions"
CUSTOMERS_TABLE = "restaurant_customers"
ORDERS_TABLE = "restaurant_orders"
ORDER_ITEMS_TABLE = "restaurant_order_items"
WORKING_HOURS_TABLE = "restaurant_working_hours"
HOLIDAYS_TABLE = "restaurant_holidays"
FAQS_TABLE = "restaurant_faqs"
PAYMENTS_TABLE = "restaurant_payment_methods"
TABLES_TABLE = "restaurant_tables"
RESERVATIONS_TABLE = "restaurant_reservations"


def _ensure_column(db, table, column, definition):
    db.cursor.execute(
        "SELECT COUNT(*) AS c FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        [table, column],
    )
    if db.cursor.fetchone()["c"] == 0:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def ensure_restaurant_schema():
    db = Database()
    try:
        # ---- Settings / Business Profile ------------------------------- #
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE} (
                user_id INT NOT NULL PRIMARY KEY,
                currency_code VARCHAR(10) NOT NULL DEFAULT 'PKR',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        for col, definition in (
            ("business_name", "business_name VARCHAR(255) DEFAULT NULL"),
            ("business_category", "business_category VARCHAR(255) DEFAULT NULL"),
            ("about", "about TEXT DEFAULT NULL"),
            ("phone", "phone VARCHAR(50) DEFAULT NULL"),
            ("whatsapp", "whatsapp VARCHAR(50) DEFAULT NULL"),
            ("email", "email VARCHAR(255) DEFAULT NULL"),
            ("address", "address TEXT DEFAULT NULL"),
            ("city", "city VARCHAR(255) DEFAULT NULL"),
            ("logo_url", "logo_url VARCHAR(500) DEFAULT NULL"),
            ("delivery_charges", "delivery_charges DECIMAL(10,2) NOT NULL DEFAULT 0"),
            ("minimum_order", "minimum_order DECIMAL(10,2) NOT NULL DEFAULT 0"),
            ("estimated_delivery_time", "estimated_delivery_time VARCHAR(100) DEFAULT NULL"),
            ("payment_methods", "payment_methods TEXT DEFAULT NULL"),
            ("delivery_rules", "delivery_rules TEXT DEFAULT NULL"),
            ("tax_rate", "tax_rate DECIMAL(5,2) NOT NULL DEFAULT 0"),
            ("service_charge_rate", "service_charge_rate DECIMAL(5,2) NOT NULL DEFAULT 0"),
            ("primary_color", "primary_color VARCHAR(20) NOT NULL DEFAULT '#0ea5e9'"),
            ("secondary_color", "secondary_color VARCHAR(20) NOT NULL DEFAULT '#2563eb'"),
            ("accent_color", "accent_color VARCHAR(20) NOT NULL DEFAULT '#10b981'"),
            ("app_background", "app_background VARCHAR(20) NOT NULL DEFAULT '#f8fbff'"),
        ):
            _ensure_column(db, SETTINGS_TABLE, col, definition)

        # ---- Categories ------------------------------------------------ #
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

        # ---- Menu Items ------------------------------------------------ #
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {MENU_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                category_id INT DEFAULT NULL,
                name VARCHAR(255) NOT NULL,
                description TEXT DEFAULT NULL,
                price DECIMAL(10,2) NOT NULL DEFAULT 0,
                prep_time_minutes INT NOT NULL DEFAULT 0,
                is_available TINYINT(1) NOT NULL DEFAULT 1,
                is_featured TINYINT(1) NOT NULL DEFAULT 0,
                image_url VARCHAR(500) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        # ---- Variations (Small/Medium/Large with price adjustment) ----- #
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {VARIATIONS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                menu_item_id INT DEFAULT NULL,
                name VARCHAR(255) NOT NULL,
                price_adjustment DECIMAL(10,2) NOT NULL DEFAULT 0,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                sort_order INT NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        # ---- Add-ons --------------------------------------------------- #
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {ADDONS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                price DECIMAL(10,2) NOT NULL DEFAULT 0,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                sort_order INT NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        # ---- Combo Deals ----------------------------------------------- #
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {COMBOS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                description TEXT DEFAULT NULL,
                includes TEXT DEFAULT NULL,
                price DECIMAL(10,2) NOT NULL DEFAULT 0,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        # ---- Promotions ------------------------------------------------ #
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

        # ---- Customers ------------------------------------------------- #
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {CUSTOMERS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                phone VARCHAR(50) DEFAULT NULL,
                email VARCHAR(255) DEFAULT NULL,
                address TEXT DEFAULT NULL,
                notes TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        # ---- Orders ---------------------------------------------------- #
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {ORDERS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                customer_id INT DEFAULT NULL,
                customer_name VARCHAR(255) NOT NULL,
                phone VARCHAR(50) DEFAULT NULL,
                order_type VARCHAR(20) NOT NULL DEFAULT 'delivery',
                address TEXT DEFAULT NULL,
                payment_method VARCHAR(100) DEFAULT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'pending',
                subtotal DECIMAL(10,2) NOT NULL DEFAULT 0,
                delivery_charges DECIMAL(10,2) NOT NULL DEFAULT 0,
                total DECIMAL(10,2) NOT NULL DEFAULT 0,
                notes TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_user_status (user_id, status)
            )
            """
        )
        for col, definition in (
            ("order_number", "order_number VARCHAR(30) DEFAULT NULL"),
            ("table_id", "table_id INT DEFAULT NULL"),
            ("table_number", "table_number VARCHAR(50) DEFAULT NULL"),
            ("guests", "guests INT NOT NULL DEFAULT 0"),
            ("delivery_time", "delivery_time VARCHAR(20) DEFAULT NULL"),
            ("pickup_time", "pickup_time VARCHAR(20) DEFAULT NULL"),
            ("tax", "tax DECIMAL(10,2) NOT NULL DEFAULT 0"),
            ("discount", "discount DECIMAL(10,2) NOT NULL DEFAULT 0"),
            ("service_charges", "service_charges DECIMAL(10,2) NOT NULL DEFAULT 0"),
            ("payment_status", "payment_status VARCHAR(20) NOT NULL DEFAULT 'pending'"),
            ("coupon_code", "coupon_code VARCHAR(100) DEFAULT NULL"),
            ("assigned_driver", "assigned_driver VARCHAR(255) DEFAULT NULL"),
            ("assigned_waiter", "assigned_waiter VARCHAR(255) DEFAULT NULL"),
            ("assigned_kitchen_staff", "assigned_kitchen_staff VARCHAR(255) DEFAULT NULL"),
            ("internal_notes", "internal_notes TEXT DEFAULT NULL"),
            ("customer_notes", "customer_notes TEXT DEFAULT NULL"),
            ("source", "source VARCHAR(30) NOT NULL DEFAULT 'manual'"),
            ("email", "email VARCHAR(255) DEFAULT NULL"),
        ):
            _ensure_column(db, ORDERS_TABLE, col, definition)

        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {ORDER_ITEMS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                order_id INT NOT NULL,
                menu_item_id INT DEFAULT NULL,
                item_name VARCHAR(255) NOT NULL,
                variation_name VARCHAR(255) DEFAULT NULL,
                addons TEXT DEFAULT NULL,
                unit_price DECIMAL(10,2) NOT NULL DEFAULT 0,
                quantity INT NOT NULL DEFAULT 1,
                line_total DECIMAL(10,2) NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_order (order_id)
            )
            """
        )
        _ensure_column(db, ORDER_ITEMS_TABLE, "item_notes", "item_notes TEXT DEFAULT NULL")

        # ---- Working Hours --------------------------------------------- #
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
                is_closed TINYINT(1) NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        # ---- Holidays -------------------------------------------------- #
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {HOLIDAYS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                holiday_date DATE NOT NULL,
                title VARCHAR(255) DEFAULT NULL,
                description TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        # ---- FAQs ------------------------------------------------------ #
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

        # ---- Restaurant Tables ----------------------------------------- #
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLES_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                table_number VARCHAR(50) NOT NULL,
                capacity INT NOT NULL DEFAULT 2,
                location VARCHAR(255) DEFAULT NULL,
                floor VARCHAR(100) DEFAULT NULL,
                availability VARCHAR(20) NOT NULL DEFAULT 'available',
                sort_order INT NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_user_tables (user_id)
            )
            """
        )

        # ---- Reservations ---------------------------------------------- #
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {RESERVATIONS_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                reservation_number VARCHAR(30) DEFAULT NULL,
                customer_id INT DEFAULT NULL,
                customer_name VARCHAR(255) NOT NULL,
                phone VARCHAR(50) DEFAULT NULL,
                email VARCHAR(255) DEFAULT NULL,
                guests INT NOT NULL DEFAULT 2,
                reservation_date DATE NOT NULL,
                reservation_time TIME NOT NULL,
                table_id INT DEFAULT NULL,
                table_number VARCHAR(50) DEFAULT NULL,
                occasion VARCHAR(50) DEFAULT NULL,
                special_notes TEXT DEFAULT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_user_res_date (user_id, reservation_date),
                INDEX idx_table_date (table_id, reservation_date)
            )
            """
        )

        # ---- Payment Methods ------------------------------------------- #
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
