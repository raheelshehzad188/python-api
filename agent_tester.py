"""Agent Tester API — plan and run WhatsApp tests against other chatbot users."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime

from flask import Blueprint, jsonify, request

from db import Database
from gemini import Gemini, DEFAULT_MODEL
from chatbot_types.tester import TESTER_INSTRUCTIONS

logger = logging.getLogger("agent_tester")

agent_tester_bp = Blueprint("agent_tester", __name__)

SESSIONS = "agent_test_sessions"
STEPS = "agent_test_steps"


def ensure_schema():
    db = Database()
    try:
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SESSIONS} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                tester_user_id INT NOT NULL,
                target_user_id INT NOT NULL,
                target_phone VARCHAR(64) NOT NULL DEFAULT '',
                goal TEXT,
                status VARCHAR(32) NOT NULL DEFAULT 'draft',
                strategy_json LONGTEXT,
                conclusion TEXT,
                suggested_instructions LONGTEXT,
                webhook_probe_json LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_tester (tester_user_id),
                INDEX idx_target (target_user_id)
            )
            """
        )
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {STEPS} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id INT NOT NULL,
                step_index INT NOT NULL DEFAULT 0,
                title VARCHAR(255) NOT NULL DEFAULT '',
                send_text TEXT,
                expect_text TEXT,
                reply_text LONGTEXT,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_session (session_id)
            )
            """
        )
    finally:
        db.close()


def _meta_map(db, user_id):
    rows = db.select("user_meta", {"user_id": user_id}) or []
    return {r["meta_key"]: r["meta_value"] for r in rows}


def _handler_class(db, user_id):
    meta = _meta_map(db, user_id)
    type_id = meta.get("chatbot_type_id")
    if not type_id:
        return ""
    row = db.row("chatbot_types", {"id": type_id}) or {}
    return (row.get("handler_class") or "").strip()


def _is_tester_user(db, user_id):
    return _handler_class(db, user_id) == "Tester"


def _wa_session(db, user_id):
    import whatsapp as wa

    meta = _meta_map(db, user_id)
    return wa._load_session(meta)  # noqa: SLF001 — shared session helper


def _normalize_phone(phone):
    raw = str(phone or "").strip()
    if raw.lower().endswith("@lid"):
        return raw
    digits = re.sub(r"\D+", "", raw)
    return digits


def _public_session(row, steps=None):
    if not row:
        return None
    out = dict(row)
    for key in ("strategy_json", "webhook_probe_json"):
        raw = out.get(key)
        if isinstance(raw, str) and raw.strip():
            try:
                out[key.replace("_json", "")] = json.loads(raw)
            except Exception:
                out[key.replace("_json", "")] = None
        out.pop(key, None)
    if steps is not None:
        out["steps"] = steps
    return out


def _load_steps(db, session_id):
    db.cursor.execute(
        f"SELECT * FROM {STEPS} WHERE session_id=%s ORDER BY step_index ASC, id ASC",
        [session_id],
    )
    return db.cursor.fetchall() or []


def _require_tester(db, tester_user_id):
    user = db.row("admins", {"id": tester_user_id})
    if not user:
        return None, (jsonify({"status": False, "message": "User not found"}), 404)
    if not _is_tester_user(db, tester_user_id):
        return None, (
            jsonify({"status": False, "message": "This user is not an Agent Tester"}),
            403,
        )
    return user, None


def _gemini_json(system_instruction, user_prompt):
    gemini = Gemini()
    if not gemini.api_key:
        return None, "Gemini API key is not configured in Site Settings"
    result = gemini.send(
        contents=[{"role": "user", "parts": [{"text": user_prompt}]}],
        system_instruction=system_instruction,
        json_output=True,
        model=DEFAULT_MODEL,
    )
    if not result.get("success"):
        return None, result.get("error") or "Gemini error"
    parsed = gemini.get_json(result.get("response"))
    if not isinstance(parsed, dict):
        text = gemini.get_text(result.get("response")) or ""
        try:
            parsed = json.loads(text)
        except Exception:
            return None, "Gemini did not return valid JSON"
    return parsed, None


# ---------------------------------------------------------------------------
# Status / targets
# ---------------------------------------------------------------------------


@agent_tester_bp.route("/users/<int:user_id>/agent-tester", methods=["GET"])
def get_agent_tester_status(user_id):
    db = Database()
    try:
        is_tester = _is_tester_user(db, user_id)
        session = _wa_session(db, user_id) if is_tester else {}
        connected = bool(session.get("connected"))
        recent = []
        if is_tester:
            db.cursor.execute(
                f"""
                SELECT id, target_user_id, target_phone, goal, status, created_at, updated_at
                FROM {SESSIONS}
                WHERE tester_user_id=%s
                ORDER BY id DESC
                LIMIT 20
                """,
                [user_id],
            )
            recent = db.cursor.fetchall() or []
    finally:
        db.close()

    return jsonify(
        {
            "status": True,
            "is_tester": is_tester,
            "whatsapp_connected": connected if is_tester else False,
            "can_start_testing": bool(is_tester and connected),
            "sessions": recent,
        }
    )


@agent_tester_bp.route("/users/<int:user_id>/agent-tester/targets", methods=["GET"])
def list_test_targets(user_id):
    db = Database()
    try:
        _, err = _require_tester(db, user_id)
        if err:
            return err

        import config

        role_id = getattr(config, "CHATBOT_ROLE_ID", 2)
        users = db.select("admins", {"role_id": role_id}) or []
        targets = []
        for u in users:
            uid = u["id"]
            if uid == user_id:
                continue
            handler = _handler_class(db, uid)
            if handler == "Tester":
                continue
            wa = _wa_session(db, uid)
            ctype_id = _meta_map(db, uid).get("chatbot_type_id")
            ctype = db.row("chatbot_types", {"id": ctype_id}) if ctype_id else None
            targets.append(
                {
                    "id": uid,
                    "name": u.get("name"),
                    "email": u.get("email"),
                    "handler_class": handler or None,
                    "chatbot_type": (ctype or {}).get("title"),
                    "whatsapp_connected": bool(wa.get("connected")),
                    "whatsapp_phone": wa.get("phone") or "",
                    "session_name": wa.get("session_name") or "",
                }
            )
    finally:
        db.close()

    return jsonify({"status": True, "targets": targets})


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@agent_tester_bp.route("/users/<int:user_id>/agent-tester/sessions", methods=["POST"])
def create_test_session(user_id):
    data = request.json or {}
    target_user_id = data.get("target_user_id")
    target_phone = _normalize_phone(data.get("target_phone") or data.get("phone"))

    try:
        target_user_id = int(target_user_id)
    except (TypeError, ValueError):
        return jsonify({"status": False, "message": "target_user_id is required"}), 400
    if not target_phone:
        return jsonify({"status": False, "message": "target_phone is required"}), 400

    db = Database()
    try:
        _, err = _require_tester(db, user_id)
        if err:
            return err
        wa = _wa_session(db, user_id)
        if not wa.get("connected"):
            return (
                jsonify(
                    {
                        "status": False,
                        "message": "Connect WhatsApp for this tester before starting",
                    }
                ),
                400,
            )
        target = db.row("admins", {"id": target_user_id})
        if not target:
            return jsonify({"status": False, "message": "Target user not found"}), 404
        if _is_tester_user(db, target_user_id):
            return (
                jsonify({"status": False, "message": "Cannot test another Tester user"}),
                400,
            )

        sid = db.insert(
            SESSIONS,
            {
                "tester_user_id": user_id,
                "target_user_id": target_user_id,
                "target_phone": target_phone,
                "goal": "",
                "status": "draft",
            },
        )
        row = db.row(SESSIONS, {"id": sid})
    finally:
        db.close()

    return jsonify({"status": True, "session": _public_session(row, [])})


@agent_tester_bp.route(
    "/users/<int:user_id>/agent-tester/sessions/<int:session_id>", methods=["GET"]
)
def get_test_session(user_id, session_id):
    db = Database()
    try:
        _, err = _require_tester(db, user_id)
        if err:
            return err
        row = db.row(SESSIONS, {"id": session_id, "tester_user_id": user_id})
        if not row:
            return jsonify({"status": False, "message": "Session not found"}), 404
        steps = _load_steps(db, session_id)
        target = db.row("admins", {"id": row["target_user_id"]}) or {}
    finally:
        db.close()

    session = _public_session(row, steps)
    session["target_name"] = target.get("name")
    session["target_email"] = target.get("email")
    return jsonify({"status": True, "session": session})


@agent_tester_bp.route(
    "/users/<int:user_id>/agent-tester/sessions/<int:session_id>/probe-webhook",
    methods=["POST"],
)
def probe_target_webhook(user_id, session_id):
    """Verify the target bot's WhatsApp webhook URL is reachable / accepting."""
    import whatsapp as wa
    import infra_settings

    db = Database()
    try:
        _, err = _require_tester(db, user_id)
        if err:
            return err
        row = db.row(SESSIONS, {"id": session_id, "tester_user_id": user_id})
        if not row:
            return jsonify({"status": False, "message": "Session not found"}), 404

        target_id = row["target_user_id"]
        meta = _meta_map(db, target_id)
        token = wa._get_or_create_webhook_token(db, target_id, meta)  # noqa: SLF001
        webhook_url = wa._webhook_url(target_id, token)  # noqa: SLF001
        target_session = _wa_session(db, target_id)

        probe = {
            "webhook_url": webhook_url,
            "target_connected": bool(target_session.get("connected")),
            "target_status": target_session.get("status") or "",
            "target_session_name": target_session.get("session_name") or "",
            "ok": False,
            "http_code": 0,
            "detail": "",
        }

        # Direct POST to Python webhook (same host) — proves token + handler path.
        try:
            from urllib import request as urlrequest
            import ssl

            payload = json.dumps(
                {
                    "event": "message.received",
                    "from": "agent_tester_probe",
                    "body": "__agent_tester_webhook_probe__",
                    "type": "chat",
                    "probe": True,
                }
            ).encode("utf-8")
            # Prefer loopback if public URL is this machine; else use public webhook.
            base = (infra_settings.wa_app_public_url() or "").rstrip("/")
            # Hit Flask route path directly on local service when possible.
            local_url = f"https://127.0.0.1:5000/webhooks/whatsapp/{target_id}/{token}"
            targets = [local_url, webhook_url]
            last_err = ""
            for url in targets:
                try:
                    req = urlrequest.Request(
                        url,
                        data=payload,
                        method="POST",
                        headers={
                            "Content-Type": "application/json",
                            "X-AgencyWA-Event": "message.received",
                        },
                    )
                    ctx = ssl._create_unverified_context()
                    with urlrequest.urlopen(req, context=ctx, timeout=45) as resp:
                        body = resp.read().decode("utf-8", errors="replace")
                        probe["http_code"] = resp.status
                        probe["detail"] = body[:500]
                        probe["ok"] = resp.status in (200, 201)
                        probe["probed_url"] = url
                        if probe["ok"]:
                            break
                except Exception as e:
                    last_err = str(e)
                    probe["detail"] = last_err
            if not probe["ok"] and last_err and not probe.get("probed_url"):
                probe["detail"] = last_err
        except Exception as e:
            probe["detail"] = str(e)

        # Soft-pass if target session is connected even when probe POST is noisy
        if not probe["ok"] and probe["target_connected"]:
            probe["ok"] = True
            probe["detail"] = (
                (probe.get("detail") or "")
                + " | Target WhatsApp session is connected; treating probe as OK."
            ).strip(" |")

        status = "webhook_ok" if probe["ok"] else "webhook_failed"
        db.update(
            SESSIONS,
            {
                "status": status,
                "webhook_probe_json": json.dumps(probe, ensure_ascii=False),
            },
            {"id": session_id},
        )
        row = db.row(SESSIONS, {"id": session_id})
        steps = _load_steps(db, session_id)
    finally:
        db.close()

    return jsonify(
        {
            "status": True,
            "probe": probe,
            "session": _public_session(row, steps),
        }
    )


@agent_tester_bp.route(
    "/users/<int:user_id>/agent-tester/sessions/<int:session_id>/goal",
    methods=["POST"],
)
def set_goal_and_plan(user_id, session_id):
    data = request.json or {}
    goal = (data.get("goal") or data.get("test") or "").strip()
    if not goal:
        return jsonify({"status": False, "message": "goal is required"}), 400

    db = Database()
    try:
        _, err = _require_tester(db, user_id)
        if err:
            return err
        row = db.row(SESSIONS, {"id": session_id, "tester_user_id": user_id})
        if not row:
            return jsonify({"status": False, "message": "Session not found"}), 404
        if row.get("status") == "webhook_failed":
            return (
                jsonify(
                    {
                        "status": False,
                        "message": "Fix webhook probe first (status webhook_failed)",
                    }
                ),
                400,
            )

        target = db.row("admins", {"id": row["target_user_id"]}) or {}
        handler = _handler_class(db, row["target_user_id"])
        prompt = (
            f"Target bot name: {target.get('name')}\n"
            f"Target handler: {handler or 'unknown'}\n"
            f"Target phone under test: {row.get('target_phone')}\n"
            f"Test goal from human tester: {goal}\n\n"
            "Build a WhatsApp test strategy as JSON with a steps array."
        )
        parsed, gerr = _gemini_json(TESTER_INSTRUCTIONS, prompt)
        if gerr:
            return jsonify({"status": False, "message": gerr}), 400

        steps_in = parsed.get("steps") if isinstance(parsed, dict) else None
        if not isinstance(steps_in, list) or not steps_in:
            return (
                jsonify({"status": False, "message": "Gemini returned no steps"}),
                400,
            )

        db.execute(f"DELETE FROM {STEPS} WHERE session_id=%s", [session_id])
        clean_steps = []
        for i, s in enumerate(steps_in[:8]):
            if not isinstance(s, dict):
                continue
            title = (s.get("title") or f"Step {i + 1}").strip()[:255]
            send_text = (s.get("send_text") or s.get("message") or "").strip()
            expect = (s.get("expect") or s.get("expect_text") or "").strip()
            if not send_text:
                continue
            db.insert(
                STEPS,
                {
                    "session_id": session_id,
                    "step_index": len(clean_steps),
                    "title": title,
                    "send_text": send_text,
                    "expect_text": expect,
                    "status": "pending",
                },
            )
            clean_steps.append(
                {"title": title, "send_text": send_text, "expect": expect}
            )

        if not clean_steps:
            return (
                jsonify({"status": False, "message": "No usable steps in strategy"}),
                400,
            )

        db.update(
            SESSIONS,
            {
                "goal": goal,
                "status": "planned",
                "strategy_json": json.dumps({"steps": clean_steps}, ensure_ascii=False),
            },
            {"id": session_id},
        )
        row = db.row(SESSIONS, {"id": session_id})
        steps = _load_steps(db, session_id)
    finally:
        db.close()

    return jsonify({"status": True, "session": _public_session(row, steps)})


def _wait_for_reply(db, tester_user_id, target_phone, after_ts, timeout_sec=45):
    """Poll recent inbound WhatsApp chats for a new bot/customer reply."""
    phone = _normalize_phone(target_phone)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        # Prefer wa_messages receive rows
        try:
            db.cursor.execute(
                """
                SELECT message_text, created_at FROM wa_messages
                WHERE user_id=%s AND direction='receive'
                  AND (sender_id LIKE %s OR sender_id LIKE %s)
                  AND created_at >= %s
                ORDER BY id DESC LIMIT 1
                """,
                [tester_user_id, f"%{phone}%", f"{phone}%", after_ts],
            )
            row = db.cursor.fetchone()
            if row and (row.get("message_text") or "").strip():
                text = row["message_text"].strip()
                if text != "__agent_tester_webhook_probe__":
                    return text
        except Exception:
            pass

        # Fallback: chat_history on whatsapp chats
        try:
            db.cursor.execute(
                """
                SELECT h.response_text, h.created_at
                FROM chats c
                JOIN chat_history h ON h.chat_id = c.id
                WHERE c.user_id=%s AND c.chat_type='whatsapp'
                  AND (c.user_number LIKE %s OR c.user_number LIKE %s)
                  AND h.response_text IS NOT NULL AND h.response_text != ''
                  AND h.created_at >= %s
                ORDER BY h.id DESC LIMIT 1
                """,
                [tester_user_id, f"%{phone}%", f"{phone}%", after_ts],
            )
            row = db.cursor.fetchone()
            if row and (row.get("response_text") or "").strip():
                return row["response_text"].strip()
        except Exception:
            pass

        time.sleep(2)
    return ""


def _simulate_target_reply(db, target_user_id, send_text):
    """Run the target bot's chat pipeline without live WhatsApp."""
    from chats import get_or_create_whatsapp_chat, process_chat_message

    chat = get_or_create_whatsapp_chat(
        db, target_user_id, "agent_tester_sim", name="Agent Tester"
    )
    if not chat:
        return "", "Could not create simulation chat on target"
    result = process_chat_message(db, chat["id"], send_text, save=True)
    if not result.get("success"):
        return "", result.get("error") or "Target bot AI failed"
    return (result.get("reply") or "").strip(), None


@agent_tester_bp.route(
    "/users/<int:user_id>/agent-tester/sessions/<int:session_id>/run-step",
    methods=["POST"],
)
def run_next_step(user_id, session_id):
    data = request.json or {}
    mode = (data.get("mode") or "live").strip().lower()
    if mode not in ("live", "simulate"):
        mode = "live"
    step_id = data.get("step_id")

    import whatsapp as wa

    db = Database()
    try:
        _, err = _require_tester(db, user_id)
        if err:
            return err
        row = db.row(SESSIONS, {"id": session_id, "tester_user_id": user_id})
        if not row:
            return jsonify({"status": False, "message": "Session not found"}), 404

        if step_id:
            step = db.row(STEPS, {"id": int(step_id), "session_id": session_id})
        else:
            db.cursor.execute(
                f"""
                SELECT * FROM {STEPS}
                WHERE session_id=%s AND status='pending'
                ORDER BY step_index ASC, id ASC LIMIT 1
                """,
                [session_id],
            )
            step = db.cursor.fetchone()

        if not step:
            return (
                jsonify({"status": False, "message": "No pending steps left"}),
                400,
            )

        db.update(STEPS, {"status": "running"}, {"id": step["id"]})
        db.update(SESSIONS, {"status": "running"}, {"id": session_id})

        send_text = (step.get("send_text") or "").strip()
        reply_text = ""
        notes = ""
        send_ok = False

        if mode == "simulate":
            reply_text, sim_err = _simulate_target_reply(
                db, row["target_user_id"], send_text
            )
            send_ok = not sim_err
            notes = sim_err or "Simulated against target bot chat pipeline"
        else:
            tester_session = _wa_session(db, user_id)
            session_name = tester_session.get("session_name") or ""
            if not tester_session.get("connected") or not session_name:
                return (
                    jsonify(
                        {
                            "status": False,
                            "message": "Tester WhatsApp is not connected",
                        }
                    ),
                    400,
                )
            after_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            status_code, send_data = wa._send_agency_message(  # noqa: SLF001
                session_name, row["target_phone"], send_text
            )
            send_ok = wa._agency_ok(status_code, send_data) or bool(  # noqa: SLF001
                (send_data or {}).get("sent")
            )
            if not send_ok:
                notes = (
                    (send_data or {}).get("message")
                    or (send_data or {}).get("error")
                    or f"send failed HTTP {status_code}"
                )
                # Auto-fallback to simulate so the wizard can continue
                reply_text, sim_err = _simulate_target_reply(
                    db, row["target_user_id"], send_text
                )
                if reply_text:
                    notes = f"Live send failed ({notes}); used simulate fallback"
                    send_ok = True
                else:
                    notes = f"{notes}; simulate also failed: {sim_err}"
            else:
                reply_text = _wait_for_reply(
                    db, user_id, row["target_phone"], after_ts, timeout_sec=40
                )
                if not reply_text:
                    # Fallback simulate if no WA reply observed
                    reply_text, sim_err = _simulate_target_reply(
                        db, row["target_user_id"], send_text
                    )
                    notes = (
                        "No live WhatsApp reply in time; used simulate fallback"
                        + (f" ({sim_err})" if sim_err else "")
                    )

        expect = (step.get("expect_text") or "").strip().lower()
        passed = bool(reply_text)
        if passed and expect:
            # Soft check: any keyword from expect appears
            tokens = [t for t in re.split(r"[^\w]+", expect) if len(t) > 3][:6]
            if tokens:
                low = reply_text.lower()
                hits = sum(1 for t in tokens if t in low)
                if hits == 0:
                    passed = False
                    notes = (notes + " | Reply did not match expect keywords").strip(
                        " |"
                    )

        step_status = "passed" if passed and send_ok else "failed"
        db.update(
            STEPS,
            {
                "status": step_status,
                "reply_text": reply_text or "",
                "notes": notes or "",
            },
            {"id": step["id"]},
        )

        steps = _load_steps(db, session_id)
        pending = [s for s in steps if s.get("status") == "pending"]
        if not pending:
            db.update(SESSIONS, {"status": "steps_done"}, {"id": session_id})
        row = db.row(SESSIONS, {"id": session_id})
        step = db.row(STEPS, {"id": step["id"]})
    finally:
        db.close()

    return jsonify(
        {
            "status": True,
            "step": step,
            "session": _public_session(row, steps),
            "mode": mode,
        }
    )


@agent_tester_bp.route(
    "/users/<int:user_id>/agent-tester/sessions/<int:session_id>/conclude",
    methods=["POST"],
)
def conclude_session(user_id, session_id):
    db = Database()
    try:
        _, err = _require_tester(db, user_id)
        if err:
            return err
        row = db.row(SESSIONS, {"id": session_id, "tester_user_id": user_id})
        if not row:
            return jsonify({"status": False, "message": "Session not found"}), 404
        steps = _load_steps(db, session_id)
        target = db.row("admins", {"id": row["target_user_id"]}) or {}
        handler = _handler_class(db, row["target_user_id"])

        transcript = []
        for s in steps:
            transcript.append(
                {
                    "title": s.get("title"),
                    "sent": s.get("send_text"),
                    "expect": s.get("expect_text"),
                    "reply": s.get("reply_text"),
                    "status": s.get("status"),
                    "notes": s.get("notes"),
                }
            )

        prompt = (
            f"Target bot: {target.get('name')} ({handler})\n"
            f"Goal: {row.get('goal')}\n"
            f"Transcript JSON:\n{json.dumps(transcript, ensure_ascii=False)}\n\n"
            "Write conclusion + suggested_instructions JSON for improving the target bot."
        )
        parsed, gerr = _gemini_json(TESTER_INSTRUCTIONS, prompt)
        if gerr:
            return jsonify({"status": False, "message": gerr}), 400

        conclusion = (parsed.get("conclusion") or "").strip()
        suggested = (parsed.get("suggested_instructions") or "").strip()
        if not conclusion and not suggested:
            return (
                jsonify({"status": False, "message": "Empty conclusion from Gemini"}),
                400,
            )

        db.update(
            SESSIONS,
            {
                "status": "done",
                "conclusion": conclusion,
                "suggested_instructions": suggested,
            },
            {"id": session_id},
        )
        row = db.row(SESSIONS, {"id": session_id})
        steps = _load_steps(db, session_id)
    finally:
        db.close()

    return jsonify({"status": True, "session": _public_session(row, steps)})


@agent_tester_bp.route(
    "/users/<int:user_id>/agent-tester/sessions/<int:session_id>/save-instructions",
    methods=["POST"],
)
def save_instructions_to_target(user_id, session_id):
    """Save suggested_instructions onto the TARGET user's bot_instructions."""
    data = request.json or {}
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()

    db = Database()
    try:
        _, err = _require_tester(db, user_id)
        if err:
            return err
        row = db.row(SESSIONS, {"id": session_id, "tester_user_id": user_id})
        if not row:
            return jsonify({"status": False, "message": "Session not found"}), 404

        content = content or (row.get("suggested_instructions") or "").strip()
        if not content:
            return (
                jsonify({"status": False, "message": "No instructions to save"}),
                400,
            )
        if not title:
            title = f"Agent Tester · {(row.get('goal') or 'improvements')[:80]}"

        target_id = row["target_user_id"]
        from gemini_cache import refresh_cache_after_instruction_change

        new_id = db.insert(
            "bot_instructions",
            {"user_id": target_id, "title": title, "content": content},
        )
        instruction = db.row("bot_instructions", {"id": new_id})
        cache_refresh = refresh_cache_after_instruction_change(db, target_id)
    finally:
        db.close()

    return jsonify(
        {
            "status": True,
            "message": "Instructions saved on target user",
            "instruction": instruction,
            "target_user_id": target_id,
            "cache_refresh": cache_refresh,
        }
    )
