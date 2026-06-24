import logging
from typing import List, Dict, Any
from fastapi import APIRouter, Request, Response
from db.init import get_connection

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/webhooks/sendgrid/status")
async def handle_sendgrid_status(request: Request):
    """
    Handle SendGrid Event Webhook POST requests.
    Expects a JSON array of events.
    """
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

            # SendGrid often appends `.filterXXXX` to the message ID in the webhook payload
            # So we split by `.` to get the raw message ID that matches the DB
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

    # Always return 200 OK
    return Response(status_code=200)
