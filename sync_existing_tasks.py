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
    "4ddeb225-8a97-43e2-b13f-76e1ceba2421": "HVAC",
    "696b02c7-0ba1-4db9-88a8-d2046a34f988": "Heat Pump",
    "e7c41b98-904a-49a1-aeac-869689de714e": "Insulation",
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

SERVICE_DATE_MAP = {
    "HVAC": config.CLICKUP_FIELD_DATE_HVAC,
    "Heat Pump": config.CLICKUP_FIELD_DATE_HVAC,
    "Mini-Split": config.CLICKUP_FIELD_DATE_HVAC,
    "Central AC": config.CLICKUP_FIELD_DATE_HVAC,
    "Air Handler": config.CLICKUP_FIELD_DATE_HVAC,
    "Insulation": config.CLICKUP_FIELD_DATE_INSULATION,
    "Attic Insulation": config.CLICKUP_FIELD_DATE_INSULATION,
    "Wall Insulation": config.CLICKUP_FIELD_DATE_INSULATION,
    "Basement Insulation": config.CLICKUP_FIELD_DATE_INSULATION,
    "Electrical": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "Electrical Upgrade": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "Main Panel Upgrade": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "EV Charger": config.CLICKUP_FIELD_DATE_ELECTRICAL,
    "Energy Assessment": config.CLICKUP_FIELD_DATE_ASSESSMENT,
    "Vermiculite": config.CLICKUP_FIELD_DATE_REMEDIATION,
    "Asbestos Remediation": config.CLICKUP_FIELD_DATE_REMEDIATION,
    "Mold Remediation": config.CLICKUP_FIELD_DATE_REMEDIATION,
    "Solar": config.CLICKUP_FIELD_DATE_SOLAR,
    "Roof": config.CLICKUP_FIELD_DATE_ROOF,
    "Roof Repair": config.CLICKUP_FIELD_DATE_ROOF,
    "Roof Replacement": config.CLICKUP_FIELD_DATE_ROOF,
}

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
    return None

def _get_scope_of_work(custom_fields: list) -> list[str]:
    for field in custom_fields:
        if field.get("id") == CLICKUP_FIELD_SCOPE:
            val = field.get("value")
            if not val or not isinstance(val, list):
                return []
            
            selected = []
            options = field.get("type_config", {}).get("options", [])
            _index_to_name = {opt.get("orderindex"): (opt.get("name") or opt.get("label") or "").strip() for opt in options if isinstance(opt.get("orderindex"), int)}
            
            for item in val:
                if isinstance(item, str):
                    if item.strip():
                        selected.append(SCOPE_LABELS.get(item, item.strip()))
                elif isinstance(item, int):
                    selected.append(_index_to_name.get(item, str(item)))
                elif isinstance(item, dict):
                    label = (item.get("label") or item.get("name") or "").strip()
                    if label:
                        selected.append(label)
                    elif item.get("id"):
                        selected.append(SCOPE_LABELS.get(item["id"], item["id"]))
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

    # 4. Determine Appointment Date
    appointment_at = None
    for service in service_labels:
        field_id = SERVICE_DATE_MAP.get(service)
        if field_id:
            for field in custom_fields:
                if field.get("id") == field_id:
                    val = field.get("value")
                    if val is not None:
                        try:
                            # Convert to ISO-8601
                            utc_dt = datetime.fromtimestamp(int(val) / 1000, tz=timezone.utc)
                            appointment_at = utc_dt.isoformat()
                        except (ValueError, TypeError):
                            logger.warning(f"  Invalid date format for {task_id}")
            if appointment_at:
                break
                
    if not appointment_at:
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
                """INSERT OR REPLACE INTO appointments
                   (id, calendar_source, customer_name, customer_phone, customer_email,
                    appointment_at, appointment_type, location, language, language_source,
                    no_reminder, raw_title, synced_at, updated_at)
                   VALUES (?, 'clickup', ?, ?, ?, ?, ?, ?, 'en', 'default',
                           FALSE, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
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

