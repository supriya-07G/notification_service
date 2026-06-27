import logging
from fastapi import APIRouter, Request, Response
from sendgrid.helpers.eventwebhook import EventWebhook, EventWebhookHeader

import config
from db.init import get_connection

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_sendgrid_signature(raw_body: bytes, headers: dict) -> bool:
    """Verify SendGrid Event Webhook ECDSA signature.

    Requires SENDGRID_WEBHOOK_VERIFY_KEY in .env (from SendGrid Mail Settings
    → Event Webhook → Signed Event Webhook → Verification Key).
    If the key is not configured, reject all requests.
    """
    verify_key = getattr(config, "SENDGRID_WEBHOOK_VERIFY_KEY", "")
    if not verify_key:
        logger.warning("SENDGRID_WEBHOOK_VERIFY_KEY not set — rejecting webhook")
        return False

    signature = headers.get(EventWebhookHeader.SIGNATURE, "")
    timestamp = headers.get(EventWebhookHeader.TIMESTAMP, "")
    if not signature or not timestamp:
        return False

    try:
        ew = EventWebhook()
        key = ew.convert_public_key_to_ecdsa(verify_key)
        return ew.verify_signature(raw_body.decode("utf-8"), signature, timestamp, key)
    except Exception as e:
        logger.warning("SendGrid signature verification error: %s", e)
        return False


@router.post("/webhooks/sendgrid/status")
async def handle_sendgrid_status(request: Request):
    """Handle SendGrid Event Webhook POST requests.

    Validates ECDSA signature before processing.
    Expects a JSON array of events.
    """
    raw_body = await request.body()

    if not _verify_sendgrid_signature(raw_body, dict(request.headers)):
        logger.warning("SendGrid webhook rejected: invalid signature")
        return Response(status_code=403)

    try:
        events = await request.json()
    except Exception as e:
        logger.error("Failed to parse SendGrid webhook JSON: %s", e)
        return Response(status_code=200)

    if not isinstance(events, list):
        events = [events]

    conn = get_connection()
    try:
        for event_data in events:
            sg_message_id = event_data.get("sg_message_id")
            event_type = event_data.get("event")

            if not sg_message_id or not event_type:
                continue

            raw_sg_message_id = sg_message_id.split('.')[0] if '.' in sg_message_id else sg_message_id

            row = conn.execute(
                "SELECT appointment_id, rule_name FROM email_queue WHERE sg_message_id LIKE ?",
                [f"{raw_sg_message_id}%"]
            ).fetchone()

            if not row:
                logger.warning("Received SendGrid event for unknown sg_message_id: %s", sg_message_id)
                continue

            appointment_id = row["appointment_id"]
            rule_name = row["rule_name"]

            if event_type == "delivered":
                conn.execute(
                    "UPDATE email_queue SET sent_at = CURRENT_TIMESTAMP WHERE sg_message_id LIKE ?",
                    [f"{raw_sg_message_id}%"]
                )
                conn.execute(
                    """UPDATE notification_attempts
                       SET status = 'delivered', status_updated_at = CURRENT_TIMESTAMP
                       WHERE appointment_id = ? AND rule_name = ? AND channel = 'email'""",
                    [appointment_id, rule_name]
                )
                logger.info("SendGrid delivered: email %s for appt %s", sg_message_id, appointment_id)

            elif event_type in ("bounce", "dropped", "spam_report"):
                conn.execute(
                    "UPDATE email_queue SET error = ? WHERE sg_message_id LIKE ?",
                    [event_type, f"{raw_sg_message_id}%"]
                )
                conn.execute(
                    """UPDATE notification_attempts
                       SET status = 'failed', error_code = 'SENDGRID_ERROR', error_message = ?, status_updated_at = CURRENT_TIMESTAMP
                       WHERE appointment_id = ? AND rule_name = ? AND channel = 'email'""",
                    [event_type, appointment_id, rule_name]
                )
                logger.error("SendGrid failed (%s): email %s for appt %s", event_type, sg_message_id, appointment_id)

        conn.commit()
    except Exception as e:
        logger.error("Error processing SendGrid webhook: %s", e)
    finally:
        conn.close()

    return Response(status_code=200)
