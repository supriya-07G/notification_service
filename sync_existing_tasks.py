"""sync_existing_tasks.py — One-time import of existing ClickUp tasks.

Fetches all non-archived tasks from the ACTIVE HVAC ClickUp list and
inserts them into the appointments table. Auto-resolves quarantine records
when data is fixed, and cleans up orphaned quarantine records.
"""

import sys
import time
import json
import sqlite3
import logging
from datetime import datetime, timezone

import requests
import phonenumbers
from dotenv import load_dotenv

load_dotenv()
import config
from db.init import get_connection

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────

LIST_ID   = "901317175958"
API_TOKEN = config.CLICKUP_API_TOKEN
PAGE_SIZE = 100

CLICKUP_FIELD_NAME = config.CLICKUP_FIELD_NAME
CLICKUP_FIELD_PHONE = config.CLICKUP_FIELD_PHONE
CLICKUP_FIELD_EMAIL = config.CLICKUP_FIELD_EMAIL
CLICKUP_FIELD_SCOPE = config.CLICKUP_FIELD_SCOPE
CLICKUP_FIELD_PROGRAM = "8fcd3ac4-8323-4f09-8826-c5d8e96fbd46"

SCOPE_LABELS = {
    # HVAC — verified against live API 2026-06-30
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
    "5f66261e-3728-475b-83e4-4bd7f872703c": "Temecula Remediation X",
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
    "61a7a104-dad4-4846-b0a0-68afff7b764f": "Air Handler + Heat Pump X",
    "468098a5-e320-4144-91fd-fc6df590120e": "NTP Palmetto X",
}

SERVICE_DATE_MAP = {
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

# Allowlist of custom-field IDs holding real SCHEDULED/UPCOMING appointment dates.
# Date selection reads ONLY these, INDEPENDENT of Scope Of Work — so a task with
# no scope set still resolves its dates. Completion/marker fields (e.g.
# "Installation Completed", "Close Date") are deliberately EXCLUDED: they record
# when work finished or a deal closed, not when the customer is expected, and must
# never be chosen as the appointment time. Deduped, empty IDs dropped.
APPOINTMENT_DATE_FIELDS = list(dict.fromkeys(
    fid for fid in (
        config.CLICKUP_FIELD_DATE_HVAC,
        config.CLICKUP_FIELD_DATE_INSULATION,
        config.CLICKUP_FIELD_DATE_ELECTRICAL,
        config.CLICKUP_FIELD_DATE_ASSESSMENT,
        config.CLICKUP_FIELD_DATE_REMEDIATION,
        config.CLICKUP_FIELD_DATE_SOLAR,
        config.CLICKUP_FIELD_DATE_ROOF,
    ) if fid
))


stats = {"fetched": 0, "inserted": 0, "updated": 0, "quarantined": 0, "skipped": 0, "resolved": 0, "orphaned_cleaned": 0}

# ── API Fetching ───────────────────────────────────────────────────────────

def fetch_all_tasks() -> list[dict]:
    """Fetch all non-archived tasks from the ClickUp list with retries."""
    if not API_TOKEN:
        logger.error("FATAL: CLICKUP_API_TOKEN is missing from config.")
        sys.exit(1)

    headers = {"Authorization": API_TOKEN}
    url = f"https://api.clickup.com/api/v2/list/{LIST_ID}/task"
    all_tasks = []
    page = 0

    logger.info(f"Fetching tasks from ClickUp list {LIST_ID}...")

    while True:
        params = {"page": page, "limit": PAGE_SIZE, "archived": "false", "include_closed": "true"}
        success = False
        
        for attempt, backoff in enumerate([1, 2, 4]):
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                success = True
                break
            except requests.RequestException as e:
                logger.warning(f"  Attempt {attempt + 1} failed for page {page}: {e}")
                time.sleep(backoff)
                
        if not success:
            logger.error(f"  ERROR: Failed to fetch page {page} after 3 attempts.")
            break

        tasks = resp.json().get("tasks", [])
        if not tasks:
            break

        all_tasks.extend(tasks)
        logger.info(f"  Page {page + 1}: {len(tasks)} tasks (total: {len(all_tasks)})")

        if len(tasks) < PAGE_SIZE:
            break

        page += 1
        time.sleep(0.3)

    stats["fetched"] = len(all_tasks)
    return all_tasks


# ── Field Extraction ───────────────────────────────────────────────────────

def _get_text_field(custom_fields: list, field_id: str) -> str | None:
    for field in custom_fields:
        if field.get("id") == field_id:
            val = field.get("value")
            if isinstance(val, str) and val.strip():
                return val.strip()
            # ClickUp may return phone/number fields as a numeric scalar (e.g. a
            # phone entered without a leading '+'). Coerce so it isn't dropped.
            if isinstance(val, (int, float)):
                return str(val)
    return None

def _get_scope_of_work(custom_fields: list) -> list[str]:
    for field in custom_fields:
        if field.get("id") == CLICKUP_FIELD_SCOPE:
            val = field.get("value")
            if not val or not isinstance(val, list):
                return []

            options = field.get("type_config", {}).get("options", [])
            _index_to_name: dict[int, str] = {}
            _id_to_name: dict[str, str] = {}
            for opt in options:
                name = (opt.get("name") or opt.get("label") or "").strip()
                idx = opt.get("orderindex")
                opt_id = opt.get("id")
                if isinstance(idx, int):
                    _index_to_name[idx] = name or str(idx)
                if isinstance(opt_id, str) and opt_id:
                    _id_to_name[opt_id] = name or opt_id

            selected = []
            for item in val:
                if isinstance(item, str) and item.strip():
                    label = SCOPE_LABELS.get(item) or _id_to_name.get(item) or item.strip()
                    selected.append(label)
                elif isinstance(item, int):
                    selected.append(_index_to_name.get(item, str(item)))
                elif isinstance(item, dict):
                    label = (item.get("label") or item.get("name") or "").strip()
                    if label:
                        selected.append(label)
                    elif item.get("id"):
                        item_id = item["id"]
                        selected.append(SCOPE_LABELS.get(item_id) or _id_to_name.get(item_id) or item_id)
            return selected
    return []

def _validate_phone(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    try:
        parsed = phonenumbers.parse(raw.strip(), "US")
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    return None


# ── Task Processing ────────────────────────────────────────────────────────

def process_task(task: dict, conn: sqlite3.Connection) -> str | None:
    """Process a single task. Returns the task_id if processed, else None."""
    task_id = task.get("id", "")
    task_name = task.get("name", "").strip()
    custom_fields = task.get("custom_fields", [])
    appt_id = f"clickup_{task_id}"

    # 1. Extract Core Fields
    parts = task_name.split('|')
    customer_name = _get_text_field(custom_fields, CLICKUP_FIELD_NAME)
    if not customer_name:
        customer_name = parts[0].strip() if parts[0].strip() else None
    location = parts[1].strip() if len(parts) > 1 else None

    raw_phone = _get_text_field(custom_fields, CLICKUP_FIELD_PHONE)
    customer_email = _get_text_field(custom_fields, CLICKUP_FIELD_EMAIL)
    
    service_labels = _get_scope_of_work(custom_fields)
    appointment_type = ", ".join(service_labels) if service_labels else "service"

    # 2. Validate Phone
    customer_phone = _validate_phone(raw_phone)
    phone_valid = bool(customer_phone)

    # 4. Determine Appointment Date — read EVERY allowlisted date field on the
    # task, ignoring Scope Of Work entirely. Pick the SOONEST upcoming date
    # (>= now); if none are upcoming, fall back to the MOST RECENT. Decoupling
    # from scope fixes two bugs: (1) scope-less tasks used to map to zero date
    # fields and get skipped despite having real dates; (2) returning customers'
    # stale past dates on an old service field no longer win just by appearing
    # earlier in the list.
    appointment_at = None
    now_utc = datetime.now(timezone.utc)
    date_candidates: list[tuple[datetime, str]] = []  # (utc_dt, field_id)
    for field_id in APPOINTMENT_DATE_FIELDS:
        for field in custom_fields:
            if field.get("id") == field_id:
                val = field.get("value")
                if val is not None:
                    try:
                        cand_dt = datetime.fromtimestamp(int(val) / 1000, tz=timezone.utc)
                        date_candidates.append((cand_dt, field_id))
                    except (ValueError, TypeError, OSError):
                        logger.warning(f"  Invalid date format for {task_id}")
                break

    upcoming = [c for c in date_candidates if c[0] >= now_utc]
    if upcoming:
        chosen = min(upcoming, key=lambda c: c[0])
    elif date_candidates:
        chosen = max(date_candidates, key=lambda c: c[0])
    else:
        chosen = None

    date_field_used = chosen[1] if chosen else None
    if chosen:
        appointment_at = chosen[0].strftime("%Y-%m-%d %H:%M:%S")
                
    if not appointment_at:
        # Still refresh contact fields for rows already in DB so stale email/phone/name
        # don't persist when ClickUp is updated after the initial insert.
        existing = conn.execute("SELECT id FROM appointments WHERE id=?", (appt_id,)).fetchone()
        if existing:
            conn.execute(
                """UPDATE appointments SET
                       customer_name  = COALESCE(?, customer_name),
                       customer_phone = COALESCE(?, customer_phone),
                       customer_email = COALESCE(?, customer_email),
                       updated_at     = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (customer_name, _validate_phone(raw_phone), customer_email, appt_id),
            )
            conn.commit()
        logger.info(f"  Skipped: {task_id} ({task_name!r}) — no scheduled date")
        stats["skipped"] += 1
        return task_id

    # 3 & 5. Handle Quarantine and UPSERT
    is_valid = phone_valid and customer_name
    
    try:
        # Check existing quarantine
        quar_row = conn.execute(
            "SELECT id, resolved FROM appointment_quarantine WHERE gcal_event_id = ?",
            (appt_id,)
        ).fetchone()

        if is_valid:
            # Upsert into appointments
            existing = conn.execute("SELECT id FROM appointments WHERE id = ?", (appt_id,)).fetchone()
            conn.execute(
                """INSERT INTO appointments
                   (id, calendar_source, customer_name, customer_phone, customer_email,
                    appointment_at, appointment_type, location, notes,
                    language, language_source, no_reminder,
                    raw_title, synced_at, updated_at)
                   VALUES (?, 'clickup', ?, ?, ?, ?, ?, ?, NULL,
                           'en', 'default', FALSE,
                           ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                   ON CONFLICT(id) DO UPDATE SET
                       customer_name    = excluded.customer_name,
                       customer_phone   = excluded.customer_phone,
                       customer_email   = excluded.customer_email,
                       appointment_at   = excluded.appointment_at,
                       appointment_type = excluded.appointment_type,
                       location         = excluded.location,
                       raw_title        = excluded.raw_title,
                       synced_at        = CURRENT_TIMESTAMP,
                       updated_at       = CURRENT_TIMESTAMP
                """,
                (appt_id, customer_name, customer_phone, customer_email,
                 appointment_at, appointment_type, location, task_name),
            )
            
            if quar_row and quar_row["resolved"] == 0:
                conn.execute(
                    "UPDATE appointment_quarantine SET resolved = 1, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (quar_row["id"],)
                )
                logger.info(f"  ✅ Auto-resolved quarantine for task {task_id}")
                stats["resolved"] += 1
            
            if existing:
                logger.info(f"  ✅ Updated task {task_id}")
                stats["updated"] += 1
            else:
                logger.info(f"  ✅ Inserted task {task_id}")
                stats["inserted"] += 1
                
        else:
            # Guard: if this customer is ALREADY a valid row in appointments, a
            # missing/unparseable phone on THIS fetch must not quarantine them —
            # we already have good data. Treat as a skip and preserve stored data.
            already = conn.execute(
                "SELECT id FROM appointments WHERE id = ?", (appt_id,)
            ).fetchone()
            if already:
                conn.execute(
                    """UPDATE appointments SET
                           customer_name  = COALESCE(?, customer_name),
                           customer_email = COALESCE(?, customer_email),
                           customer_phone = COALESCE(?, customer_phone),
                           updated_at     = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (customer_name, customer_email, customer_phone, appt_id),
                )
                # customer_phone here is the VALIDATED phone (None if invalid),
                # so COALESCE never overwrites a good stored phone with junk.
                if quar_row and quar_row["resolved"] == 0:
                    conn.execute(
                        "UPDATE appointment_quarantine SET resolved = 1, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (quar_row["id"],),
                    )
                conn.commit()
                logger.info(f"  ↩︎ Skipped {task_id}: already in appointments, keeping existing data")
                stats["skipped"] += 1
                return task_id

            # Missing data -> Quarantine
            reason = "missing_name" if not customer_name else ("missing_phone" if not raw_phone else "invalid_phone")

            if quar_row:
                # Update existing quarantine
                if quar_row["resolved"] == 1:
                    # Re-open it
                    conn.execute(
                        "UPDATE appointment_quarantine SET resolved = 0, quarantine_reason = ?, appointment_at = ? WHERE id = ?",
                        (reason, appointment_at, quar_row["id"])
                    )
                else:
                    # Optionally update raw data (we just update the reason and date)
                    conn.execute(
                        "UPDATE appointment_quarantine SET quarantine_reason = ?, appointment_at = ? WHERE id = ?",
                        (reason, appointment_at, quar_row["id"])
                    )
                logger.info(f"  ⏳ Task {task_id} remains quarantined: {reason}")
            else:
                # Insert new quarantine
                conn.execute(
                    """INSERT INTO appointment_quarantine
                       (gcal_event_id, calendar_source, raw_title, appointment_at, quarantine_reason)
                       VALUES (?, 'clickup', ?, ?, ?)""",
                    (appt_id, task_name, appointment_at, reason),
                )
                logger.info(f"  ❌ Quarantined task {task_id}: {reason}")
                stats["quarantined"] += 1

        conn.commit()
    except Exception as e:
        logger.error(f"  ERROR processing {task_id}: {e}")
        conn.rollback()
        stats["skipped"] += 1

    return task_id


def cleanup_orphaned(conn: sqlite3.Connection, active_task_ids: set) -> None:
    """Resolve quarantine entries for tasks that were deleted in ClickUp."""
    rows = conn.execute(
        "SELECT id, gcal_event_id FROM appointment_quarantine WHERE resolved = 0 AND calendar_source = 'clickup'"
    ).fetchall()
    
    for r in rows:
        gcal_id = r["gcal_event_id"]
        if gcal_id.startswith("clickup_"):
            task_id = gcal_id[8:]
            if task_id not in active_task_ids:
                conn.execute(
                    "UPDATE appointment_quarantine SET resolved = 1, resolved_at = CURRENT_TIMESTAMP, quarantine_reason = ? WHERE id = ?",
                    ("Task deleted from ClickUp", r["id"])
                )
                logger.info(f"  🧹 Cleaned up orphaned quarantine: {task_id}")
                stats["orphaned_cleaned"] += 1
    conn.commit()


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 60)
    logger.info("  EcoSave — Robust ClickUp Task Sync")
    logger.info("=" * 60)
    
    try:
        conn = get_connection()
    except Exception as e:
        logger.error(f"FATAL: Cannot connect to database: {e}")
        sys.exit(1)

    tasks = fetch_all_tasks()
    if not tasks:
        logger.info("\nNo tasks found. Nothing to import.")
        conn.close()
        return

    logger.info(f"\nProcessing {len(tasks)} tasks...\n")
    
    active_task_ids = set()
    for task in tasks:
        task_id = process_task(task, conn)
        if task_id:
            active_task_ids.add(task_id)

    # 7. Cleanup Orphaned Quarantine
    cleanup_orphaned(conn, active_task_ids)

    conn.close()

    logger.info("\n" + "=" * 60)
    logger.info("  Sync complete.")
    logger.info(f"  Fetched:     {stats['fetched']}")
    logger.info(f"  Inserted:    {stats['inserted']}")
    logger.info(f"  Updated:     {stats['updated']}")
    logger.info(f"  Skipped:     {stats['skipped']}  (no date)")
    logger.info(f"  Quarantined: {stats['quarantined']}  (newly quarantined)")
    logger.info(f"  Auto-Resolv: {stats['resolved']}  (fixed data)")
    logger.info(f"  Cleaned:     {stats['orphaned_cleaned']}  (orphaned)")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()

