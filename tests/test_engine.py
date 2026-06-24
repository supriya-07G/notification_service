"""tests/test_engine.py — Unit tests for notification_engine.

Uses test_db and non_closing_db fixtures (in-memory DB with schema + seed data).
Mocks channels.twilio_sms.send to avoid real Twilio calls.
Mocks datetime so we can control "now" precisely.
"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytz
import pytest

from db.settings import Settings


# ── Helpers ──────────────────────────────────────────────────────────────────

TZ = pytz.timezone("America/New_York")


def _insert_appointment(conn, appt_id, hours_from_now, now, **overrides):
    """Insert a test appointment *hours_from_now* hours into the future."""
    appt_at = now + timedelta(hours=hours_from_now)
    defaults = {
        "id": appt_id,
        "calendar_source": "hvac",
        "customer_name": "Alice Test",
        "customer_phone": "+12133734253",
        "customer_email": "alice@example.com",
        "appointment_at": appt_at.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S'),
        "appointment_type": "estimate",
        "location": "123 Main St",
        "language": "en",
        "no_reminder": False,
        "raw_title": "Test Appt",
        "raw_description": "",
    }
    defaults.update(overrides)
    conn.execute(
        """INSERT INTO appointments
           (id, calendar_source, customer_name, customer_phone, customer_email,
            appointment_at, appointment_type, location, language, no_reminder,
            raw_title, raw_description)
           VALUES (:id, :calendar_source, :customer_name, :customer_phone,
                   :customer_email, :appointment_at, :appointment_type,
                   :location, :language, :no_reminder,
                   :raw_title, :raw_description)""",
        defaults,
    )
    conn.commit()
    return defaults


def _run_engine(non_closing_db, now, can_send_return=True, is_quiet=False):
    """Run the engine with a fixed 'now' and mocked SMS send."""
    mock_sms_send = MagicMock(return_value="SM_fake_sid")

    with patch("notification_engine.get_connection", return_value=non_closing_db), \
         patch("notification_engine.datetime") as mock_dt, \
         patch("notification_engine.Settings.is_quiet_hours_active", return_value=is_quiet), \
         patch("notification_engine.sms_send", mock_sms_send), \
         patch("notification_engine.can_send", return_value=can_send_return):

        # Make datetime.now(tz) return our fixed time
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.strptime = datetime.strptime
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        from notification_engine import run
        stats = run()

    return stats, mock_sms_send


# ── Tests ────────────────────────────────────────────────────────────────────

class TestEngineKillSwitch:
    def test_paused_exits_immediately(self, test_db, non_closing_db):
        """When notifications_paused='true', engine returns without sending."""
        settings = Settings(test_db)
        settings.set("notifications_paused", "true")

        now = TZ.localize(datetime(2024, 6, 15, 10, 0))
        _insert_appointment(test_db, "appt-paused", 24, now)

        stats, mock_send = _run_engine(non_closing_db, now)

        assert stats["sms_sent"] == 0
        mock_send.assert_not_called()


class TestEngineQuietHours:
    def test_quiet_hours_exits_immediately(self, test_db, non_closing_db):
        """At 3am (outside 08:00–20:00 window), engine exits with no sends."""
        now = TZ.localize(datetime(2024, 6, 15, 3, 0))  # 3am — quiet
        _insert_appointment(test_db, "appt-quiet", 24, now)

        stats, mock_send = _run_engine(non_closing_db, now, is_quiet=True)

        assert stats["sms_sent"] == 0
        mock_send.assert_not_called()


class TestEngine24hRule:
    def test_appointment_24h_away_sends_sms(self, test_db, non_closing_db):
        """Appointment exactly 24h out matches customer_24h rule → SMS attempt created."""
        now = TZ.localize(datetime(2024, 6, 15, 10, 0))
        _insert_appointment(test_db, "appt-24h", 24, now)

        stats, mock_send = _run_engine(non_closing_db, now)

        assert stats["sms_sent"] >= 1
        mock_send.assert_called()

        # Verify notification_attempts row was inserted
        row = test_db.execute(
            "SELECT * FROM notification_attempts WHERE appointment_id='appt-24h' AND channel='sms'"
        ).fetchone()
        assert row is not None
        assert row["rule_name"] == "customer_24h"


class TestEngineDedup:
    def test_duplicate_attempt_skipped(self, test_db, non_closing_db):
        """Second engine run for same appointment+rule+channel is deduped."""
        now = TZ.localize(datetime(2024, 6, 15, 10, 0))
        appt_data = _insert_appointment(test_db, "appt-dup", 24, now)

        # Pre-insert the notification_attempts row to simulate first run
        test_db.execute(
            """INSERT INTO notification_attempts
               (appointment_id, appointment_at, rule_name, channel, to_address, status)
               VALUES (?, ?, 'customer_24h', 'sms', ?, 'queued')""",
            ["appt-dup", appt_data["appointment_at"], "+12133734253"],
        )
        test_db.commit()

        stats, mock_send = _run_engine(non_closing_db, now)

        assert stats["skipped_dedup"] >= 1


class TestEngineOptOut:
    def test_opted_out_phone_skipped(self, test_db, non_closing_db):
        """Phone in opt_outs → status='skipped_optout', SMS not sent."""
        now = TZ.localize(datetime(2024, 6, 15, 10, 0))
        _insert_appointment(test_db, "appt-optout", 24, now)

        # Insert opt-out
        test_db.execute(
            "INSERT INTO opt_outs (phone, channel, source) VALUES (?, ?, ?)",
            ["+12133734253", "sms", "inbound_stop"],
        )
        test_db.commit()

        stats, mock_send = _run_engine(non_closing_db, now, can_send_return=False)

        assert stats["skipped_optout"] >= 1
        mock_send.assert_not_called()

        # Check attempt status
        row = test_db.execute(
            "SELECT status FROM notification_attempts WHERE appointment_id='appt-optout' AND channel='sms'"
        ).fetchone()
        assert row is not None
        assert row["status"] == "skipped_optout"


class TestEngineNoReminder:
    def test_no_reminder_appointment_skipped(self, test_db, non_closing_db):
        """Appointments with no_reminder=TRUE are not processed at all."""
        now = TZ.localize(datetime(2024, 6, 15, 10, 0))
        _insert_appointment(test_db, "appt-noremind", 24, now, no_reminder=True)

        stats, mock_send = _run_engine(non_closing_db, now)

        assert stats["sms_sent"] == 0
        mock_send.assert_not_called()

        # No notification_attempts row should exist
        row = test_db.execute(
            "SELECT * FROM notification_attempts WHERE appointment_id='appt-noremind'"
        ).fetchone()
        assert row is None


class TestEngineNoMatchingWindow:
    def test_48h_away_no_matching_rule(self, test_db, non_closing_db):
        """Appointment 48h out doesn't match any rule window → nothing sent."""
        now = TZ.localize(datetime(2024, 6, 15, 10, 0))
        _insert_appointment(test_db, "appt-48h", 48, now)

        stats, mock_send = _run_engine(non_closing_db, now)

        assert stats["sms_sent"] == 0
        assert stats["email_queued"] == 0
        mock_send.assert_not_called()


class TestEngineEmailQueued:
    def test_24h_appointment_queues_email(self, test_db, non_closing_db):
        """Appointment 24h out with customer_24h rule → email written to email_queue."""
        test_db.execute(
            """INSERT INTO message_templates
               (channel, appointment_type, language, rule_name, subject, body)
               VALUES ('email', 'all', 'en', 'customer_24h', 'Test Subj', 'Test Body')"""
        )
        test_db.commit()

        now = TZ.localize(datetime(2024, 6, 15, 10, 0))
        _insert_appointment(test_db, "appt-email", 24, now)

        stats, _ = _run_engine(non_closing_db, now)

        assert stats["email_queued"] >= 1

        row = test_db.execute(
            "SELECT * FROM email_queue WHERE appointment_id='appt-email'"
        ).fetchone()
        assert row is not None
        assert row["rule_name"] == "customer_24h"
        assert row["to_address"] == "alice@example.com"
