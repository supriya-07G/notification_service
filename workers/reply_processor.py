import logging
import httpx
import os
import sys
from html import escape as html_escape

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from db.init import get_connection
from utils.log_helpers import mask_phone, mask_name
from db.settings import Settings
from twilio.rest import Client
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

logger = logging.getLogger(__name__)

from utils.sms_keywords import STOP_WORDS, START_WORDS, CONFIRM_WORDS, classify

# classify() is imported from utils.sms_keywords (uses whole-word regex matching).
# Do not redefine it here.

def send_discord_alert(message_row: dict, classification: str, appointment: dict = None):
    """POST to DISCORD_WEBHOOK_URL with embed showing customer reply details."""
    if not config.DISCORD_WEBHOOK_URL:
        return
    color = {'reschedule_request': 15158332, 'question': 16776960, 'unknown': 9807270}.get(classification, 9807270)
    embed = {
        "title": f"Customer Reply — {classification.replace('_',' ').title()}",
        "color": color,
        "fields": [
            {"name": "From", "value": f"{appointment['customer_name']} ({mask_phone(message_row['from_address'])})" if appointment else mask_phone(message_row['from_address']), "inline": True},
            {"name": "Channel", "value": message_row['channel'], "inline": True},
            {"name": "Message", "value": "(see dashboard for details)", "inline": False},
        ]
    }
    if appointment:
        embed['fields'].append({
            "name": "Appointment",
            "value": f"{mask_name(appointment['customer_name'])} — {appointment['appointment_at']}",
            "inline": False
        })
    try:
        httpx.post(config.DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=5)
    except Exception as e:
        logger.warning(f"Discord alert failed: {e}")

def send_sms_alert(message_row: dict, classification: str, appointment: dict, settings: Settings, conn):
    if settings.get("alert_sms_enabled", "true") != "true":
        return
        
    sms_from = settings.get("alert_sms_from", config.TWILIO_SMS_NUMBER)
    sms_to_raw = settings.get("alert_sms_to", "")
    use_staff = settings.get("alert_sms_use_staff", "true") == "true"
    
    recipients = set([n.strip() for n in sms_to_raw.split(",") if n.strip()])
    
    if use_staff:
        staff = conn.execute("SELECT phone FROM admin_users WHERE is_active=1 AND phone IS NOT NULL").fetchall()
        for s in staff:
            if s["phone"]:
                recipients.add(s["phone"].strip())
                
    if not recipients:
        return
        
    client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    from_display = f"{appointment['customer_name']} ({mask_phone(message_row['from_address'])})" if appointment else mask_phone(message_row['from_address'])
    appt_text = f"\nAppt: {appointment['appointment_at']}" if appointment else ""
    safe_body = (message_row['body'] or '')[:100].replace('\n', ' ')
    body = f"⚠️ Alert: Customer replied '{classification.upper()}'\nFrom: {from_display}{appt_text}\nMsg: {safe_body}"
    
    for to in recipients:
        try:
            client.messages.create(to=to, from_=sms_from, body=body)
        except Exception as e:
            logger.warning(f"SMS alert failed to {to}: {e}")

def send_email_alert(message_row: dict, classification: str, appointment: dict, settings: Settings, conn=None):
    if settings.get("alert_email_enabled", "true") != "true":
        return

    email_from = settings.get("alert_email_from", config.SENDGRID_FROM_EMAIL)
    email_to_raw = settings.get("alert_email_to", "")

    recipients = set(e.strip() for e in email_to_raw.split(",") if e.strip())

    if conn and settings.get("alert_email_use_staff", "true") == "true":
        staff = conn.execute(
            "SELECT email FROM admin_users WHERE is_active=1 AND email IS NOT NULL"
        ).fetchall()
        for s in staff:
            if s["email"]:
                recipients.add(s["email"].strip())

    if not recipients:
        return
        
    appt_text = f"<p><strong>Appointment:</strong> {html_escape(appointment['customer_name'])} ({html_escape(str(appointment['appointment_at']))})</p>" if appointment else ""

    html_content = f"""
    <h3>Customer Reply Alert: {html_escape(classification.upper())}</h3>
    <p><strong>From:</strong> {html_escape(message_row['from_address'])}</p>
    {appt_text}
    <p><strong>Message:</strong></p>
    <blockquote style="border-left: 4px solid #ccc; padding-left: 10px; color: #555;">{html_escape(message_row['body'] or '')}</blockquote>
    """
    
    try:
        sg = SendGridAPIClient(config.SENDGRID_API_KEY)
        for to in recipients:
            message = Mail(
                from_email=email_from,
                to_emails=to,
                subject=f"EcoSave Alert: Customer Reply ({classification.upper()})",
                html_content=html_content
            )
            sg.send(message)
    except Exception as e:
        logger.warning(f"Email alert failed: {e}")

def run():
    conn = get_connection()
    settings = Settings(conn)
    rows = conn.execute(
        "SELECT * FROM inbound_messages WHERE processed=FALSE OR processed=0 ORDER BY received_at"
    ).fetchall()

    for msg in rows:
        cl = classify(msg['body'] or '')
        escalated = cl in ('reschedule_request', 'question', 'unknown')
        escalated_to = 'discord,sms,email' if escalated else None

        if escalated:
            # Find the nearest upcoming appointment for this phone number.
            # Using ASC + appointment_at >= now gives the soonest future booking,
            # which is the one the customer is replying about. The old query used
            # no date filter and ORDER BY ASC LIMIT 1, which fetched the oldest
            # historical appointment instead (L7 fix).
            appt = conn.execute(
                """SELECT * FROM appointments
                   WHERE customer_phone = ?
                     AND appointment_at >= datetime('now')
                   ORDER BY appointment_at ASC
                   LIMIT 1""",
                [msg['from_address']]
            ).fetchone()
            
            appt_dict = dict(appt) if appt else None
            msg_dict = dict(msg)
            
            send_discord_alert(msg_dict, cl, appt_dict)
            send_sms_alert(msg_dict, cl, appt_dict, settings, conn)
            send_email_alert(msg_dict, cl, appt_dict, settings, conn)

        conn.execute("""
            UPDATE inbound_messages
            SET processed=TRUE, processed_at=CURRENT_TIMESTAMP,
                classification=?, escalated=?, escalated_to=?
            WHERE id=?
        """, [cl, escalated, escalated_to, msg['id']])
        conn.commit()

    conn.close()
    logger.info(f"Reply processor: processed {len(rows)} messages")

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run()
