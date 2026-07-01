"""routes/sms_inbound.py — Inbound SMS webhook (Twilio).

Rule 3: Every inbound Twilio webhook validates X-Twilio-Signature.
Rule 5: STOP/CANCEL/END/STOPALL/UNSUBSCRIBE → opt-out; START/UNSTOP/SUBSCRIBE → opt-in.
         Keyword classification uses utils.sms_keywords (shared with reply_processor) with
         whole-word matching to prevent false positives ("change" ⊄ "exchange", etc.).
"""

import logging
import urllib.request

from fastapi import APIRouter, HTTPException, Request, Response
from twilio.request_validator import RequestValidator

import config
from db.init import get_connection
from utils.log_helpers import mask_phone
from utils.sms_keywords import STOP_WORDS, START_WORDS, classify as _classify

logger = logging.getLogger(__name__)

router = APIRouter()


def validate_twilio(request: Request, form_data: dict) -> None:
    """Validate X-Twilio-Signature. Raise 403 on failure (Rule 3)."""
    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    signature = request.headers.get("X-Twilio-Signature", "")
    
    # Reconstruct the original request URL using WEBHOOK_BASE_URL
    base_url = getattr(config, "WEBHOOK_BASE_URL", "").rstrip('/')
    path = request.url.path
    query = request.url.query
    url = f"{base_url}{path}"
    if query:
        url += f"?{query}"
        
    if not validator.validate(url, form_data, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


# Keyword constants and classify() are shared with workers/reply_processor.py via
# utils/sms_keywords.py. Do NOT redefine them here — use the imported names.

_STATUS_EMOJI = {
    "stop":               "🚫",
    "start":              "✅",
    "confirm":            "✅",
    "reschedule_request": "🔄",
    "question":           "❓",
    "unknown":            "💬",
}


@router.post("/webhooks/twilio/sms")
async def sms_inbound(request: Request):
    """Receive an inbound SMS from Twilio."""
    form = dict(await request.form())
    validate_twilio(request, form)

    from_phone = form.get("From", "")
    body = form.get("Body", "").strip()
    sid = form.get("MessageSid", "")

    conn = get_connection()
    body_upper = body.upper()

    try:
        # ALWAYS log the message so reply_processor can see it
        conn.execute(
            """INSERT OR IGNORE INTO inbound_messages
               (from_address, channel, body, twilio_sid)
               VALUES (?, ?, ?, ?)""",
            [from_phone, "sms", body, sid],
        )
        conn.commit()
        logger.info("Inbound SMS logged: %s from %s", sid, mask_phone(from_phone))

        if body_upper in STOP_WORDS:
            conn.execute(
                "INSERT OR REPLACE INTO opt_outs (phone, channel, source) VALUES (?, ?, ?)",
                [from_phone, "sms", "inbound_stop"],
            )
            conn.commit()
            logger.info("Opt-out recorded: %s (STOP)", mask_phone(from_phone))

        elif body_upper in START_WORDS:
            conn.execute(
                "DELETE FROM opt_outs WHERE phone=? AND channel='sms'",
                [from_phone],
            )
            conn.commit()
            logger.info("Opt-in recorded: %s (START)", mask_phone(from_phone))

        customer = conn.execute(
            "SELECT customer_name FROM appointments WHERE customer_phone=? ORDER BY appointment_at DESC LIMIT 1",
            [from_phone],
        ).fetchone()
        customer_name = customer["customer_name"] if customer else None

    finally:
        conn.close()

    _notify_discord(from_phone, body, _classify(body), customer_name)

    return Response(content="<Response/>", media_type="application/xml")


def _notify_discord(from_phone: str, body: str, status: str = "unknown", customer_name: str = None) -> None:
    """Fire-and-forget POST to Discord webhook."""
    url = config.DISCORD_WEBHOOK_URL
    if not url:
        return
    try:
        import json as _json
        emoji = _STATUS_EMOJI.get(status, "💬")
        label = status.replace("_", " ").title()
        from_display = f"{customer_name} ({mask_phone(from_phone)})" if customer_name else mask_phone(from_phone)
        payload = _json.dumps({
            "content": f"📩 **Customer Reply**\n**From:** {from_display}\n**Status:** {emoji} {label}\n**Message:** (see dashboard)"
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:
        logger.warning("Discord notify failed: %s", exc)
