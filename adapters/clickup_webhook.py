"""adapters/clickup_webhook.py — ClickUp webhook handler.

Receives ClickUp task webhook payloads, validates HMAC signatures,
enforces idempotency, validates phone numbers, and upserts into the
appointments table or quarantines bad data.

Field mapping (all IDs loaded from .env via config.py):
  - customer_name    ← ⭐ Full Name               (text)
  - customer_phone   ← ⭐ Phone                   (phone)
  - customer_email   ← ⭐ Email                   (email)
  - appointment_type ← ⭐ Scope Of Work (Complete) (multi-select)
  - appointment_at   ← service-specific date field (epoch ms)

Tasks without a scheduled date are SKIPPED (not quarantined) —
they simply haven't been scheduled yet and will arrive via future webhooks.
"""

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
import os
import re

import phonenumbers
from fastapi import Request

import config
from db.init import get_connection
from utils.log_helpers import mask_phone, mask_name

logger = logging.getLogger(__name__)

# ── ClickUp Field IDs (from .env via config.py) ────────────────────────────
FIELD_CUSTOMER_NAME  = config.CLICKUP_FIELD_NAME   # ⭐ Full Name
FIELD_CUSTOMER_PHONE = config.CLICKUP_FIELD_PHONE  # ⭐ Phone
FIELD_CUSTOMER_EMAIL = config.CLICKUP_FIELD_EMAIL  # ⭐ Email
FIELD_SCOPE_OF_WORK  = config.CLICKUP_FIELD_SCOPE  # ⭐ Scope Of Work (Complete) — multi-select

# ── Scope UUID → Human-Readable Label Map ──────────────────────────────────
# ClickUp stores selected options as UUIDs in the Scope Of Work field.
# This lookup converts them to display names.
SCOPE_LABELS: dict[str, str] = {
    "4ddeb225-8a97-43e2-b13f-76e1ceba2421": "HVAC",
    "696b02c7-0ba1-4db9-88a8-d2046a34f988": "Heat Pump",
    "029b2cc2-32aa-45b5-a958-0fa46f96ffd4": "Mini-Split",
    "9d46ddb7-f097-4937-a3c1-1a3dcf837db6": "Central AC",
    "e7c41b98-904a-49a1-aeac-869689de714e": "Insulation",
    "c80f8596-414b-4cf0-89e9-dccf51124535": "Attic Insulation",
    "fbc67cca-743f-475d-9fe2-1f601e11de4b": "Wall Insulation",
    "cada0c3d-2e55-4e57-9190-a56b4a9286a2": "Basement Insulation",
    "37b1a40a-8ada-4365-b788-335dd400dcab": "Electrical Upgrade",
    "bcf515dc-72b3-4fa0-a9fa-438493457ab8": "Electrical",
    "af3f18c2-20b1-43f5-9e06-5095b367186c": "Main Panel Upgrade",
    "521b903b-3669-4290-8d10-c7ecd07a6cb6": "Knob & Tube",
    "046912e0-cbf5-406c-b79d-9ee844d244a1": "EV Charger",
    "502b3fcd-4b7e-48db-aace-bdbaf79ebd07": "Electric Baseboard",
    "feacb0d0-9289-46b5-aa5c-a59e68ddf982": "Meter Relocation",
    "da3d9daf-028b-41c5-a6ea-8bd4bf349c6a": "Water Heater",
    "8a43d3e6-d871-4a67-9494-cb9d55c5239a": "Oil Tank Removal",
    "4bf4a983-8444-4753-86ca-6098e7790f1e": "Boiler Removal",
    "d51c724e-a960-424d-b9a9-c2911859c8f8": "Vermiculite",
    "9408b957-b42a-4cf9-8999-2ec3a31c4f49": "Asbestos Remediation",
    "b2bc7dd2-8b93-4b3d-aec0-9e2c2273dd34": "Mold Remediation",
    "a18215d4-ee3c-432e-b180-cbdf517f8d41": "Solar",
    "d8eee3b9-2a6c-4c92-8dd2-cb2b670cb689": "Battery Storage",
    "50cdd4db-ee69-4c02-a481-62d78983808f": "Roof",
    "7dc55451-0260-43f4-8b60-f947a4d5b931": "Roof Repair",
    "3009845e-916b-468e-847d-4b2083ab1ed4": "Roof Replacement",
    "b4ecd29c-c818-403c-9737-6b8544c4568b": "Tree Work",
    "d767d7bb-2c17-4938-8345-df0fba2d51d8": "Air Sealing",
    "8e278224-a70a-44d7-9b93-053b9c104b10": "Gas Furnace",
    "f2889c32-8564-446f-99b6-562e59164d88": "Mass Save / Rebates",
    "d4958870-3efc-4066-9719-c7089853e2e3": "Energy Assessment",
    "1ede2bbd-8760-45f3-bee3-18d6a9e246a3": "Generator / Backup Power",
    "540181f5-a3ed-4fb1-9cb6-0bfd9658397f": "Maintenance",
    "aef35ff9-76a7-4c8f-aad0-f59941043feb": "Other",
}

# ── Service → Date Field mapping ───────────────────────────────────────────
# Maps each scope-of-work label (lowercase) to its corresponding date field ID.
# First match wins when a task has multiple services selected.
_SERVICE_DATE_FIELD_MAP: dict[str, str] = {
    # HVAC / Heat Pump
    "heat pump":            config.CLICKUP_FIELD_DATE_HVAC,
    "mini-split":           config.CLICKUP_FIELD_DATE_HVAC,
    "mini split":           config.CLICKUP_FIELD_DATE_HVAC,
    "hvac":                 config.CLICKUP_FIELD_DATE_HVAC,
    "central ac":           config.CLICKUP_FIELD_DATE_HVAC,
    "air handler":          config.CLICKUP_FIELD_DATE_HVAC,
    # Insulation
    "insulation":           config.CLICKUP_FIELD_DATE_INSULATION,
    "attic insulation":     config.CLICKUP_FIELD_DATE_INSULATION,
    "wall insulation":      config.CLICKUP_FIELD_DATE_INSULATION,
    "basement insulation":  config.CLICKUP_FIELD_DATE_INSULATION,
    # Electrical
    "electrical":           config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "electrical upgrade":   config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "main panel":           config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "ev charger":           config.CLICKUP_FIELD_DATE_ELECTRICAL,
    # Energy Assessment
    "energy assessment":    config.CLICKUP_FIELD_DATE_ASSESSMENT,
    # Remediation
    "vermiculite":          config.CLICKUP_FIELD_DATE_REMEDIATION,
    "asbestos":             config.CLICKUP_FIELD_DATE_REMEDIATION,
    "mold remediation":     config.CLICKUP_FIELD_DATE_REMEDIATION,
    # Solar
    "solar":                config.CLICKUP_FIELD_DATE_SOLAR,
    # Roof
    "roof":                 config.CLICKUP_FIELD_DATE_ROOF,
    "roof repair":          config.CLICKUP_FIELD_DATE_ROOF,
    "roof replacement":     config.CLICKUP_FIELD_DATE_ROOF,
}

# Fallback date field when no scope match found
_DEFAULT_DATE_FIELD = config.CLICKUP_FIELD_DATE_HVAC


# ── Signature Verification ─────────────────────────────────────────────────

def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Verify ClickUp webhook HMAC-SHA256 signature."""
    if not signature or not secret:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Idempotency ────────────────────────────────────────────────────────────

def _is_duplicate(conn, external_event_id: str) -> bool:
    row = conn.execute(
        "SELECT id FROM webhook_events WHERE source = 'clickup' AND external_event_id = ?",
        (external_event_id,),
    ).fetchone()
    return row is not None


def _record_event(conn, external_event_id: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO webhook_events (source, external_event_id, processed_at)
           VALUES ('clickup', ?, CURRENT_TIMESTAMP)""",
        (external_event_id,),
    )


# ── Audit Logging ──────────────────────────────────────────────────────────

def _audit_log(conn, action: str, entity_id: str, details: dict) -> None:
    conn.execute(
        """INSERT INTO audit_log (action, source, entity_id, details)
           VALUES (?, 'clickup_webhook', ?, ?)""",
        (action, entity_id, json.dumps(details)),
    )


# ── Field Extractors ───────────────────────────────────────────────────────

def _get_text_field(custom_fields: list, field_id: str) -> str | None:
    """Extract a plain text / email / phone custom field value."""
    if not field_id:
        return None
    for field in custom_fields:
        if field.get("id") != field_id:
            continue
        val = field.get("value")
        if isinstance(val, str) and val.strip():
            return val.strip()
        return None
    return None


def _get_date_field_raw(custom_fields: list, field_id: str) -> str | None:
    """Extract a date field and return the raw epoch-ms string."""
    if not field_id:
        return None
    for field in custom_fields:
        if field.get("id") != field_id:
            continue
        val = field.get("value")
        if val is None:
            return None
        try:
            return str(int(val))
        except (ValueError, TypeError):
            return None
    return None


def _get_scope_of_work(custom_fields: list, field_id: str) -> list[str]:
    """Extract the Scope Of Work multi-select field.

    ClickUp may return `value` in multiple formats:
      - List of option UUID strings, e.g. ["696b02c7-...", "029b2cc2-..."]
      - List of selected orderindexes, e.g. [0, 2]
      - List of dicts with "label"/"name" keys
      - type_config.options array with orderindex/name/label/id metadata

    We resolve to human-readable service names using SCOPE_LABELS first,
    then type_config.options, then direct label fields, then raw ID.
    """
    if not field_id:
        return []
    for field in custom_fields:
        if field.get("id") != field_id:
            continue
        val = field.get("value")
        if not val or not isinstance(val, list):
            return []

        options = field.get("type_config", {}).get("options", [])
        selected: list[str] = []

        # Build helpers from type_config.options
        _index_to_name: dict[int, str] = {}
        _id_to_name: dict[str, str] = {}
        for opt in options:
            idx = opt.get("orderindex")
            opt_id = opt.get("id")
            name = (opt.get("name") or opt.get("label") or "").strip()
            if isinstance(idx, int):
                _index_to_name[idx] = name or str(idx)
            if isinstance(opt_id, str) and opt_id:
                _id_to_name[opt_id] = name or opt_id

        for item in val:
            if isinstance(item, str) and not item.strip():
                continue
            # UUID string values
            if isinstance(item, str):
                label = SCOPE_LABELS.get(item) or _id_to_name.get(item)
                if not label:
                    label = item
                selected.append(label)
                continue
            # Integer order indexes
            if isinstance(item, int):
                selected.append(_index_to_name.get(item, str(item)))
                continue
            # Dict variants
            if isinstance(item, dict):
                label = (item.get("label") or item.get("name") or "").strip()
                if label:
                    selected.append(label)
                    continue
                item_id = item.get("id")
                if isinstance(item_id, str):
                    selected.append(SCOPE_LABELS.get(item_id) or _id_to_name.get(item_id) or item_id)
                    continue

        return selected
    return []


def _epoch_ms_to_datetime_str(epoch_ms_str: str | None) -> str | None:
    """Convert an epoch-milliseconds string to 'YYYY-MM-DD HH:MM:SS' (UTC)."""
    if not epoch_ms_str:
        return None
    try:
        epoch_ms = int(epoch_ms_str)
        utc_dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
        return utc_dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError) as e:
        logger.warning("Failed to parse epoch ms '%s': %s", epoch_ms_str, e)
        return None


def _resolve_date_field(service_labels: list[str]) -> str:
    """Return the correct date field ID for the given list of service labels.

    Iterates through the labels in order and returns the first field ID found
    in the service→date map. Falls back to HVAC date field.
    """
    for label in service_labels:
        field_id = _SERVICE_DATE_FIELD_MAP.get(label.lower().strip())
        if field_id:
            return field_id
    return _DEFAULT_DATE_FIELD


# ── Phone Validation ───────────────────────────────────────────────────────

def _validate_phone(phone_str: str | None) -> str | None:
    """Validate and format to E.164. Returns None if invalid."""
    if not phone_str or not phone_str.strip():
        return None
    try:
        parsed = phonenumbers.parse(phone_str.strip(), "US")
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    return None


# ── Quarantine ─────────────────────────────────────────────────────────────

def _quarantine(conn, data: dict, reason: str) -> None:
    """Insert a record into appointment_quarantine and audit log."""
    conn.execute(
        """INSERT INTO appointment_quarantine
           (gcal_event_id, calendar_source, raw_title, raw_description,
            appointment_at, quarantine_reason)
           VALUES (?, 'clickup', ?, ?, ?, ?)""",
        (
            f"clickup_{data['task_id']}",
            data.get("raw_title"),
            data.get("raw_description"),
            data.get("appointment_at"),
            reason,
        ),
    )
    _audit_log(conn, "quarantined", f"clickup_{data['task_id']}", {
        "reason":        reason,
        "task_id":       data["task_id"],
        "customer_name": data.get("customer_name"),
        "raw_phone":     data.get("customer_phone"),
    })
    logger.warning(
        "Quarantined ClickUp task %s: %s (name=%s, phone=%s)",
        data["task_id"], reason,
        mask_name(data.get("customer_name")), mask_phone(data.get("customer_phone")),
    )


# ── Payload Extraction ─────────────────────────────────────────────────────

def _extract_appointment_data(payload: dict) -> dict:
    """Extract all appointment fields from a ClickUp webhook payload."""
    task             = payload.get("task", payload)
    task_id          = task.get("id", "")
    task_name        = task.get("name", "").strip()
    task_description = (task.get("description") or "").strip()
    custom_fields    = task.get("custom_fields", [])

    # Extract customer name and location from task name (split on first pipe)
    parts = task_name.split('|')
    customer_name = parts[0].strip() if parts[0].strip() else None
    location = parts[1].strip() if len(parts) > 1 else None

    # ── Phone & Email (extraction unchanged — was already working correctly)
    customer_phone = _get_text_field(custom_fields, FIELD_CUSTOMER_PHONE)
    customer_email = _get_text_field(custom_fields, FIELD_CUSTOMER_EMAIL)

    # ── Appointment Type: ⭐ Scope Of Work (Complete) — multi-select
    # Gives the actual service ("Heat Pump", "Insulation"), NOT the payment method.
    service_labels   = _get_scope_of_work(custom_fields, FIELD_SCOPE_OF_WORK)
    appointment_type = ", ".join(service_labels) if service_labels else "service"

    # ── Appointment Date: service-specific date field (epoch ms)
    # Different services have different date fields; resolve based on scope.
    date_field_id = _resolve_date_field(service_labels)
    raw_date      = _get_date_field_raw(custom_fields, date_field_id)
    appointment_at = _epoch_ms_to_datetime_str(raw_date)

    return {
        "task_id":          task_id,
        "customer_name":    customer_name,
        "customer_phone":   customer_phone,
        "customer_email":   customer_email,
        "appointment_at":   appointment_at,   # None = not yet scheduled
        "appointment_type": appointment_type,
        "location":         location,
        "service_labels":   service_labels,   # kept for logging
        "date_field_used":  date_field_id,    # kept for logging
        "raw_title":        task_name,
        "raw_description":  task_description[:500] if task_description else None,
    }


# ── Main Processor ─────────────────────────────────────────────────────────

def process_webhook(payload: dict) -> dict:
    """Process a single ClickUp webhook payload.

    Returns {"status": "ok", "action": "created|updated|quarantined|duplicate|skipped"}
    """
    event = payload.get("event", "")
    if event not in ("taskCreated", "taskUpdated"):
        logger.debug("Ignoring ClickUp event type: %s", event)
        return {"status": "ok", "action": "skipped", "reason": f"ignored event: {event}"}

    history_items     = payload.get("history_items", [])
    history_id        = history_items[0].get("id", "") if history_items else str(int(time.time()))
    task_data         = payload.get("task", payload)
    task_id           = task_data.get("id", "")

    if not task_id:
        logger.warning("ClickUp webhook missing task_id — skipping")
        return {"status": "ok", "action": "skipped", "reason": "no task_id"}

    external_event_id = f"{task_id}_{history_id}"

    conn = get_connection()
    try:
        # ── Idempotency ────────────────────────────────────────────────────
        if _is_duplicate(conn, external_event_id):
            logger.info("Duplicate ClickUp webhook %s — skipping", external_event_id)
            return {"status": "ok", "action": "duplicate"}

        # ── Extract fields ──────────────────────────────────────────────────
        data = _extract_appointment_data(payload)

        # ── Skip tasks with no Installation Start Date (not yet scheduled) ──
        # These are valid tasks that simply haven't been scheduled yet.
        # They will arrive again via webhook when the date is set.
        if not data["appointment_at"]:
            logger.info(
                "Skipping ClickUp task %s (%r): no scheduled date in field %s (scope: %s)",
                task_id,
                data.get("raw_title"),
                data.get("date_field_used", "unknown"),
                data.get("service_labels"),
            )
            _record_event(conn, external_event_id)
            conn.commit()
            return {"status": "ok", "action": "skipped", "reason": "no_scheduled_date"}

        # ── Validate: customer name ─────────────────────────────────────────
        if not data["customer_name"] or not data["customer_name"].strip():
            _quarantine(conn, data, "missing_name")
            _record_event(conn, external_event_id)
            conn.commit()
            return {"status": "ok", "action": "quarantined", "reason": "missing_name"}

        # ── Validate: phone number ──────────────────────────────────────────
        formatted_phone = _validate_phone(data["customer_phone"])
        if not formatted_phone:
            reason = "missing_phone" if not data["customer_phone"] else "invalid_phone"
            _quarantine(conn, data, reason)
            _record_event(conn, external_event_id)
            conn.commit()
            return {"status": "ok", "action": "quarantined", "reason": reason}

        # ── UPSERT into appointments ────────────────────────────────────────
        appt_id  = f"clickup_{task_id}"
        existing = conn.execute(
            "SELECT id FROM appointments WHERE id = ?", (appt_id,)
        ).fetchone()

        conn.execute(
            """INSERT INTO appointments
               (id, calendar_source, customer_name, customer_phone, customer_email,
                appointment_at, appointment_type, location, notes,
                language, language_source, no_reminder,
                raw_title, raw_description, synced_at, updated_at)
               VALUES (?, 'clickup', ?, ?, ?, ?, ?, ?, ?,
                       'en', 'default', FALSE,
                       ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET
                   customer_name    = excluded.customer_name,
                   customer_phone   = excluded.customer_phone,
                   customer_email   = excluded.customer_email,
                   appointment_at   = excluded.appointment_at,
                   appointment_type = excluded.appointment_type,
                   location         = excluded.location,
                   notes            = excluded.notes,
                   raw_title        = excluded.raw_title,
                   raw_description  = excluded.raw_description,
                   synced_at        = CURRENT_TIMESTAMP,
                   updated_at       = CURRENT_TIMESTAMP
            """,
            (
                appt_id,
                data["customer_name"].strip(),
                formatted_phone,
                data.get("customer_email"),
                data["appointment_at"],
                data["appointment_type"],
                data["location"],
                data.get("raw_description"),   # notes = raw_description
                data.get("raw_title"),
                data.get("raw_description"),
            ),
        )

        action = "appointment_updated" if existing else "appointment_created"
        _audit_log(conn, action, appt_id, {
            "task_id":          task_id,
            "customer_name":    data["customer_name"],
            "customer_phone":   formatted_phone,
            "appointment_at":   data["appointment_at"],
            "appointment_type": data["appointment_type"],
            "scope_labels":     data["service_labels"],
        })

        _record_event(conn, external_event_id)
        conn.commit()

        logger.info(
            "ClickUp %s: id=%s name=%r phone=%s type=%r at=%s",
            action, appt_id,
            mask_name(data["customer_name"]), mask_phone(formatted_phone),
            data["appointment_type"], data["appointment_at"],
        )
        return {"status": "ok", "action": "created" if not existing else "updated"}

    except Exception as e:
        logger.error("Error processing ClickUp webhook: %s", e, exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"status": "ok", "action": "error", "reason": str(e)}
    finally:
        conn.close()
