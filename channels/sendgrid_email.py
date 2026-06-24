"""channels/sendgrid_email.py — Async email worker.

Rule 6: Emails are never sent inline by the engine. The engine writes to
email_queue; this worker drains the queue independently on its own cron.

Runs as:  python channels/sendgrid_email.py
Cron:     5,35 * * * *

The email_queue table stores template_id and template_data (JSON).
The worker fetches the template by id and renders it at send time.
"""

import json
import logging

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from db.init import get_connection
from db.templates import render_template

logger = logging.getLogger(__name__)


def run() -> None:
    """Drain the email_queue: fetch unsent rows, render, and send via SendGrid."""
    conn = get_connection()

    rows = conn.execute(
        """SELECT eq.*, mt.body AS tmpl_body, mt.subject AS tmpl_subject
           FROM email_queue eq
           LEFT JOIN message_templates mt ON mt.id = eq.template_id
           WHERE eq.sent_at IS NULL AND eq.attempts < 3
           ORDER BY eq.queued_at"""
    ).fetchall()

    sent = 0
    failed = 0

    for row in rows:
        # Increment attempt counter first
        conn.execute(
            "UPDATE email_queue SET attempts = attempts + 1 WHERE id = ?",
            [row["id"]],
        )
        conn.commit()

        try:
            tmpl_body = row["tmpl_body"]
            if not tmpl_body:
                raise ValueError(
                    f"No active email template (template_id={row['template_id']}) "
                    f"for email_queue row {row['id']}"
                )

            data = json.loads(row["template_data"])
            body_html = render_template(tmpl_body, data)
            subject = render_template(
                row["tmpl_subject"] or "Appointment Reminder", data
            )

            message = Mail(
                from_email=(config.SENDGRID_FROM_EMAIL, config.SENDGRID_FROM_NAME),
                to_emails=row["to_address"],
                subject=subject,
                html_content=body_html,
            )
            sg = SendGridAPIClient(config.SENDGRID_API_KEY)
            response = sg.send(message)
            sg_id = response.headers.get("X-Message-Id", "")

            conn.execute(
                "UPDATE email_queue SET sent_at = CURRENT_TIMESTAMP, sg_message_id = ? WHERE id = ?",
                [sg_id, row["id"]],
            )
            conn.commit()
            sent += 1
            logger.info("Email sent: %s → %s", sg_id, row["to_address"])

        except Exception as e:
            conn.execute(
                "UPDATE email_queue SET error = ? WHERE id = ?",
                [str(e), row["id"]],
            )
            conn.commit()
            failed += 1
            logger.error("Email failed to %s: %s", row["to_address"], e)

    print(f"Email worker: sent={sent} failed={failed}")
    conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=config.LOG_LEVEL)
    run()
