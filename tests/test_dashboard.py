"""tests/test_dashboard.py — Dashboard route tests with session-based auth.

Uses test_db and non_closing_db fixtures (in-memory DB with schema + seed data).
Authenticates via session cookie instead of HTTP Basic Auth.
"""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from db.settings import Settings
from db.admin_users import hash_password
from auth.csrf import generate_csrf_token
from auth.session import _serializer, COOKIE_NAME


# ── Helper: Create test admin + get session cookie ─────────────────────────

def _create_test_admin(conn, email="admin@ecosave-group.com", password="TestPass123!@#"):
    """Insert a test admin user and return a signed session cookie dict."""
    hashed = hash_password(password)
    conn.execute(
        """
        INSERT INTO system_settings (key, value, updated_by)
        VALUES ('dashboard_password_hash', ?, 'test')
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        [hashed]
    )
    conn.execute(
        "INSERT OR IGNORE INTO admin_users (email, password_hash, role, is_active) VALUES (?, 'SHARED', ?, ?)",
        [email, "admin", 1],
    )
    conn.commit()
    token = _serializer.dumps({"email": email})
    return {COOKIE_NAME: token}


@pytest.fixture
def client(non_closing_db):
    """FastAPI TestClient with session auth cookie pre-set."""
    with patch("routes.dashboard.get_connection", return_value=non_closing_db), \
         patch("auth.session.get_connection", return_value=non_closing_db), \
         patch("db.admin_users.get_connection", return_value=non_closing_db):
        from webhook_server import app
        with TestClient(app) as tc:
            # Create admin user and set cookie
            cookies = _create_test_admin(non_closing_db)
            tc.cookies.update(cookies)
            yield tc


@pytest.fixture
def unauthed_client(non_closing_db):
    """FastAPI TestClient WITHOUT session cookie (for testing redirects)."""
    with patch("routes.dashboard.get_connection", return_value=non_closing_db), \
         patch("auth.session.get_connection", return_value=non_closing_db), \
         patch("db.admin_users.get_connection", return_value=non_closing_db):
        from webhook_server import app
        with TestClient(app) as tc:
            yield tc


class TestDashboardAuth:
    def test_get_dashboard_without_session_redirects_to_login(self, unauthed_client):
        resp = unauthed_client.get("/dashboard/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/dashboard/login" in resp.headers["location"]

    def test_get_dashboard_with_session_returns_200(self, client):
        resp = client.get("/dashboard/")
        assert resp.status_code == 200
        assert "System Overview" in resp.text

    def test_login_page_loads(self, unauthed_client):
        resp = unauthed_client.get("/dashboard/login")
        assert resp.status_code == 200
        assert "Sign In" in resp.text
        assert "csrf_token" in resp.text


class TestDashboardPages:
    def test_get_quarantine(self, client, test_db):
        # Insert unresolved quarantine item
        test_db.execute(
            """INSERT INTO appointment_quarantine
               (gcal_event_id, calendar_source, raw_title, raw_description, appointment_at, quarantine_reason, resolved)
               VALUES ('event-q1', 'hvac', 'John Doe, invalid phone', 'description here', '2026-06-16 10:00:00', 'invalid_phone', 0)"""
        )
        test_db.commit()

        resp = client.get("/dashboard/quarantine")
        assert resp.status_code == 200
        assert "John Doe, invalid phone" in resp.text
        assert "Quarantine Queue" in resp.text

    def test_get_settings(self, client):
        resp = client.get("/dashboard/settings")
        assert resp.status_code == 200
        assert "System Settings" in resp.text

    def test_get_deliveries(self, client, test_db):
        # Insert a notification attempt
        test_db.execute(
            """INSERT INTO appointments (id, calendar_source, customer_name, customer_phone, appointment_at, appointment_type)
               VALUES ('appt-d1', 'hvac', 'Alice Smith', '+12133734253', '2026-06-16 12:00:00', 'install')"""
        )
        test_db.execute(
            """INSERT INTO notification_attempts
               (appointment_id, appointment_at, rule_name, channel, to_address, provider_sid, status)
               VALUES ('appt-d1', '2026-06-16 12:00:00', 'customer_24h', 'sms', '+12133734253', 'SM_test_d1', 'delivered')"""
        )
        test_db.commit()

        resp = client.get("/dashboard/deliveries")
        assert resp.status_code == 200
        assert "Alice Smith" in resp.text
        assert "SM_test_d1" in resp.text


class TestDashboardPOSTActions:
    def test_post_settings_updates_db(self, client, test_db):
        resp = client.post(
            "/dashboard/settings",
            data={
                "notifications_paused": "true",
                "sms_enabled": "true",
                "email_enabled": "true",
                "quiet_hours_start": "09:00",
                "quiet_hours_end": "18:00",
                "quiet_hours_enabled": "true",
                "reminder_72h_enabled": "true",
                "timezone": "America/New_York",
                "csrf_token": generate_csrf_token(),
            },
            follow_redirects=True
        )
        assert resp.status_code == 200
        assert "System settings updated successfully!" in resp.text

        # Verify DB value
        settings = Settings(test_db)
        assert settings.is_paused() is True
        assert settings.get("quiet_hours_start") == "09:00"
        assert settings.get("quiet_hours_end") == "18:00"
        # Since reminder_24h_enabled wasn't sent, it should be false (unchecked)
        assert settings.get("reminder_24h_enabled") == "false"

    def test_post_appointment_language_override(self, client, test_db):
        # Insert an appointment
        test_db.execute(
            """INSERT INTO appointments (id, calendar_source, customer_name, customer_phone, appointment_at, appointment_type)
               VALUES ('appt-lang-1', 'hvac', 'Bob', '+12133734253', '2026-06-16 12:00:00', 'service')"""
        )
        test_db.commit()

        resp = client.post(
            "/dashboard/appointments/appt-lang-1/language",
            data={"language": "es", "csrf_token": generate_csrf_token()},
            follow_redirects=True
        )
        assert resp.status_code == 200

        # Verify DB update
        row = test_db.execute("SELECT language, language_source FROM appointments WHERE id='appt-lang-1'").fetchone()
        assert row["language"] == "es"
        assert row["language_source"] == "override"

    def test_post_appointment_toggle_no_reminder(self, client, test_db):
        # Insert an appointment
        test_db.execute(
            """INSERT INTO appointments (id, calendar_source, customer_name, customer_phone, appointment_at, appointment_type, no_reminder)
               VALUES ('appt-rem-1', 'hvac', 'Charlie', '+12133734253', '2026-06-16 12:00:00', 'service', 0)"""
        )
        test_db.commit()

        # Toggle to True
        resp = client.post(
            "/dashboard/appointments/appt-rem-1/no-reminder",
            follow_redirects=True
        )
        assert resp.status_code == 200
        row = test_db.execute("SELECT no_reminder FROM appointments WHERE id='appt-rem-1'").fetchone()
        assert bool(row["no_reminder"]) is True

        # Toggle back to False
        client.post(
            "/dashboard/appointments/appt-rem-1/no-reminder",
            follow_redirects=True
        )
        row = test_db.execute("SELECT no_reminder FROM appointments WHERE id='appt-rem-1'").fetchone()
        assert bool(row["no_reminder"]) is False

    def test_post_quarantine_dismiss(self, client, test_db):
        test_db.execute(
            """INSERT INTO appointment_quarantine
               (id, gcal_event_id, calendar_source, raw_title, appointment_at, quarantine_reason, resolved)
               VALUES (100, 'event-q2', 'hvac', 'Dismiss test', '2026-06-16 10:00:00', 'invalid_phone', 0)"""
        )
        test_db.commit()

        resp = client.post(
            "/dashboard/quarantine/100/dismiss",
            follow_redirects=True
        )
        assert resp.status_code == 200

        # Verify quarantine row resolved_by
        row = test_db.execute("SELECT resolved, resolved_by FROM appointment_quarantine WHERE id=100").fetchone()
        assert bool(row["resolved"]) is True
        assert row["resolved_by"] == "admin_dismiss"

        # Verify no appointment was created
        row_appt = test_db.execute("SELECT * FROM appointments WHERE id='event-q2'").fetchone()
        assert row_appt is None

    def test_post_quarantine_resolve_valid_phone(self, client, test_db):
        test_db.execute(
            """INSERT INTO appointment_quarantine
               (id, gcal_event_id, calendar_source, raw_title, raw_description, appointment_at, quarantine_reason, resolved)
               VALUES (101, 'event-q3', 'solar', 'Solar Estimate - Invalid Phone', 'some description', '2026-06-16 14:00:00', 'invalid_phone', 0)"""
        )
        test_db.commit()

        resp = client.post(
            "/dashboard/quarantine/101/resolve",
            data={
                "phone": "213-373-4253",
                "customer_name": "Dave Valid"
            },
            follow_redirects=True
        )
        assert resp.status_code == 200

        # Verify quarantine marked resolved
        row_q = test_db.execute("SELECT resolved, resolved_by FROM appointment_quarantine WHERE id=101").fetchone()
        assert bool(row_q["resolved"]) is True
        assert row_q["resolved_by"] == "admin_resolve"

        # Verify appointment created
        row_appt = test_db.execute("SELECT * FROM appointments WHERE id='event-q3'").fetchone()
        assert row_appt is not None
        assert row_appt["customer_name"] == "Dave Valid"
        assert row_appt["customer_phone"] == "+12133734253"
        assert row_appt["appointment_type"] == "estimate"
        assert row_appt["calendar_source"] == "solar"

    def test_post_quarantine_resolve_invalid_phone(self, client, test_db):
        test_db.execute(
            """INSERT INTO appointment_quarantine
               (id, gcal_event_id, calendar_source, raw_title, raw_description, appointment_at, quarantine_reason, resolved)
               VALUES (102, 'event-q4', 'solar', 'Solar Service', 'desc', '2026-06-16 14:00:00', 'invalid_phone', 0)"""
        )
        test_db.commit()

        resp = client.post(
            "/dashboard/quarantine/102/resolve",
            data={
                "phone": "not-a-phone",
                "customer_name": "Dave Invalid"
            },
            follow_redirects=True
        )
        # Should return 200 with validation error in response body, not redirect
        assert resp.status_code == 200
        assert "Invalid phone number" in resp.text
        assert "Dave Invalid" in resp.text

        # Verify quarantine NOT resolved
        row_q = test_db.execute("SELECT resolved FROM appointment_quarantine WHERE id=102").fetchone()
        assert bool(row_q["resolved"]) is False

        # Verify appointment NOT created
        row_appt = test_db.execute("SELECT * FROM appointments WHERE id='event-q4'").fetchone()
        assert row_appt is None
