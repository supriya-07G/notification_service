"""routes/status_callback.py — Twilio status callback webhook.

Rule 3: Validates X-Twilio-Signature.
Rule 4: Updates notification_attempts status from Twilio delivery reports.
"""

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from twilio.request_validator import RequestValidator

import config
from db.init import get_connection

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


@router.post("/webhooks/twilio/status")
async def twilio_status(request: Request):
    """Receive a Twilio message status callback."""
    form = dict(await request.form())
    validate_twilio(request, form)

    sid = form.get("MessageSid", "")
    status = form.get("MessageStatus", "")
    error = form.get("ErrorCode", "")

    conn = get_connection()
    try:
        conn.execute(
            """UPDATE notification_attempts
               SET status = ?, status_updated_at = CURRENT_TIMESTAMP, error_code = ?
               WHERE provider_sid = ?""",
            [status, error or None, sid],
        )
        conn.commit()
        logger.info("Status update: %s → %s", sid, status)
    finally:
        conn.close()

    return Response(content="<Response/>", media_type="application/xml")
