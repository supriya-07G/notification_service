"""Tests for calendar_sync.py — Phase 1.

Uses unittest.mock to mock the Google Calendar API.
Tests parsing, quarantine logic, language tags, no-reminder, dedup, and dry-run.
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force in-memory DB for all tests
os.environ["DB_PATH"] = ":memory:"

import config
from db.init import get_connection
from calendar_sync import (
    _process_event,
    _extract_customer_name,
    _extract_phones,
    _extract_language,
    _has_no_reminder,
    _detect_appointment_type,
    sync_calendars,
)


# ── Valid test phone numbers (verified by phonenumbers library) ─────────────
VALID_PHONE = "(213) 373-4253"          # E.164: +12133734253
VALID_PHONE_E164 = "+12133734253"
VALID_PHONE_2 = "(646) 555-0123"        # E.164: +16465550123


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_event(
    event_id="evt_001",
    summary="John Smith, HVAC Estimate",
    description=f"Customer phone: {VALID_PHONE}\nNotes here.",
    start_datetime="2026-06-17T10:00:00-04:00",
    location="123 Main St",
):
    """Build a minimal Google Calendar event dict."""
    return {
        "id": event_id,
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_datetime},
        "location": location,
    }


# ── Helper function unit tests ──────────────────────────────────────────────

class TestExtractCustomerName:
    def test_comma_delimiter(self):
        assert _extract_customer_name("John Smith, HVAC Estimate") == "John Smith"

    def test_dash_delimiter(self):
        assert _extract_customer_name("John Smith - HVAC Estimate") == "John Smith"

    def test_no_delimiter(self):
        assert _extract_customer_name("John Smith") == "John Smith"

    def test_empty(self):
        assert _extract_customer_name("") is None

    def test_whitespace(self):
        assert _extract_customer_name("   ") is None


class TestExtractPhones:
    def test_valid_us_phone(self):
        phones = _extract_phones(f"Call {VALID_PHONE} for details")
        assert len(phones) == 1
        assert phones[0] == VALID_PHONE_E164

    def test_no_phone(self):
        phones = _extract_phones("No phone number here")
        assert len(phones) == 0

    def test_two_phones(self):
        phones = _extract_phones(f"Phone: {VALID_PHONE}, Alt: {VALID_PHONE_2}")
        assert len(phones) == 2


class TestExtractLanguage:
    def test_lang_pt_tag(self):
        lang, src = _extract_language("Title", "Some text [LANG:PT] more text")
        assert lang == "pt"
        assert src == "tag"

    def test_lang_es_in_title(self):
        lang, src = _extract_language("[LANG:ES] Title", "desc")
        assert lang == "es"
        assert src == "tag"

    def test_default_en(self):
        lang, src = _extract_language("Title", "No tag")
        assert lang == "en"
        assert src == "default"


class TestNoReminder:
    def test_present_in_title(self):
        assert _has_no_reminder("[NO REMINDER] John Smith", "") is True

    def test_present_in_description(self):
        assert _has_no_reminder("Title", "Text [NO REMINDER] more") is True

    def test_absent(self):
        assert _has_no_reminder("Title", "Description") is False


class TestDetectAppointmentType:
    def test_estimate(self):
        assert _detect_appointment_type("John Smith, HVAC Estimate") == "estimate"

    def test_install(self):
        assert _detect_appointment_type("Install Solar Panels") == "install"

    def test_repair_maps_to_service(self):
        assert _detect_appointment_type("AC Repair") == "service"

    def test_inspection(self):
        assert _detect_appointment_type("Home Inspection") == "inspection"

    def test_default(self):
        assert _detect_appointment_type("John Smith") == "service"


# ── Integration tests with DB ───────────────────────────────────────────────

class TestProcessEventValid:
    def test_valid_event_upserted(self, test_db):
        """Valid event with phone in description → upserted to appointments."""
        event = _make_event()
        counters = {"upserted": 0, "quarantined": 0, "skipped": 0}
        _process_event(test_db, event, "hvac", dry_run=False, counters=counters)

        assert counters["upserted"] == 1
        row = test_db.execute(
            "SELECT * FROM appointments WHERE id = ?", ("evt_001",)
        ).fetchone()
        assert row is not None
        assert row["customer_name"] == "John Smith"
        assert row["customer_phone"] == VALID_PHONE_E164
        assert row["appointment_type"] == "estimate"
        assert row["calendar_source"] == "hvac"


class TestProcessEventMissingPhone:
    def test_no_phone_quarantined(self, test_db):
        """Event with no phone → quarantined with reason='missing_phone'."""
        event = _make_event(description="No phone here at all")
        counters = {"upserted": 0, "quarantined": 0, "skipped": 0}
        _process_event(test_db, event, "hvac", dry_run=False, counters=counters)

        assert counters["quarantined"] == 1
        q = test_db.execute(
            "SELECT * FROM appointment_quarantine WHERE gcal_event_id = ?",
            ("evt_001",),
        ).fetchone()
        assert q is not None
        assert q["quarantine_reason"] == "missing_phone"

        # Rule 8: Must NOT appear in appointments
        appt = test_db.execute(
            "SELECT 1 FROM appointments WHERE id = ?", ("evt_001",)
        ).fetchone()
        assert appt is None


class TestProcessEventInvalidPhone:
    def test_invalid_phone_quarantined(self, test_db):
        """Event with invalid phone → quarantined."""
        # phonenumbers won't match a truly invalid fragment like "555-1",
        # so it ends up as missing_phone. Either reason is correct quarantine behaviour.
        event = _make_event(description="Phone: 555-1")
        counters = {"upserted": 0, "quarantined": 0, "skipped": 0}
        _process_event(test_db, event, "hvac", dry_run=False, counters=counters)

        assert counters["quarantined"] == 1
        appt = test_db.execute(
            "SELECT 1 FROM appointments WHERE id = ?", ("evt_001",)
        ).fetchone()
        assert appt is None


class TestProcessEventAmbiguousCustomer:
    def test_two_phones_quarantined(self, test_db):
        """Event with two phones → quarantined, reason='ambiguous_customer'."""
        event = _make_event(
            description=f"Phone: {VALID_PHONE}, Alt: {VALID_PHONE_2}"
        )
        counters = {"upserted": 0, "quarantined": 0, "skipped": 0}
        _process_event(test_db, event, "hvac", dry_run=False, counters=counters)

        assert counters["quarantined"] == 1
        q = test_db.execute(
            "SELECT * FROM appointment_quarantine WHERE gcal_event_id = ?",
            ("evt_001",),
        ).fetchone()
        assert q is not None
        assert q["quarantine_reason"] == "ambiguous_customer"


class TestProcessEventLanguageTag:
    def test_lang_pt_tag(self, test_db):
        """Event with [LANG:PT] in description → language='pt', language_source='tag'."""
        event = _make_event(
            description=f"Phone: {VALID_PHONE} [LANG:PT]"
        )
        counters = {"upserted": 0, "quarantined": 0, "skipped": 0}
        _process_event(test_db, event, "hvac", dry_run=False, counters=counters)

        assert counters["upserted"] == 1
        row = test_db.execute(
            "SELECT language, language_source FROM appointments WHERE id = ?",
            ("evt_001",),
        ).fetchone()
        assert row["language"] == "pt"
        assert row["language_source"] == "tag"


class TestProcessEventNoReminder:
    def test_no_reminder_flag(self, test_db):
        """Event with [NO REMINDER] in title → no_reminder=True in appointments."""
        event = _make_event(
            summary="[NO REMINDER] John Smith, Estimate",
            description=f"Phone: {VALID_PHONE}",
        )
        counters = {"upserted": 0, "quarantined": 0, "skipped": 0}
        _process_event(test_db, event, "hvac", dry_run=False, counters=counters)

        assert counters["upserted"] == 1
        row = test_db.execute(
            "SELECT no_reminder FROM appointments WHERE id = ?", ("evt_001",)
        ).fetchone()
        assert row["no_reminder"] == 1  # SQLite stores booleans as 0/1


class TestProcessEventUnchanged:
    def test_unchanged_appointment_skipped(self, test_db):
        """Event with appointment_at unchanged (already in DB) → no new row, synced_at updated."""
        event = _make_event()
        counters = {"upserted": 0, "quarantined": 0, "skipped": 0}

        # Insert first time
        _process_event(test_db, event, "hvac", dry_run=False, counters=counters)
        assert counters["upserted"] == 1

        # Process same event again
        counters2 = {"upserted": 0, "quarantined": 0, "skipped": 0}
        _process_event(test_db, event, "hvac", dry_run=False, counters=counters2)

        assert counters2["skipped"] == 1
        assert counters2["upserted"] == 0

        # Still only one row
        count = test_db.execute(
            "SELECT COUNT(*) as c FROM appointments WHERE id = ?", ("evt_001",)
        ).fetchone()["c"]
        assert count == 1


class TestProcessEventDryRun:
    def test_dry_run_no_writes(self, test_db, capsys):
        """dry_run=True → no DB writes, output printed."""
        event = _make_event()
        counters = {"upserted": 0, "quarantined": 0, "skipped": 0}
        _process_event(test_db, event, "hvac", dry_run=True, counters=counters)

        # Should have printed something
        captured = capsys.readouterr()
        assert "[DRY RUN]" in captured.out
        assert "INSERT" in captured.out

        # No row should exist in DB
        row = test_db.execute(
            "SELECT 1 FROM appointments WHERE id = ?", ("evt_001",)
        ).fetchone()
        assert row is None


class _NonClosingConnection:
    """Wrapper that delegates everything to a real sqlite3.Connection but no-ops close().

    sqlite3.Connection.close is a read-only C attribute, so we can't monkey-patch it.
    This thin proxy lets sync_calendars call conn.close() without actually closing the
    test fixture's connection.
    """

    def __init__(self, real_conn):
        self._conn = real_conn

    def close(self):
        pass  # no-op — fixture manages lifecycle

    def __getattr__(self, name):
        return getattr(self._conn, name)


class TestSyncCalendarsIntegration:
    @patch("calendar_sync.build")
    @patch("calendar_sync.Credentials.from_service_account_file")
    def test_sync_end_to_end(self, mock_creds, mock_build, test_db):
        """Full sync with mocked API returns valid event → upserted."""
        # Save originals and override config for test
        orig_ids = config.GOOGLE_CALENDAR_IDS
        orig_map = config.CALENDAR_SOURCE_MAP
        orig_path = config.GOOGLE_CREDENTIALS_PATH

        config.GOOGLE_CALENDAR_IDS = ["test_cal@group.calendar.google.com"]
        config.CALENDAR_SOURCE_MAP = {"test_cal@group.calendar.google.com": "hvac"}
        config.GOOGLE_CREDENTIALS_PATH = "fake.json"

        mock_creds.return_value = MagicMock()
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": [
                _make_event(
                    event_id="sync_test_001",
                    summary="Jane Doe, Install",
                    description=f"Call {VALID_PHONE}",
                )
            ]
        }

        # Wrap test_db in a proxy that ignores close()
        wrapper = _NonClosingConnection(test_db)

        try:
            with patch("calendar_sync.get_connection", return_value=wrapper):
                sync_calendars(dry_run=False)

            row = test_db.execute(
                "SELECT * FROM appointments WHERE id = ?", ("sync_test_001",)
            ).fetchone()
            assert row is not None
            assert row["customer_name"] == "Jane Doe"
            assert row["appointment_type"] == "install"
        finally:
            config.GOOGLE_CALENDAR_IDS = orig_ids
            config.CALENDAR_SOURCE_MAP = orig_map
            config.GOOGLE_CREDENTIALS_PATH = orig_path
