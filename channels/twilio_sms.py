"""channels/twilio_sms.py — Send SMS via Twilio.

Rule 2: Credentials from config (loaded from .env). Never logged.
Rule 4: status_callback URL on every outbound message.
Rule 5: can_send() checks opt_outs before every send.
"""

import logging

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

import config
from utils.log_helpers import mask_phone

logger = logging.getLogger(__name__)


def can_send(phone: str, conn) -> bool:
    """Return False if *phone* is opted out of SMS (or all channels)."""
    row = conn.execute(
        "SELECT 1 FROM opt_outs WHERE phone=? AND channel IN ('sms','all')",
        [phone],
    ).fetchone()
    if row:
        logger.info("SMS skipped — opted out: %s", mask_phone(phone))
        return False
    return True


def send(to: str, body: str, attempt_id: int, conn) -> str | None:
    """Send an SMS and update the notification_attempts row.

    Returns the Twilio MessageSid on success, None on failure.
    On TwilioRestException: sets status='failed', error_code, error_message.
    Does not raise — the engine continues processing other appointments.
    """
    client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    try:
        msg = client.messages.create(
            to=to,
            from_=config.TWILIO_SMS_NUMBER,
            body=body,
            status_callback=f"{config.WEBHOOK_BASE_URL}/webhooks/twilio/status",
        )
        conn.execute(
            "UPDATE notification_attempts SET provider_sid=?, status='queued' WHERE id=?",
            [msg.sid, attempt_id],
        )
        conn.commit()
        logger.info("SMS queued: %s → %s", msg.sid, mask_phone(to))
        return msg.sid
    except TwilioRestException as e:
        conn.execute(
            """UPDATE notification_attempts
               SET status='failed', error_code=?, error_message=?
               WHERE id=?""",
            [str(e.code), e.msg, attempt_id],
        )
        conn.commit()
        logger.error("SMS failed to %s: %s %s", mask_phone(to), e.code, e.msg)
        return None
    except Exception as e:
        logger.error("SMS network error to %s: %s", mask_phone(to), e)
        conn.execute(
            """UPDATE notification_attempts
               SET status='failed', error_code='NETWORK_ERROR', error_message=?
               WHERE id=?""",
            [str(e), attempt_id],
        )
        conn.commit()
        return None
