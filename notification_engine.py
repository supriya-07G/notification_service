"""notification_engine.py — Main notification orchestrator.

Run by cron every 30 minutes:  */30 * * * *  python notification_engine.py

Flow:
  1. Check kill switch (Rule 11) and quiet hours (Rule 9).
  2. Load appointments within the next 73 hours.
  3. For each appointment × rule × channel:
     a. Check per-rule and per-channel toggles.
     b. Check timing window (±15 min / +30 min).
     c. Dedup via UNIQUE constraint on notification_attempts (Rule 7).
     d. Fetch + render the message template.
     e. SMS: opt-out check (Rule 5), then send via Twilio.
     f. Email: queue to email_queue — never send inline (Rule 6).
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta

import pytz

import config
from channels.twilio_sms import can_send, send as sms_send
from db.init import get_connection
from db.settings import Settings
from db.templates import get_template, render_template

logger = logging.getLogger(__name__)


def run() -> dict:
    """Execute one engine cycle. Returns stats dict."""
    conn = get_connection()
    settings = Settings(conn)

    stats = {
        "sms_sent": 0,
        "email_queued": 0,
        "skipped_dedup": 0,
        "skipped_optout": 0,
        "skipped_no_address": 0,
        "failed": 0,
    }

    # ── RULE 11: kill switch ────────────────────────────────────────────
    if settings.is_paused():
        logger.info("Notifications paused (kill switch). Exiting.")
        conn.close()
        return stats

    # ── RULE 9: quiet hours ─────────────────────────────────────────────
    if settings.is_quiet_hours_active():
        logger.info("Outside allowed send window (quiet hours). Exiting.")
        conn.close()
        return stats

    # ── Load upcoming appointments ──────────────────────────────────────
    tz_name = settings.get("timezone", "America/New_York")
    tz = pytz.timezone(tz_name)
    now_utc = datetime.now(pytz.utc)
    window_end_utc = now_utc + timedelta(hours=73)

    appointments = conn.execute(
        """SELECT * FROM appointments
           WHERE appointment_at BETWEEN ? AND ?
             AND no_reminder = 0
           ORDER BY appointment_at""",
        [now_utc.strftime('%Y-%m-%d %H:%M:%S'), window_end_utc.strftime('%Y-%m-%d %H:%M:%S')],
    ).fetchall()

    for appt in appointments:
        appt_at_str = appt["appointment_at"].replace('Z', '+00:00')
        if 'T' in appt_at_str:
            appt_at_utc = datetime.fromisoformat(appt_at_str)
            if appt_at_utc.tzinfo is not None:
                appt_at_utc = appt_at_utc.astimezone(pytz.utc).replace(tzinfo=None)
        else:
            appt_at_utc = datetime.strptime(appt_at_str, '%Y-%m-%d %H:%M:%S')
        appt_at_utc = pytz.utc.localize(appt_at_utc)
        appt_at = appt_at_utc.astimezone(tz)
        
        hours_until = (appt_at_utc - now_utc).total_seconds() / 3600

        for rule in config.NOTIFICATION_RULES:
            if not settings.is_rule_enabled(rule["name"]):
                continue

            # Timing window: target ± 15 min before / + 30 min after
            target = rule["hours_before"]
            if not (target - 0.25 <= hours_until <= target + 0.5):
                continue

            for channel in rule["channels"]:
                # Channel toggle
                if channel == "sms" and not settings.is_sms_enabled():
                    continue
                if channel == "email" and not settings.is_email_enabled():
                    continue

                to_address = (
                    appt["customer_phone"]
                    if channel == "sms"
                    else appt["customer_email"]
                )
                if not to_address:
                    stats["skipped_no_address"] += 1
                    continue

                # ── RULE 7: dedup via UNIQUE constraint ─────────────────
                try:
                    cursor = conn.execute(
                        """INSERT INTO notification_attempts
                           (appointment_id, appointment_at, rule_name,
                            channel, to_address, status)
                           VALUES (?, ?, ?, ?, ?, 'pending')""",
                        [
                            appt["id"],
                            appt["appointment_at"],
                            rule["name"],
                            channel,
                            to_address,
                        ],
                    )
                    conn.commit()
                    attempt_id = cursor.lastrowid
                except sqlite3.IntegrityError:
                    stats["skipped_dedup"] += 1
                    continue

                # ── Fetch + render template ─────────────────────────────
                tmpl = get_template(
                    conn,
                    channel,
                    appt["appointment_type"] or "all",
                    appt["language"] or "en",
                    rule["name"],
                )
                if not tmpl:
                    logger.warning(
                        "No template: channel=%s type=%s lang=%s rule=%s",
                        channel,
                        appt["appointment_type"],
                        appt["language"],
                        rule["name"],
                    )
                    continue

                appt_local = appt_at
                template_data = {
                    "customer_name": appt["customer_name"] or "Valued Customer",
                    "appointment_type": appt["appointment_type"] or "appointment",
                    "appointment_date": appt_local.strftime("%A, %B %d"),
                    "appointment_time": appt_local.strftime("%I:%M %p"),
                    "location": appt["location"] or "",
                    "calendar_source": appt["calendar_source"],
                }
                body = render_template(tmpl["body"], template_data)

                if channel == "sms":
                    # ── RULE 5: opt-out check ───────────────────────────
                    if not can_send(to_address, conn):
                        conn.execute(
                            "UPDATE notification_attempts SET status='skipped_optout' WHERE id=?",
                            [attempt_id],
                        )
                        conn.commit()
                        stats["skipped_optout"] += 1
                        continue

                    sid = sms_send(to_address, body, attempt_id, conn)
                    if sid:
                        stats["sms_sent"] += 1
                    else:
                        stats["failed"] += 1

                elif channel == "email":
                    # ── RULE 5b: opt-out check for email ────────────────
                    row = conn.execute(
                        "SELECT 1 FROM opt_outs WHERE phone=? AND channel IN ('email','all')",
                        [appt["customer_phone"]]
                    ).fetchone()
                    if row:
                        conn.execute(
                            "UPDATE notification_attempts SET status='skipped_optout' WHERE id=?",
                            [attempt_id],
                        )
                        conn.commit()
                        stats["skipped_optout"] += 1
                        continue

                    # ── RULE 6: queue, never send inline ────────────────
                    conn.execute(
                        """INSERT INTO email_queue
                           (appointment_id, to_address, rule_name,
                            template_id, template_data)
                           VALUES (?, ?, ?, ?, ?)""",
                        [
                            appt["id"],
                            to_address,
                            rule["name"],
                            tmpl["id"],
                            json.dumps(template_data),
                        ],
                    )
                    conn.commit()
                    conn.execute(
                        "UPDATE notification_attempts SET status='queued' WHERE id=?",
                        [attempt_id],
                    )
                    conn.commit()
                    stats["email_queued"] += 1

    logger.info("Engine complete: %s", stats)
    conn.close()
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=config.LOG_LEVEL)
    result = run()
    print(f"Engine complete: {result}")
