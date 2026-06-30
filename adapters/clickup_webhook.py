"""adapters/clickup_webhook.py — ClickUp webhook handler.
see if the git is actually working lala
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
# These UUIDs come from type_config.options on field 34fa9090-98fc-4ae6-be6a-2da9c688c018.
# Verified against live API on 2026-06-30.
SCOPE_LABELS: dict[str, str] = {
    # HVAC
    "cc6fe608-05ca-4298-8f4c-e0df19a756fa": "HVAC",
    "d8ca3147-0185-47b0-ae61-44eaebc89dd6": "Heat Pump",
    "c56e9a03-0d34-4bd6-9cb3-853571f06e0c": "R410 - Mini VRF",
    "c0560a98-619d-4b1d-aa3c-1f4a78f3603d": "Mini-Split",
    "6980e526-0f1c-44c6-978b-e0ee5f3e13d3": "Central AC",
    "82bcbc10-d3b2-4713-8feb-335475b2fbe1": "Air Handler",
    "f0ad4286-a927-40e0-b259-212f7d1677e2": "Gas Furnace",
    "22dd2511-7574-4bee-bb1e-264b17682093": "Boiler",
    "6e79b69e-5a47-4a64-b3e3-65a762787ea0": "Baseboard Heater",
    "fbb30366-1a60-4c48-af3b-bcfb03989d3b": "Water Heater",
    "ec5a5fee-e03b-4b79-841a-dbf5701604e8": "Oil Tank Removal",
    "f7f0a4b0-22e2-4e3c-9f11-39607f71c9a7": "Boiler Removal",
    # Insulation
    "f4307032-6e54-4ae6-bd31-4f97099a26b8": "Insulation",
    "8d4514d4-79d5-4d41-a276-2d0c458f7551": "Attic Insulation",
    "c15958fe-cab4-4e1a-b1a2-4599b04a3a89": "Wall Insulation",
    "7e8dacef-ba87-4c2d-abbd-21dbd0d45c55": "Basement/Crawl Insulation",
    "f534e9a5-7097-45ea-9463-d6cd4cdb9498": "Air Sealing",
    "42b9c79b-c8e4-44d5-adcb-cd2cb6e40008": "Duct Sealing",
    "84a51901-1de3-4072-b0ae-37205e168e9b": "Ductwork",
    # Electrical
    "dd14a99e-98cf-40ad-bf4c-ccd6ab14e1a1": "Electrical",
    "7dd77582-b573-4240-9057-8a4027a2753d": "Electrical Upgrade",
    "0a6a642b-1a33-4ebb-acb4-bb074a94f956": "Main Panel Upgrade",
    "5c4a2c4e-05c2-47a4-a8c7-bccc63138db0": "Knob & Tube",
    "491c5005-52a5-4b39-9ff5-367614468701": "Electric Baseboard",
    "9ae51236-c0ba-40d8-baca-9b16b9f6cc93": "Electric Car Charging Station",
    "bc0fd96f-56c1-41f8-8804-6407842f17d8": "EV Charger",
    "ba2aef2e-8f65-4655-a050-aa58659a9480": "Meter Relocation",
    "ca56e81a-1236-4729-9b55-66d98ecbd552": "Switches/Outlets/Fixtures - Install",
    "495a79c3-c211-420f-b147-9f63ddd64089": "Interior Lighting - Install",
    "131104a0-0801-4cbf-8b0b-e08ba801ac8d": "Generator / Backup Power",
    # Assessment
    "0621d49c-f2be-4533-9540-96086057a4e1": "Energy Assessment",
    # Remediation
    "41e294c6-1c04-42bf-b79d-49f1a10a4cee": "Vermiculite Remediation",
    "9890794d-306e-4f9d-919e-e23de37a9132": "Asbestos Remediation",
    "2e3e72ed-6d5c-4d7a-96c0-728688d82f3a": "Mold Remediation",
    # Solar
    "dc2cd534-bc6b-4f13-8ce1-86969c6a0ec1": "Solar",
    "9be2d9e3-485a-411b-8d4e-315daa5cb8a8": "Battery Storage",
    # Roof
    "be9ad691-3151-4e4c-8ee2-0faf9b06d32d": "Roof",
    "906a2c8a-f24b-4aa3-ab8c-af37ba1c8728": "Roof Repair",
    "55c9407e-7b3e-4a67-b7c5-bdfa68d0f46e": "Roof Replacement",
    "bca8904d-3b13-4cc1-8600-fd69b90c1b8a": "Gutter Work",
    # Other
    "7d073b58-bb9d-4b52-8acc-ffef28585e84": "Mass Save / Rebates",
    "77a2951f-383d-408e-93c7-7cf666cd9ed9": "Tree Work",
    "18ef3837-66a0-4d8b-a330-a7e9cbd6a7a6": "Structural Repairs",
    "f75587d4-fbae-4814-90bd-9c8b11ab1ccc": "Vent Relocation",
    "c427054f-83dc-49dc-af5d-5f03e2485b14": "Chimney Work",
    "73baddde-6383-4ec8-9f45-809beb3eeb9d": "Maintenance",
    "25b3ac69-913b-4a76-8ff1-759ee4f354cb": "Drywall / Finish Repair",
    "0b94d381-0111-4d1f-ad0e-1b9fa84d3c3a": "WINDOWS",
    "de92cd50-0dd4-42df-b951-39b9cb338e43": "Commercial Mass Save",
    "faad5ea7-ba2a-49b4-82b1-cd65986880d2": "Pergola",
    "5fcf594a-c600-41c9-bf1d-bc942a63d92e": "Payoff Electric Bill",
    "c6824339-2204-43b6-9325-5f6fce55f8c1": "Other / Manual Entry X",
    "de93ec40-3600-40a1-8c2c-b810be9c9f1c": "Urgent Attention X",
    "f37ae3de-bc0d-4d15-8ccf-ded18ee06326": "Credit Work X",
    "aa0dc574-429a-4db5-8964-3afe1e2c66cb": "Appliances X",
    "b1ff0fbb-547f-4496-98be-60acbd42c2bb": "Apply for Permit X",
    "5f66261e-3728-475b-83e4-4bd7f872703c": "Temecula Remediation X",
    "61a7a104-dad4-4846-b0a0-68afff7b764f": "Air Handler + Heat Pump X",
    "468098a5-e320-4144-91fd-fc6df590120e": "NTP Palmetto X",
}

# ── Service → Date Field mapping ───────────────────────────────────────────
_SERVICE_DATE_FIELD_MAP: dict[str, str] = {
    # HVAC
    "HVAC": config.CLICKUP_FIELD_DATE_HVAC,
    "Heat Pump": config.CLICKUP_FIELD_DATE_HVAC,
    "R410 - Mini VRF": config.CLICKUP_FIELD_DATE_HVAC,
    "Mini-Split": config.CLICKUP_FIELD_DATE_HVAC,
    "Central AC": config.CLICKUP_FIELD_DATE_HVAC,
    "Air Handler": config.CLICKUP_FIELD_DATE_HVAC,
    "Gas Furnace": config.CLICKUP_FIELD_DATE_HVAC,
    "Boiler": config.CLICKUP_FIELD_DATE_HVAC,
    "Baseboard Heater": config.CLICKUP_FIELD_DATE_HVAC,
    "Water Heater": config.CLICKUP_FIELD_DATE_HVAC,
    "Oil Tank Removal": config.CLICKUP_FIELD_DATE_HVAC,
    "Boiler Removal": config.CLICKUP_FIELD_DATE_HVAC,
    "Air Handler + Heat Pump X": config.CLICKUP_FIELD_DATE_HVAC,
    # Insulation
    "Insulation": config.CLICKUP_FIELD_DATE_INSULATION,
    "Attic Insulation": config.CLICKUP_FIELD_DATE_INSULATION,
    "Wall Insulation": config.CLICKUP_FIELD_DATE_INSULATION,
    "Basement/Crawl Insulation": config.CLICKUP_FIELD_DATE_INSULATION,
    "Air Sealing": config.CLICKUP_FIELD_DATE_INSULATION,
    "Duct Sealing": config.CLICKUP_FIELD_DATE_INSULATION,
    "Ductwork": config.CLICKUP_FIELD_DATE_INSULATION,
    "Mass Save / Rebates": config.CLICKUP_FIELD_DATE_INSULATION,
    # Electrical
    "Electrical": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "Electrical Upgrade": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "Main Panel Upgrade": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "Knob & Tube": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "Electric Baseboard": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "Electric Car Charging Station": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "EV Charger": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "Meter Relocation": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "Switches/Outlets/Fixtures - Install": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "Interior Lighting - Install": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "Generator / Backup Power": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    # Assessment
    "Energy Assessment": config.CLICKUP_FIELD_DATE_ASSESSMENT,
    # Remediation
    "Vermiculite Remediation": config.CLICKUP_FIELD_DATE_REMEDIATION,
    "Asbestos Remediation": config.CLICKUP_FIELD_DATE_REMEDIATION,
    "Mold Remediation": config.CLICKUP_FIELD_DATE_REMEDIATION,
    "Temecula Remediation X": config.CLICKUP_FIELD_DATE_REMEDIATION,
    # Solar
    "Solar": config.CLICKUP_FIELD_DATE_SOLAR,
    "Battery Storage": config.CLICKUP_FIELD_DATE_SOLAR,
    # Roof
    "Roof": config.CLICKUP_FIELD_DATE_ROOF,
    "Roof Repair": config.CLICKUP_FIELD_DATE_ROOF,
    "Roof Replacement": config.CLICKUP_FIELD_DATE_ROOF,
    "Gutter Work": config.CLICKUP_FIELD_DATE_ROOF,
}



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


def _resolve_date_fields(service_labels: list[str]) -> list[str]:
    """Return an ordered list of date field IDs mapped from the task's scope labels.

    Only returns fields that the scope labels explicitly map to via
    _SERVICE_DATE_FIELD_MAP. If no label maps to a field, returns an empty
    list and the task is skipped as "no scheduled date."
    """
    seen: set[str] = set()
    ordered: list[str] = []

    for label in service_labels:
        fid = _SERVICE_DATE_FIELD_MAP.get(label.strip())
        if fid and fid not in seen:
            seen.add(fid)
            ordered.append(fid)

    return ordered


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

    # Extract customer name from the dedicated field when present; otherwise fall back to the task title.
    parts = task_name.split('|')
    customer_name = _get_text_field(custom_fields, FIELD_CUSTOMER_NAME)
    if not customer_name:
        customer_name = parts[0].strip() if parts[0].strip() else None
    location = parts[1].strip() if len(parts) > 1 else None

    # ── Phone & Email (extraction unchanged — was already working correctly)
    customer_phone = _get_text_field(custom_fields, FIELD_CUSTOMER_PHONE)
    customer_email = _get_text_field(custom_fields, FIELD_CUSTOMER_EMAIL)

    # ── Appointment Type: ⭐ Scope Of Work (Complete) — multi-select
    # Gives the actual service ("Heat Pump", "Insulation"), NOT the payment method.
    service_labels   = _get_scope_of_work(custom_fields, FIELD_SCOPE_OF_WORK)
    appointment_type = ", ".join(service_labels) if service_labels else "service"

    # ── Appointment Date: try date fields in priority order until one has a value
    date_field_id  = None
    raw_date       = None
    for fid in _resolve_date_fields(service_labels):
        raw_date = _get_date_field_raw(custom_fields, fid)
        if raw_date:
            date_field_id = fid
            break
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

    history_items = payload.get("history_items", [])
    if history_items:
        event_id = history_items[0].get("id", "")
    else:
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        event_id = f"hash_{payload_hash}"

    task_data = payload.get("task", payload)
    task_id = task_data.get("id", "")

    if not task_id:
        logger.warning("ClickUp webhook missing task_id — skipping")
        return {"status": "ok", "action": "skipped", "reason": "no task_id"}

    external_event_id = f"{task_id}_{event_id}"

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
