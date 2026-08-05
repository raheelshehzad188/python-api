"""Idempotent backfill for the per-staff scheduling system.

For every Services business it:
  - Ensures each staff has structured working_days / work_start / work_end.
  - Ensures max_bookings_per_slot >= 1 and a normalized status.
  - Populates the staff_services pivot from assigned_service_ids, or, when a
    staff has no assignments, distributes catalog services so every service is
    covered by at least a few staff (round-robin).

Safe to run multiple times. Usage:
    python3 migrate_staff_scheduling.py            # all services users
    python3 migrate_staff_scheduling.py 12         # one user
"""

from __future__ import annotations

import sys

from db import Database
from services_schema import ensure_services_schema

DEFAULT_DAYS = "0,1,2,3,4,5"  # Mon-Sat
DEFAULT_START = "09:00:00"
DEFAULT_END = "18:00:00"
DEFAULT_BREAK_START = "13:00:00"
DEFAULT_BREAK_END = "14:00:00"

VALID_STATUS = {"available", "busy", "leave", "inactive", "active"}


def _services_user_ids(db):
    rows = db.select("services_settings", {}) or []
    return [r["user_id"] for r in rows]


def _parse_csv_ids(raw):
    out = []
    for p in (raw or "").split(","):
        p = p.strip()
        if p.isdigit():
            out.append(int(p))
    return out


def migrate_user(db, user_id):
    staff = db.select("services_staff", {"user_id": user_id}) or []
    services = db.select("services_catalog", {"user_id": user_id}) or []
    service_ids = [s["id"] for s in services]
    if not staff:
        return {"user_id": user_id, "staff": 0, "assigned": 0}

    # Which staff already have assignments (csv or pivot)?
    db.cursor.execute(
        "SELECT staff_id, COUNT(*) c FROM staff_services WHERE user_id=%s GROUP BY staff_id",
        [user_id],
    )
    pivot_counts = {r["staff_id"]: r["c"] for r in db.cursor.fetchall()}

    assigned_total = 0
    rr = 0  # round-robin cursor for unassigned staff
    n_services = len(service_ids)

    for idx, s in enumerate(staff):
        sid = s["id"]
        updates = {}

        # Schedule defaults
        if not s.get("working_days"):
            updates["working_days"] = DEFAULT_DAYS
        if not s.get("work_start"):
            updates["work_start"] = DEFAULT_START
        if not s.get("work_end"):
            updates["work_end"] = DEFAULT_END
        if not s.get("break_start"):
            updates["break_start"] = DEFAULT_BREAK_START
        if not s.get("break_end"):
            updates["break_end"] = DEFAULT_BREAK_END
        if not s.get("max_bookings_per_slot") or int(s.get("max_bookings_per_slot") or 0) < 1:
            updates["max_bookings_per_slot"] = 1

        status = (s.get("status") or "active").lower()
        if status not in VALID_STATUS:
            status = "available"
            updates["status"] = status
        elif status == "active":
            updates["status"] = "available"

        # Resolve this staff's service ids
        ids = _parse_csv_ids(s.get("assigned_service_ids"))
        has_pivot = pivot_counts.get(sid, 0) > 0

        if not ids and not has_pivot and n_services:
            # Distribute: give each staff a rotating slice of services (~1/3 of catalog, min 3)
            take = max(3, n_services // 3)
            chosen = [service_ids[(rr + k) % n_services] for k in range(take)]
            rr += take
            ids = sorted(set(chosen))
            updates["assigned_service_ids"] = ",".join(str(i) for i in ids)

        if updates:
            db.update("services_staff", updates, {"id": sid, "user_id": user_id})

        # Sync pivot (only add missing; keep idempotent)
        if ids and not has_pivot:
            for svc_id in ids:
                try:
                    db.insert("staff_services", {
                        "user_id": user_id,
                        "staff_id": sid,
                        "service_id": svc_id,
                    })
                    assigned_total += 1
                except Exception:
                    pass

    return {"user_id": user_id, "staff": len(staff), "assigned": assigned_total}


def main():
    ensure_services_schema()
    db = Database()
    try:
        if len(sys.argv) > 1 and sys.argv[1].isdigit():
            targets = [int(sys.argv[1])]
        else:
            targets = _services_user_ids(db)
        for uid in targets:
            print(migrate_user(db, uid))
    finally:
        db.close()
    print("MIGRATE_DONE")


if __name__ == "__main__":
    main()
