"""Google Calendar sync — Phase 1.

Main function: sync_calendars(dry_run=False)

For each configured calendar, fetches events in the next 72 hours,
parses customer data, validates phones, extracts language/tags, and
upserts into the appointments table. Bad data is quarantined (Rule 8).
"""
import argparse
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import pytz
import phonenumbers
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

import config
from db.init import get_connection

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Regex patterns ──────────────────────────────────────────────────────────
_LANG_TAG_RE = re.compile(r"\[LANG:(EN|PT|ES)\]", re.IGNORECASE)
_NO_REMINDER_RE = re.compile(r"\[NO\s*REMINDER\]", re.IGNORECASE)
_APPT_TYPE_KEYWORDS = {
    "estimate": "estimate",
    "install": "install",
    "service": "service",
    "repair": "service",
    "inspection": "inspection",
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _extract_customer_name(summary: str) -> str | None:
    """Extract customer name from event summary.

    Text before first comma, or before " - ", or the full summary.
    Returns None only if summary is empty/whitespace.
    """
    if not summary or not summary.strip():
        return None
    # Try comma first
    if "," in summary:
        return summary.split(",", 1)[0].strip() or None
    # Try dash separator
    if " - " in summary:
        return summary.split(" - ", 1)[0].strip() or None
    return summary.strip()


def _extract_phones(text: str) -> list[str]:
    """Return list of E.164 formatted valid phone numbers found in text."""
    phones: list[str] = []
    for match in phonenumbers.PhoneNumberMatcher(text, "US"):
        if phonenumbers.is_valid_number(match.number):
            phones.append(
                phonenumbers.format_number(
                    match.number, phonenumbers.PhoneNumberFormat.E164
                )
            )
    return phones


def _extract_language(title: str, description: str) -> tuple[str, str]:
    """Return (language, language_source)."""
    combined = f"{title} {description}"
    m = _LANG_TAG_RE.search(combined)
    if m:
        return m.group(1).lower(), "tag"
    return "en", "default"


def _has_no_reminder(title: str, description: str) -> bool:
    combined = f"{title} {description}"
    return bool(_NO_REMINDER_RE.search(combined))


def _detect_appointment_type(title: str) -> str:
    lower_title = title.lower()
    for keyword, appt_type in _APPT_TYPE_KEYWORDS.items():
        if keyword in lower_title:
            return appt_type
    return "service"


def _get_appointment_at(event: dict) -> str | None:
    """Extract appointment datetime ISO string from event start."""
    start = event.get("start", {})
    raw_date = start.get("dateTime") or start.get("date")
    if not raw_date:
        return None
    
    try:
        dt = datetime.fromisoformat(raw_date)
    except ValueError:
        return None

    # If it's a date-only (all-day event), assume midnight in local TZ
    if len(raw_date) == 10:  # YYYY-MM-DD
        tz = pytz.timezone(config.TZ)
        dt = tz.localize(dt)
        
    # If no timezone info, assume local TZ
    if dt.tzinfo is None:
        tz = pytz.timezone(config.TZ)
        dt = tz.localize(dt)
        
    # Convert to UTC and format as 'YYYY-MM-DD HH:MM:SS'
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime('%Y-%m-%d %H:%M:%S')


# ── Quarantine ──────────────────────────────────────────────────────────────

def _quarantine(conn, event: dict, calendar_source: str, reason: str):
    """Write a bad event to appointment_quarantine. Rule 8."""
    gcal_event_id = event.get("id", "unknown")
    raw_title = event.get("summary", "")
    raw_description = event.get("description", "")
    appointment_at = _get_appointment_at(event)

    logger.warning("QUARANTINE [%s] event=%s", reason, gcal_event_id)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO appointment_quarantine
                (gcal_event_id, calendar_source, raw_title, raw_description,
                 appointment_at, quarantine_reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (gcal_event_id, calendar_source, raw_title, raw_description,
             appointment_at, reason),
        )
        conn.commit()
    except Exception as e:
        logger.error("Failed to quarantine event %s: %s", gcal_event_id, e)
        conn.rollback()


# ── Per-event processing ────────────────────────────────────────────────────

def _process_event(conn, event: dict, calendar_source: str, dry_run: bool,
                   counters: dict):
    """Process a single calendar event. Returns nothing; mutates counters."""
    gcal_event_id = event.get("id", "")
    summary = event.get("summary", "")
    description = event.get("description", "")
    appointment_at = _get_appointment_at(event)

    if not appointment_at:
        _quarantine(conn, event, calendar_source, "parse_error")
        counters["quarantined"] += 1
        return

    # 1. Customer name
    customer_name = _extract_customer_name(summary)
    if not customer_name:
        _quarantine(conn, event, calendar_source, "missing_name")
        counters["quarantined"] += 1
        return

    # 2. Phone extraction
    text_to_scan = f"{summary} {description}"
    phones = _extract_phones(text_to_scan)

    if len(phones) == 0:
        # Try just description with raw digit patterns
        raw_phones = _extract_phones(description)
        if len(raw_phones) == 0:
            _quarantine(conn, event, calendar_source, "missing_phone")
            counters["quarantined"] += 1
            return
        phones = raw_phones

    if len(phones) >= 2:
        _quarantine(conn, event, calendar_source, "ambiguous_customer")
        counters["quarantined"] += 1
        return

    customer_phone = phones[0]

    # Re-validate (belt and suspenders — _extract_phones already validates)
    parsed = phonenumbers.parse(customer_phone, "US")
    if not phonenumbers.is_valid_number(parsed):
        _quarantine(conn, event, calendar_source, "invalid_phone")
        counters["quarantined"] += 1
        return

    # 3. Language
    language, language_source = _extract_language(summary, description)

    # 4. No-reminder flag
    no_reminder = _has_no_reminder(summary, description)

    # 5. Appointment type
    appointment_type = _detect_appointment_type(summary)

    # 6. Location / notes
    location = event.get("location", "")
    notes = description

    # 7. Check existing row
    existing = conn.execute(
        "SELECT appointment_at FROM appointments WHERE id = ?", (gcal_event_id,)
    ).fetchone()

    if dry_run:
        action = "INSERT"
        if existing:
            if existing["appointment_at"] == appointment_at:
                action = "SKIP (no change)"
            else:
                action = "UPDATE (rescheduled)"
        print(
            f"[DRY RUN] {action}: id={gcal_event_id}, name={customer_name}, "
            f"phone={customer_phone}, at={appointment_at}, type={appointment_type}, "
            f"lang={language}, no_reminder={no_reminder}"
        )
        if action.startswith("SKIP"):
            counters["skipped"] += 1
        else:
            counters["upserted"] += 1
        return

    if existing and existing["appointment_at"] == appointment_at:
        # No change to time — update synced_at and calendar_source
        conn.execute(
            "UPDATE appointments SET synced_at = CURRENT_TIMESTAMP, calendar_source = ? WHERE id = ?",
            (calendar_source, gcal_event_id),
        )
        conn.commit()
        counters["skipped"] += 1
        return

    # 8. UPSERT
    conn.execute(
        """
        INSERT INTO appointments
            (id, calendar_source, customer_name, customer_phone, customer_email,
             technician_email, appointment_at, appointment_type, location, notes,
             language, language_source, no_reminder, raw_title, raw_description,
             synced_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            calendar_source = excluded.calendar_source,
            customer_name = excluded.customer_name,
            customer_phone = excluded.customer_phone,
            appointment_at = excluded.appointment_at,
            appointment_type = excluded.appointment_type,
            location = excluded.location,
            notes = excluded.notes,
            language = excluded.language,
            language_source = excluded.language_source,
            no_reminder = excluded.no_reminder,
            raw_title = excluded.raw_title,
            raw_description = excluded.raw_description,
            synced_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            gcal_event_id, calendar_source, customer_name, customer_phone,
            None,  # customer_email
            None,  # technician_email
            appointment_at, appointment_type, location, notes,
            language, language_source, no_reminder, summary, description,
        ),
    )
    conn.commit()
    counters["upserted"] += 1


# ── Main sync function ─────────────────────────────────────────────────────

def sync_calendars(dry_run: bool = False):
    """Sync all configured Google Calendar sources.

    Fetches events from now to now+72h, parses and validates each event,
    and upserts valid ones into the appointments table. Bad events are
    quarantined with a reason code (Rule 8).
    """
    if not config.GOOGLE_CALENDAR_IDS:
        logger.error("No GOOGLE_CALENDAR_IDS configured. Nothing to sync.")
        return

    # Build Google Calendar API service
    try:
        creds = Credentials.from_service_account_file(
            config.GOOGLE_CREDENTIALS_PATH,
            scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        )
        service = build("calendar", "v3", credentials=creds)
    except Exception as e:
        logger.error("Failed to initialise Google Calendar API: %s", e)
        return

    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(hours=72)).isoformat()

    counters = {"upserted": 0, "quarantined": 0, "skipped": 0}
    conn = get_connection()

    try:
        for cal_id in config.GOOGLE_CALENDAR_IDS:
            calendar_source = config.CALENDAR_SOURCE_MAP.get(cal_id, "other")
            logger.info("Syncing calendar %s (source=%s)", cal_id, calendar_source)

            try:
                events_result = service.events().list(
                    calendarId=cal_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                ).execute()
            except Exception as e:
                logger.error("Error fetching calendar %s: %s", cal_id, e)
                continue

            events = events_result.get("items", [])
            logger.info("Found %d events in %s", len(events), cal_id)

            for event in events:
                _process_event(conn, event, calendar_source, dry_run, counters)
    finally:
        conn.close()

    summary_msg = (
        f"Sync complete: {counters['upserted']} appointments upserted, "
        f"{counters['quarantined']} quarantined, "
        f"{counters['skipped']} skipped (no change)"
    )
    logger.info(summary_msg)
    print(summary_msg)


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sync Google Calendar events to the appointments database."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be inserted without writing to the database.",
    )
    args = parser.parse_args()
    sync_calendars(dry_run=args.dry_run)
