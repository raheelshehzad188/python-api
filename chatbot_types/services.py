from services_tools import run_tool, TOOL_NAMES


class Services:
    """Production AI receptionist for any appointment / service business.

    Completely isolated from Ecommerce and Job Posting.
    All industry facts come from this user's DB + generated cache.
    """

    name = "Services"
    label = "Services"

    def __init__(self, db=None, user_id=None, meta=None):
        self.db = db
        self.user_id = user_id
        self.meta = meta or {}

    def cache_payload(self):
        """Auto-generated business knowledge for Gemini context cache."""
        if not self.db or not self.user_id:
            return ""

        parts = ["# Business Knowledge (ONLY source of truth)"]

        try:
            from services_settings import _ensure_user_defaults

            _ensure_user_defaults(self.db, self.user_id)
        except Exception:
            pass

        settings = {}
        currency = "USD"
        try:
            settings = self.db.row("services_settings", {"user_id": self.user_id}) or {}
            currency = settings.get("currency_code") or "USD"
            parts.append("## Business Information")
            for label, key in (
                ("Name", "business_name"),
                ("Category", "business_category"),
                ("About", "about"),
                ("Phone", "phone"),
                ("Email", "email"),
                ("Website", "website"),
                ("Address", "address"),
                ("City", "city"),
                ("Maps", "maps_link"),
                ("Parking", "parking_info"),
                ("Booking rules", "booking_rules"),
            ):
                val = (settings.get(key) or "").strip()
                if val:
                    parts.append(f"- {label}: {val}")
            parts.append(f"- Currency: {currency}")
        except Exception:
            parts.append("## Business Information: unavailable")

        # Categories
        try:
            cats = self.db.select("services_categories", {"user_id": self.user_id}) or []
            cats = [c for c in cats if c.get("is_active", 1)]
            if cats:
                parts.append("## Categories")
                for c in cats:
                    parts.append(f"  - id={c.get('id')} | {c.get('name')}: {c.get('description') or '-'}")
            else:
                parts.append("## Categories: none")
        except Exception:
            parts.append("## Categories: none")

        # Working hours
        try:
            hours = self.db.select("services_working_hours", {"user_id": self.user_id}) or []
            by_day = {int(r.get("day_of_week") or 0): r for r in hours}
            parts.append("## Working Hours (Mon=0 .. Sun=6) — FAQ only; slots via get_available_slots")
            for day in range(7):
                r = by_day.get(day) or {}
                if r.get("is_closed"):
                    parts.append(f"  - Day {day}: CLOSED")
                else:
                    parts.append(
                        f"  - Day {day}: open={r.get('open_time') or '-'}, "
                        f"break={r.get('break_start') or '-'}-{r.get('break_end') or '-'}, "
                        f"close={r.get('close_time') or '-'}"
                    )
        except Exception:
            parts.append("## Working Hours: unavailable")

        # Holidays
        try:
            holidays = self.db.select("services_holidays", {"user_id": self.user_id}) or []
            if holidays:
                parts.append("## Holidays")
                for h in holidays:
                    d = h.get("holiday_date")
                    date_str = d.isoformat() if hasattr(d, "isoformat") else str(d or "")
                    title = (h.get("title") or h.get("reason") or "").strip()
                    desc = (h.get("description") or "").strip()
                    line = f"  - {date_str}"
                    if title:
                        line += f" | {title}"
                    if desc:
                        line += f" — {desc}"
                    parts.append(line)
            else:
                parts.append("## Holidays: none")
        except Exception:
            parts.append("## Holidays: none")

        # Services catalog
        try:
            catalog = self.db.select("services_catalog", {"user_id": self.user_id}) or []
            active = [s for s in catalog if (s.get("status") or "active") != "inactive"]
            if active:
                parts.append("## Services")
                for s in active:
                    desc = (s.get("description") or s.get("ai_context") or "").strip()
                    line = (
                        f"  - id={s.get('id')} | {s.get('name')}: "
                        f"duration={s.get('duration_minutes') or 0} min, "
                        f"price={s.get('price') or 0} {currency}"
                    )
                    if s.get("category_id"):
                        line += f", category_id={s.get('category_id')}"
                    if desc:
                        line += f". Description: {desc}"
                    if s.get("related_service_ids"):
                        line += f". Related: {s.get('related_service_ids')}"
                    parts.append(line)
            else:
                parts.append("## Services: none configured")
        except Exception:
            parts.append("## Services: unavailable")

        # Staff / packages / promotions / memberships / FAQs / policies / payments
        sections = (
            (
                "Staff",
                "services_staff",
                lambda r: (
                    f"  - id={r.get('id')} | {r.get('name')}: role={r.get('role') or '-'}, "
                    f"services={r.get('assigned_service_ids') or '-'}, "
                    f"hours={r.get('working_hours') or '-'}"
                ),
                lambda r: (r.get("status") or "active") != "inactive" and r.get("is_active", 1),
            ),
            (
                "Packages",
                "services_packages",
                lambda r: (
                    f"  - {r.get('name')}: price={r.get('price')}, includes={r.get('includes') or '-'} "
                    "(offer ONLY if customer asks about packages — never auto-apply when they name services)"
                ),
                lambda r: r.get("is_active", 1),
            ),
            (
                "Promotions",
                "services_promotions",
                lambda r: f"  - {r.get('title')}: {r.get('discount') or ''} — {r.get('description') or ''}",
                lambda r: r.get("is_active", 1),
            ),
            (
                "Memberships",
                "services_memberships",
                lambda r: f"  - {r.get('name')}: price={r.get('price')}, benefits={r.get('benefits') or '-'}",
                lambda r: r.get("is_active", 1),
            ),
            (
                "FAQs",
                "services_faqs",
                lambda r: f"  - Q: {r.get('question')} | A: {r.get('answer')}",
                lambda r: True,
            ),
            (
                "Policies",
                "services_policies",
                lambda r: f"  - [{r.get('policy_type') or 'policy'}] {r.get('title')}: {r.get('content')}",
                lambda r: r.get("is_active", 1),
            ),
            (
                "Payment Methods",
                "services_payment_methods",
                lambda r: f"  - {r.get('name')}: {r.get('details') or '-'}",
                lambda r: r.get("is_active", 1),
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

        parts.append(
            "## AI / Tool Rules\n"
            "- Business Knowledge above is the ONLY source of truth.\n"
            "- NEVER invent services, prices, duration, staff, policies, packages, promotions, or dates.\n"
            "- NEVER invent combo/bundle/package names when the customer asks for multiple services.\n"
            "- Packages: offer ONLY if customer asks about packages — never auto-apply.\n"
            "- Multiple services: ALWAYS call quote_services first; list each + totals; never merge into a package.\n"
            "- NEVER resolve today/tomorrow/kal/Monday yourself — call resolve_date or pass the phrase to get_available_slots / book_appointment.\n"
            "- NEVER calculate availability or invent slots. Use get_available_slots only; never offer a slot ending after closes_at.\n"
            "- NEVER decide cancel policy. cancel_booking returns {allowed, reason, booking} — explain that only.\n"
            "- After book/cancel/reschedule: confirm ONLY using booking.date / booking.from / booking.to / booking.service from tool_result.\n"
            f"- Allowed tools: {', '.join(TOOL_NAMES)}.\n"
            "- Mirror the customer's language. Keep replies short.\n"
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
                    "result": {"success": False, "error": "Services context missing"},
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
