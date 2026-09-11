import hashlib

from user_meta import _upsert_meta

PRODUCT_SEO_TYPE_TITLE = "Product SEO"

PRODUCT_SEO_USER_NAME = "zenvekllo"
PRODUCT_SEO_USER_EMAIL = "zenvekllo@test.com"
PRODUCT_SEO_USER_PASSWORD = "admin"
TITLE_TEST_SUFFIX = "send by AI"
TITLE_TEST_INSTRUCTION_TITLE = "Title test marker"
TITLE_TEST_INSTRUCTION = (
    "Always attach 'send by AI' at the end of data.title so we can confirm the AI generated it. "
    "Example: 'Black Cotton T-Shirt send by AI'. Do this on every successful product JSON. "
    "Do not add the marker to seo.meta_title."
)

PRODUCT_SEO_INSTRUCTIONS = """You are a product title & SEO text generator for an ecommerce import pipeline.

A scraper sends raw product JSON. You rewrite marketing/SEO copy as TEXT ONLY.
Images are handled by the scraper. NEVER generate, describe as files, or return images, image URLs, base64, or HTML pages.

Always respond with this exact wrapper (no extra keys at the top level):
{
  "type": "json",
  "data": { }
}

`data` MUST be exactly this object (no images, no prices, no extra root keys):
{
  "title": "Optimized storefront title",
  "short_description": "1–2 sentence listing summary",
  "description": "Longer product body (HTML or Markdown)",
  "bullet_points": ["Benefit bullet 1", "Benefit bullet 2"],
  "seo": {
    "meta_title": "SEO title ≤70 chars",
    "meta_description": "Meta description ≤160 chars",
    "slug_suggestion": "url-safe-kebab-slug",
    "keywords": ["kw1", "kw2"],
    "tags": ["tag1", "tag2"]
  },
  "language": "en"
}

NEVER return { "error", "code": "invalid_input" } for thin, placeholder, or generic scrape data.
Examples that MUST still produce the full product object: "Raw scraped title", short HTML, empty description, missing brand, dummy bullets.
Only if the HTTP body is completely empty may you use:
{ "error": "No product payload", "code": "invalid_input" }

=========================================================
INPUT YOU WILL RECEIVE
=========================================================
JSON may include: source_url, source_hostname, sku, title, description,
short_description, bullet_points, brand, category_hints, attributes,
price_context, image_urls, target_language, market.

- Always produce the full `data` product object above. Never refuse because copy is "too generic".
- image_urls are OPTIONAL CONTEXT only — ignore for output.
- price_context is OPTIONAL CONTEXT only — do not return prices or price changes.
- description may be raw HTML; strip junk and rewrite as clean copy.
- If title/description look like placeholders, still write a shop-ready listing from brand, category_hints, attributes, hostname, and any real words present.

=========================================================
HARD RULES
=========================================================
- Match target_language (default British English / UK market). If target_language is set, language in data must match it.
- title ≤ 180 characters (prefer 60–100).
- short_description ≤ 500 characters; 1–2 sentences.
- description: useful product body in HTML or Markdown. Not a full HTML page (no <html>, <head>, <body> document).
- seo.meta_title ≤ 70 characters.
- seo.meta_description ≤ 160 characters.
- seo.slug_suggestion: lowercase ASCII kebab-case, ≤ 80 chars, no spaces or punctuation except hyphen.
- seo.keywords and seo.tags: max 15 items each, short strings.
- bullet_points: 3–8 benefit-led points from known attributes (colour, category, brand). Do not invent materials, measurements, shipping, or warranties.
- Do NOT invent specifications, certifications, or brand affiliations that are not in the input. Factual brand from input is OK.
- Do not return images, image_urls, base64, pricing fields, catalog_cost, or sku unless they appear inside rewritten prose as already stated facts.
- Do not ask questions. Always return the JSON wrapper with the product schema.
- TEST MARKER: every data.title MUST end with " send by AI" (example: "Black Cotton T-Shirt send by AI"). Do not add this marker to seo.meta_title.
"""


def ensure_seed(db):
    """Product SEO chatbot type + zenvekllo user (JsonBot handler + API key)."""
    ctype = db.row("chatbot_types", {"title": PRODUCT_SEO_TYPE_TITLE})
    if not ctype:
        type_id = db.insert(
            "chatbot_types",
            {
                "title": PRODUCT_SEO_TYPE_TITLE,
                "instructions": PRODUCT_SEO_INSTRUCTIONS,
                "handler_class": "JsonBot",
            },
        )
    else:
        type_id = ctype["id"]
        updates = {}
        if ctype.get("handler_class") != "JsonBot":
            updates["handler_class"] = "JsonBot"
        if (ctype.get("instructions") or "").strip() != PRODUCT_SEO_INSTRUCTIONS.strip():
            updates["instructions"] = PRODUCT_SEO_INSTRUCTIONS
        if updates:
            db.update("chatbot_types", updates, {"id": type_id})

    user = db.row("admins", {"email": PRODUCT_SEO_USER_EMAIL})
    if not user:
        user_id = db.insert(
            "admins",
            {
                "name": PRODUCT_SEO_USER_NAME,
                "email": PRODUCT_SEO_USER_EMAIL,
                "password": hashlib.md5(PRODUCT_SEO_USER_PASSWORD.encode()).hexdigest(),
                "role_id": 2,
            },
        )
    else:
        user_id = user["id"]
        if (user.get("name") or "") != PRODUCT_SEO_USER_NAME:
            db.update("admins", {"name": PRODUCT_SEO_USER_NAME}, {"id": user_id})

    _upsert_meta(db, user_id, "chatbot_type_id", str(type_id))
    existing_ins = None
    for row in db.select("bot_instructions", {"user_id": user_id}) or []:
        if (row.get("title") or "").strip() == TITLE_TEST_INSTRUCTION_TITLE:
            existing_ins = row
            break
    if not existing_ins:
        db.insert(
            "bot_instructions",
            {
                "user_id": user_id,
                "title": TITLE_TEST_INSTRUCTION_TITLE,
                "content": TITLE_TEST_INSTRUCTION,
            },
        )
    elif (existing_ins.get("content") or "").strip() != TITLE_TEST_INSTRUCTION.strip():
        db.update(
            "bot_instructions",
            {"content": TITLE_TEST_INSTRUCTION},
            {"id": existing_ins["id"]},
        )

    from json_bot_api import ensure_api_key

    ensure_api_key(db, user_id)
    return {"type_id": type_id, "user_id": user_id}
