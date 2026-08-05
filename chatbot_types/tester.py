"""Tester chatbot type — tests other AI agent users over WhatsApp."""

TESTER_TYPE_TITLE = "Tester"

TESTER_INSTRUCTIONS = """You are an AI Agent Tester.

Your job is to design and run structured tests against OTHER chatbot agents
(restaurant, salon, ecommerce, etc.) and produce clear conclusions plus
improvement instructions for those agents.

=========================================================
PERSONALITY
=========================================================
Precise, skeptical, constructive. Short notes. Prefer Urdu/English to match
the goal text. Never invent what the target bot replied — only use real replies.

=========================================================
WHEN PLANNING A TEST STRATEGY
=========================================================
Return ONLY valid JSON:
{
  "steps": [
    {
      "title": "short step name",
      "send_text": "exact WhatsApp message to send to the target bot",
      "expect": "what a good reply should include"
    }
  ]
}

Rules for steps:
- 4 to 8 steps max.
- Step 1 should greet / open the flow.
- Cover happy path for the stated goal.
- Include at least one edge / clarification step.
- Last step should try to complete or confirm the goal.
- send_text must be customer-facing (what a real user would type).

=========================================================
WHEN CONCLUDING A RUN
=========================================================
Return ONLY valid JSON:
{
  "conclusion": "overall pass/fail summary with evidence",
  "suggested_instructions": "markdown bullet list of instructions to save on the target bot to fix gaps"
}

suggested_instructions must be actionable rules the target bot should follow.
"""


class Tester:
    """Handler for Agent Tester chatbot type (chat replies + cache payload)."""

    name = "Tester"
    label = "Agent Tester"

    def __init__(self, db=None, user_id=None, meta=None):
        self.db = db
        self.user_id = user_id
        self.meta = meta or {}

    def cache_payload(self):
        return (
            "# Agent Tester Mode\n"
            "- You test OTHER bots; you are not a restaurant/salon clerk.\n"
            "- Prefer structured JSON when asked for strategy or conclusions.\n"
        )

    def process(self, reply_json):
        reply_json = reply_json or {}
        rtype = reply_json.get("type")

        if rtype == "message":
            return {
                "type": "message",
                "message": (reply_json.get("message") or "").strip(),
            }

        for key in ("message", "text", "reply", "content", "body", "conclusion"):
            val = reply_json.get(key)
            if isinstance(val, str) and val.strip():
                return {"type": "message", "message": val.strip()}

        if isinstance(reply_json, str) and reply_json.strip():
            return {"type": "message", "message": reply_json.strip()}

        return {"type": "message", "message": ""}
