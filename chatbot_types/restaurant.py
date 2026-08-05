from restaurant_tools import run_tool, TOOL_NAMES


class Restaurant:
    """Production AI ordering assistant for a restaurant / food business.

    Completely isolated from General, Ecommerce, Job Posting and Services.
    Every fact (menu, prices, variations, add-ons, delivery rules) comes from
    this user's DB + generated cache. Totals are always computed by backend
    tools, never by the model.
    """

    name = "Restaurant"
    label = "Restaurant"

    def __init__(self, db=None, user_id=None, meta=None):
        self.db = db
        self.user_id = user_id
        self.meta = meta or {}

    def cache_payload(self):
        """Auto-generated restaurant knowledge for the Gemini context cache."""
        if not self.db or not self.user_id:
            return ""

        parts = ["# Restaurant Knowledge (ONLY source of truth)"]

        try:
            from restaurant_settings import _ensure_user_defaults

            _ensure_user_defaults(self.db, self.user_id)
        except Exception:
            pass

        currency = "PKR"

        # Business profile
        try:
            s = self.db.row("restaurant_settings", {"user_id": self.user_id}) or {}
            currency = s.get("currency_code") or "PKR"
            parts.append("## Business Information")
            for label, key in (
                ("Name", "business_name"),
                ("Category", "business_category"),
                ("About", "about"),
                ("Phone", "phone"),
                ("WhatsApp", "whatsapp"),
                ("Email", "email"),
                ("Address", "address"),
                ("City", "city"),
                ("Estimated Delivery", "estimated_delivery_time"),
                ("Payment Methods", "payment_methods"),
                ("Delivery Rules", "delivery_rules"),
            ):
                val = (str(s.get(key) or "")).strip()
                if val:
                    parts.append(f"- {label}: {val}")
            parts.append(f"- Currency: {currency}")
            parts.append(f"- Delivery Charges: {float(s.get('delivery_charges') or 0)} {currency}")
            parts.append(f"- Minimum Order: {float(s.get('minimum_order') or 0)} {currency}")
        except Exception:
            parts.append("## Business Information: unavailable")

        # Categories
        cat_names = {}
        try:
            cats = [c for c in (self.db.select("restaurant_categories", {"user_id": self.user_id}) or []) if c.get("is_active", 1)]
            cats = sorted(cats, key=lambda c: int(c.get("sort_order") or 0))
            cat_names = {c["id"]: c.get("name") or "" for c in cats}
            if cats:
                parts.append("## Categories")
                for c in cats:
                    parts.append(f"  - {c.get('name')}: {c.get('description') or '-'}")
            else:
                parts.append("## Categories: none")
        except Exception:
            parts.append("## Categories: none")

        # Menu items + variations
        try:
            items = [m for m in (self.db.select("restaurant_menu_items", {"user_id": self.user_id}) or []) if m.get("is_available", 1)]
            if items:
                parts.append("## Menu (prices are base price; add variation adjustment + add-ons)")
                for m in items:
                    cat = cat_names.get(m.get("category_id"), "Other")
                    line = (
                        f"  - id={m.get('id')} | {m.get('name')} [{cat}]: "
                        f"{float(m.get('price') or 0)} {currency}"
                    )
                    if m.get("prep_time_minutes"):
                        line += f", prep={m.get('prep_time_minutes')} min"
                    if m.get("is_featured"):
                        line += ", FEATURED"
                    desc = (m.get("description") or "").strip()
                    if desc:
                        line += f". {desc}"
                    variations = self.db.select(
                        "restaurant_variations", {"user_id": self.user_id, "menu_item_id": m["id"]}
                    ) or []
                    variations = [v for v in variations if v.get("is_active", 1)]
                    if variations:
                        vtxt = "; ".join(
                            f"{v.get('name')} ({'+' if float(v.get('price_adjustment') or 0) >= 0 else ''}{float(v.get('price_adjustment') or 0)})"
                            for v in variations
                        )
                        line += f". Variations: {vtxt}"
                    parts.append(line)
            else:
                parts.append("## Menu: none configured")
        except Exception:
            parts.append("## Menu: unavailable")

        # Add-ons
        try:
            addons = [a for a in (self.db.select("restaurant_addons", {"user_id": self.user_id}) or []) if a.get("is_active", 1)]
            if addons:
                parts.append("## Add-ons")
                for a in addons:
                    parts.append(f"  - {a.get('name')}: {float(a.get('price') or 0)} {currency}")
            else:
                parts.append("## Add-ons: none")
        except Exception:
            parts.append("## Add-ons: none")

        # Combo deals / promotions / working hours / holidays / payments / faqs
        sections = (
            (
                "Combo Deals",
                "restaurant_combos",
                lambda r: f"  - {r.get('name')}: {float(r.get('price') or 0)} {currency} — {r.get('includes') or r.get('description') or '-'}",
                lambda r: r.get("is_active", 1),
            ),
            (
                "Promotions",
                "restaurant_promotions",
                lambda r: f"  - {r.get('title')}: {r.get('discount') or ''} {('— ' + r.get('description')) if r.get('description') else ''}",
                lambda r: r.get("is_active", 1),
            ),
            (
                "Payment Methods",
                "restaurant_payment_methods",
                lambda r: f"  - {r.get('name')}: {r.get('details') or '-'}",
                lambda r: r.get("is_active", 1),
            ),
            (
                "FAQs",
                "restaurant_faqs",
                lambda r: f"  - Q: {r.get('question')} | A: {r.get('answer')}",
                lambda r: True,
            ),
        )
        for title, table, fmt, pred in sections:
            try:
                rows = [r for r in (self.db.select(table, {"user_id": self.user_id}) or []) if pred(r)]
                if rows:
                    parts.append(f"## {title}")
                    for r in rows:
                        parts.append(fmt(r))
                else:
                    parts.append(f"## {title}: none")
            except Exception:
                parts.append(f"## {title}: none")

        # Working hours
        try:
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            hours = self.db.select("restaurant_working_hours", {"user_id": self.user_id}) or []
            by_day = {int(r.get("day_of_week") or 0): r for r in hours}
            parts.append("## Working Hours")
            for day in range(7):
                r = by_day.get(day) or {}
                if r.get("is_closed"):
                    parts.append(f"  - {days[day]}: CLOSED")
                else:
                    parts.append(f"  - {days[day]}: {r.get('open_time') or '-'} to {r.get('close_time') or '-'}")
        except Exception:
            parts.append("## Working Hours: unavailable")

        try:
            holidays = self.db.select("restaurant_holidays", {"user_id": self.user_id}) or []
            if holidays:
                parts.append("## Holidays (closed)")
                for h in holidays:
                    d = h.get("holiday_date")
                    date_str = d.isoformat() if hasattr(d, "isoformat") else str(d or "")
                    line = f"  - {date_str}"
                    if h.get("title"):
                        line += f" | {h.get('title')}"
                    if h.get("description"):
                        line += f" — {h.get('description')}"
                    parts.append(line)
            else:
                parts.append("## Holidays: none")
        except Exception:
            parts.append("## Holidays: none")

        parts.append(
            "## AI / Tool Rules\n"
            "- Restaurant Knowledge above is the ONLY source of truth.\n"
            "- NEVER invent menu items, prices, variations, add-ons, delivery charges, or combos.\n"
            "- NEVER calculate order totals yourself — place_order returns the authoritative total.\n"
            "- Follow the ordering flow: item -> variation (if any) -> add-ons -> quantity -> delivery/pickup -> address (delivery only) -> payment method -> confirm -> place_order.\n"
            "- Delivery charges and minimum order come from get_business_info / place_order only.\n"
            "- NEVER confirm an order before place_order succeeds. Confirm ONLY using the order object from tool_result.\n"
            "- cancel_order returns {allowed, reason, order} — explain that only; never decide cancellation policy yourself.\n"
            "- track_order returns the current status — report it exactly.\n"
            f"- Allowed tools: {', '.join(TOOL_NAMES)}.\n"
            "- Mirror the customer's language. Keep replies short and friendly.\n"
            "- Remember conversation context; do not re-ask known details."
        )
        return "\n".join(parts)

    def process(self, reply_json):
        reply_json = reply_json or {}
        rtype = (reply_json.get("type") or "").strip().lower()

        if rtype == "tool":
            tool_name = (
                reply_json.get("name")
                or reply_json.get("tool")
                or reply_json.get("tool_name")
                or ""
            ).strip()
            args = reply_json.get("args") or reply_json.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}
            if not self.db or not self.user_id:
                return {
                    "type": "tool_result",
                    "tool": tool_name,
                    "result": {"success": False, "error": "Restaurant context missing"},
                }
            return {
                "type": "tool_result",
                "tool": tool_name,
                "args": args,
                "result": run_tool(self.db, self.user_id, tool_name, args),
            }

        if rtype == "message":
            return {"type": "message", "message": (reply_json.get("message") or "").strip()}

        for key in ("message", "text", "reply", "content", "body"):
            val = reply_json.get(key)
            if isinstance(val, str) and val.strip():
                return {"type": "message", "message": val.strip()}

        if isinstance(reply_json, str) and reply_json.strip():
            return {"type": "message", "message": reply_json.strip()}

        return {"type": "message", "message": ""}
